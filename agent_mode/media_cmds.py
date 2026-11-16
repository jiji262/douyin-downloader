"""`live`、`transcribe` 的参数注册与入口。接口见 agent_mode/entry.py。

实现分别在 `agent_mode/media_live.py` 和 `agent_mode/media_transcribe.py`，
到真正执行时才 import，保证 `--help` 和其他 verb 不加载下载器。
"""

import argparse
from typing import Any, Dict

from agent_mode.entry import bounded_int

LIVE_MAX_DURATION_S = 21600


def register(subparsers: Any) -> None:
    live = subparsers.add_parser("live", help="record a live room for a bounded duration")
    live.add_argument("url")
    live.add_argument(
        "--max-duration",
        type=bounded_int(1, LIVE_MAX_DURATION_S),
        required=True,
        metavar="S",
        help="stop recording after S seconds (required)",
    )
    live.add_argument("--out", metavar="DIR", help="output directory (default: ./douyin-downloads)")
    live.add_argument("--events", action="store_true", help="write NDJSON progress to stderr")
    live.set_defaults(handler=run_live)

    transcribe = subparsers.add_parser(
        "transcribe", help="transcribe local media files with Whisper"
    )
    transcribe.add_argument("path", help="media file or directory")
    transcribe.add_argument("--model", metavar="NAME", help="Whisper model (default: base)")
    transcribe.add_argument("--language", metavar="CODE", help="spoken language (default: zh)")
    transcribe.add_argument("--out", metavar="DIR", help="default: next to each input file")
    transcribe.add_argument("--events", action="store_true", help="write NDJSON progress to stderr")
    transcribe.set_defaults(handler=run_transcribe)


async def run_live(args: argparse.Namespace) -> Dict[str, Any]:
    from agent_mode.media_live import record_live

    return await record_live(args)


async def run_transcribe(args: argparse.Namespace) -> Dict[str, Any]:
    from agent_mode.media_transcribe import transcribe_path

    return await transcribe_path(args)
