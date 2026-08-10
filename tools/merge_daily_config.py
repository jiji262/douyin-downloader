"""Merge Douzy config.yml + config.automation.yml → daily generated yml."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import yaml


def merge(base, overlay):
    result = dict(base)
    for key, value in overlay.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = merge(result[key], value)
        else:
            result[key] = value
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("douzy_config")
    parser.add_argument("overlay")
    parser.add_argument("out")
    parser.add_argument("database_path")
    parser.add_argument("collect_limit", type=int)
    args = parser.parse_args()

    def load(path: str):
        return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}

    cfg = merge(load(args.douzy_config), load(args.overlay))
    cfg["path"] = os.environ.get("DOUYIN_MEDIA_ROOT") or cfg.get("path") or r"G:\media\douyin"
    cfg["cookie"] = "auto"
    cfg["auto_cookie"] = True
    cfg["database"] = True
    cfg["database_path"] = args.database_path
    cfg.setdefault("transcript", {})
    cfg["transcript"]["enabled"] = False
    cfg.setdefault("mcp_transcript", {})
    cfg["mcp_transcript"]["enabled"] = True
    cfg["mcp_transcript"].setdefault("prefer_local_video", True)
    cfg.setdefault("number", {})
    cfg["number"]["collect"] = args.collect_limit
    cfg.setdefault("increase", {})
    cfg["increase"]["collect"] = True
    cfg["mode"] = ["collect"]
    cfg["link"] = ["https://www.douyin.com/user/self"]
    cfg["music"] = True
    cfg["collect_use_aweme_author_dir"] = True
    cfg["group_by_mode"] = False

    out = Path(args.out)
    out.write_text(
        yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    print(str(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
