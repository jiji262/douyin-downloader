"""抖音已结束直播回放下载。"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from core.downloader_base import BaseDownloader, DownloadResult
from core.ffmpeg import resolve_ffmpeg_path
from utils.logger import setup_logger
from utils.naming import (
    DEFAULT_FILE_TEMPLATE,
    DEFAULT_FOLDER_TEMPLATE,
    build_live_context,
    render_template,
)

logger = setup_logger("LiveReplayDownloader")
LIVE_REPLAY_TIMEZONE = timezone(timedelta(hours=8))
_REMUX_TIMEOUT_SECONDS = 600.0
_PROCESS_REAP_TIMEOUT_SECONDS = 5.0


class LiveReplayDownloader(BaseDownloader):
    async def download(self, parsed_url: Dict[str, Any]) -> DownloadResult:
        result = DownloadResult()
        result.total = 1
        self._progress_set_item_total(1, "直播回放")

        episode_id = str(parsed_url.get("episode_id") or "").strip()
        if not episode_id:
            logger.error("No episode_id found in parsed URL")
            result.failed += 1
            self._progress_advance_item("failed", "missing episode_id")
            return result

        self._progress_update_step("获取回放信息", f"episode_id={episode_id}")
        episode = await self.api_client.get_live_replay_episode(episode_id)
        if not episode:
            logger.error("Live replay episode not found: %s", episode_id)
            result.failed += 1
            self._progress_advance_item("failed", episode_id)
            return result

        room_id = str(episode.get("attach_room_id_str") or episode.get("attach_room_id") or "")
        if not room_id:
            logger.error("Live replay room_id missing: %s", episode_id)
            result.failed += 1
            self._progress_advance_item("failed", episode_id)
            return result

        replay_id = str(parsed_url.get("replay_id") or "").strip() or None
        replay = await self.api_client.get_live_replay_info(
            episode_id, room_id, replay_id=replay_id
        )
        if not replay:
            logger.error("Live replay playable info not found: %s", episode_id)
            result.failed += 1
            self._progress_advance_item("failed", episode_id)
            return result

        video_url, audio_url = self._select_playback_tracks(self._play_urls(replay))
        if not video_url:
            logger.error("No playable live replay video URL: %s", episode_id)
            result.failed += 1
            self._progress_advance_item("failed", episode_id)
            return result

        save_dir, file_stem = self._plan_output_paths(episode, replay, episode_id)
        final_path = save_dir / f"{file_stem}.mp4"
        video_path = save_dir / f"{file_stem}.video.mp4"
        audio_path = save_dir / f"{file_stem}.audio.mp4"

        self._progress_update_step("下载回放视频", final_path.name)
        if not await self._download_track(video_url, video_path):
            result.failed += 1
            self._progress_advance_item("failed", episode_id)
            return result

        output_paths: List[Path]
        remux_status = "merged"
        if audio_url:
            self._progress_update_step("下载回放音频", final_path.name)
            if not await self._download_track(audio_url, audio_path):
                logger.warning(
                    "Live replay audio download failed; keeping video track: %s", episode_id
                )
                output_paths = [video_path]
                remux_status = "audio_download_failed"
            else:
                self._progress_update_step("合并音视频", final_path.name)
                if await self._remux_tracks(video_path, audio_path, final_path):
                    self._cleanup_temp(video_path, audio_path)
                    output_paths = [final_path]
                else:
                    logger.warning(
                        "Live replay remux failed or ffmpeg missing; keeping separate tracks: %s",
                        episode_id,
                    )
                    output_paths = [video_path, audio_path]
                    remux_status = "remux_failed"
        else:
            os.replace(str(video_path), str(final_path))
            output_paths = [final_path]
            remux_status = "video_only"

        await self._record_outputs(
            episode, replay, episode_id, room_id, save_dir, output_paths, remux_status
        )
        result.success += 1
        self._progress_advance_item("success", episode_id)
        return result

    @staticmethod
    def _play_urls(replay: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
        video_info = replay.get("video_info") if isinstance(replay, dict) else None
        unfold = video_info.get("unfold_play_info") if isinstance(video_info, dict) else None
        play_urls = unfold.get("play_urls") if isinstance(unfold, dict) else None
        return [item for item in play_urls or [] if isinstance(item, dict)]

    @staticmethod
    def _select_playback_tracks(
        play_urls: Iterable[Dict[str, Any]],
    ) -> Tuple[Optional[str], Optional[str]]:
        video_candidates = []
        audio_url: Optional[str] = None
        for item in play_urls:
            url = LiveReplayDownloader._entry_url(item)
            if not url:
                continue
            height = LiveReplayDownloader._to_int(item.get("height"))
            width = LiveReplayDownloader._to_int(item.get("width"))
            if height <= 0 and width <= 0:
                audio_url = audio_url or url
            else:
                video_candidates.append((height, width, url))
        if not video_candidates:
            return None, audio_url
        video_candidates.sort(key=lambda row: (row[0], row[1]), reverse=True)
        return video_candidates[0][2], audio_url

    @staticmethod
    def _entry_url(item: Dict[str, Any]) -> Optional[str]:
        for key in ("main", "backup", "url", "play_url"):
            value = item.get(key)
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                return value
        return None

    @staticmethod
    def _to_int(value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    def _plan_output_paths(
        self, episode: Dict[str, Any], replay: Dict[str, Any], episode_id: str
    ) -> Tuple[Path, str]:
        title = str(replay.get("title") or episode.get("title") or "直播回放").strip() or "直播回放"
        owner = episode.get("owner") if isinstance(episode.get("owner"), dict) else {}
        author_name = str(owner.get("nickname") or "unknown").strip() or "unknown"
        started_at = self._started_at(episode)
        date = started_at.strftime("%Y-%m-%d_%H%M")
        context = build_live_context(
            room_id=episode_id,
            title=title,
            author_name=author_name,
            started_at=started_at,
            mode="live_replay",
        )
        filename_template = self.config.get("filename_template") or DEFAULT_FILE_TEMPLATE
        folder_template = self.config.get("folder_template") or DEFAULT_FOLDER_TEMPLATE
        file_stem = render_template(filename_template, context, fallback=f"{date}_{episode_id}")
        folder_name = render_template(folder_template, context, fallback=f"{date}_{episode_id}")
        save_dir = self.file_manager.get_save_path(
            author_name=author_name,
            mode="live_replay",
            aweme_title=title,
            aweme_id=episode_id,
            folderstyle=self.config.get("folderstyle", True),
            download_date=date,
            folder_name=folder_name,
            author_sec_uid=None,
            author_dir_style=self.config.get("author_dir") or "nickname",
            group_by_mode=self.config.get("group_by_mode", True),
        )
        return save_dir, file_stem

    @staticmethod
    def _started_at(episode: Dict[str, Any]) -> datetime:
        extra = episode.get("episode_extra_basic_info")
        ts = extra.get("room_start_time") if isinstance(extra, dict) else None
        try:
            if ts:
                return datetime.fromtimestamp(int(ts), tz=LIVE_REPLAY_TIMEZONE)
            return datetime.now(LIVE_REPLAY_TIMEZONE)
        except (OSError, OverflowError, TypeError, ValueError):
            return datetime.now(LIVE_REPLAY_TIMEZONE)

    async def _download_track(self, url: str, target_path: Path) -> bool:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        session = await self.api_client.get_session()
        return bool(
            await self._download_with_retry(
                url,
                target_path,
                session,
                headers=self._download_headers(),
            )
        )

    async def _remux_tracks(self, video_path: Path, audio_path: Path, output_path: Path) -> bool:
        ffmpeg = resolve_ffmpeg_path()
        if not ffmpeg:
            logger.error("ffmpeg not found; cannot merge live replay tracks")
            return False
        tmp_path = output_path.with_suffix(f".tmp{output_path.suffix}")
        cmd = [
            ffmpeg,
            "-y",
            "-i",
            str(video_path),
            "-i",
            str(audio_path),
            "-c",
            "copy",
            str(tmp_path),
        ]
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=_REMUX_TIMEOUT_SECONDS
            )
            if proc.returncode != 0:
                logger.error("ffmpeg merge failed: %s", stderr.decode("utf-8", "ignore")[-500:])
                return False
            os.replace(str(tmp_path), str(output_path))
            return True
        except asyncio.TimeoutError:
            logger.error("ffmpeg merge timed out after %ss", int(_REMUX_TIMEOUT_SECONDS))
            if proc is not None:
                await self._kill_and_reap(proc)
            return False
        except asyncio.CancelledError:
            if proc is not None:
                await self._kill_and_reap(proc)
            raise
        except Exception as exc:
            logger.error("ffmpeg merge failed: %s", exc)
            if proc is not None:
                await self._kill_and_reap(proc)
            return False
        finally:
            self._cleanup_temp(tmp_path)

    @staticmethod
    async def _kill_and_reap(proc: asyncio.subprocess.Process) -> None:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        except Exception as exc:
            logger.warning("Failed to kill ffmpeg pid=%s: %s", proc.pid, exc)
        try:
            await asyncio.wait_for(proc.wait(), timeout=_PROCESS_REAP_TIMEOUT_SECONDS)
        except Exception as exc:
            logger.warning("Failed to reap ffmpeg pid=%s: %s", proc.pid, exc)

    @staticmethod
    def _cleanup_temp(*paths: Path) -> None:
        for path in paths:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                logger.debug("Failed to cleanup temp file: %s", path)

    async def _record_outputs(
        self,
        episode: Dict[str, Any],
        replay: Dict[str, Any],
        episode_id: str,
        room_id: str,
        save_dir: Path,
        output_paths: List[Path],
        remux_status: str,
    ) -> None:
        title = str(replay.get("title") or episode.get("title") or "直播回放")
        owner = episode.get("owner") if isinstance(episode.get("owner"), dict) else {}
        author_name = str(owner.get("nickname") or "unknown")
        metadata_json = json.dumps(
            {"episode": episode, "replay": replay, "remux_status": remux_status},
            ensure_ascii=False,
        )
        if self.database:
            await self.database.add_aweme(
                {
                    "aweme_id": episode_id,
                    "aweme_type": "live_replay",
                    "title": title,
                    "author_id": owner.get("id") or owner.get("uid"),
                    "author_name": author_name,
                    "create_time": int(self._started_at(episode).timestamp()),
                    "file_path": str(save_dir),
                    "metadata": metadata_json,
                    "author_sec_uid": "",
                    "cover_urls": json.dumps([]),
                    "job_id": self.job_id or "",
                }
            )
        await self.metadata_handler.append_download_manifest(
            self.file_manager.base_path,
            {
                "date": self._started_at(episode).strftime("%Y-%m-%d_%H%M"),
                "aweme_id": episode_id,
                "author_name": author_name,
                "desc": title,
                "media_type": "live_replay",
                "mode": "live_replay",
                "room_id": room_id,
                "file_names": [path.name for path in output_paths],
                "file_paths": [
                    str(path.relative_to(self.file_manager.base_path)) for path in output_paths
                ],
                "remux_status": remux_status,
                "metadata": metadata_json,
            },
        )
