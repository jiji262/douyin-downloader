#!/usr/bin/env python3
"""Backfill / hand-test MCP transcript for one already-downloaded aweme.

Does not scan the whole media library: caller passes video path (or save_dir + stem).

Examples:
  python tools/run_mcp_transcript.py --aweme-id 7648284266559835402 --video G:\\media\\douyin\\...\\xxx.mp4
  python tools/run_mcp_transcript.py --aweme-id 7665740374463705009 --save-dir DIR --stem FILE_STEM

API key: DOUYIN_API_KEY (or API_KEY / OPENAI_API_KEY). Never pass keys on the CLI.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Repo root on sys.path when run as script
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from local_pipeline.mcp_transcript import (  # noqa: E402
    DEFAULT_MCP_PYTHON,
    DEFAULT_MCP_SCRIPTS,
    append_factory_records,
    build_factory_record,
    process_aweme_transcript,
    title_from_stem,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="MCP extract_text backfill for one aweme")
    parser.add_argument("--aweme-id", required=True)
    parser.add_argument("--video", type=Path, help="Path to .mp4 (stem/dir inferred)")
    parser.add_argument("--save-dir", type=Path)
    parser.add_argument("--stem", type=str)
    parser.add_argument("--author", default="")
    parser.add_argument("--title", default="")
    parser.add_argument(
        "--media-root",
        type=Path,
        default=Path(r"G:\media\douyin"),
        help="Root for _factory index append",
    )
    parser.add_argument("--mcp-scripts", default=DEFAULT_MCP_SCRIPTS)
    parser.add_argument("--mcp-python", default=DEFAULT_MCP_PYTHON)
    args = parser.parse_args()

    if args.video:
        video_path = args.video.resolve()
        save_dir = video_path.parent
        stem = video_path.stem
        media_path = str(video_path)
        if not args.author:
            # author dir is typically parent of the aweme folder
            try:
                args.author = video_path.parent.parent.name
            except Exception:
                args.author = ""
    elif args.save_dir and args.stem:
        save_dir = args.save_dir.resolve()
        stem = args.stem
        media_path = str(save_dir / f"{stem}.mp4")
    else:
        parser.error("Provide --video or both --save-dir and --stem")
        return 2

    title = (args.title or "").strip() or title_from_stem(stem, args.aweme_id)

    mcp_cfg = {
        "mcp_scripts_path": args.mcp_scripts,
        "mcp_python": args.mcp_python,
    }
    result = process_aweme_transcript(
        aweme_id=args.aweme_id,
        save_dir=save_dir,
        file_stem=stem,
        mcp_cfg=mcp_cfg,
        video_path=Path(media_path) if Path(media_path).is_file() else None,
    )
    status = result.get("status")
    print(f"status={status} path={result.get('transcript_path')}")
    if result.get("error"):
        print(f"error={result.get('error')}")

    # Do not append a weaker "skipped" factory row that would clobber an earlier ok
    # when ingest prefers last-write; still append for ok/failed.
    if status == "skipped":
        return 0

    record = build_factory_record(
        aweme_id=args.aweme_id,
        author=args.author,
        title=title,
        media_path=media_path,
        transcript_path=result.get("transcript_path"),
        transcript_status=str(status or "failed"),
        source="backfill",
    )
    append_factory_records(args.media_root, record)
    return 0 if status in {"ok", "skipped"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
