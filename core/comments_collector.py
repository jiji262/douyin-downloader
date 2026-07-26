"""Collect top-level Douyin comments and flatten replies into one JSON list."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, List, Optional, Tuple

from utils.logger import setup_logger

if TYPE_CHECKING:  # pragma: no cover
    from core.api_client import DouyinAPIClient
    from storage.metadata_handler import MetadataHandler


logger = setup_logger("CommentsCollector")

ReplyBrowserFallback = Callable[
    [str, Optional[str], List[Dict[str, Any]], int, int],
    Awaitable[Dict[str, Any]],
]

_SAFE_REPLY_ERROR_CODES = {
    "reply_api_failed",
    "reply_response_invalid",
    "reply_login_required",
    "reply_verification_required",
    "reply_timeout",
    "reply_comment_not_found",
    "reply_browser_failed",
}


def _comment_id(comment: Dict[str, Any]) -> str:
    return str(comment.get("cid") or comment.get("comment_id") or "").strip()


def _reply_total(comment: Dict[str, Any]) -> int:
    try:
        return max(0, int(comment.get("reply_comment_total") or 0))
    except (TypeError, ValueError):
        return 0


def _normalize_comment(
    comment: Dict[str, Any], *, parent_comment_id: str = ""
) -> Dict[str, Any]:
    normalized = dict(comment)
    key = _comment_id(normalized)
    normalized["parent_comment_id"] = parent_comment_id
    if key:
        normalized.setdefault("comment_key", key)
    return normalized


def _safe_error_code(value: Any, default: str = "reply_api_failed") -> str:
    code = str(value or "").strip()
    return code if code in _SAFE_REPLY_ERROR_CODES else default


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
        max_replies_per_comment: int = 20,
        max_replies_per_content: int = 200,
        reply_browser_fallback: Optional[ReplyBrowserFallback] = None,
        content_url_resolver: Optional[Callable[[str], Optional[str]]] = None,
    ):
        self.api_client = api_client
        self.metadata_handler = metadata_handler
        self.include_replies = bool(include_replies)
        self.max_comments = int(max_comments or 0)
        self.page_size = max(1, int(page_size or 20))
        self.retry_delay_seconds = float(retry_delay_seconds or 1.0)
        self.max_replies_per_comment = max(0, int(max_replies_per_comment or 0))
        self.max_replies_per_content = max(0, int(max_replies_per_content or 0))
        self.reply_browser_fallback = reply_browser_fallback
        self.content_url_resolver = content_url_resolver
        self._content_urls: Dict[str, str] = {}
        self._last_reply_metrics: Dict[str, Any] = {}

    def set_content_url(self, aweme_id: str, content_url: str) -> None:
        """Attach a safe detail-page URL for a later browser fallback."""
        if aweme_id and content_url:
            self._content_urls[str(aweme_id)] = str(content_url)

    def _content_url(self, aweme_id: str) -> Optional[str]:
        if aweme_id in self._content_urls:
            return self._content_urls[aweme_id]
        if self.content_url_resolver is None:
            return None
        try:
            value = self.content_url_resolver(aweme_id)
        except Exception:  # noqa: BLE001
            return None
        return str(value) if value else None

    async def collect_and_save(
        self, aweme_id: str, output_path: Path
    ) -> Optional[Dict[str, Any]]:
        comments = await self.collect(aweme_id)
        if comments is None:
            return None

        payload = {
            "aweme_id": aweme_id,
            "count": len(comments),
            "include_replies": self.include_replies,
            "comments": comments,
            **self._last_reply_metrics,
        }
        saved = await self.metadata_handler.save_metadata(payload, output_path)
        if not saved:
            logger.warning("Failed to save comments for %s to %s", aweme_id, output_path)
            return None
        return payload

    async def collect(self, aweme_id: str) -> Optional[List[Dict[str, Any]]]:
        all_comments: List[Dict[str, Any]] = []
        seen_ids: set[str] = set()
        cursor = 0

        while True:
            try:
                page = await self.api_client.get_aweme_comments(
                    aweme_id,
                    cursor=cursor,
                    count=self.page_size,
                    # Reply fetching is deliberately orchestrated here so API
                    # failures can be handed to the browser fallback.
                    include_replies=False,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Comments fetch error for %s cursor=%s: %s",
                    aweme_id,
                    cursor,
                    exc,
                )
                return None

            if not isinstance(page, dict):
                return None
            items = page.get("items") or []
            if not isinstance(items, list):
                return None
            if not items:
                break

            for item in items:
                if not isinstance(item, dict):
                    continue
                normalized = _normalize_comment(item)
                key = _comment_id(normalized)
                if key and key in seen_ids:
                    continue
                if key:
                    seen_ids.add(key)
                all_comments.append(normalized)
                if 0 < self.max_comments <= len(all_comments):
                    all_comments = all_comments[: self.max_comments]
                    break

            if 0 < self.max_comments <= len(all_comments):
                break
            if not page.get("has_more"):
                break
            next_cursor = page.get("max_cursor") or 0
            if next_cursor == cursor:
                logger.warning(
                    "Comments cursor stuck (aweme=%s, cursor=%s); stopping.",
                    aweme_id,
                    cursor,
                )
                break
            cursor = next_cursor
            await asyncio.sleep(self.retry_delay_seconds * 0.1)

        self._last_reply_metrics = self._empty_reply_metrics()
        if self._replies_enabled():
            await self._collect_replies(aweme_id, all_comments)
        return all_comments

    def _replies_enabled(self) -> bool:
        return bool(
            self.include_replies
            and self.max_replies_per_comment > 0
            and self.max_replies_per_content > 0
        )

    @staticmethod
    def _empty_reply_metrics() -> Dict[str, Any]:
        return {
            "replies_truncated": False,
            "reply_failures": [],
            "reply_api_attempted": 0,
            "reply_api_succeeded": 0,
            "reply_api_failed": 0,
            "reply_browser_fallback_attempted": 0,
            "reply_browser_fallback_succeeded": 0,
            "reply_browser_fallback_failed": 0,
            "reply_fallback_failure_counts": {},
        }

    async def _collect_replies(
        self, aweme_id: str, comments: List[Dict[str, Any]]
    ) -> None:
        failed: List[Dict[str, Any]] = []
        content_reply_count = 0
        metrics = self._last_reply_metrics

        for comment in comments:
            if content_reply_count >= self.max_replies_per_content:
                if _reply_total(comment) > 0:
                    metrics["replies_truncated"] = True
                continue
            parent_id = _comment_id(comment)
            if not parent_id or _reply_total(comment) <= 0:
                continue

            metrics["reply_api_attempted"] += 1
            remaining = self.max_replies_per_content - content_reply_count
            replies, error_code, truncated = await self._collect_reply_pages(
                aweme_id,
                parent_id,
                min(self.max_replies_per_comment, remaining),
            )
            metrics["replies_truncated"] = metrics["replies_truncated"] or truncated
            if error_code:
                metrics["reply_api_failed"] += 1
                failed.append(
                    {
                        "comment": comment,
                        "comment_id": parent_id,
                        "error_code": error_code,
                    }
                )
            else:
                metrics["reply_api_succeeded"] += 1
            content_reply_count += self._append_replies(
                comments, replies, parent_id
            )

        if not failed:
            return

        if self.reply_browser_fallback is None:
            self._record_failures(failed, fallback=False)
            return

        metrics["reply_browser_fallback_attempted"] += len(failed)
        try:
            result = await self.reply_browser_fallback(
                aweme_id,
                self._content_url(aweme_id),
                failed,
                self.max_replies_per_comment,
                max(0, self.max_replies_per_content - content_reply_count),
            )
        except asyncio.TimeoutError:
            result = {"failures": [{"error_code": "reply_timeout"}]}
        except Exception as exc:  # noqa: BLE001
            logger.warning("Browser reply fallback failed for %s: %s", aweme_id, exc)
            result = {"failures": [{"error_code": "reply_browser_failed"}]}

        if not isinstance(result, dict):
            result = {"failures": [{"error_code": "reply_browser_failed"}]}
        metrics["replies_truncated"] = metrics["replies_truncated"] or bool(
            result.get("truncated")
        )
        browser_replies = result.get("replies") or result.get("comments") or []
        if not isinstance(browser_replies, list):
            browser_replies = []
        added = 0
        for reply in browser_replies:
            if not isinstance(reply, dict) or added >= self.max_replies_per_content:
                break
            parent_id = str(reply.get("parent_comment_id") or "").strip()
            if not parent_id and len(failed) == 1:
                parent_id = str(failed[0]["comment_id"])
            normalized = _normalize_comment(reply, parent_comment_id=parent_id)
            key = _comment_id(normalized)
            if not key or any(_comment_id(item) == key for item in comments):
                continue
            comments.append(normalized)
            added += 1
        content_reply_count += added
        if added:
            metrics["reply_browser_fallback_succeeded"] += len(
                {str(item.get("comment_id")) for item in failed if item.get("comment_id")}
            )

        result_failures = result.get("failures") or []
        if not isinstance(result_failures, list):
            result_failures = []
        if result_failures:
            metrics["reply_browser_fallback_failed"] += len(failed)
            self._record_failures(result_failures, fallback=True)
        elif not added:
            metrics["reply_browser_fallback_failed"] += len(failed)
            self._record_failures(
                [{"error_code": "reply_browser_failed"} for _ in failed],
                fallback=True,
            )

    async def _collect_reply_pages(
        self, aweme_id: str, parent_id: str, limit: int
    ) -> Tuple[List[Dict[str, Any]], Optional[str], bool]:
        replies: List[Dict[str, Any]] = []
        seen_ids: set[str] = set()
        cursor = 0
        truncated = False
        while len(replies) < limit:
            count = min(self.page_size, limit - len(replies))
            try:
                page = await self.api_client.get_aweme_comment_replies(
                    aweme_id=aweme_id,
                    comment_id=parent_id,
                    cursor=cursor,
                    count=count,
                )
            except Exception as exc:  # noqa: BLE001
                code = _safe_error_code(
                    getattr(exc, "error_code", None),
                    "reply_api_failed",
                )
                return replies, code, truncated

            if not isinstance(page, dict):
                return replies, "reply_response_invalid", truncated
            items = page.get("items")
            if not isinstance(items, list):
                return replies, "reply_response_invalid", truncated
            if not items:
                if replies:
                    break
                return replies, "reply_response_invalid", truncated

            for item in items:
                if not isinstance(item, dict):
                    continue
                normalized = _normalize_comment(item, parent_comment_id=parent_id)
                key = _comment_id(normalized)
                if key and key in seen_ids:
                    continue
                if key:
                    seen_ids.add(key)
                replies.append(normalized)
                if len(replies) >= limit:
                    break

            if len(replies) >= limit:
                truncated = bool(page.get("has_more"))
                break
            if not page.get("has_more"):
                break
            next_cursor = page.get("max_cursor") or 0
            if next_cursor == cursor:
                truncated = True
                break
            cursor = next_cursor
            await asyncio.sleep(self.retry_delay_seconds * 0.1)
        return replies, None, truncated

    @staticmethod
    def _append_replies(
        comments: List[Dict[str, Any]],
        replies: List[Dict[str, Any]],
        parent_id: str,
    ) -> int:
        added = 0
        existing_ids = {_comment_id(item) for item in comments if _comment_id(item)}
        for reply in replies:
            key = _comment_id(reply)
            if key and key in existing_ids:
                continue
            normalized = _normalize_comment(reply, parent_comment_id=parent_id)
            comments.append(normalized)
            if key:
                existing_ids.add(key)
            added += 1
        return added

    def _record_failures(
        self, failures: List[Dict[str, Any]], *, fallback: bool
    ) -> None:
        metrics = self._last_reply_metrics
        for failure in failures:
            if not isinstance(failure, dict):
                continue
            code = _safe_error_code(
                failure.get("error_code"),
                "reply_browser_failed",
            )
            metrics["reply_failures"].append({"error_code": code})
            if fallback:
                counts = metrics["reply_fallback_failure_counts"]
                counts[code] = counts.get(code, 0) + 1
