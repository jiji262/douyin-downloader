from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from auth import CookieManager
from config import ConfigLoader
from web.schemas import (
    ConfigResponse,
    ConfigUpdateRequest,
    CookieUpdateRequest,
    ErrorResponse,
)
from utils.logger import setup_logger

logger = setup_logger("ConfigRoute")

router = APIRouter(prefix="/api", tags=["config"])

_config: Optional[ConfigLoader] = None
_cookie_manager: Optional[CookieManager] = None
_config_path: str = "config.yml"


def set_config_path(config_path: str):
    global _config_path, _config, _cookie_manager
    _config_path = config_path
    _config = None
    _cookie_manager = None


def get_config() -> ConfigLoader:
    global _config
    if _config is None:
        _config = ConfigLoader(_config_path)
    return _config


def get_cookie_manager() -> CookieManager:
    global _cookie_manager
    if _cookie_manager is None:
        config = get_config()
        _cookie_manager = CookieManager()
        _cookie_manager.set_cookies(config.get_cookies())
    return _cookie_manager


@router.get(
    "/config",
    response_model=ConfigResponse,
    summary="获取当前配置",
    description="获取当前的下载配置信息",
)
async def get_current_config(config: ConfigLoader = Depends(get_config)):
    return ConfigResponse(
        path=config.get("path", "downloads"),
        thread=int(config.get("thread", 5) or 5),
        cover=bool(config.get("cover", True)),
        music=bool(config.get("music", True)),
        avatar=bool(config.get("avatar", False)),
        save_json=bool(config.get("json", False)),
        proxy=config.get("proxy"),
        rate_limit=float(config.get("rate_limit", 2) or 2),
    )


@router.put(
    "/config",
    response_model=ConfigResponse,
    responses={400: {"model": ErrorResponse}},
    summary="更新配置",
    description="更新下载配置",
)
async def update_config(
    request: ConfigUpdateRequest,
    config: ConfigLoader = Depends(get_config),
):
    try:
        if request.path is not None:
            config.update("path", request.path)
        if request.thread is not None:
            config.update("thread", request.thread)
        if request.cover is not None:
            config.update("cover", request.cover)
        if request.music is not None:
            config.update("music", request.music)
        if request.avatar is not None:
            config.update("avatar", request.avatar)
        if request.save_json is not None:
            config.update("json", request.save_json)
        if request.proxy is not None:
            config.update("proxy", request.proxy)
        if request.rate_limit is not None:
            config.update("rate_limit", request.rate_limit)

        logger.info("Config updated")
        return ConfigResponse(
            path=config.get("path", "downloads"),
            thread=int(config.get("thread", 5) or 5),
            cover=bool(config.get("cover", True)),
            music=bool(config.get("music", True)),
            avatar=bool(config.get("avatar", False)),
            save_json=bool(config.get("json", False)),
            proxy=config.get("proxy"),
            rate_limit=float(config.get("rate_limit", 2) or 2),
        )
    except Exception as e:
        logger.error("Failed to update config: %s", e)
        raise HTTPException(status_code=500, detail=f"更新配置失败: {str(e)}")


@router.get(
    "/cookies",
    summary="获取 Cookie 状态",
    description="获取当前 Cookie 的有效性状态",
)
async def get_cookie_status(
    cookie_manager: CookieManager = Depends(get_cookie_manager),
):
    is_valid = cookie_manager.validate_cookies()
    cookies = cookie_manager.get_cookies()
    cookie_keys = list(cookies.keys()) if cookies else []

    return {
        "valid": is_valid,
        "cookie_count": len(cookie_keys),
        "cookie_keys": cookie_keys,
    }


@router.post(
    "/cookies",
    responses={400: {"model": ErrorResponse}},
    summary="更新 Cookie",
    description="更新抖音下载所需的 Cookie",
)
async def update_cookies(
    request: CookieUpdateRequest,
    config: ConfigLoader = Depends(get_config),
    cookie_manager: CookieManager = Depends(get_cookie_manager),
):
    if not request.cookies:
        raise HTTPException(status_code=400, detail="Cookie 不能为空")

    try:
        cookie_manager.set_cookies(request.cookies)
        config.update_cookies(request.cookies)

        logger.info("Cookies updated, count: %d", len(request.cookies))

        is_valid = cookie_manager.validate_cookies()
        return {
            "message": "Cookie 已更新",
            "valid": is_valid,
            "cookie_count": len(request.cookies),
        }
    except Exception as e:
        logger.error("Failed to update cookies: %s", e)
        raise HTTPException(status_code=500, detail=f"更新 Cookie 失败: {str(e)}")
