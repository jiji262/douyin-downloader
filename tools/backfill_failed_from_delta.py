#!/usr/bin/env python3
"""Backfill MCP transcripts for failed rows listed in a factory delta JSONL.

Reads only the given delta file (no recursive media-root scan). Prefer local
mp4 ASR. Re-appends factory rows under today's Asia/Shanghai day key.

Examples:
  python tools/backfill_failed_from_delta.py --delta G:\\media\\douyin\\_factory\\delta-2026-08-09.jsonl
  python tools/backfill_failed_from_delta.py --delta ... --limit 3 --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from local_pipeline.mcp_transcript import (  # noqa: E402
    DEFAULT_MCP_PYTHON,
    DEFAULT_MCP_SCRIPTS,
    append_factory_records,
    build_factory_record,
    factory_day_key,
    process_aweme_transcript,
    title_from_stem,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill failed transcripts from a delta JSONL")
    parser.add_argument("--delta", type=Path, required=True)
    parser.add_argument("--media-root", type=Path, default=Path(r"G:\media\douyin"))
    parser.add_argument("--mcp-scripts", default=DEFAULT_MCP_SCRIPTS)
    parser.add_argument("--mcp-python", default=DEFAULT_MCP_PYTHON)
    parser.add_argument("--limit", type=int, default=0, help="Max rows to attempt (0=all)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--statuses",
        default="failed",
        help="Comma-separated transcript_status to retry (default: failed)",
    )
    args = parser.parse_args()

    if not args.delta.is_file():
        print(f"delta missing: {args.delta}", file=sys.stderr)
        return 2

    want = {s.strip() for s in args.statuses.split(",") if s.strip()}
    rows = []
    for line in args.delta.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if str(obj.get("transcript_status") or "") not in want:
            continue
        if not obj.get("aweme_id") or not obj.get("media_path"):
            continue
        rows.append(obj)

    # Latest row wins per aweme_id
    by_id = {}
    for obj in rows:
        by_id[str(obj["aweme_id"])] = obj
    items = list(by_id.values())
    if args.limit and args.limit > 0:
        items = items[: args.limit]

    print(f"day_key_now={factory_day_key()} candidates={len(items)} dry_run={args.dry_run}")
    ok = failed = skipped = 0
    mcp_cfg = {
        "mcp_scripts_path": args.mcp_scripts,
        "mcp_python": args.mcp_python,
        "prefer_local_video": True,
    }

    for obj in items:
        aweme_id = str(obj["aweme_id"])
        media_path = Path(str(obj["media_path"]))
        if not media_path.is_file():
            print(f"SKIP missing media {aweme_id}: {media_path}")
            skipped += 1
            continue
        save_dir = media_path.parent
        stem = media_path.stem
        author = str(obj.get("author") or "")
        title = str(obj.get("title") or "") or title_from_stem(stem, aweme_id)
        print(f"TRY {aweme_id} {media_path.name}")
        if args.dry_run:
            continue

        result = process_aweme_transcript(
            aweme_id=aweme_id,
            save_dir=save_dir,
            file_stem=stem,
            mcp_cfg=mcp_cfg,
            video_path=media_path,
        )
        status = str(result.get("status") or "failed")
        print(f"  status={status} via={result.get('via')} err={result.get('error')}")
        if status == "skipped":
            skipped += 1
            continue
        record = build_factory_record(
            aweme_id=aweme_id,
            author=author,
            title=title,
            media_path=str(media_path),
            transcript_path=result.get("transcript_path"),
            transcript_status=status,
            source="backfill",
        )
        append_factory_records(args.media_root, record)
        if status == "ok":
            ok += 1
        else:
            failed += 1

    print(f"done ok={ok} failed={failed} skipped={skipped}")
    if args.dry_run:
        return 0
    if ok == 0 and failed > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
