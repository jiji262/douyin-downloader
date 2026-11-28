"""作者主页截图的文件名与去重规则（桌面版与 CLI 共用）。

每张截图的文件名都带截图日期和时间，旧的全部保留；同一天已经存过一张就不再截，
免得每次下载、每轮订阅检查都重开一次浏览器访问主页。想当天重截，删掉当天那张
即可。文件名格式必须与 ``desktop/src/main/homepage-screenshot.ts`` 的
``SCREENSHOT_FILENAME_RE`` 一致，否则 Electron 会拒收截图请求。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from utils.logger import setup_logger

logger = setup_logger("HomepageScreenshot")

HOMEPAGE_SCREENSHOT_STEM = "主页截图"


def next_homepage_screenshot_path(author_dir: Path, now: datetime) -> Optional[Path]:
    """返回这次截图的保存路径；``now`` 当天已有截图时返回 ``None``。

    只认改名完成的 ``主页截图_YYYY-MM-DD_HHMM.png``：写到一半的点开头 ``.tmp``
    和旧版不带时间的 ``主页截图.png`` 都不算当天截过。
    """
    day_prefix = f"{HOMEPAGE_SCREENSHOT_STEM}_{now:%Y-%m-%d}_"
    taken_today = author_dir.glob(f"{day_prefix}[0-9][0-9][0-9][0-9].png")
    if any(path.is_file() for path in taken_today):
        return None
    return author_dir / f"{day_prefix}{now:%H%M}.png"


async def request_homepage_screenshot(
    api_client: Any,
    author_dir: Path,
    sec_uid: str,
    *,
    profile: Optional[Dict[str, Any]] = None,
) -> None:
    """当天还没有截图时请求一张；异常留给调用方兜住，截图不能拖垮下载或订阅检查。"""
    target = next_homepage_screenshot_path(author_dir, datetime.now())
    if target is None:
        logger.info("Homepage screenshot skipped: already saved today for %s", sec_uid)
        return
    saved = await api_client.save_user_homepage_screenshot(
        sec_uid, target.resolve(), profile=profile
    )
    if not saved:
        logger.warning("Homepage screenshot was not saved for %s", sec_uid)
