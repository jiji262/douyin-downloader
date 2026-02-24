import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiofiles
import aiohttp

from config import ConfigLoader
from storage import Database, FileManager
from utils.logger import setup_logger

logger = setup_logger("TranscriptManager")


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

    def _response_formats(self) -> List[str]:
        formats = self._cfg().get("response_formats", ["txt", "json"])
        if not isinstance(formats, list):
            return ["txt", "json"]
        normalized = [str(item).strip().lower() for item in formats if str(item).strip()]
        return normalized or ["txt", "json"]

    def _resolve_api_key(self) -> str:
        transcript_cfg = self._cfg()
        api_key_env = str(transcript_cfg.get("api_key_env", "OPENAI_API_KEY")).strip()
        if api_key_env:
            env_value = os.getenv(api_key_env, "").strip()
            if env_value:
                return env_value

        return str(transcript_cfg.get("api_key", "")).strip()

    def _api_url(self) -> str:
        api_url = str(
            self._cfg().get(
                "api_url", "https://api.openai.com/v1/audio/transcriptions"
            )
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
            relative_dir = video_dir.resolve().relative_to(
                self.file_manager.base_path.resolve()
            )
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
            logger.warning(
                "Transcript skipped for aweme %s: missing_api_key", aweme_id
            )
            return {"status": "skipped", "reason": "missing_api_key"}

        try:
            payload = await self._call_openai_transcription(
                api_key=api_key,
                video_path=video_path,
                model=model,
            )
            text = str(payload.get("text", "")).strip()
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
            logger.error("Transcript failed for aweme %s: %s", aweme_id, error_message)
            return {
                "status": "failed",
                "reason": "transcription_error",
                "error": error_message,
            }

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
        self, api_key: str, video_path: Path, model: str
    ) -> Dict[str, Any]:
        if not video_path.exists():
            raise FileNotFoundError(f"Video file not found: {video_path}")

        transcript_cfg = self._cfg()
        language_hint = str(transcript_cfg.get("language_hint", "")).strip()
        api_url = self._api_url()

        form = aiohttp.FormData()
        form.add_field("model", model)
        form.add_field("response_format", "json")
        if language_hint:
            form.add_field("language", language_hint)

        content_type = self._guess_video_content_type(video_path)
        with video_path.open("rb") as f:
            form.add_field(
                "file",
                f,
                filename=video_path.name,
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
