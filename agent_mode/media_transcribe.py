"""`transcribe` 的实现：用本机的 Whisper 把本地媒体文件转成文字，不联网调用任何抖音接口。

复用 `cli/whisper_transcribe.py` 的 `transcribe_file` / `find_videos` / `find_ffmpeg`。
输出目录总是显式传入（默认是输入文件所在目录），不会退回到 cwd 下的 `./transcripts`。

`transcribe_file` 用 `Path.write_text` 按路径落盘，会跟随符号链接；输出目录又可能被别人写
（共享的媒体目录，里面的目录能被改名替换）。所以让它只写系统临时目录下的私有目录，
发布时再在输出目录里独占新建一个随机名文件、经 fd 写入，最后 `os.replace` 成最终名字：
替换的是目录项本身，已有的符号链接、硬链接都不会被写穿。
"""

import argparse
import asyncio
import contextlib
import os
import secrets
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent_mode import state
from agent_mode.envelope import (
    ERROR_MESSAGE_MAX_CHARS,
    AgentError,
    add_warning,
    clean_text,
    install_action,
)
from agent_mode.events import make_reporter

DEFAULT_MODEL = "base"
DEFAULT_LANGUAGE = "zh"
OUTPUT_FORMATS = ("txt",)
TRANSCRIPT_SUFFIX = ".transcript.txt"
WORK_DIR_PREFIX = "douyin-dl-transcribe-"
STAGING_PREFIX = ".douyin-dl-tmp-"
STAGING_SUFFIX = ".txt"
STAGING_TOKEN_BYTES = 8
# 与 Path.write_text 相同，实际权限再经 umask
NEW_FILE_MODE = 0o666
# 只新建、不跟随符号链接：预先放在那个名字上的链接或文件只会让 open 失败
_STAGING_FLAGS = (
    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
)
WORKER_THREAD_NAME = "douyin-dl-transcribe"
WHISPER_PACKAGE = "openai-whisper"
FFMPEG_PACKAGE = "imageio-ffmpeg==0.6.0"
STEP_LOAD_MODEL = "load_model"
STEP_TRANSCRIBE = "transcribe"
REASON_NO_RESULT = "no speech detected, or the audio could not be extracted"


def _load_whisper() -> Any:
    try:
        import whisper
    except ImportError:
        raise AgentError(
            "DEPENDENCY_MISSING",
            "openai-whisper is not installed; `transcribe` needs it.",
            action=install_action(WHISPER_PACKAGE),
        ) from None
    return whisper


def _require_ffmpeg(tools: Any) -> str:
    ffmpeg = tools.find_ffmpeg()
    if not ffmpeg:
        raise AgentError(
            "DEPENDENCY_MISSING",
            "FFmpeg was not found; `transcribe` needs it to extract audio.",
            action=install_action(FFMPEG_PACKAGE),
        )
    return str(ffmpeg)


def _collect_inputs(tools: Any, source: Path) -> List[Path]:
    """文件原样返回；目录递归找 .mp4（沿用人用脚本的 find_videos）。"""
    if source.is_file():
        return [source]
    videos = [Path(video) for video in tools.find_videos(source)]
    if not videos:
        raise AgentError("USAGE", "No .mp4 files found under %s" % source)
    return videos


def _check_model(whisper: Any, name: str) -> None:
    available = getattr(whisper, "available_models", None)
    names = list(available()) if callable(available) else []
    if names and name not in names:
        raise AgentError(
            "USAGE", "Unknown Whisper model '%s'. Available: %s" % (name, ", ".join(names))
        )


def _deliver(future: Any, error: Optional[BaseException], value: Any) -> None:
    if future.done():
        return  # 等待方已经被取消，结果没人要了
    if error is not None:
        future.set_exception(error)
    else:
        future.set_result(value)


async def _in_thread(function: Any, *args: Any) -> Any:
    """在 daemon 线程里跑同步的重计算，事件循环才能响应中断。

    不用默认线程池：取消 await 停不掉线程，`asyncio.run` 收尾时还会等它跑完。
    daemon 线程被取消后直接丢下不管，进程退出时随之结束。
    """
    loop = asyncio.get_event_loop()
    future = loop.create_future()

    def work() -> None:
        error, value = None, None
        try:
            value = function(*args)
        except BaseException as exc:  # noqa: BLE001 — 原样交还给等待方
            error = exc
        try:
            loop.call_soon_threadsafe(_deliver, future, error, value)
        except RuntimeError:
            pass  # 事件循环已经关闭（被中断后线程才跑完）

    threading.Thread(target=work, name=WORKER_THREAD_NAME, daemon=True).start()
    return await future


def _publish(produced: Path, transcript: Path) -> bool:
    """把私有目录里的结果发布到最终路径；没有产出时返回 False。"""
    if produced.is_symlink() or not produced.is_file():
        return False
    content = produced.read_bytes()
    token = secrets.token_hex(STAGING_TOKEN_BYTES)
    staging = transcript.with_name(STAGING_PREFIX + token + STAGING_SUFFIX)
    fd = os.open(str(staging), _STAGING_FLAGS, NEW_FILE_MODE)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
        os.replace(str(staging), str(transcript))
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(str(staging))
        raise
    return True


async def _transcribe_one(tools: Any, video: Path, transcript: Path, **options: Any) -> str:
    """转写一个文件；成功返回空串，失败返回原因。"""
    work_dir = None
    try:
        transcript.parent.mkdir(parents=True, exist_ok=True)
        work_dir = Path(tempfile.mkdtemp(prefix=WORK_DIR_PREFIX))
        ok = await _in_thread(
            tools.transcribe_file,
            video,
            options["model"],
            options["ffmpeg"],
            set(OUTPUT_FORMATS),
            options["language"],
            None,
            str(work_dir),
        )
        published = bool(ok) and _publish(work_dir / transcript.name, transcript)
    except Exception as exc:  # noqa: BLE001 — 单个文件失败不该中断整批
        detail = clean_text(str(exc), ERROR_MESSAGE_MAX_CHARS)
        return "%s: %s" % (type(exc).__name__, detail) if detail else type(exc).__name__
    finally:
        # 被中断（CancelledError）时也走这里；删不掉就算了
        if work_dir is not None:
            shutil.rmtree(str(work_dir), ignore_errors=True)
    return "" if published else REASON_NO_RESULT


class _Progress:
    """整批的进度；被中断时据此给出已完成的部分和正在处理的文件。"""

    def __init__(self) -> None:
        self.items: List[Dict[str, str]] = []
        self.failed: List[Dict[str, str]] = []
        self.current: Optional[Path] = None

    def result(self) -> Dict[str, Any]:
        return {
            "items": self.items,
            "count": len(self.items),
            "failed": len(self.failed),
            "failed_items": self.failed,
        }

    def interrupted(self) -> AgentError:
        current = str(self.current) if self.current else None
        return AgentError(
            "INTERRUPTED",
            "Transcription was interrupted; `items` lists the files already done and "
            "`interrupted_input` the file that was in progress (no transcript written for it).",
            data=dict(self.result(), interrupted_input=current),
        )


def _transcript_path(tools: Any, video: Path, out_dir: Optional[Path]) -> Path:
    # 文件名规则与 transcribe_file 内部一致，才能报出它实际写的路径
    stem = tools._safe_stem(video.stem)
    return (out_dir or video.parent) / (stem + TRANSCRIPT_SUFFIX)


async def _run_batch(tools: Any, videos: List[Path], **options: Any) -> None:
    reporter, out_dir = options.pop("reporter"), options.pop("out_dir")
    progress = options.pop("progress")
    seen = set()
    reporter.set_item_total(len(videos), STEP_TRANSCRIBE)
    for video in videos:
        transcript = _transcript_path(tools, video, out_dir)
        if transcript in seen:
            add_warning("several inputs map to %s; later ones overwrite it" % transcript)
        seen.add(transcript)
        reporter.update_step(STEP_TRANSCRIBE, video.name)
        progress.current = video
        reason = await _transcribe_one(tools, video, transcript, **options)
        progress.current = None
        if reason:
            progress.failed.append({"input": str(video), "reason": reason})
            reporter.advance_item("failed", str(video), reason=reason)
            continue
        progress.items.append({"input": str(video), "transcript_path": str(transcript)})
        reporter.advance_item("success", str(video))


async def _load_and_run(whisper: Any, tools: Any, **options: Any) -> None:
    model_name, progress = options.pop("model_name"), options["progress"]
    videos = options.pop("videos")
    options["reporter"].update_step(STEP_LOAD_MODEL, model_name)
    try:
        model = await _in_thread(whisper.load_model, model_name)
        await _run_batch(tools, videos, model=model, **options)
    except asyncio.CancelledError:
        raise progress.interrupted() from None


async def transcribe_path(args: argparse.Namespace) -> Dict[str, Any]:
    source = state.resolve_path(args.path)
    if not source.exists():
        raise AgentError("USAGE", "Path does not exist: %s" % source)
    whisper = _load_whisper()
    from cli import whisper_transcribe as tools

    ffmpeg = _require_ffmpeg(tools)
    videos = _collect_inputs(tools, source)
    model_name = args.model or DEFAULT_MODEL
    _check_model(whisper, model_name)
    progress = _Progress()
    await _load_and_run(
        whisper,
        tools,
        videos=videos,
        model_name=model_name,
        ffmpeg=ffmpeg,
        language=args.language or DEFAULT_LANGUAGE,
        out_dir=state.resolve_path(args.out) if args.out else None,
        reporter=make_reporter(args.events),
        progress=progress,
    )
    data = progress.result()
    if data["failed"]:
        raise AgentError(
            "PARTIAL",
            "%d of %d file(s) could not be transcribed; see `failed_items`."
            % (data["failed"], len(videos)),
            data=data,
        )
    return data
