"""MCP extract_text hook + ``_factory`` index (production transcript path).

Built-in Douzy ``TranscriptManager`` stays off; this module calls
``douyin-mcp-server`` ``extract_text`` via the MCP venv (subprocess),
writes ``{stem}.transcript.txt`` next to the downloaded media, and
appends ``_factory`` manifest/delta rows.

API key: prefer env ``DOUYIN_API_KEY``, then ``API_KEY`` / ``OPENAI_API_KEY``
(aligned with MCP / SiliconFlow). Never log or commit the key.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from utils.logger import setup_logger

logger = setup_logger("McpTranscript")

DEFAULT_MCP_SCRIPTS = (
    r"D:\dev\github\douyin-mcp-server\douyin-video\scripts"
)
DEFAULT_MCP_PYTHON = r"D:\dev\github\douyin-mcp-server\.venv\Scripts\python.exe"

# Fixed UTC+8 (Asia/Shanghai has no DST). Avoids Windows tzdata dependency.
FACTORY_TZ = timezone(timedelta(hours=8))

_EXTRACT_WORKER = r"""
import json
import os
import sys

scripts = sys.argv[1]
share = sys.argv[2]
api_key = sys.argv[3]
sys.path.insert(0, scripts)
os.environ.setdefault("DOUYIN_API_KEY", api_key)
from douyin_downloader import extract_text

result = extract_text(
    share,
    api_key=api_key,
    output_dir=None,
    save_video=False,
    show_progress=False,
)
text = (result or {}).get("text") or ""
sys.stdout.write(json.dumps({"text": text}, ensure_ascii=False))
"""

# Prefer local mp4: skip fragile HTML/share parse (videoInfoRes / _ROUTER_DATA).
_EXTRACT_LOCAL_WORKER = r"""
import json
import os
import shutil
import sys
from pathlib import Path

scripts = sys.argv[1]
video = sys.argv[2]
api_key = sys.argv[3]
sys.path.insert(0, scripts)
os.environ.setdefault("DOUYIN_API_KEY", api_key)
os.environ.setdefault("API_KEY", api_key)
from douyin_downloader import DouyinProcessor

processor = DouyinProcessor(api_key)
src = Path(video)
if not src.is_file():
    raise FileNotFoundError(f"local video missing: {src}")
# Hardlink/copy into processor temp so sidecar mp3 never lands in media root.
local = processor.temp_dir / src.name
try:
    os.link(str(src), str(local))
except OSError:
    shutil.copy2(src, local)
audio_path = processor.extract_audio(local, show_progress=False)
text = processor.extract_text_from_audio(audio_path, show_progress=False) or ""
sys.stdout.write(json.dumps({"text": text, "via": "local_video"}, ensure_ascii=False))
"""

_HTML_PARSE_FAILURE_RE = re.compile(
    r"(从HTML中解析视频信息失败|videoInfoRes|_ROUTER_DATA|无法从JSON中解析视频)",
    re.IGNORECASE,
)


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def mcp_transcript_enabled(config: Any) -> bool:
    raw = None
    if hasattr(config, "get"):
        raw = config.get("mcp_transcript")
    elif isinstance(config, dict):
        raw = config.get("mcp_transcript")
    if not isinstance(raw, dict):
        return False
    return _as_bool(raw.get("enabled"), default=False)


def resolve_api_key(mcp_cfg: Optional[Dict[str, Any]] = None) -> Optional[str]:
    cfg = mcp_cfg or {}
    names = []
    primary = str(cfg.get("api_key_env") or "DOUYIN_API_KEY").strip()
    if primary:
        names.append(primary)
    for name in ("DOUYIN_API_KEY", "API_KEY", "OPENAI_API_KEY"):
        if name not in names:
            names.append(name)
    for name in names:
        value = os.environ.get(name)
        if value and str(value).strip():
            return str(value).strip()
    return None


def transcript_txt_path(save_dir: Path, file_stem: str) -> Path:
    return Path(save_dir) / f"{file_stem}.transcript.txt"


def transcript_err_path(save_dir: Path, file_stem: str) -> Path:
    return Path(save_dir) / f"{file_stem}.transcript.err.txt"


def share_link_for_aweme(aweme_id: str) -> str:
    return f"https://www.douyin.com/video/{aweme_id}"


def _existing_nonempty_transcript(path: Path) -> bool:
    try:
        if not path.is_file():
            return False
        return bool(path.read_text(encoding="utf-8").strip())
    except OSError:
        return False


def factory_day_key(ts: Optional[str] = None) -> str:
    """Local calendar day (UTC+8) for ``delta-YYYY-MM-DD.jsonl`` filenames.

    ``ts`` may be ISO-8601 (``...Z`` or offset). Falls back to now in UTC+8.
    """
    if ts:
        raw = str(ts).strip()
        try:
            if raw.endswith("Z"):
                raw = raw[:-1] + "+00:00"
            dt = datetime.fromisoformat(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(FACTORY_TZ).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return datetime.now(FACTORY_TZ).strftime("%Y-%m-%d")


def _run_mcp_worker(
    worker: str,
    arg: str,
    *,
    api_key: str,
    mcp_scripts_path: str,
    mcp_python: str,
    timeout_seconds: int = 600,
) -> str:
    scripts = str(Path(mcp_scripts_path).resolve())
    python_exe = str(Path(mcp_python).resolve()) if mcp_python else sys.executable
    if not Path(python_exe).is_file():
        raise FileNotFoundError(f"MCP python not found: {python_exe}")
    if not (Path(scripts) / "douyin_downloader.py").is_file():
        raise FileNotFoundError(f"MCP scripts missing douyin_downloader.py: {scripts}")

    completed = subprocess.run(
        [python_exe, "-c", worker, scripts, arg, api_key],
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )
    if completed.returncode != 0:
        err = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(err or f"extract worker exit {completed.returncode}")

    stdout = (completed.stdout or "").strip()
    if not stdout:
        raise RuntimeError("extract worker returned empty stdout")
    payload = json.loads(stdout)
    text = payload.get("text") if isinstance(payload, dict) else None
    if not isinstance(text, str) or not text.strip():
        raise RuntimeError("extract worker returned empty text")
    return text.strip()


def call_extract_text(
    share_link: str,
    *,
    api_key: str,
    mcp_scripts_path: str,
    mcp_python: str,
    timeout_seconds: int = 600,
) -> str:
    """Invoke MCP ``extract_text`` via share URL (HTML parse + re-download)."""
    return _run_mcp_worker(
        _EXTRACT_WORKER,
        share_link,
        api_key=api_key,
        mcp_scripts_path=mcp_scripts_path,
        mcp_python=mcp_python,
        timeout_seconds=timeout_seconds,
    )


def call_extract_text_from_local_video(
    video_path: str,
    *,
    api_key: str,
    mcp_scripts_path: str,
    mcp_python: str,
    timeout_seconds: int = 600,
) -> str:
    """ASR from an already-downloaded mp4 (no HTML / videoInfoRes).

    Temp audio stays under MCP ``DouyinProcessor.temp_dir`` and is cleaned on
    process exit — never written into the media root.
    """
    return _run_mcp_worker(
        _EXTRACT_LOCAL_WORKER,
        str(video_path),
        api_key=api_key,
        mcp_scripts_path=mcp_scripts_path,
        mcp_python=mcp_python,
        timeout_seconds=timeout_seconds,
    )


def _is_html_parse_failure(message: str) -> bool:
    return bool(_HTML_PARSE_FAILURE_RE.search(message or ""))


def process_aweme_transcript(
    *,
    aweme_id: str,
    save_dir: Path,
    file_stem: str,
    mcp_cfg: Optional[Dict[str, Any]] = None,
    extract_fn: Optional[Callable[..., str]] = None,
    video_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Sync entry: write transcript sidecar; never raise for caller download.

    When ``video_path`` exists, prefer local ASR (stable). Share-URL extract is
    only used when local media is missing, or as a no-op path for injected
    ``extract_fn`` tests. HTML / ``videoInfoRes`` failures fall back to local.

    Returns dict with ``status`` in pending|ok|failed|skipped and paths.
    """
    cfg = dict(mcp_cfg or {})
    txt_path = transcript_txt_path(save_dir, file_stem)
    err_path = transcript_err_path(save_dir, file_stem)
    local_video: Optional[Path] = None
    if video_path is not None:
        candidate = Path(video_path)
        if candidate.is_file():
            local_video = candidate
    if local_video is None:
        guessed = Path(save_dir) / f"{file_stem}.mp4"
        if guessed.is_file():
            local_video = guessed

    if _existing_nonempty_transcript(txt_path):
        return {
            "status": "skipped",
            "reason": "existing_transcript",
            "transcript_path": str(txt_path),
            "error_path": None,
        }

    api_key = resolve_api_key(cfg)
    if not api_key:
        message = (
            "missing API key: set DOUYIN_API_KEY (or API_KEY / OPENAI_API_KEY)"
        )
        try:
            err_path.write_text(message + "\n", encoding="utf-8")
        except OSError:
            pass
        return {
            "status": "failed",
            "reason": "missing_api_key",
            "transcript_path": str(txt_path),
            "error_path": str(err_path),
            "error": message,
        }

    scripts = str(cfg.get("mcp_scripts_path") or DEFAULT_MCP_SCRIPTS)
    python_exe = str(cfg.get("mcp_python") or DEFAULT_MCP_PYTHON)
    timeout = int(cfg.get("timeout_seconds") or 600)
    link = share_link_for_aweme(aweme_id)
    prefer_local = _as_bool(cfg.get("prefer_local_video"), default=True)

    def _write_ok(text: str, *, via: str) -> Dict[str, Any]:
        txt_path.write_text(text + "\n", encoding="utf-8")
        if err_path.is_file():
            try:
                err_path.unlink()
            except OSError:
                pass
        return {
            "status": "ok",
            "via": via,
            "transcript_path": str(txt_path),
            "error_path": None,
        }

    last_error = ""
    try:
        # Injected extract_fn: keep single-path behavior for unit tests.
        if extract_fn is not None:
            text = extract_fn(
                link,
                api_key=api_key,
                mcp_scripts_path=scripts,
                mcp_python=python_exe,
                timeout_seconds=timeout,
            )
            return _write_ok(text, via="extract_fn")

        attempted_local = False

        if prefer_local and local_video is not None:
            attempted_local = True
            try:
                text = call_extract_text_from_local_video(
                    str(local_video),
                    api_key=api_key,
                    mcp_scripts_path=scripts,
                    mcp_python=python_exe,
                    timeout_seconds=timeout,
                )
                return _write_ok(text, via="local_video")
            except Exception as local_exc:  # noqa: BLE001
                last_error = (
                    f"local_video: "
                    f"{str(local_exc).strip() or local_exc.__class__.__name__}"
                )
                logger.warning(
                    "Local ASR failed for aweme %s, trying share URL: %s",
                    aweme_id,
                    last_error[:200],
                )

        try:
            text = call_extract_text(
                link,
                api_key=api_key,
                mcp_scripts_path=scripts,
                mcp_python=python_exe,
                timeout_seconds=timeout,
            )
            via = "share_url"
            if attempted_local and last_error:
                via = "share_url_after_local_fail"
            return _write_ok(text, via=via)
        except Exception as share_exc:  # noqa: BLE001
            share_msg = str(share_exc).strip() or share_exc.__class__.__name__
            last_error = (
                f"{last_error} | share_url: {share_msg}" if last_error else share_msg
            )
            if (
                local_video is not None
                and not attempted_local
                and _is_html_parse_failure(share_msg)
            ):
                try:
                    text = call_extract_text_from_local_video(
                        str(local_video),
                        api_key=api_key,
                        mcp_scripts_path=scripts,
                        mcp_python=python_exe,
                        timeout_seconds=timeout,
                    )
                    logger.info(
                        "MCP transcript recovered via local video for aweme %s",
                        aweme_id,
                    )
                    return _write_ok(text, via="local_video_fallback")
                except Exception as local_exc:  # noqa: BLE001
                    last_error = (
                        f"{last_error} | local_fallback: "
                        f"{str(local_exc).strip() or local_exc.__class__.__name__}"
                    )

        message = last_error
        if api_key and api_key in message:
            message = message.replace(api_key, "***")
        try:
            err_path.write_text(message[:2000] + "\n", encoding="utf-8")
        except OSError:
            pass
        logger.warning("MCP transcript failed for aweme %s: %s", aweme_id, message[:200])
        return {
            "status": "failed",
            "reason": "extract_failed",
            "transcript_path": str(txt_path),
            "error_path": str(err_path),
            "error": message[:500],
        }
    except Exception as exc:  # noqa: BLE001 — last-resort guard
        message = str(exc).strip() or exc.__class__.__name__
        if api_key and api_key in message:
            message = message.replace(api_key, "***")
        try:
            err_path.write_text(message[:2000] + "\n", encoding="utf-8")
        except OSError:
            pass
        logger.warning("MCP transcript failed for aweme %s: %s", aweme_id, message[:200])
        return {
            "status": "failed",
            "reason": "extract_failed",
            "transcript_path": str(txt_path),
            "error_path": str(err_path),
            "error": message[:500],
        }


def title_from_stem(file_stem: str, aweme_id: str = "") -> str:
    """Best-effort title from ``{date}_{title}_{id}`` stem when API desc missing."""
    stem = (file_stem or "").strip()
    if not stem:
        return ""
    text = stem
    # strip leading YYYY-MM-DD_
    if len(text) >= 11 and text[4] == "-" and text[7] == "-" and text[10] == "_":
        text = text[11:]
    aid = (aweme_id or "").strip()
    if aid and text.endswith("_" + aid):
        text = text[: -(len(aid) + 1)]
    elif aid and text.endswith(aid):
        text = text[: -len(aid)].rstrip("_")
    return text.strip(" _-")[:200]


async def _run_in_thread(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Thread offload compatible with Python 3.8 (no ``asyncio.to_thread``)."""
    to_thread = getattr(asyncio, "to_thread", None)
    if to_thread is not None:
        return await to_thread(func, *args, **kwargs)
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: func(*args, **kwargs))


async def process_aweme_transcript_async(
    *,
    aweme_id: str,
    save_dir: Path,
    file_stem: str,
    mcp_cfg: Optional[Dict[str, Any]] = None,
    extract_fn: Optional[Callable[..., str]] = None,
    video_path: Optional[Path] = None,
) -> Dict[str, Any]:
    return await _run_in_thread(
        process_aweme_transcript,
        aweme_id=aweme_id,
        save_dir=save_dir,
        file_stem=file_stem,
        mcp_cfg=mcp_cfg,
        extract_fn=extract_fn,
        video_path=video_path,
    )


def content_hash_for(
    aweme_id: str,
    transcript_status: str,
    transcript_path: Optional[str] = None,
) -> str:
    digest = hashlib.sha256()
    digest.update(aweme_id.encode("utf-8"))
    digest.update(b"|")
    digest.update(transcript_status.encode("utf-8"))
    if transcript_path:
        path = Path(transcript_path)
        if path.is_file():
            try:
                digest.update(b"|")
                digest.update(path.read_bytes()[:65536])
            except OSError:
                pass
    return digest.hexdigest()[:16]


def append_factory_records(
    media_root: Path,
    record: Dict[str, Any],
) -> None:
    """Append one JSONL line to manifest.jsonl and delta-YYYY-MM-DD.jsonl.

    Day key is **Asia/Shanghai (UTC+8) local calendar date**, matching
    ``ingest-douyin-delta.ps1`` ``(Get-Date)`` — not the UTC prefix of ``ts``.
    """
    factory_dir = Path(media_root) / "_factory"
    factory_dir.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False) + "\n"
    day_key = factory_day_key(record.get("ts"))

    manifest = factory_dir / "manifest.jsonl"
    delta = factory_dir / f"delta-{day_key}.jsonl"
    with manifest.open("a", encoding="utf-8") as handle:
        handle.write(line)
    with delta.open("a", encoding="utf-8") as handle:
        handle.write(line)


def build_factory_record(
    *,
    aweme_id: str,
    author: str,
    title: str,
    media_path: str,
    transcript_path: Optional[str],
    transcript_status: str,
    source: str = "cli",
    op: str = "upsert",
) -> Dict[str, Any]:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "ts": ts,
        "op": op,
        "aweme_id": aweme_id,
        "author": author or "",
        "title": title or "",
        "media_path": media_path,
        "transcript_path": transcript_path or "",
        "transcript_status": transcript_status,
        "source": source,
        "content_hash": content_hash_for(aweme_id, transcript_status, transcript_path),
        "ref_id": f"MED-{aweme_id}",
    }
