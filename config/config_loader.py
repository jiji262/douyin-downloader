import json
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
            if cookies_config.strip().lower() == "auto":
                return self._load_auto_cookies()
            return self._parse_cookie_string(cookies_config)
        elif isinstance(cookies_config, dict):
            return sanitize_cookies(cookies_config)
        if self._auto_cookie_enabled():
            return self._load_auto_cookies()
        return {}

    def _parse_cookie_string(self, cookie_str: str) -> Dict[str, str]:
        return sanitize_cookies(parse_cookie_header(cookie_str))

    def _auto_cookie_enabled(self) -> bool:
        raw_value = self.config.get("auto_cookie")
        if isinstance(raw_value, str):
            return raw_value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(raw_value)

    def _load_auto_cookies(self) -> Dict[str, str]:
        for path in self._candidate_auto_cookie_paths():
            cookies = self._load_cookie_file(path)
            if cookies:
                logger.info("Loaded auto cookies from %s", path)
                return cookies
        return {}

    def _candidate_auto_cookie_paths(self) -> List[Path]:
        config_dir = (
            Path(self.config_path).resolve().parent
            if self.config_path
            else Path.cwd().resolve()
        )
        search_roots = [
            config_dir,
            config_dir.parent,
            Path.cwd().resolve(),
        ]
        candidates: List[Path] = []
        for root in search_roots:
            candidates.extend(
                [
                    root / "config" / "cookies.json",
                    root / ".cookies.json",
                ]
            )

        unique: List[Path] = []
        seen: set[str] = set()
        for candidate in candidates:
            resolved = str(candidate.resolve())
            if resolved in seen:
                continue
            seen.add(resolved)
            unique.append(candidate)
        return unique

    @staticmethod
    def _load_cookie_file(path: Path) -> Dict[str, str]:
        if not path.exists():
            return {}
        try:
            raw_data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Failed to load auto cookie file %s: %s", path, exc)
            return {}

        if raw_data is None:
            return {}
        if not isinstance(raw_data, dict):
            logger.warning("Auto cookie file %s is not a JSON object", path)
            return {}
        return sanitize_cookies(raw_data)

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
