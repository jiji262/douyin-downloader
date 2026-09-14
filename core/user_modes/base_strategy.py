from __future__ import annotations

from abc import ABC
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set

from core.api_client import LoginRequiredError
from core.downloader_base import DownloadResult
from utils.logger import setup_logger

if TYPE_CHECKING:
    from core.user_downloader import UserDownloader

logger = setup_logger("UserModeStrategy")

_MEDIA_TYPE_CHOICES = {"video", "gallery"}

# 抖音 web 端每页固定 20 条（更大的 count 会被服务端截断）。
PAGE_SIZE = 20

# 置顶 = 作者主页置顶（config.example.yml:33）。收藏 / 喜欢 / 收藏合集里的
# 条目属于别人，is_top 与浏览者的保存意图无关，按它丢弃等于悄悄漏下用户
# 明确收藏过的作品。
_VIEWER_FEED_MODES = {"collect", "collectmix", "like"}

_MODE_SCOPE_LABELS = {
    "post": "作品列表",
    "like": "喜欢列表",
    "collect": "收藏夹",
    "collectmix": "收藏合集",
    "mix": "合集列表",
    "music": "音乐列表",
}


# 空页的三种失败成因。前两种是确凿的「这次请求没成功」,抛异常;
# ``items_missing`` 见 ``_raise_if_page_request_failed`` 的说明,只软中断。
_CAUSE_REQUEST_FAILED = "请求失败"
_CAUSE_SERVER_ERROR = "接口报错"
_CAUSE_ITEMS_MISSING = "接口未返回列表"
_HARD_PAGE_FAILURE_CAUSES = frozenset({_CAUSE_REQUEST_FAILED, _CAUSE_SERVER_ERROR})

# 不折叠成「请求失败」的异常:没有 page_bridge_code(非 bridge 异常,含
# LoginRequiredError)与 bridge 服务未启动(UNAVAILABLE)。
_UNFOLDABLE_BRIDGE_CODES = frozenset({None, "UNAVAILABLE"})


class PageRequestFailedError(RuntimeError):
    """某一页请求失败（限流 / HTTP 错误 / 重试耗尽），而不是「翻到底了」。

    抛出来而不是 ``break``：``server.jobs`` 会把它记成 FAILED 并把原因推到
    SSE，用户看到真实原因，而不是在只下了前 20 条之后看到「成功」。
    """


async def fetch_page_folding_bridge_failure(fetcher: Any, *args: Any, **kwargs: Any) -> Any:
    """取一页;page bridge 的传输失败折成空 raw(= 请求失败)。

    aiohttp 路径的失败在 ``_request_json`` 里就回 ``{}``;桌面版经 page bridge 的
    TIMEOUT / PAGE_LOAD_FAILED 等却是抛出来的(异常带 ``page_bridge_code``)。
    分页 walk 统一折成 ``{}``,交给「空页三档」判据处理,别让一次瞬时失败绕过
    整页重试 / 按模式兜底,把整个任务打成 0 计数。``LoginRequiredError`` 不带
    这个属性,照常上抛。``UNAVAILABLE``(桌面 bridge 服务没起来)是确定性故障,
    重试 / 重新登录都没用,也照常上抛,让任务卡片带上真实原因。
    共享文件,不能 import ``core.page_bridge``,所以鸭子类型。
    """
    try:
        return await fetcher(*args, **kwargs)
    except Exception as exc:
        if getattr(exc, "page_bridge_code", None) in _UNFOLDABLE_BRIDGE_CODES:
            raise
        logger.warning("Page fetch via page bridge failed, treated as request failure: %s", exc)
        return {}


class BaseUserModeStrategy(ABC):
    mode_name = ""
    api_method_name = ""
    # 走查跑完了但内容不全时的人话原因。``UserDownloader`` 逐 mode 读它并挂到
    # ``DownloadResult`` 上,任务卡片显示「已完成,但没取全」而不是干净的成功。
    incomplete_reason: Optional[str] = None

    def __init__(self, downloader: "UserDownloader"):
        self.downloader = downloader
        self.incomplete_reason = None

    async def download_mode(
        self,
        sec_uid: str,
        user_info: Dict[str, Any],
        seen_aweme_ids: Optional[set[str]] = None,
    ) -> DownloadResult:
        items = await self.collect_items(sec_uid, user_info)
        items = self.apply_filters(items)
        author_name = user_info.get("nickname", "unknown")
        if seen_aweme_ids is None:
            seen_aweme_ids = set()
        return await self.downloader._download_mode_items(
            mode=self.mode_name,
            items=items,
            author_name=author_name,
            # Feed entries may be co-authored by another account. Keep the
            # directory nickname and sec_uid sourced from the same profile.
            author_sec_uid=user_info.get("sec_uid") or sec_uid,
            seen_aweme_ids=seen_aweme_ids,
        )

    async def collect_items(self, sec_uid: str, user_info: Dict[str, Any]) -> List[Dict[str, Any]]:
        return await self._collect_paged_aweme(sec_uid, user_info)

    def apply_filters(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        filtered = (
            items if self.mode_name in _VIEWER_FEED_MODES else self._filter_pinned_items(items)
        )
        filtered = self.downloader._filter_by_time(filtered)
        filtered = self._filter_by_media_type(filtered)
        return self.downloader._limit_count(filtered, self.mode_name)

    def _filter_pinned_items(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        filterer = getattr(self.downloader, "_filter_pinned_items", None)
        if callable(filterer):
            return filterer(items)
        return items

    def _scope_label(self) -> str:
        return _MODE_SCOPE_LABELS.get(self.mode_name, self.mode_name or "列表")

    @staticmethod
    def _page_request_failed(page: Dict[str, Any]) -> bool:
        """区分「请求失败」与「这一页真的没有了」。

        真实 api_client 的每一页都带 ``raw``；只有请求本身失败（HTTP 错误、
        非 JSON、重试耗尽、page bridge 非 200）时 ``_normalize_paged_response``
        才会给出空 ``raw``。测试替身可能压根不带 ``raw``，那就按正常空页处理。
        """
        return "raw" in page and not page.get("raw")

    @classmethod
    def _empty_page_failure_cause(cls, page: Dict[str, Any]) -> Optional[str]:
        """一条都没有的页里，哪些是确凿的失败证据，返回人话原因（否则 None）。

        「翻到底了」只有一种可信形态：请求成功、``status_code`` 为 0、列表是
        真 ``[]``。另外三种长得一样却都是失败——空 ``raw``（请求本身失败）、
        非零 ``status_code``（服务端报错，如风控 2154）、``items_missing``
        （``"aweme_list": null``，见 docs/spec/gotchas.md）。
        """
        if page.get("items"):
            return None
        if cls._page_request_failed(page):
            return _CAUSE_REQUEST_FAILED
        if page.get("status_code"):
            return _CAUSE_SERVER_ERROR
        if page.get("items_missing"):
            return _CAUSE_ITEMS_MISSING
        return None

    def _raise_if_page_request_failed(
        self, page: Dict[str, Any], *, scope: str, page_index: int
    ) -> None:
        """空页分三档:正常翻到底 / 软中断(留住已抓到的) / 硬失败(抛)。

        ``items_missing``(``"aweme_list": null``)是**软**的:本仓自己的同步层把
        它记作「推进中的分页空洞」并跳过继续翻(见
        ``core.my_content_service`` 的 ``_MAX_CONSECUTIVE_EMPTY_COLLECT_PAGES``),
        它也可能只是「这个收藏夹是空的」。对它抛异常会把「下了 20/60 条」变成
        「0 条 + 失败」,比原来的静默截断更糟。所以这里只停下走查、留住已抓到的
        条目并记原因,由 ``UserDownloader`` 上报「没取全」。
        真正的硬失败(请求本身失败、服务端报错码)才抛。
        """
        cause = self._empty_page_failure_cause(page)
        if cause is None:
            return
        detail = f"{scope} 第 {page_index} 页{cause}"
        if cause not in _HARD_PAGE_FAILURE_CAUSES:
            logger.warning("%s page %d %s, stopping walk", scope, page_index, cause)
            self.incomplete_reason = f"{detail}，内容可能不完整，请稍后重试"
            return
        logger.warning("%s page %d %s, aborting walk", scope, page_index, cause)
        raise PageRequestFailedError(f"{detail}（可能被限流或需要重新登录），请稍后重试")

    def _configured_media_types(self) -> Optional[Set[str]]:
        if self.mode_name == "music":
            return None
        raw = self.downloader.config.get("media_types", None)
        if not isinstance(raw, (list, tuple, set)):
            return None
        media_types = {value for value in raw if isinstance(value, str)}
        selected = media_types.intersection(_MEDIA_TYPE_CHOICES)
        if not selected or selected == _MEDIA_TYPE_CHOICES:
            return None
        return selected

    def _media_type_filter_enabled(self) -> bool:
        return self._configured_media_types() is not None

    def _filter_by_media_type(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        selected = self._configured_media_types()
        if selected is None:
            return items
        detector = getattr(self.downloader, "_detect_media_type", None)
        if not callable(detector):
            return items
        return [item for item in items if detector(item) in selected]

    async def _collect_paged_aweme(
        self, sec_uid: str, _user_info: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        fetcher = getattr(self.downloader.api_client, self.api_method_name, None)
        if not callable(fetcher):
            logger.warning(
                "Mode %s skipped: API method %s not implemented",
                self.mode_name,
                self.api_method_name,
            )
            return []

        aweme_list: List[Dict[str, Any]] = []
        max_cursor = 0
        has_more = True

        number_limit = int(self.downloader.config.get("number", {}).get(self.mode_name, 0) or 0)
        media_filter_enabled = self._media_type_filter_enabled()
        page_index = 0
        while has_more:
            await self.downloader.rate_limiter.acquire()
            request_cursor = max_cursor
            page_index += 1
            page_data = await fetch_page_folding_bridge_failure(
                fetcher, sec_uid, request_cursor, PAGE_SIZE
            )
            page = self._normalize_page_data(page_data)
            page_items = self.select_items(page)
            if not page_items:
                self._raise_if_page_request_failed(
                    page, scope=self._scope_label(), page_index=page_index
                )
                break

            aweme_list.extend(page_items)

            if number_limit > 0:
                if media_filter_enabled:
                    if len(self._filter_by_media_type(aweme_list)) >= number_limit:
                        break
                elif len(aweme_list) >= number_limit:
                    aweme_list = aweme_list[:number_limit]
                    break

            has_more = bool(page.get("has_more", False))
            max_cursor = int(page.get("max_cursor", 0) or 0)
            if has_more and max_cursor == request_cursor:
                logger.warning(
                    "Mode %s cursor did not advance (%s), stop paging",
                    self.mode_name,
                    max_cursor,
                )
                break

        return aweme_list

    def select_items(self, page_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        items = page_data.get("items")
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
        return []

    async def _collect_paged_entries(
        self,
        fetcher,
        *fetch_args: Any,
        count: int = PAGE_SIZE,
        scope: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        entries: List[Dict[str, Any]] = []
        max_cursor = 0
        has_more = True
        page_index = 0

        while has_more:
            await self.downloader.rate_limiter.acquire()
            request_cursor = max_cursor
            page_index += 1
            page_data = await fetch_page_folding_bridge_failure(
                fetcher, *fetch_args, request_cursor, count
            )
            page = self._normalize_page_data(page_data)
            page_items = self.select_items(page)
            if not page_items:
                self._raise_if_page_request_failed(
                    page, scope=scope or self._scope_label(), page_index=page_index
                )
                break

            entries.extend(page_items)
            has_more = bool(page.get("has_more", False))
            max_cursor = int(page.get("max_cursor", 0) or 0)
            if has_more and max_cursor == request_cursor:
                logger.warning(
                    "Mode %s cursor did not advance (%s), stop paging",
                    self.mode_name,
                    max_cursor,
                )
                break

        return entries

    async def _expand_metadata_items(
        self,
        raw_items: List[Dict[str, Any]],
        id_field: str,
        id_aliases: List[str],
        fetch_method_name: str,
    ) -> List[Dict[str, Any]]:
        """Shared expansion logic for mix/music strategies that receive metadata
        items instead of aweme items. Fetches the actual aweme list for each
        metadata entry using the given API method."""
        fetcher = getattr(self.downloader.api_client, fetch_method_name, None)
        if not callable(fetcher):
            return []

        expanded: List[Dict[str, Any]] = []
        seen_aweme: set[str] = set()

        for item in raw_items:
            entry_id = item.get(id_field)
            if not entry_id:
                for alias in id_aliases:
                    candidate = item.get(alias)
                    if not candidate:
                        info = item.get(f"{id_field.split('_')[0]}_info")
                        if isinstance(info, dict):
                            candidate = info.get(id_field) or info.get("id")
                    if candidate:
                        entry_id = candidate
                        break
            if not entry_id:
                continue

            cursor = 0
            has_more = True
            page_index = 0
            while has_more:
                await self.downloader.rate_limiter.acquire()
                page_index += 1
                try:
                    page_data = await fetcher(str(entry_id), cursor=cursor, count=20)
                except LoginRequiredError:
                    raise
                except Exception as exc:
                    # aiohttp 路径的失败都在 ``_request_json`` 里被折成空 ``raw``，
                    # 能走到这里的只有 page bridge 的传输失败(TIMEOUT / RENDERER_GONE
                    # 等)。以前 ``break`` 把它吞成「这个合集没有作品」，任务照样
                    # 成功；与下面的空页判定同档，按硬失败上抛。
                    logger.warning(
                        "Expansion fetch failed for %s=%s: %s",
                        id_field,
                        entry_id,
                        exc,
                    )
                    raise PageRequestFailedError(
                        f"{self._scope_label()} {entry_id} 第 {page_index} 页请求失败"
                        f"（{exc}），请稍后重试"
                    ) from exc
                page = self._normalize_page_data(page_data)
                page_items = page.get("items", [])
                if not page_items:
                    # 失败页与「这个合集翻完了」的 items 都是空；``break`` 会让
                    # 一次限流悄悄砍掉合集剩下的作品，任务照样报成功。
                    self._raise_if_page_request_failed(
                        page,
                        scope=f"{self._scope_label()} {entry_id}",
                        page_index=page_index,
                    )
                    break

                for aweme in page_items:
                    extracted = self._extract_aweme_from_item(aweme)
                    if not extracted:
                        continue
                    aweme_id = str(extracted.get("aweme_id") or "")
                    if not aweme_id or aweme_id in seen_aweme:
                        continue
                    seen_aweme.add(aweme_id)
                    expanded.append(extracted)

                has_more = bool(page.get("has_more", False))
                next_cursor = int(page.get("max_cursor", 0) or 0)
                if has_more and next_cursor == cursor:
                    logger.warning(
                        "%s %s cursor did not advance",
                        id_field,
                        entry_id,
                    )
                    break
                cursor = next_cursor

        return expanded

    @staticmethod
    def _extract_aweme_from_item(item: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(item, dict):
            return None
        if item.get("aweme_id"):
            return item
        for key in ("aweme", "aweme_info", "aweme_detail"):
            value = item.get(key)
            if isinstance(value, dict) and value.get("aweme_id"):
                return value
        return None

    @staticmethod
    def _normalize_page_data(data: Any) -> Dict[str, Any]:
        if not isinstance(data, dict):
            return {
                "items": [],
                "items_missing": True,
                "has_more": False,
                "max_cursor": 0,
                "status_code": -1,
            }

        if isinstance(data.get("items"), list):
            return {
                "items": data.get("items") or [],
                "items_missing": bool(data.get("items_missing")),
                "has_more": bool(data.get("has_more")),
                "max_cursor": int(data.get("max_cursor", 0) or 0),
                "status_code": int(data.get("status_code", 0) or 0),
                "raw": data.get("raw", data),
                "risk_flags": data.get("risk_flags", {}),
            }

        raw_items = data.get("aweme_list")
        return {
            "items": raw_items if isinstance(raw_items, list) else [],
            # 未归一化的原始响应：``aweme_list`` 在但不是列表 = 服务端没给列表。
            "items_missing": "aweme_list" in data and not isinstance(raw_items, list),
            "has_more": bool(data.get("has_more")),
            "max_cursor": int(data.get("max_cursor", 0) or 0),
            "status_code": int(data.get("status_code", 0) or 0),
            "raw": data,
            "risk_flags": {},
        }
