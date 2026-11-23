"""旧版截断掉作品 ID 的文件名：增量下载要认出盘上已有的作品，而不是再下一遍。

0.5.0 及更早的版本把整段 ``{date}_{title}_{id}`` 截到 80 个字符，长标题作品的文件名
没有作品 ID。下面用冻结的旧规则造出这类文件名，两个时期的清洗规则都覆盖。
"""

import re
from datetime import datetime

import pytest

from auth import CookieManager
from config import ConfigLoader
from control import QueueManager, RateLimiter, RetryHandler
from core import item_reasons
from core.api_client import DouyinAPIClient
from core.video_downloader import VideoDownloader
from storage import FileManager

LEGACY_CAP = 80
PUBLISH_TS = 1747303025
LONG_DESC = (
    "（问句思维）“我怎么能忘记呢？”不能先找“我”字 问句有特定的框架，动词提前不能忘~"
    "#零基础英语 #英语语法 #四级英语 #英语口语天天练"
)


def _legacy_sanitize(name, collapse_blank_runs):
    """冻结的旧版 sanitize_filename：早期把空白和下划线一起折叠，后来分开折叠。"""
    name = name.replace("\n", " ").replace("\r", " ")
    name = re.sub(r'[<>:"/\\|?*#\x00-\x1f]', "_", name)
    if collapse_blank_runs:
        name = re.sub(r"[\s_]+", "_", name)
    else:
        name = re.sub(r" +", " ", re.sub(r"_+", "_", name))
    name = name.strip("._- ")
    if len(name) > LEGACY_CAP:
        name = name[:LEGACY_CAP].rstrip("._- ")
    return name or "untitled"


def _publish_date(item):
    return datetime.fromtimestamp(item["create_time"]).strftime("%Y-%m-%d")


def _legacy_stem(item, collapse_blank_runs=False):
    raw = "{0}_{1}_{2}".format(_publish_date(item), item["desc"], item["aweme_id"])
    return _legacy_sanitize(raw, collapse_blank_runs)


def _aweme(aweme_id, desc=LONG_DESC, create_time=PUBLISH_TS):
    return {"aweme_id": aweme_id, "desc": desc, "create_time": create_time}


def _write(path, data=b"media"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _legacy_dir(root, item):
    return root / "作者" / "post" / _legacy_stem(item)


def _downloader(root):
    config = ConfigLoader()
    config.update(path=str(root))
    return VideoDownloader(
        config,
        DouyinAPIClient({}),
        FileManager(str(root)),
        CookieManager(str(root / ".cookies.json")),
        database=None,
        rate_limiter=RateLimiter(max_per_second=5),
        retry_handler=RetryHandler(max_retries=1),
        queue_manager=QueueManager(max_workers=1),
    )


@pytest.mark.parametrize("collapse_blank_runs", [True, False])
async def test_legacy_truncated_video_name_counts_as_downloaded(tmp_path, collapse_blank_runs):
    item = _aweme("7514233556677889900")
    stem = _legacy_stem(item, collapse_blank_runs)
    assert item["aweme_id"][:4] not in stem
    _write(_legacy_dir(tmp_path, item) / f"{stem}.mp4")

    downloader = _downloader(tmp_path)

    assert await downloader._should_download(item["aweme_id"], aweme=item) is False
    assert (
        downloader.pop_item_reason(item["aweme_id"], "skipped")
        == item_reasons.SKIP_LOCAL_FILE_EXISTS
    )


async def test_legacy_name_with_leftover_id_digits_counts_as_downloaded(tmp_path):
    # 截断刚好留下 16 位 ID：正则会把它当成另一个作品的 ID，不能因此认不出本作品。
    item = _aweme("7504479745674743040", desc="a" * 52)
    stem = _legacy_stem(item)
    assert stem.endswith("_" + item["aweme_id"][:16])
    _write(_legacy_dir(tmp_path, item) / f"{stem}.mp4")

    assert await _downloader(tmp_path)._should_download(item["aweme_id"], aweme=item) is False


async def test_legacy_truncated_gallery_names_count_as_downloaded(tmp_path):
    item = _aweme("7499893700321103139")
    folder = _legacy_dir(tmp_path, item)
    stem = _legacy_stem(item)
    _write(folder / f"{stem}_1.webp")
    _write(folder / f"{stem}_2.jpeg")
    _write(folder / f"{stem}_live_1.mp4")

    assert await _downloader(tmp_path)._should_download(item["aweme_id"], aweme=item) is False


async def test_legacy_gallery_name_trimmed_below_cap_counts_as_downloaded(tmp_path):
    # 截断点落在「 #」上，去掉结尾的空格和下划线只剩 78 个字符，加上 _1 又回到 80。
    item = _aweme("7499893700321103141", desc="很" * 67 + " #标签一 #标签二")
    stem = _legacy_stem(item)
    assert len(stem) == 78
    _write(_legacy_dir(tmp_path, item) / f"{stem}_1.webp")

    assert await _downloader(tmp_path)._should_download(item["aweme_id"], aweme=item) is False


async def test_legacy_live_photo_name_alone_counts_as_downloaded(tmp_path):
    # 与按 ID 认文件的口径一致：实况视频本身也是主媒体。
    item = _aweme("7499893700321103140")
    _write(_legacy_dir(tmp_path, item) / f"{_legacy_stem(item)}_live_1.mp4")

    assert await _downloader(tmp_path)._should_download(item["aweme_id"], aweme=item) is False


async def test_legacy_name_still_redownloads_when_primary_media_is_gone(tmp_path):
    item = _aweme("7500409638891326735")
    folder = _legacy_dir(tmp_path, item)
    stem = _legacy_stem(item)
    _write(folder / f"{stem}_cover.jpg")
    _write(folder / f"{stem}.mp4", data=b"")

    assert await _downloader(tmp_path)._should_download(item["aweme_id"], aweme=item) is True


async def test_legacy_name_does_not_cover_other_captions_or_days(tmp_path):
    saved = _aweme("7503808558182812980")
    _write(_legacy_dir(tmp_path, saved) / f"{_legacy_stem(saved)}.mp4")
    other_caption = _aweme("7503808558182812981", desc="同一天的另一条" + LONG_DESC)
    other_day = _aweme("7503808558182812982", create_time=PUBLISH_TS + 86400 * 2)

    downloader = _downloader(tmp_path)

    assert await downloader._should_download(other_caption["aweme_id"], aweme=other_caption)
    assert await downloader._should_download(other_day["aweme_id"], aweme=other_day)


async def test_short_name_without_id_still_redownloads(tmp_path):
    # 自定义模板去掉 {id} 时按文档会再次下载；只有旧版截断名才走兜底。
    item = _aweme("7613345678901234567", desc="短标题")
    _write(tmp_path / "作者" / "post" / "{0}_短标题.mp4".format(_publish_date(item)))

    assert await _downloader(tmp_path)._should_download(item["aweme_id"], aweme=item) is True
