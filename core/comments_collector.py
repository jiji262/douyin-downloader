"""Bounded, parent-linked Douyin comment collection."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from core.api_client import LoginRequiredError
from utils.logger import setup_logger

if TYPE_CHECKING:  # pragma: no cover
    from core.api_client import DouyinAPIClient
    from storage.metadata_handler import MetadataHandler

logger = setup_logger("CommentsCollector")

_REPLY_FAILURE_CODES = frozenset(
    {
        "reply_timeout",
        "reply_login_required",
        "reply_verification_required",
        "reply_api_failed",
        "reply_response_invalid",
    }
)


@dataclass(frozen=True)
class ReplyFailure:
    aweme_id: str
    comment_id: str
    error_code: str

    def __post_init__(self) -> None:
        if (
            not self.aweme_id
            or not self.comment_id
            or self.error_code not in _REPLY_FAILURE_CODES
        ):
            raise ValueError("invalid reply failure")


@dataclass(frozen=True)
class CommentCollectionResult:
    comments: Tuple[Dict[str, Any], ...]
    top_level_comment_count: int
    reply_comment_count: int
    replies_truncated: bool
    reply_failures: Tuple[ReplyFailure, ...]


class CommentsCollector:
    def __init__(
        self,
        api_client: "DouyinAPIClient",
        metadata_handler: "MetadataHandler",
        *,
        include_replies: bool = False,
        max_comments: int = 0,
        max_replies_per_comment: int = 0,
        max_replies_per_content: int = 0,
        page_size: int = 20,
        retry_delay_seconds: float = 1.0,
    ):
        self.api_client = api_client
        self.metadata_handler = metadata_handler
        self.include_replies = bool(include_replies)
        self.max_comments = int(max_comments or 0)
        self.max_replies_per_comment = int(max_replies_per_comment or 0)
        self.max_replies_per_content = int(max_replies_per_content or 0)
        if self.include_replies and not (
            1 <= self.max_replies_per_comment <= 20
            and 1 <= self.max_replies_per_content <= 200
        ):
            raise ValueError("reply limits must be within 1..20 and 1..200")
        if not self.include_replies and (
            self.max_replies_per_comment or self.max_replies_per_content
        ):
            raise ValueError("reply limits require include_replies")
        self.page_size = max(1, int(page_size or 20))
        self.retry_delay_seconds = float(retry_delay_seconds or 1.0)

    async def collect_and_save(
        self, aweme_id: str, output_path: Path
    ) -> Optional[Dict[str, Any]]:
        result = await self.collect(aweme_id)
        if result is None:
            return None

        payload = {
            "aweme_id": aweme_id,
            "count": len(result.comments),
            "include_replies": self.include_replies,
            "top_level_comment_count": result.top_level_comment_count,
            "reply_comment_count": result.reply_comment_count,
            "replies_truncated": result.replies_truncated,
            "reply_failures": [
                {
                    "aweme_id": item.aweme_id,
                    "comment_id": item.comment_id,
                    "error_code": item.error_code,
                }
                for item in result.reply_failures
            ],
            "comments": list(result.comments),
        }
        saved = await self.metadata_handler.save_metadata(payload, output_path)
        if not saved:
            logger.warning("Failed to save comments for %s to %s", aweme_id, output_path)
            return None
        return payload

    async def _collect_replies(
        self,
        *,
        aweme_id: str,
        parent_comment_id: str,
        budget: int,
        seen_ids: set[str],
    ) -> tuple[list[dict[str, Any]], ReplyFailure | None]:
        rows: list[dict[str, Any]] = []
        cursor = 0
        while len(rows) < budget:
            request_count = min(self.page_size, budget - len(rows))
            try:
                page = await self.api_client.get_aweme_comment_replies(
                    aweme_id=aweme_id,
                    comment_id=parent_comment_id,
                    cursor=cursor,
                    count=request_count,
                )
            except LoginRequiredError:
                return rows, ReplyFailure(
                    aweme_id, parent_comment_id, "reply_login_required"
                )
            except (asyncio.TimeoutError, TimeoutError):
                return rows, ReplyFailure(aweme_id, parent_comment_id, "reply_timeout")
            except Exception:  # noqa: BLE001
                return rows, ReplyFailure(
                    aweme_id, parent_comment_id, "reply_api_failed"
                )

            if not isinstance(page, dict):
                return rows, ReplyFailure(
                    aweme_id, parent_comment_id, "reply_response_invalid"
                )
            risk_flags = page.get("risk_flags")
            if not isinstance(risk_flags, dict):
                return rows, ReplyFailure(
                    aweme_id, parent_comment_id, "reply_response_invalid"
                )
            if risk_flags.get("verify_page"):
                return rows, ReplyFailure(
                    aweme_id, parent_comment_id, "reply_verification_required"
                )
            if risk_flags.get("login_tip"):
                return rows, ReplyFailure(
                    aweme_id, parent_comment_id, "reply_login_required"
                )
            if page.get("status_code") not in (0, None):
                return rows, ReplyFailure(
                    aweme_id, parent_comment_id, "reply_api_failed"
                )
            items = page.get("items")
            if not isinstance(items, list):
                return rows, ReplyFailure(
                    aweme_id, parent_comment_id, "reply_response_invalid"
                )

            for raw in items:
                if not isinstance(raw, dict):
                    continue
                reply_id = str(raw.get("cid") or raw.get("comment_id") or "").strip()
                if not reply_id or reply_id in seen_ids:
                    continue
                seen_ids.add(reply_id)
                row = dict(raw)
                row.pop("_replies", None)
                row["aweme_id"] = aweme_id
                row["parent_comment_id"] = parent_comment_id
                rows.append(row)
                if len(rows) >= budget:
                    break

            if not page.get("has_more"):
                return rows, None
            next_cursor = page.get("max_cursor")
            if isinstance(next_cursor, bool) or not isinstance(next_cursor, int):
                return rows, ReplyFailure(
                    aweme_id, parent_comment_id, "reply_response_invalid"
                )
            if next_cursor == cursor:
                return rows, ReplyFailure(
                    aweme_id, parent_comment_id, "reply_response_invalid"
                )
            cursor = next_cursor
            await asyncio.sleep(self.retry_delay_seconds * 0.1)

        return rows, None

    async def collect(
        self, aweme_id: str
    ) -> Optional[CommentCollectionResult]:
        rows: List[Dict[str, Any]] = []
        root_count = 0
        reply_count = 0
        cursor = 0
        seen_ids: set[str] = set()
        failures: list[ReplyFailure] = []

        while self.max_comments <= 0 or root_count < self.max_comments:
            try:
                page = await self.api_client.get_aweme_comments(
                    aweme_id,
                    cursor=cursor,
                    count=self.page_size,
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

            for raw in items:
                if not isinstance(raw, dict):
                    continue
                root_id = str(raw.get("cid") or raw.get("comment_id") or "").strip()
                if root_id and root_id in seen_ids:
                    continue
                if root_id:
                    seen_ids.add(root_id)
                root = dict(raw)
                root.pop("_replies", None)
                root["aweme_id"] = aweme_id
                root["parent_comment_id"] = ""
                rows.append(root)
                root_count += 1

                remaining = self.max_replies_per_content - reply_count
                try:
                    declared = max(0, int(root.get("reply_comment_total") or 0))
                except (TypeError, ValueError):
                    declared = 0
                if (
                    self.include_replies
                    and root_id
                    and declared > 0
                    and remaining > 0
                ):
                    budget = min(self.max_replies_per_comment, remaining)
                    replies, failure = await self._collect_replies(
                        aweme_id=aweme_id,
                        parent_comment_id=root_id,
                        budget=budget,
                        seen_ids=seen_ids,
                    )
                    rows.extend(replies)
                    reply_count += len(replies)
                    if failure is not None:
                        failures.append(failure)

                if 0 < self.max_comments <= root_count:
                    break

            if 0 < self.max_comments <= root_count or not page.get("has_more"):
                break
            next_cursor = page.get("max_cursor") or 0
            if isinstance(next_cursor, bool) or not isinstance(next_cursor, int):
                break
            if next_cursor == cursor:
                logger.warning(
                    "Comments cursor stuck (aweme=%s, cursor=%s); stopping.",
                    aweme_id,
                    cursor,
                )
                break
            cursor = next_cursor
            await asyncio.sleep(self.retry_delay_seconds * 0.1)

        return CommentCollectionResult(
            comments=tuple(rows),
            top_level_comment_count=root_count,
            reply_comment_count=reply_count,
            replies_truncated=bool(failures),
            reply_failures=tuple(failures),
        )
