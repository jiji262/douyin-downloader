"""Re-run a known list of failed aweme_ids against a previously-completed job.

This module implements the "in-place retry" (方案 B) leg of the download
failure retry redesign. The entry point is :func:`retry_failed_awemes`, which:

1. Parses the original job URL to recover ``url_type`` and (when applicable)
   ``sec_uid`` / ``mix_id`` / ``music_id``.
2. Instantiates a :class:`core.downloader_base.BaseDownloader` via
   :class:`DownloaderFactory`. The factory routes ``video``/``gallery`` to
   :class:`VideoDownloader` and batch types (``user``/``collection``/
   ``music``) to their respective batch downloader; either way we only use
   it as a host for the shared ``_download_aweme_assets`` method and the
   ``mode`` semantics attached to the class, not for its batch-paging
   behaviour.
3. Derives the mode (``post`` / ``like`` / ``mix`` / ``music`` / ``None``
   for single-video) from the job's ``overrides``.
4. Fetches each ``aweme_id``'s detail via ``api_client.get_video_detail``,
   then runs ``_download_aweme_assets(aweme_data, author_name, mode=mode)``.
5. Emits per-item progress through the reporter so SSE subscribers see the
   retry unfold, and forwards per-item outcome to ``on_item_outcome`` so
   :class:`server.jobs.JobManager` can update ``job.success`` / ``failed``
   in place.

The sibling CLI project does not use this module — it ships in the shared
``core/`` tree per ``AGENTS.md`` and will be synced, but only the desktop
sidecar currently wires a retry_executor. Keeping the helper here rather
than inside ``server/`` avoids leaking HTTP concerns into the download
strategies and keeps the sync story straightforward.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from auth import CookieManager
from config import ConfigLoader
from control import QueueManager, RateLimiter, RetryHandler
from core.api_client import DouyinAPIClient
from core.downloader_factory import DownloaderFactory
from core.url_parser import URLParser
from storage import Database, FileManager
from utils.logger import setup_logger
from utils.validators import is_short_url, normalize_short_url

logger = setup_logger("RetryExecutor")


def _derive_mode(overrides: Optional[Dict[str, Any]]) -> Optional[str]:
    """Pull the first configured download mode from a job's overrides.

    Batch jobs persist their mode as ``overrides["mode"] = ["post"]`` etc.
    Single-video jobs leave ``mode`` unset — which naturally means the
    files land at ``<base>/<author>/<leaf>`` without a mode subdirectory.
    Returning ``None`` for that case is intentional: ``get_save_path`` then
    omits the middle segment, matching the original run.
    """
    if not overrides:
        return None
    raw = overrides.get("mode")
    if isinstance(raw, str):
        candidate = raw.strip()
        return candidate or None
    if isinstance(raw, list) and raw:
        first = raw[0]
        if isinstance(first, str):
            candidate = first.strip()
            return candidate or None
    return None


def _derive_url_type_for_factory(url_type: Optional[str]) -> str:
    """Map the URL parser's type to a DownloaderFactory key.

    Per-aweme retry always uses the per-item code path
    (``_download_aweme_assets``), so for batch-flavoured URLs we reuse
    :class:`VideoDownloader` (single-item host). This keeps the factory
    from kicking off a new paging run that would re-enumerate the whole
    user profile / mix / music collection.
    """
    # Every branch maps to ``video`` today; keeping the mapping explicit
    # so future URL types (live/collect) slot in without silently falling
    # back to batch enumeration.
    if url_type in ("video", "gallery", "user", "collection", "music"):
        return "video"
    return "video"


async def retry_failed_awemes(
    url: str,
    *,
    aweme_ids: List[str],
    config: ConfigLoader,
    file_manager: FileManager,
    cookie_manager: CookieManager,
    database: Optional[Database] = None,
    rate_limiter: Optional[RateLimiter] = None,
    retry_handler: Optional[RetryHandler] = None,
    queue_manager: Optional[QueueManager] = None,
    reporter: Any = None,
    overrides: Optional[Dict[str, Any]] = None,
    author_hint: Optional[Dict[str, Any]] = None,
    on_item_outcome: Optional[Callable[[str], None]] = None,
) -> Dict[str, int]:
    """Retry the given aweme ids in place and return summary counts.

    Returns a dict with ``attempted`` / ``succeeded`` / ``failed`` /
    ``skipped`` counters so callers that are not subscribed to the reporter
    (e.g. unit tests) can assert outcomes. The actual SSE event stream is
    driven by ``reporter`` when provided.
    """
    counts = {
        "attempted": 0,
        "succeeded": 0,
        "failed": 0,
        "skipped": 0,
    }
    if not aweme_ids:
        return counts

    # Apply overrides (mode/path/…) for the duration of the retry so the
    # downloader's `get_save_path` picks up the same output_dir and folder
    # template that the original run used. Snapshot + restore the values
    # we overwrite, matching `_execute_download` in server/app.py.
    snap: Dict[str, Any] = {}
    if overrides:
        for k in overrides.keys():
            snap[k] = config.get(k)
        config.update(**overrides)

    try:
        cookies = cookie_manager.get_cookies()
        async with DouyinAPIClient(cookies) as api_client:
            if is_short_url(url):
                resolved = await api_client.resolve_short_url(normalize_short_url(url))
                if not resolved:
                    raise RuntimeError(f"Failed to resolve short URL during retry: {url}")
                url = resolved

            parsed = URLParser.parse(url)
            if not parsed:
                raise RuntimeError(f"Unsupported URL during retry: {url}")

            mode = _derive_mode(overrides)
            factory_type = _derive_url_type_for_factory(parsed.get("type"))

            downloader = DownloaderFactory.create(
                factory_type,
                config,
                api_client,
                file_manager,
                cookie_manager,
                database,
                rate_limiter,
                retry_handler,
                queue_manager,
                progress_reporter=reporter,
            )
            if downloader is None:
                raise RuntimeError(f"No downloader available for retry (url_type={factory_type})")

            if reporter is not None:
                try:
                    reporter.on_job_start(
                        url=url,
                        url_type=parsed.get("type") or factory_type,
                        total=len(aweme_ids),
                    )
                except Exception:
                    pass
                # set_item_total drives the percentage bar in the renderer;
                # advance_item later hooks into the same running total.
                try:
                    reporter.set_item_total(
                        len(aweme_ids), detail=f"重试 {len(aweme_ids)} 个失败作品"
                    )
                except Exception:
                    pass

            # Default author name falls back to whatever is stored in the
            # per-aweme detail payload. Hint is only used when the detail
            # lookup itself fails — in that case we still want a sensible
            # folder for any partial artifacts the downloader writes.
            hint_nickname = None
            if author_hint and isinstance(author_hint, dict):
                hint_nickname = author_hint.get("nickname")

            for aid in aweme_ids:
                aid_str = str(aid or "").strip()
                if not aid_str:
                    counts["skipped"] += 1
                    counts["attempted"] += 1
                    if on_item_outcome:
                        on_item_outcome("skipped")
                    if reporter is not None:
                        try:
                            reporter.on_log(
                                level="warning",
                                message="跳过 · aweme_id 为空",
                            )
                        except Exception:
                            pass
                    continue

                aweme_data = await api_client.get_video_detail(aid_str)
                if not aweme_data:
                    counts["failed"] += 1
                    counts["attempted"] += 1
                    if on_item_outcome:
                        on_item_outcome("failed")
                    if reporter is not None:
                        try:
                            reporter.on_log(
                                level="error",
                                message=f"获取作品详情失败 · {aid_str}",
                            )
                            reporter.advance_item("failed", detail=aid_str)
                        except Exception:
                            pass
                    continue

                author = aweme_data.get("author") or {}
                author_name = author.get("nickname") or hint_nickname or "unknown"

                ok = False
                try:
                    ok = await downloader._download_aweme_assets(aweme_data, author_name, mode=mode)
                except Exception as exc:  # pragma: no cover — defensive
                    logger.warning(
                        "Retry of aweme %s raised %s: %s",
                        aid_str,
                        type(exc).__name__,
                        exc,
                    )
                    ok = False

                counts["attempted"] += 1
                if ok:
                    counts["succeeded"] += 1
                    if on_item_outcome:
                        on_item_outcome("ok")
                    if reporter is not None:
                        try:
                            reporter.advance_item("success", detail=aid_str)
                        except Exception:
                            pass
                else:
                    counts["failed"] += 1
                    if on_item_outcome:
                        on_item_outcome("failed")
                    if reporter is not None:
                        try:
                            reporter.advance_item("failed", detail=aid_str)
                        except Exception:
                            pass
    finally:
        if overrides:
            config.update(**snap)

    return counts
