import os
import logging
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from utils.cookie_utils import parse_cookie_header, sanitize_cookies

from .default_config import DEFAULT_CONFIG

logger = logging.getLogger("ConfigLoader")


class ConfigLoader:
    def __init__(self, config_path: Optional[str] = None):
        self.config_path = config_path
        self.config = self._load_config()

    def _load_config(self) -> Dict[str, Any]:
        config = deepcopy(DEFAULT_CONFIG)
        override_sources: List[Dict[str, Any]] = []

        if self.config_path and os.path.exists(self.config_path):
            with open(self.config_path, "r", encoding="utf-8") as f:
                file_config = yaml.safe_load(f) or {}
                config = self._merge_config(config, file_config)
                override_sources.append(file_config)

        env_config = self._load_env_config()
        if env_config:
            config = self._merge_config(config, env_config)
            override_sources.append(env_config)

        return self._normalize_mix_aliases(config, override_sources)

    def _merge_config(
        self, base: Dict[str, Any], override: Dict[str, Any]
    ) -> Dict[str, Any]:
        result = base.copy()
        for key, value in override.items():
            if (
                key in result
                and isinstance(result[key], dict)
                and isinstance(value, dict)
            ):
                result[key] = self._merge_config(result[key], value)
            else:
                result[key] = value
        return result

    def _load_env_config(self) -> Dict[str, Any]:
        env_config = {}
        if os.getenv("DOUYIN_COOKIE"):
            env_config["cookie"] = os.getenv("DOUYIN_COOKIE")
        if os.getenv("DOUYIN_PATH"):
            env_config["path"] = os.getenv("DOUYIN_PATH")
        if os.getenv("DOUYIN_THREAD"):
            try:
                env_config["thread"] = int(os.getenv("DOUYIN_THREAD"))
            except (TypeError, ValueError):
                logger.warning(
                    "Invalid DOUYIN_THREAD value: %s, ignoring",
                    os.getenv("DOUYIN_THREAD"),
                )
        if os.getenv("DOUYIN_PROXY"):
            env_config["proxy"] = os.getenv("DOUYIN_PROXY")
        return env_config

    def _normalize_mix_aliases(
        self, config: Dict[str, Any], override_sources: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        # canonical key 为 mix，allmix 作为兼容别名保留并同步
        normalization_rules = (
            ("number", 0),
            ("increase", False),
        )
        for section, default_value in normalization_rules:
            section_config = config.get(section)
            if not isinstance(section_config, dict):
                section_config = {}
                config[section] = section_config

            mix_value = section_config.get("mix")
            allmix_value = section_config.get("allmix")
            section_default = DEFAULT_CONFIG.get(section, {})
            default_mix_value = (
                section_default.get("mix", default_value)
                if isinstance(section_default, dict)
                else default_value
            )
            default_allmix_value = (
                section_default.get("allmix", default_value)
                if isinstance(section_default, dict)
                else default_value
            )

            mix_is_default = mix_value == default_mix_value
            allmix_is_default = allmix_value == default_allmix_value

            mix_explicit = self._is_key_explicit_in_sources(
                override_sources, section, "mix"
            )
            allmix_explicit = self._is_key_explicit_in_sources(
                override_sources, section, "allmix"
            )

            if mix_explicit:
                canonical_value = mix_value
                if allmix_explicit and allmix_value != mix_value:
                    logger.warning(
                        "mix/allmix conflict detected in %s: mix=%s, allmix=%s; using mix=%s",
                        section,
                        mix_value,
                        allmix_value,
                        mix_value,
                    )
            elif allmix_explicit:
                canonical_value = allmix_value
            elif not mix_is_default:
                canonical_value = mix_value
                if not allmix_is_default and allmix_value != mix_value:
                    logger.warning(
                        "mix/allmix conflict detected in %s: mix=%s, allmix=%s; using mix=%s",
                        section,
                        mix_value,
                        allmix_value,
                        mix_value,
                    )
            elif not allmix_is_default:
                canonical_value = allmix_value
            else:
                canonical_value = default_value

            section_config["mix"] = canonical_value
            section_config["allmix"] = canonical_value

        return config

    @staticmethod
    def _is_key_explicit_in_sources(
        sources: List[Dict[str, Any]], section: str, key: str
    ) -> bool:
        for source in sources:
            if not isinstance(source, dict):
                continue
            section_value = source.get(section)
            if isinstance(section_value, dict) and key in section_value:
                return True
        return False

    def update(self, **kwargs):
        for key, value in kwargs.items():
            if key in self.config:
                if isinstance(self.config[key], dict) and isinstance(value, dict):
                    self.config[key].update(value)
                else:
                    self.config[key] = value
            else:
                self.config[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self.config.get(key, default)

    def get_cookies(self) -> Dict[str, str]:
        cookies_config = self.config.get("cookies") or self.config.get("cookie")

        if isinstance(cookies_config, str):
            if cookies_config == "auto":
                return {}
            return self._parse_cookie_string(cookies_config)
        elif isinstance(cookies_config, dict):
            return sanitize_cookies(cookies_config)
        return {}

    def _parse_cookie_string(self, cookie_str: str) -> Dict[str, str]:
        return sanitize_cookies(parse_cookie_header(cookie_str))

    def get_links(self) -> List[str]:
        links = self.config.get("link", [])
        if isinstance(links, str):
            return [links]
        return links

    def validate(self) -> bool:
        if not self.get_links():
            return False
        if not self.config.get("path"):
            return False

        thread = self.config.get("thread")
        if thread is not None:
            try:
                thread_val = int(thread)
                if thread_val < 1:
                    raise ValueError
                self.config["thread"] = thread_val
            except (TypeError, ValueError):
                logger.warning("Invalid thread value: %s, using default 5", thread)
                self.config["thread"] = 5

        retry_times = self.config.get("retry_times")
        if retry_times is not None:
            try:
                retry_val = int(retry_times)
                if retry_val < 0:
                    raise ValueError
                self.config["retry_times"] = retry_val
            except (TypeError, ValueError):
                logger.warning("Invalid retry_times value: %s, using default 3", retry_times)
                self.config["retry_times"] = 3

        for field in ("start_time", "end_time"):
            value = self.config.get(field)
            if value and isinstance(value, str):
                from datetime import datetime
                try:
                    datetime.strptime(value, "%Y-%m-%d")
                except ValueError:
                    logger.warning("Invalid %s format: %s (expected YYYY-MM-DD), clearing", field, value)
                    self.config[field] = ""

        return True
