"""抖音放映厅（``lvdetail``）版权影视的能力门禁。

这类链接**解析得出来**（``url_type == "lvdetail"``）但永远不可下载：整轨
MPEG-CENC(AES-CTR) 加密，且作品详情接口对这类 id 直接回
``filter_reason=lvideo_not_support``。

这里锁住的是「用户看到的是真实原因，而不是通用的无法识别 / No downloader」——
即门禁本身，不是分类（分类在 ``tests/test_url_parser.py``）。
"""

from __future__ import annotations

import asyncio

import pytest

from config import ConfigLoader
from core import UNSUPPORTED_URL_TYPE_DETAIL, DownloaderFactory
from server.app import _execute_download, _ServerDeps

LVDETAIL_URL = (
    "https://www.douyin.com/lvdetail/6828500371023856142"
    "?previous_page_enter_method=live_cover&previous_page_sub_tab=movie"
)
GATE_DETAIL = UNSUPPORTED_URL_TYPE_DETAIL["lvdetail"]


def test_gate_detail_is_permanent_not_deferred():
    # 与 TikTok 的 "暂不支持（将于后续版本支持）" 区分：DRM 不是排期问题，
    # 文案里出现「暂」会让用户一直等一个不会来的版本。
    assert "不支持" in GATE_DETAIL
    assert "暂不支持" not in GATE_DETAIL
    assert "DRM" in GATE_DETAIL


def test_factory_refuses_to_build_a_downloader():
    assert (
        DownloaderFactory.create(
            "lvdetail",
            ConfigLoader(None),
            api_client=None,  # type: ignore[arg-type]
            file_manager=None,  # type: ignore[arg-type]
            cookie_manager=None,  # type: ignore[arg-type]
        )
        is None
    )


def test_execute_download_raises_the_real_reason(monkeypatch, tmp_path):
    """门禁必须早于 DownloaderFactory.create —— 工厂对这个类型返回 None，
    真让它走到那一步，用户看到的就是一条毫无信息量的内部错误。"""
    config = ConfigLoader(None)
    config.update(path=str(tmp_path))
    deps = _ServerDeps(config)

    created = []
    monkeypatch.setattr(
        DownloaderFactory, "create", staticmethod(lambda *a, **kw: created.append(a) or None)
    )

    with pytest.raises(RuntimeError, match="放映厅"):
        asyncio.run(_execute_download(LVDETAIL_URL, deps))

    assert created == []
