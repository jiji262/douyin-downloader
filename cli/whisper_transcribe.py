#!/usr/bin/env python3
"""
whisper_transcribe.py — 对 douyin-downloader 下载的视频进行 Whisper 语音识别

安装:
  pip install openai-whisper rich
  # ffmpeg: conda install -c conda-forge ffmpeg  或放 ffmpeg.exe 到同目录

用法:
  python whisper_transcribe.py                          # 扫描 ./Downloaded/ 下所有mp4
  python whisper_transcribe.py -d ./Downloaded/          # 指定目录
  python whisper_transcribe.py -f video.mp4              # 单个文件
  python whisper_transcribe.py -d ./Downloaded/ -m medium # 用medium模型
  python whisper_transcribe.py -d ./Downloaded/ --srt     # 同时输出SRT
  python whisper_transcribe.py --skip-existing --sc       # 跳过已有 + 繁转简
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

console = Console()

# ── 颜色主题 (区别于 douyin-downloader 的 cyan/magenta) ──
THEME = {
    "accent":  "bright_green",
    "banner":  "bold bright_green",
    "info":    "dodger_blue1",
    "success": "green",
    "warning": "yellow",
    "error":   "red",
    "dim":     "dim white",
    "file":    "bright_cyan",
    "model":   "orchid",
}


# ============================================================
# TranscribeDisplay — rich 进度显示
# ============================================================
class TranscribeDisplay:
    def __init__(self):
        self.console = console
        self._progress_ctx: Optional[Progress] = None
        self._progress: Optional[Progress] = None
        self._overall_id: Optional[int] = None
        self._file_id: Optional[int] = None
        self._file_index = 0
        self._file_total = 0
        self._stats = {"success": 0, "failed": 0, "skipped": 0}

    # ── banner ──
    def show_banner(self):
        banner = Text()
        banner.append("  🎙  Whisper 视频转录工具\n", style="bold bright_green")
        banner.append("  ── Video → Text via OpenAI Whisper ──", style="dim bright_green")
        panel = Panel(banner, border_style="bright_green", expand=False, padding=(0, 2))
        self.console.print(panel)
        self.console.print()

    # ── progress lifecycle ──
    def start_session(self, total: int):
        self._file_total = total
        self._file_index = 0
        self._stats = {"success": 0, "failed": 0, "skipped": 0}

        self._progress_ctx = Progress(
            SpinnerColumn(style="bright_green"),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(bar_width=30, complete_style="bright_green", finished_style="green"),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            TextColumn("[dim]{task.fields[detail]}"),
            console=self.console,
            transient=True,
            refresh_per_second=6,
        )
        self._progress = self._progress_ctx.__enter__()
        self._overall_id = self._progress.add_task(
            "[bright_green]总体进度[/]",
            total=max(total, 1),
            completed=0,
            detail=f"共 {total} 个视频",
        )

    def stop_session(self):
        if self._file_id is not None and self._progress:
            self._progress.remove_task(self._file_id)
            self._file_id = None
        if self._progress_ctx is not None:
            self._progress_ctx.__exit__(None, None, None)
        self._progress_ctx = None
        self._progress = None
        self._overall_id = None

    # ── per-file ──
    def start_file(self, index: int, name: str):
        self._file_index = index
        if self._file_id is not None and self._progress:
            self._progress.remove_task(self._file_id)
        if not self._progress:
            return
        self._file_id = self._progress.add_task(
            self._file_desc("提取音频"),
            total=4,  # 提取音频 → 识别 → 转换 → 保存
            completed=0,
            detail=self._shorten(name, 50),
        )

    def advance_file(self, step: str, detail: str = ""):
        if not self._progress or self._file_id is None:
            return
        self._progress.advance(self._file_id, 1)
        self._progress.update(
            self._file_id,
            description=self._file_desc(step),
            detail=detail,
        )

    def complete_file(self, status: str, detail: str = ""):
        if status in self._stats:
            self._stats[status] += 1
        if self._progress:
            if self._file_id is not None:
                self._progress.update(
                    self._file_id, completed=4,
                    description=self._file_desc("完成" if status == "success" else "跳过" if status == "skipped" else "失败"),
                    detail=detail,
                )
                self._progress.remove_task(self._file_id)
                self._file_id = None
            if self._overall_id is not None:
                self._progress.advance(self._overall_id, 1)
                self._progress.update(
                    self._overall_id,
                    detail=f"✓{self._stats['success']}  ✗{self._stats['failed']}  ⊘{self._stats['skipped']}",
                )

    # ── summary table ──
    def show_summary(self):
        table = Table(
            title="Transcription Summary",
            show_header=True,
            header_style=f"bold {THEME['accent']}",
            border_style=THEME["accent"],
        )
        table.add_column("Metric", style=THEME["info"])
        table.add_column("Count", justify="right", style=THEME["success"])

        total = self._stats["success"] + self._stats["failed"] + self._stats["skipped"]
        table.add_row("Total", str(total))
        table.add_row("Success", str(self._stats["success"]))
        table.add_row("Failed", str(self._stats["failed"]))
        table.add_row("Skipped", str(self._stats["skipped"]))
        if total > 0:
            rate = self._stats["success"] / total * 100
            table.add_row("Success Rate", f"{rate:.1f}%")

        self.console.print()
        self.console.print(table)

    # ── logging ──
    def info(self, msg: str):
        self._out().print(f"[{THEME['info']}]ℹ[/] {msg}")

    def success(self, msg: str):
        self._out().print(f"[{THEME['success']}]✓[/] {msg}")

    def warning(self, msg: str):
        self._out().print(f"[{THEME['warning']}]⚠[/] {msg}")

    def error(self, msg: str):
        self._out().print(f"[{THEME['error']}]✗[/] {msg}")

    def dep_ok(self, name: str, detail: str = ""):
        self._out().print(f"  [{THEME['success']}]✓[/] {name}  [{THEME['dim']}]{detail}[/]")

    def dep_fail(self, name: str, hint: str):
        self._out().print(f"  [{THEME['error']}]✗[/] {name}  [{THEME['dim']}]{hint}[/]")

    # ── internal ──
    def _file_desc(self, step: str) -> str:
        return f"[{THEME['accent']}]{self._file_index}/{self._file_total}[/] · {step}"

    def _out(self) -> Console:
        return self._progress.console if self._progress else self.console

    @staticmethod
    def _shorten(text: str, max_len: int = 50) -> str:
        t = (text or "").strip()
        return t if len(t) <= max_len else f"{t[:max_len - 3]}..."


display = TranscribeDisplay()


# ============================================================
# 核心功能
# ============================================================
def find_ffmpeg():
    p = shutil.which("ffmpeg")
    if p:
        return p
    local = Path(__file__).parent / "ffmpeg.exe"
    if local.exists():
        return str(local)
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        pass
    return None


def extract_audio(video_path, audio_path, ffmpeg_path="ffmpeg"):
    cmd = [
        ffmpeg_path, "-i", str(video_path),
        "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
        str(audio_path), "-y", "-loglevel", "quiet",
    ]
    result = subprocess.run(cmd, capture_output=True)
    return result.returncode == 0 and Path(audio_path).exists()


def _format_srt_time(seconds):
    h, r = divmod(seconds, 3600)
    m, r = divmod(r, 60)
    s = int(r)
    ms = int((r - s) * 1000)
    return f"{int(h):02d}:{int(m):02d}:{s:02d},{ms:03d}"


def transcribe_file(video_path, model, ffmpeg_path, output_formats, language, converter):
    video_path = Path(video_path)
    stem = video_path.stem
    out_dir = video_path.parent
    txt_path = out_dir / f"{stem}.transcript.txt"
    srt_path = out_dir / f"{stem}.transcript.srt"

    tmpdir = tempfile.mkdtemp(prefix="whisper_")
    try:
        # Step 1: 提取音频
        audio_path = os.path.join(tmpdir, "audio.wav")
        if not extract_audio(video_path, audio_path, ffmpeg_path):
            display.advance_file("失败", "音频提取失败")
            return False
        audio_mb = os.path.getsize(audio_path) / 1024 / 1024
        display.advance_file("识别中", f"音频 {audio_mb:.1f}MB")

        # Step 2: Whisper 识别
        result = model.transcribe(audio_path, language=language, verbose=False)
        segments = result.get("segments", [])
        detected_lang = result.get("language", language)

        if not segments:
            display.advance_file("无内容", "未检测到语音")
            return False

        # Step 3: 繁转简
        def _cv(text):
            return converter.convert(text) if converter and text else text

        text_lines = [_cv(seg["text"].strip()) for seg in segments if seg.get("text", "").strip()]
        tag = "→简" if converter else ""
        display.advance_file("保存", f"{len(segments)}段 lang={detected_lang} {tag}")

        # Step 4: 写文件
        saved = []
        if "txt" in output_formats:
            txt_path.write_text("\n".join(text_lines), encoding="utf-8")
            saved.append(txt_path.name)
        if "srt" in output_formats:
            srt_lines = []
            for i, seg in enumerate(segments, 1):
                text = _cv(seg["text"].strip())
                if text:
                    srt_lines.append(
                        f"{i}\n{_format_srt_time(seg['start'])} --> {_format_srt_time(seg['end'])}\n{text}\n"
                    )
            srt_path.write_text("\n".join(srt_lines), encoding="utf-8")
            saved.append(srt_path.name)

        display.advance_file("完成", " + ".join(saved))
        return True

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def find_videos(directory, skip_existing=False):
    directory = Path(directory)
    if not directory.exists():
        display.error(f"目录不存在: {directory}")
        return []

    videos = sorted(directory.rglob("*.mp4"))

    if skip_existing:
        filtered = []
        for v in videos:
            txt2 = v.parent / f"{v.stem}.transcript.txt"
            if txt2.exists():
                display.info(f"跳过 {v.name} (已有transcript)")
            else:
                filtered.append(v)
        videos = filtered

    return videos


# ============================================================
# Main
# ============================================================
def main():
    parser = argparse.ArgumentParser(
        description="Whisper 视频转录工具 — 批量语音识别",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python whisper_transcribe.py -d ./Downloaded/\n"
            "  python whisper_transcribe.py -f video.mp4 -m medium\n"
            "  python whisper_transcribe.py -d ./Downloaded/ --srt --sc --skip-existing"
        ),
    )
    parser.add_argument("-d", "--dir", default="./Downloaded", help="视频目录 (默认 ./Downloaded/)")
    parser.add_argument("-f", "--file", help="单个视频文件")
    parser.add_argument("-m", "--model", default="base",
                        choices=["tiny", "base", "small", "medium", "large"],
                        help="Whisper模型 (默认 base)")
    parser.add_argument("-l", "--language", default="zh", help="语言 (默认 zh)")
    parser.add_argument("--srt", action="store_true", help="同时输出SRT字幕")
    parser.add_argument("--skip-existing", action="store_true", help="跳过已有transcript的视频")
    parser.add_argument("--sc", action="store_true", help="繁体转简体 (需 pip install OpenCC)")

    args = parser.parse_args()

    # ── Banner ──
    display.show_banner()

    # ── 依赖检查 ──
    console.print(f"  [{THEME['dim']}]检查依赖...[/]")

    ffmpeg_path = find_ffmpeg()
    if not ffmpeg_path:
        display.dep_fail("ffmpeg", "conda install -c conda-forge ffmpeg  或放 ffmpeg.exe 到同目录")
        sys.exit(1)
    display.dep_ok("ffmpeg", ffmpeg_path)

    try:
        import whisper
    except ImportError:
        display.dep_fail("openai-whisper", "pip install openai-whisper")
        sys.exit(1)
    display.dep_ok("whisper", "已安装")

    converter = None
    if args.sc:
        try:
            from opencc import OpenCC
            converter = OpenCC('t2s')
            display.dep_ok("OpenCC", "繁体→简体")
        except ImportError:
            display.dep_fail("OpenCC", "pip install OpenCC")
            sys.exit(1)

    console.print()

    # ── 收集视频 ──
    if args.file:
        videos = [Path(args.file)]
        if not videos[0].exists():
            display.error(f"文件不存在: {args.file}")
            sys.exit(1)
    else:
        videos = find_videos(args.dir, skip_existing=args.skip_existing)

    if not videos:
        display.warning("没有找到需要处理的视频文件")
        return

    display.info(f"找到 {len(videos)} 个视频")

    # ── 加载模型 ──
    display.info(f"加载 Whisper 模型: [{THEME['model']}]{args.model}[/]  (首次需下载)")
    model = whisper.load_model(args.model)
    display.success(f"模型 [{THEME['model']}]{args.model}[/] 加载完成")
    console.print()

    # ── 输出格式 ──
    output_formats = {"txt"}
    if args.srt:
        output_formats.add("srt")

    # ── 处理 ──
    display.start_session(len(videos))
    try:
        for i, video in enumerate(videos, 1):
            display.start_file(i, video.name)
            try:
                ok = transcribe_file(video, model, ffmpeg_path, output_formats, args.language, converter)
                display.complete_file("success" if ok else "failed",
                                      video.name if ok else "识别失败")
            except KeyboardInterrupt:
                display.complete_file("failed", "用户中断")
                raise
            except Exception as e:
                display.complete_file("failed", str(e)[:60])
    except KeyboardInterrupt:
        display.warning("用户中断")
    finally:
        display.stop_session()

    # ── 汇总 ──
    display.show_summary()


if __name__ == "__main__":
    main()