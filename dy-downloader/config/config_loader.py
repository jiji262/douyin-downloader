import os
import yaml
from pathlib import Path
from typing import Dict, Any, Optional, List
from .default_config import DEFAULT_CONFIG


class ConfigLoader:
    def __init__(self, config_path: Optional[str] = None):
        self.config_path = config_path
        self.config = self._load_config()

    def _load_config(self) -> Dict[str, Any]:
        config = DEFAULT_CONFIG.copy()

        if self.config_path and os.path.exists(self.config_path):
            with open(self.config_path, 'r', encoding='utf-8') as f:
                file_config = yaml.safe_load(f) or {}
                config = self._merge_config(config, file_config)

        env_config = self._load_env_config()
        if env_config:
            config = self._merge_config(config, env_config)

        return config

    def _merge_config(self, base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
        result = base.copy()
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._merge_config(result[key], value)
            else:
                result[key] = value
        return result

    def _load_env_config(self) -> Dict[str, Any]:
        env_config = {}
        if os.getenv('DOUYIN_COOKIE'):
            env_config['cookie'] = os.getenv('DOUYIN_COOKIE')
        if os.getenv('DOUYIN_PATH'):
            env_config['path'] = os.getenv('DOUYIN_PATH')
        if os.getenv('DOUYIN_THREAD'):
            env_config['thread'] = int(os.getenv('DOUYIN_THREAD'))
        return env_config

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
        cookies_config = self.config.get('cookies') or self.config.get('cookie')

        if isinstance(cookies_config, str):
            if cookies_config == 'auto':
                return {}
            return self._parse_cookie_string(cookies_config)
        elif isinstance(cookies_config, dict):
            return cookies_config
        return {}

    def _parse_cookie_string(self, cookie_str: str) -> Dict[str, str]:
        cookies = {}
        for item in cookie_str.split(';'):
            item = item.strip()
            if '=' in item:
                key, value = item.split('=', 1)
                cookies[key.strip()] = value.strip()
        return cookies

    def get_links(self) -> List[str]:
        links = self.config.get('link', [])
        if isinstance(links, str):
            return [links]
        return links

    def validate(self) -> bool:
        if not self.get_links():
            return False
        if not self.config.get('path'):
            return False
        return True
