"""下载器要把「当前博主的目录」上报给宿主任务。

任务卡片的「打开输出文件夹」原先只会打开设置里的下载根目录 —— 一个存了
几十个博主的目录，用户还得自己翻。作者目录只有下载器算得准（受
``author_dir`` 风格影响），所以由 ``_build_aweme_file_context`` 在每个作者
第一次出现时上报一次，reporter 侧再决定单作者/跨作者的取值。
"""

from __future__ import annotations

from typing import Any, Dict, List

from config import ConfigLoader
from core.video_downloader import VideoDownloader
from storage import FileManager


class _RecordingReporter:
    """只记录 ``on_output_dir``，其余进度回调按 no-op 处理。"""

    def __init__(self) -> None:
        self.paths: List[str] = []

    def on_output_dir(self, *, path: str) -> None:
        self.paths.append(path)

    def update_step(self, step: str, detail: str = "") -> None:
        pass

    def set_item_total(self, total: int, detail: str = "") -> None:
        pass

    def advance_item(self, status: str, detail: str = "") -> None:
        pass


def _aweme(aweme_id: str, sec_uid: str, nickname: str) -> Dict[str, Any]:
    return {
        "aweme_id": aweme_id,
        "desc": f"作品-{aweme_id}",
        "create_time": 1700000000,
        "author": {"nickname": nickname, "uid": "uid-1", "sec_uid": sec_uid},
    }


def _make_downloader(tmp_path, reporter, **config_overrides):
    config = ConfigLoader(None)
    config.update(path=str(tmp_path / "Downloaded"), **config_overrides)
    return VideoDownloader(
        config=config,
        api_client=None,
        file_manager=FileManager(str(tmp_path / "Downloaded")),
        cookie_manager=None,
        database=None,
        progress_reporter=reporter,
    )


def test_reports_author_dir_once_per_author(tmp_path):
    reporter = _RecordingReporter()
    downloader = _make_downloader(tmp_path, reporter)

    for aweme_id in ("1", "2", "3"):
        downloader._build_aweme_file_context(
            _aweme(aweme_id, "SEC_A", "博主甲"), "博主甲", "post"
        )

    assert reporter.paths == [str(tmp_path / "Downloaded" / "博主甲")]


def test_reports_each_distinct_author(tmp_path):
    """收藏夹会跨作者 —— 每个作者都要上报，由 reporter 决定怎么归并。"""
    reporter = _RecordingReporter()
    downloader = _make_downloader(tmp_path, reporter)

    downloader._build_aweme_file_context(_aweme("1", "SEC_A", "博主甲"), "博主甲", "collect")
    downloader._build_aweme_file_context(_aweme("2", "SEC_B", "博主乙"), "博主乙", "collect")

    assert reporter.paths == [
        str(tmp_path / "Downloaded" / "博主甲"),
        str(tmp_path / "Downloaded" / "博主乙"),
    ]


def test_reported_dir_follows_author_dir_style(tmp_path):
    """上报的目录必须与实际落盘目录一致，包括 author_dir 风格。"""
    reporter = _RecordingReporter()
    downloader = _make_downloader(tmp_path, reporter, author_dir="nickname_uid")

    context = downloader._build_aweme_file_context(
        _aweme("1", "SEC_A", "博主甲"), "博主甲", "post"
    )

    assert reporter.paths == [str(tmp_path / "Downloaded" / "博主甲_SEC_A")]
    # save_dir 落在上报目录之下，二者不能各算各的。
    assert str(context["save_dir"]).startswith(reporter.paths[0])


def test_no_reporter_is_a_noop(tmp_path):
    """CLI 没有 reporter —— 不能因为多了这条上报就崩。"""
    downloader = _make_downloader(tmp_path, None)
    assert downloader._build_aweme_file_context(
        _aweme("1", "SEC_A", "博主甲"), "博主甲", "post"
    )
