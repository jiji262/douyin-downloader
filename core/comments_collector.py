"""评论采集：针对单个作品拉取全部评论（可选含二级回复），导出为 JSON。

设计要点：
- 复用 DouyinAPIClient 的分页请求与签名；每个请求（含二级回复）先过下载器的限速器
- 与下载流程解耦：作为独立的 helper，由 BaseDownloader 在保存媒体后按需调用
- 输出位置：与媒体同目录，文件名 `{file_stem}_comments.json`
- 支持上限 max_comments（默认 0 = 不限）和 include_replies
- 请求失败不等于没有评论：``_request_json`` 碰到风控空 200 / 非 JSON / 终态 4xx
  时返回 ``{}`` 而不抛异常，归一化后和「翻到底了」长得一样。旧实现把它写成
  ``comments: []`` 的文件、作品照算成功，空文件又挡住了之后的补抓（2026-11 用户
  反馈：限流后存下的 364 份评论文件全空）。现在逐页判失败：一条没取到就不写文件，
  也不拿更少的评论覆盖已有文件；写下的部分结果标 ``complete: false``。
- ``complete`` 是新增字段。没有它的旧文件按条数判断：0 条的分不清是真没评论还是
  失败产物，补抓时重新请求；有内容的视为已完成（见 ``needs_backfill``）。
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, NamedTuple, Optional

from core.api_client import page_request_failed
from utils.logger import setup_logger

if TYPE_CHECKING:  # pragma: no cover
    from control.rate_limiter import RateLimiter
    from core.api_client import DouyinAPIClient
    from storage.metadata_handler import MetadataHandler

logger = setup_logger("CommentsCollector")

# 超过这个大小的评论文件只读开头的 count / complete：每次补抓前把几 MB 的 JSON
# 完整解析一遍太浪费，而这么大的文件也不可能是 0 条的空文件。
_FULL_PARSE_MAX_BYTES = 64 * 1024
_HEAD_BYTES = 1024
_HEAD_COUNT = re.compile(rb'"count"\s*:\s*(\d+)')
_HEAD_COMPLETE = re.compile(rb'"complete"\s*:\s*(true|false)')


class CommentsFetchResult(NamedTuple):
    comments: List[Dict[str, Any]]
    # False：中途有请求失败 / 被风控，comments 只是已取到的部分（可能为空）。
    complete: bool
    error: str = ""


class CommentsSaveResult(NamedTuple):
    # 这次请求是否完整取到；BaseDownloader 的评论熔断按它计数。
    fetched: bool
    # 盘上的评论文件此刻是否完整：可能是这次写的，也可能是保留下来的旧文件。
    complete: bool
    # 这次是否写了文件。
    saved: bool
    # 这次请求取到的条数。
    count: int


class CommentsFileState(NamedTuple):
    exists: bool
    # None：旧版本写的文件，没有 complete 字段。
    complete: Optional[bool]
    count: int


_NO_FILE = CommentsFileState(exists=False, complete=None, count=0)


def needs_backfill(state: CommentsFileState) -> bool:
    """已下载的作品补抓评论时，盘上这份评论文件是否需要重新请求。"""
    if not state.exists:
        return True
    if state.complete is None:
        return state.count == 0
    return not state.complete


async def read_comments_state(path: Path) -> CommentsFileState:
    """读评论文件的完整度；放进线程池，免得大文件阻塞事件循环。"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _read_comments_state_sync, path)


def _read_comments_state_sync(path: Path) -> CommentsFileState:
    # 读不了 / 不像评论文件的一律当作没有：补抓会重新请求并覆盖它。
    try:
        size = path.stat().st_size
        if size <= 0:
            return _NO_FILE
        if size > _FULL_PARSE_MAX_BYTES:
            with open(path, "rb") as handle:
                state = _state_from_head(handle.read(_HEAD_BYTES))
            if state is not None:
                return state
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return _NO_FILE
    if not isinstance(data, dict) or not isinstance(data.get("comments"), list):
        return _NO_FILE
    complete = data.get("complete")
    return CommentsFileState(
        exists=True,
        complete=complete if isinstance(complete, bool) else None,
        count=len(data["comments"]),
    )


def _state_from_head(head: bytes) -> Optional[CommentsFileState]:
    """按本模块写出的键顺序（comments 排最后）从文件开头读 count / complete。"""
    end = head.find(b'"comments"')
    if end < 0:
        return None
    prefix = head[:end]
    count = _HEAD_COUNT.search(prefix)
    if count is None:
        return None
    complete = _HEAD_COMPLETE.search(prefix)
    return CommentsFileState(
        exists=True,
        complete=None if complete is None else complete.group(1) == b"true",
        count=int(count.group(1)),
    )


def _page_failure(page: Any) -> str:
    """这一页是否没请求成功，返回日志用的原因；成功返回空串。

    ``comments: null`` 不算失败：没有评论的作品可能就是这个形态（同
    ``page_request_failed`` 不收 ``items_missing`` 的理由）。
    """
    if not isinstance(page, dict):
        return "unexpected response"
    if "raw" in page and not page.get("raw"):
        return "empty response (likely anti-bot)"
    if page_request_failed(page):
        return "status_code={0}".format(page.get("status_code"))
    risk_flags = page.get("risk_flags")
    if not page.get("items") and isinstance(risk_flags, dict) and risk_flags.get("verify_page"):
        return "verification required"
    return ""


def _reply_total(comment: Dict[str, Any]) -> int:
    try:
        return int(comment.get("reply_comment_total") or 0)
    except (TypeError, ValueError):
        return 0


class CommentsCollector:
    def __init__(
        self,
        api_client: "DouyinAPIClient",
        metadata_handler: "MetadataHandler",
        *,
        include_replies: bool = False,
        max_comments: int = 0,
        page_size: int = 20,
        retry_delay_seconds: float = 1.0,
        rate_limiter: Optional["RateLimiter"] = None,
    ):
        self.api_client = api_client
        self.metadata_handler = metadata_handler
        self.include_replies = include_replies
        self.max_comments = int(max_comments or 0)
        self.page_size = max(1, int(page_size or 20))
        self.retry_delay_seconds = float(retry_delay_seconds or 1.0)
        # 评论逐页请求，热门作品一个就是上百页；不过限速器的话多线程下每秒十几个
        # 请求，正好撞上风控。
        self.rate_limiter = rate_limiter

    async def collect_and_save(self, aweme_id: str, output_path: Path) -> CommentsSaveResult:
        """抓取评论，按完整度决定是否写入 output_path。

        - 完整取到：写入；0 条也写，但不覆盖已有评论的文件（多半是被限流返回了
          看似正常的空页，宁可留着旧评论）。
        - 请求失败：一条没取到就不写；取到一部分时，只在盘上没有更完整的文件时
          写入并标 ``complete: false``，下次补抓会重新请求。
        """
        result = await self.collect(aweme_id)
        count = len(result.comments)
        if result.complete and count:
            saved = await self._write(aweme_id, result.comments, True, output_path)
            return CommentsSaveResult(fetched=True, complete=saved, saved=saved, count=count)

        existing = await read_comments_state(output_path)
        existing_done = not needs_backfill(existing)
        if result.complete:
            if existing.count:
                logger.warning(
                    "Comments for %s came back empty; keeping %d saved comments in %s",
                    aweme_id,
                    existing.count,
                    output_path,
                )
                return CommentsSaveResult(
                    fetched=True, complete=existing_done, saved=False, count=0
                )
            saved = await self._write(aweme_id, [], True, output_path)
            return CommentsSaveResult(fetched=True, complete=saved, saved=saved, count=0)

        if count and existing.complete is not True and existing.count < count:
            saved = await self._write(aweme_id, result.comments, False, output_path)
            return CommentsSaveResult(fetched=False, complete=False, saved=saved, count=count)
        logger.info(
            "Comments for %s not saved (got %d, existing file has %d)",
            aweme_id,
            count,
            existing.count,
        )
        return CommentsSaveResult(fetched=False, complete=existing_done, saved=False, count=count)

    async def collect(self, aweme_id: str) -> CommentsFetchResult:
        """抓取评论列表（不写盘）。请求失败时停下，带回已取到的部分。"""
        all_comments: List[Dict[str, Any]] = []
        cursor = 0
        seen_ids: set = set()
        reply_error = ""

        while True:
            await self._throttle()
            try:
                # 二级回复由 _attach_replies 逐条请求，见那里的说明。
                page = await self.api_client.get_aweme_comments(
                    aweme_id,
                    cursor=cursor,
                    count=self.page_size,
                    include_replies=False,
                )
            except Exception as exc:
                reason = "{0}: {1}".format(type(exc).__name__, exc)
                return self._incomplete(aweme_id, cursor, all_comments, reason)

            failure = _page_failure(page)
            if failure:
                return self._incomplete(aweme_id, cursor, all_comments, failure)

            items = page.get("items") or []
            page_comments: List[Dict[str, Any]] = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                cid = item.get("cid") or item.get("comment_id")
                key = str(cid) if cid else None
                if key and key in seen_ids:
                    continue
                if key:
                    seen_ids.add(key)
                page_comments.append(item)
                if 0 < self.max_comments <= len(all_comments) + len(page_comments):
                    break
            all_comments.extend(page_comments)

            if self.include_replies and not reply_error:
                reply_error = await self._attach_replies(aweme_id, page_comments)

            if 0 < self.max_comments <= len(all_comments):
                break
            if not items or not page.get("has_more"):
                break
            next_cursor = page.get("max_cursor") or 0
            if next_cursor == cursor:
                # cursor 未推进但服务器称 has_more=True：可能是接口变更或异常返回，
                # 升级为 warning 便于线上观察。
                logger.warning(
                    "Comments cursor stuck (aweme=%s, cursor=%s, has_more=True); "
                    "stopping to avoid infinite loop.",
                    aweme_id,
                    cursor,
                )
                break
            cursor = next_cursor
            await asyncio.sleep(self.retry_delay_seconds * 0.1)  # 轻度节流

        if reply_error:
            logger.warning("Comment replies incomplete for %s: %s", aweme_id, reply_error)
            return CommentsFetchResult(all_comments, False, reply_error)
        return CommentsFetchResult(all_comments, True)

    async def _attach_replies(self, aweme_id: str, comments: List[Dict[str, Any]]) -> str:
        """给有回复的评论挂上 ``_replies``（只取第一页，同旧实现），返回失败原因。

        不用 ``get_aweme_comments(include_replies=True)``：那条路径吞掉回复请求的失败、
        也不过限速器，被风控时会悄悄存下缺回复的评论还当成完整。
        """
        for comment in comments:
            comment_id = comment.get("cid") or comment.get("comment_id")
            if not comment_id or _reply_total(comment) <= 0:
                continue
            await self._throttle()
            try:
                page = await self.api_client.get_aweme_comment_replies(
                    aweme_id=aweme_id, comment_id=str(comment_id), count=self.page_size
                )
            except Exception as exc:
                return "replies of {0}: {1}: {2}".format(comment_id, type(exc).__name__, exc)
            if not isinstance(page, dict) or ("raw" in page and not page.get("raw")):
                return "replies of {0}: empty response (likely anti-bot)".format(comment_id)
            if page.get("status_code"):
                # 单条评论的回复取不到（被删、被折叠）不说明接口出了问题，跳过这一条。
                logger.debug(
                    "Replies of comment %s unavailable: status_code=%s",
                    comment_id,
                    page.get("status_code"),
                )
                continue
            comment["_replies"] = page.get("items") or []
        return ""

    async def _throttle(self) -> None:
        if self.rate_limiter is not None:
            await self.rate_limiter.acquire()

    @staticmethod
    def _incomplete(
        aweme_id: str, cursor: int, comments: List[Dict[str, Any]], reason: str
    ) -> CommentsFetchResult:
        logger.warning(
            "Comments fetch failed for %s cursor=%s: %s (%d comments so far)",
            aweme_id,
            cursor,
            reason,
            len(comments),
        )
        return CommentsFetchResult(comments, False, reason)

    async def _write(
        self,
        aweme_id: str,
        comments: List[Dict[str, Any]],
        complete: bool,
        output_path: Path,
    ) -> bool:
        payload = {
            "aweme_id": aweme_id,
            "count": len(comments),
            "include_replies": self.include_replies,
            # 排在 comments 前面：read_comments_state 读大文件时只看开头。
            "complete": complete,
            "comments": comments,
        }
        # MetadataHandler.save_metadata 内部已吞异常并返回 bool
        if await self.metadata_handler.save_metadata(payload, output_path):
            return True
        logger.warning("Failed to save comments for %s to %s", aweme_id, output_path)
        return False
