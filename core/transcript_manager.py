import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiofiles
import aiohttp

from config import ConfigLoader
from core.audio_extraction import AudioExtractError, extract_audio
from storage import Database, FileManager
from utils.logger import setup_logger

logger = setup_logger("TranscriptManager")


# File extensions that the transcription endpoint already accepts as audio.
# When the source download is one of these we skip ``extract_audio`` and
# upload the file as-is. Lower-case keys.
_SOURCE_AUDIO_MIME = {
    ".m4a": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".aac": "audio/aac",
    ".opus": "audio/ogg",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
}


def _mask_api_key_local(value: str) -> str:
    """Pure mirror of ``server.app._mask_api_key`` for use inside the
    shared transcript pipeline (which can't import from desktop-only
    code). Same boundary semantics: empty → ``""``, 1-7 → all ``*``,
    >=8 → ``"<first 4>...<last 4>"``.

    Used to redact bearer tokens that might be echoed back in upstream
    error responses before they land in ``transcript_jobs.error_message``
    (Property 1 / 2).
    """
    if not value:
        return ""
    n = len(value)
    if n >= 8:
        return f"{value[:4]}...{value[-4:]}"
    return "*" * n


def resolve_api_key_with_source(
    transcript_cfg: Dict[str, Any],
) -> Tuple[str, str]:
    """Pure helper that resolves a transcription API key and reports
    where it came from.

    Used by both :class:`TranscriptManager` (during a real
    ``process_video`` call) and the desktop sidecar's
    ``POST /api/v1/transcript/test-connectivity`` endpoint, so the two
    code paths can never disagree on which credential they're using.

    Priority (first non-empty after strip wins):
      1. The environment variable named by ``api_key_env``
         (default ``OPENAI_API_KEY``).
      2. The ``api_key`` field persisted in ``settings.yml``.

    Returns:
        Tuple of (api_key, source) where ``source`` is one of
        ``"env"``, ``"settings"``, or ``"none"``.
    """
    api_key_env = str(transcript_cfg.get("api_key_env", "OPENAI_API_KEY") or "").strip()
    if api_key_env:
        env_value = os.getenv(api_key_env, "").strip()
        if env_value:
            return env_value, "env"

    settings_value = str(transcript_cfg.get("api_key", "") or "").strip()
    if settings_value:
        return settings_value, "settings"
    return "", "none"


class TranscriptManager:
    def __init__(
        self,
        config: ConfigLoader,
        file_manager: FileManager,
        database: Optional[Database] = None,
    ):
        self.config = config
        self.file_manager = file_manager
        self.database = database

    def _cfg(self) -> Dict[str, Any]:
        return self.config.get("transcript", {}) or {}

    def _enabled(self) -> bool:
        return bool(self._cfg().get("enabled", False))

    def _model(self) -> str:
        return str(self._cfg().get("model", "gpt-4o-mini-transcribe")).strip()

    def _upload_audio_only(self) -> bool:
        """``transcript.upload_audio_only`` flag (R1.14, default ``True``).

        Hidden from the Settings UI by design (R1.18); editable only via
        ``settings.yml`` or a direct ``PATCH /api/v1/settings`` call so a
        user wandering through the UI can't accidentally disable the
        bandwidth-saving path.
        """
        v = self._cfg().get("upload_audio_only", True)
        if v is None:
            return True
        return bool(v)

    def _response_formats(self) -> List[str]:
        formats = self._cfg().get("response_formats", ["txt", "json"])
        if not isinstance(formats, list):
            return ["txt", "json"]
        normalized = [str(item).strip().lower() for item in formats if str(item).strip()]
        return normalized or ["txt", "json"]

    def _resolve_api_key(self) -> str:
        """Resolve the API key per Requirement 5.6.

        Priority (first non-empty after strip wins):
        1. The environment variable named by ``transcript.api_key_env``
           (default ``OPENAI_API_KEY``).
        2. The ``transcript.api_key`` field persisted in ``settings.yml``.
        Falling through both returns ``""`` and the caller goes through the
        existing ``skip_reason="missing_api_key"`` branch.
        """
        api_key, _source = resolve_api_key_with_source(self._cfg())
        return api_key

    def _api_url(self) -> str:
        api_url = str(
            self._cfg().get("api_url", "https://api.openai.com/v1/audio/transcriptions")
        ).strip()
        return api_url or "https://api.openai.com/v1/audio/transcriptions"

    def resolve_output_dir(self, video_path: Path) -> Path:
        video_path = Path(video_path)
        video_dir = video_path.parent
        output_dir = str(self._cfg().get("output_dir", "")).strip()
        if not output_dir:
            return video_dir

        output_root = Path(output_dir)
        try:
            relative_dir = video_dir.resolve().relative_to(self.file_manager.base_path.resolve())
            return output_root / relative_dir
        except Exception:
            logger.warning(
                "Failed to mirror transcript path for video %s, fallback to video dir",
                video_path,
            )
            return video_dir

    def build_output_paths(self, video_path: Path) -> Tuple[Path, Path]:
        video_path = Path(video_path)
        output_dir = self.resolve_output_dir(video_path)
        output_dir.mkdir(parents=True, exist_ok=True)
        stem = video_path.stem
        return (
            output_dir / f"{stem}.transcript.txt",
            output_dir / f"{stem}.transcript.json",
        )

    async def process_video(self, video_path: Path, aweme_id: str) -> Dict[str, Any]:
        video_path = Path(video_path)

        if not self._enabled():
            return {"status": "skipped", "reason": "disabled"}

        api_key = self._resolve_api_key()
        text_path, json_path = self.build_output_paths(video_path)
        model = self._model()

        if not api_key:
            await self._record_job(
                aweme_id=aweme_id,
                video_path=video_path,
                transcript_dir=text_path.parent,
                text_path=text_path,
                json_path=json_path,
                model=model,
                status="skipped",
                skip_reason="missing_api_key",
                error_message=None,
            )
            logger.warning("Transcript skipped for aweme %s: missing_api_key", aweme_id)
            return {"status": "skipped", "reason": "missing_api_key"}

        # ------------------------------------------------------------------
        # Pick what to upload:
        #   1. Source already audio (m4a/mp3/...): pass through (R1.8).
        #   2. upload_audio_only=true (default): extract audio first (R1.1).
        #   3. upload_audio_only=false: legacy behaviour, upload the video
        #      file itself (R1.16 / R6.5).
        # ------------------------------------------------------------------
        source_ext = video_path.suffix.lower()
        is_source_audio = source_ext in _SOURCE_AUDIO_MIME
        tmp_audio_dir: Optional[tempfile.TemporaryDirectory] = None

        upload_path = video_path
        upload_filename = video_path.name
        upload_content_type = self._guess_video_content_type(video_path)

        try:
            if not is_source_audio and self._upload_audio_only():
                tmp_audio_dir = tempfile.TemporaryDirectory(
                    prefix="transcript_audio_"
                )
                try:
                    upload_path = await extract_audio(
                        video_path, Path(tmp_audio_dir.name)
                    )
                except AudioExtractError as exc:
                    error_message = str(exc)
                    await self._record_job(
                        aweme_id=aweme_id,
                        video_path=video_path,
                        transcript_dir=text_path.parent,
                        text_path=text_path,
                        json_path=json_path,
                        model=model,
                        status="failed",
                        skip_reason=None,
                        error_message=error_message,
                    )
                    logger.error(
                        "Transcript audio extraction failed for aweme %s: %s",
                        aweme_id,
                        error_message,
                    )
                    return {
                        "status": "failed",
                        "reason": "audio_extract_failed",
                        "error": error_message,
                    }
                upload_filename = f"{video_path.stem}.mp3"
                upload_content_type = "audio/mpeg"
            elif is_source_audio:
                upload_filename = video_path.name
                upload_content_type = _SOURCE_AUDIO_MIME[source_ext]

            try:
                payload = await self._call_openai_transcription(
                    api_key=api_key,
                    file_path=upload_path,
                    filename=upload_filename,
                    content_type=upload_content_type,
                    model=model,
                )
                # ``_write_outputs`` re-derives the text from ``payload`` —
                # no need to pre-extract it here.
                await self._write_outputs(payload, text_path, json_path)
                await self._record_job(
                    aweme_id=aweme_id,
                    video_path=video_path,
                    transcript_dir=text_path.parent,
                    text_path=text_path,
                    json_path=json_path,
                    model=model,
                    status="success",
                    skip_reason=None,
                    error_message=None,
                )
                return {
                    "status": "success",
                    "text_path": str(text_path),
                    "json_path": str(json_path),
                }
            except Exception as exc:
                error_message = str(exc)
                await self._record_job(
                    aweme_id=aweme_id,
                    video_path=video_path,
                    transcript_dir=text_path.parent,
                    text_path=text_path,
                    json_path=json_path,
                    model=model,
                    status="failed",
                    skip_reason=None,
                    error_message=error_message,
                )
                logger.error(
                    "Transcript failed for aweme %s: %s", aweme_id, error_message
                )
                return {
                    "status": "failed",
                    "reason": "transcription_error",
                    "error": error_message,
                }
        finally:
            if tmp_audio_dir is not None:
                # Cleanup is best-effort. R6.7: a cleanup error must not
                # surface as a transcript task failure — log a WARNING and
                # let the surrounding return path run.
                try:
                    tmp_audio_dir.cleanup()
                except Exception as exc:  # noqa: BLE001 — broad is correct here
                    logger.warning(
                        "Failed to clean up transcript audio temp dir %s: %r",
                        tmp_audio_dir.name,
                        exc,
                    )

    async def _write_outputs(
        self, payload: Dict[str, Any], text_path: Path, json_path: Path
    ) -> None:
        formats = set(self._response_formats())

        if "txt" in formats:
            text = str(payload.get("text", "")).strip()
            async with aiofiles.open(text_path, "w", encoding="utf-8") as f:
                await f.write(text)

        if "json" in formats:
            async with aiofiles.open(json_path, "w", encoding="utf-8") as f:
                await f.write(json.dumps(payload, ensure_ascii=False, indent=2))

    async def _call_openai_transcription(
        self,
        *,
        api_key: str,
        file_path: Path,
        filename: str,
        content_type: str,
        model: str,
    ) -> Dict[str, Any]:
        """POST a multipart transcription request.

        ``file_path`` is whatever the caller decided to upload — could be
        the original video, the source audio file (passthrough), or the
        ffmpeg-extracted mp3. The caller passes the appropriate
        ``filename`` + ``content_type`` so the multipart body advertises
        the right MIME.
        """
        if not file_path.exists():
            raise FileNotFoundError(f"Upload file not found: {file_path}")

        transcript_cfg = self._cfg()
        language_hint = str(transcript_cfg.get("language_hint", "")).strip()
        api_url = self._api_url()

        form = aiohttp.FormData()
        form.add_field("model", model)
        form.add_field("response_format", "json")
        if language_hint:
            form.add_field("language", language_hint)

        with file_path.open("rb") as f:
            form.add_field(
                "file",
                f,
                filename=filename,
                content_type=content_type,
            )
            timeout = aiohttp.ClientTimeout(total=600)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    api_url,
                    data=form,
                    headers={"Authorization": f"Bearer {api_key}"},
                ) as response:
                    if response.status != 200:
                        body = await response.text()
                        # Some misbehaving proxies echo the bearer token
                        # into 4xx error pages; redact before the body
                        # ends up in ``transcript_jobs.error_message``
                        # (Property 1 / 2).
                        if api_key and api_key in body:
                            body = body.replace(api_key, _mask_api_key_local(api_key))
                        raise RuntimeError(
                            f"OpenAI transcription failed: status={response.status}, body={body}"
                        )

                    payload = await response.json(content_type=None)
                    if not isinstance(payload, dict):
                        raise RuntimeError("OpenAI transcription returned invalid payload")
                    return payload

    @staticmethod
    def _guess_video_content_type(video_path: Path) -> str:
        suffix = video_path.suffix.lower()
        if suffix == ".mp4":
            return "video/mp4"
        if suffix == ".m4a":
            return "audio/mp4"
        if suffix == ".wav":
            return "audio/wav"
        if suffix == ".mp3":
            return "audio/mpeg"
        return "application/octet-stream"

    async def _record_job(
        self,
        *,
        aweme_id: str,
        video_path: Path,
        transcript_dir: Path,
        text_path: Path,
        json_path: Path,
        model: str,
        status: str,
        skip_reason: Optional[str],
        error_message: Optional[str],
    ) -> None:
        if not self.database:
            return

        await self.database.upsert_transcript_job(
            {
                "aweme_id": aweme_id,
                "video_path": str(video_path),
                "transcript_dir": str(transcript_dir),
                "text_path": str(text_path),
                "json_path": str(json_path),
                "model": model,
                "status": status,
                "skip_reason": skip_reason,
                "error_message": error_message,
            }
        )
