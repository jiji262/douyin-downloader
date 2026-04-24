"""多渠道下载完成通知。

支持：
- Bark（iOS 推送）
- Telegram Bot
- 通用 Webhook（POST JSON）
- （企业微信可通过 Webhook 模式接入）

用法：
    notifier = build_notifier(config)
    await notifier.send(
        title="下载完成",
        body="成功 12 / 失败 0 / 跳过 3",
        level="success",
    )

配置示例（default_config.py）：
    "notifications": {
        "enabled": False,
        "on_success": True,
        "on_failure": True,
        "providers": [
            {"type": "bark", "url": "https://api.day.app/<device_key>"},
            {"type": "telegram", "bot_token": "...", "chat_id": "..."},
            {"type": "webhook", "url": "https://example.com/hook",
             "headers": {"Authorization": "Bearer ..."}},
        ],
    }
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import aiohttp

from utils.logger import setup_logger

logger = setup_logger("Notifier")


class _BaseProvider:
    def __init__(self, settings: Dict[str, Any]):
        self.settings = settings or {}

    async def send(
        self, session: aiohttp.ClientSession, title: str, body: str, level: str
    ) -> bool:
        raise NotImplementedError


class BarkProvider(_BaseProvider):
    """Bark 推送，URL 形如 https://api.day.app/<device_key>。

    参考：https://bark.day.app/
    """

    async def send(
        self, session: aiohttp.ClientSession, title: str, body: str, level: str
    ) -> bool:
        base_url = str(self.settings.get("url") or "").rstrip("/")
        if not base_url:
            logger.warning("Bark notification skipped: missing url")
            return False
        sound = str(self.settings.get("sound") or "")
        # Bark 以 URL path 传参：/{device_key}/{title}/{body}
        url = f"{base_url}/{quote(title, safe='')}/{quote(body, safe='')}"
        params: Dict[str, str] = {}
        if sound:
            params["sound"] = sound
        try:
            async with session.get(url, params=params) as resp:
                ok = resp.status == 200
                if not ok:
                    logger.warning("Bark notification HTTP %s", resp.status)
                return ok
        except Exception as exc:
            logger.warning("Bark notification failed: %s", exc)
            return False


class TelegramProvider(_BaseProvider):
    """Telegram Bot 推送。需要配置 bot_token 与 chat_id。"""

    async def send(
        self, session: aiohttp.ClientSession, title: str, body: str, level: str
    ) -> bool:
        bot_token = str(self.settings.get("bot_token") or "")
        chat_id = str(self.settings.get("chat_id") or "")
        if not bot_token or not chat_id:
            logger.warning("Telegram notification skipped: missing bot_token/chat_id")
            return False
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        text = f"*{title}*\n{body}" if title else body
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
        }
        try:
            async with session.post(url, json=payload) as resp:
                ok = resp.status == 200
                if not ok:
                    logger.warning("Telegram notification HTTP %s", resp.status)
                return ok
        except Exception as exc:
            logger.warning("Telegram notification failed: %s", exc)
            return False


class WebhookProvider(_BaseProvider):
    """通用 Webhook：POST JSON {title, body, level}。可用于接企业微信/飞书/钉钉 bot。"""

    async def send(
        self, session: aiohttp.ClientSession, title: str, body: str, level: str
    ) -> bool:
        url = str(self.settings.get("url") or "")
        if not url:
            logger.warning("Webhook notification skipped: missing url")
            return False
        extra_headers = {
            str(k): str(v) for k, v in (self.settings.get("headers") or {}).items()
        }
        payload: Dict[str, Any] = {"title": title, "body": body, "level": level}
        # 允许通过 extra_body 合并到 payload，便于适配某些平台（企业微信 msgtype 等）。
        extra_body = self.settings.get("extra_body")
        if isinstance(extra_body, dict):
            payload.update(extra_body)
        try:
            async with session.post(url, json=payload, headers=extra_headers) as resp:
                ok = resp.status < 400
                if not ok:
                    logger.warning("Webhook notification HTTP %s", resp.status)
                return ok
        except Exception as exc:
            logger.warning("Webhook notification failed: %s", exc)
            return False


_PROVIDER_REGISTRY = {
    "bark": BarkProvider,
    "telegram": TelegramProvider,
    "webhook": WebhookProvider,
}


class Notifier:
    """聚合通知器，并发分发至所有启用的 provider。"""

    def __init__(
        self,
        providers: List[_BaseProvider],
        *,
        on_success: bool = True,
        on_failure: bool = True,
    ):
        self.providers = providers
        self.on_success = on_success
        self.on_failure = on_failure

    @property
    def enabled(self) -> bool:
        return bool(self.providers)

    async def send(
        self,
        title: str,
        body: str,
        *,
        level: str = "info",
    ) -> Dict[str, bool]:
        """发送通知，返回 {provider_name: ok} 映射。"""
        if not self.providers:
            return {}

        is_failure = level in {"failure", "error"}
        is_success = level in {"success", "info"}
        if is_failure and not self.on_failure:
            return {}
        if is_success and not self.on_success:
            return {}

        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10)
        ) as session:
            tasks = [
                p.send(session, title=title, body=body, level=level)
                for p in self.providers
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        summary: Dict[str, bool] = {}
        for provider, result in zip(self.providers, results):
            name = type(provider).__name__
            if isinstance(result, Exception):
                logger.warning("Provider %s crashed: %s", name, result)
                summary[name] = False
            else:
                summary[name] = bool(result)
        return summary


def build_notifier(config_source: Any) -> Notifier:
    """从 ConfigLoader 或 dict 构造 Notifier。"""
    cfg: Any
    if hasattr(config_source, "get"):
        cfg = config_source.get("notifications", {}) or {}
    elif isinstance(config_source, dict):
        cfg = config_source.get("notifications", {}) or {}
    else:
        cfg = {}

    # 用户可能误写成 `notifications: on` 等 scalar：防御性降级为 disabled。
    if not isinstance(cfg, dict):
        logger.warning(
            "notifications config must be a dict, got %s; treating as disabled.",
            type(cfg).__name__,
        )
        return Notifier(providers=[])

    if not cfg.get("enabled", False):
        return Notifier(providers=[])

    providers: List[_BaseProvider] = []
    for entry in cfg.get("providers") or []:
        if not isinstance(entry, dict):
            continue
        ptype = str(entry.get("type") or "").strip().lower()
        cls = _PROVIDER_REGISTRY.get(ptype)
        if cls is None:
            logger.warning("Unknown notification provider type: %s", ptype)
            continue
        providers.append(cls(entry))

    return Notifier(
        providers=providers,
        on_success=bool(cfg.get("on_success", True)),
        on_failure=bool(cfg.get("on_failure", True)),
    )
