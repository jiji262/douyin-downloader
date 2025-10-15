import json
from pathlib import Path
from typing import Dict, Optional
from utils.logger import setup_logger

logger = setup_logger('CookieManager')


class CookieManager:
    def __init__(self, cookie_file: str = '.cookies.json'):
        self.cookie_file = Path(cookie_file)
        self.cookies: Dict[str, str] = {}

    def set_cookies(self, cookies: Dict[str, str]):
        self.cookies = cookies
        self._save_cookies()

    def get_cookies(self) -> Dict[str, str]:
        if not self.cookies:
            self._load_cookies()
        return self.cookies

    def get_cookie_string(self) -> str:
        cookies = self.get_cookies()
        return '; '.join([f"{k}={v}" for k, v in cookies.items()])

    def _save_cookies(self):
        try:
            with open(self.cookie_file, 'w', encoding='utf-8') as f:
                json.dump(self.cookies, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Failed to save cookies: {e}")

    def _load_cookies(self):
        if not self.cookie_file.exists():
            return

        try:
            with open(self.cookie_file, 'r', encoding='utf-8') as f:
                self.cookies = json.load(f)
        except Exception as e:
            logger.error(f"Failed to load cookies: {e}")

    def validate_cookies(self) -> bool:
        required_keys = {'msToken', 'ttwid', 'odin_tt', 'passport_csrf_token'}
        cookies = self.get_cookies()
        missing = [key for key in required_keys if key not in cookies or not cookies.get(key)]
        if missing:
            logger.warning(f"Cookie validation failed, missing: {', '.join(missing)}")
            return False
        return True

    def clear_cookies(self):
        self.cookies = {}
        if self.cookie_file.exists():
            self.cookie_file.unlink()
