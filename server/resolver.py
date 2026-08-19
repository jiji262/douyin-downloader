"""Resolve Douyin work links to short-lived media CDN URLs without saving files."""

from typing import Any, Dict, List, Optional, Sequence, Tuple

import aiohttp
import httpx

from core import DouyinAPIClient, DownloaderFactory, URLParser
from utils.logger import setup_logger
from utils.validators import is_short_url, normalize_short_url

logger = setup_logger("RESTResolver")


class MediaResolveError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _usable_media_response(status: int, headers: Any) -> bool:
    if status not in (200, 206):
        return False
    content_type = str((headers or {}).get("Content-Type") or "")
    content_type = content_type.split(";", 1)[0].strip().lower()
    if not content_type:
        return True
    return not (content_type.startswith("text/") or content_type.endswith("/json"))


async def _resolve_candidate_url(
    session: Any,
    url: str,
    headers: Dict[str, str],
    proxy: str,
) -> Optional[str]:
    """Probe one candidate with one byte and return the final CDN redirect URL."""
    probe_headers = {**headers, "Range": "bytes=0-0"}
    try:
        async with session.get(
            url,
            headers=probe_headers,
            allow_redirects=True,
            proxy=proxy or None,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as response:
            if _usable_media_response(response.status, response.headers):
                return str(response.url)
    except Exception as exc:
        logger.debug("aiohttp media URL probe failed: %s", exc)

    # Match FileManager's fallback for mirrors that reject aiohttp's TLS fingerprint.
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=5.0),
            proxy=proxy or None,
            follow_redirects=True,
        ) as client:
            async with client.stream("GET", url, headers=probe_headers) as response:
                if _usable_media_response(response.status_code, response.headers):
                    return str(response.url)
    except Exception as exc:
        logger.debug("httpx media URL probe failed: %s", exc)
    return None


async def _first_reachable_url(
    session: Any,
    candidates: Sequence[Tuple[str, Dict[str, str]]],
    proxy: str,
) -> Optional[str]:
    for url, headers in candidates:
        resolved = await _resolve_candidate_url(session, url, headers, proxy)
        if resolved:
            return resolved
    return None


async def resolve_media_urls(url: str, deps: Any) -> Dict[str, Any]:
    """Resolve one video/gallery link into one URL per actual media asset."""
    async with DouyinAPIClient(
        deps.cookie_manager.get_cookies(),
        proxy=deps.config.get("proxy"),
    ) as api_client:
        if is_short_url(url):
            resolved = await api_client.resolve_short_url(normalize_short_url(url))
            if not resolved:
                raise MediaResolveError(422, "Failed to resolve short URL")
            url = resolved

        parsed = URLParser.parse(url)
        if not parsed:
            raise MediaResolveError(422, "Unsupported Douyin URL")
        if parsed["type"] not in ("video", "gallery"):
            raise MediaResolveError(422, "Only a single video or gallery URL is supported")

        aweme_id = parsed.get("aweme_id")
        if not aweme_id:
            raise MediaResolveError(422, "No aweme_id found in URL")

        await deps.rate_limiter.acquire()
        aweme_data = await api_client.get_video_detail(str(aweme_id))
        if not aweme_data:
            raise MediaResolveError(502, "Failed to fetch Douyin work detail")

        downloader = DownloaderFactory.create(
            parsed["type"],
            deps.config,
            api_client,
            deps.file_manager,
            deps.cookie_manager,
            None,
            deps.rate_limiter,
            deps.retry_handler,
            deps.queue_manager,
            progress_reporter=None,
        )
        if downloader is None:
            raise MediaResolveError(422, "No resolver for this URL type")

        session = await api_client.get_session()
        proxy = str(deps.config.get("proxy") or "").strip()
        media_type = downloader._detect_media_type(aweme_data)
        urls: List[str] = []

        if media_type == "video":
            candidates = downloader._build_video_url_candidates(aweme_data)
            candidates = await downloader._maybe_promote_original_candidate(
                aweme_data, candidates, session
            )
            selected = await _first_reachable_url(session, candidates, proxy)
            if not selected:
                raise MediaResolveError(502, "No reachable video URL found")
            urls.append(selected)
        elif media_type == "gallery":
            headers = downloader._download_headers()
            for index, group in enumerate(
                downloader._collect_image_url_candidates(aweme_data), start=1
            ):
                candidates = [(candidate, headers) for candidate in group]
                selected = await _first_reachable_url(session, candidates, proxy)
                if not selected:
                    raise MediaResolveError(502, f"No reachable URL for gallery image {index}")
                urls.append(selected)

            for index, candidate in enumerate(
                downloader._collect_image_live_urls(aweme_data), start=1
            ):
                selected = await _first_reachable_url(session, [(candidate, headers)], proxy)
                if not selected:
                    raise MediaResolveError(502, f"No reachable URL for live photo {index}")
                urls.append(selected)
        else:
            raise MediaResolveError(422, f"Unsupported media type: {media_type}")

        urls = list(dict.fromkeys(urls))
        if not urls:
            raise MediaResolveError(502, "No media URL found")
        return {
            "aweme_id": str(aweme_data.get("aweme_id") or aweme_id),
            "title": str(aweme_data.get("desc") or ""),
            "media_type": media_type,
            "urls": urls,
        }
