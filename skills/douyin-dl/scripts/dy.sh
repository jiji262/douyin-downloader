#!/bin/sh
# Fallback launcher: use douyin-dl from PATH, else a pinned uvx, else report DEPENDENCY_MISSING.
set -eu
VERSION="2.1.0"
# To run unreleased code: DOUYIN_DL_SPEC=git+https://github.com/jiji262/douyin-downloader
SPEC="${DOUYIN_DL_SPEC:-douyin-dl==${VERSION}}"
if command -v douyin-dl >/dev/null 2>&1; then
  exec douyin-dl agent "$@"
fi
if command -v uvx >/dev/null 2>&1; then
  exec uvx --from "$SPEC" douyin-dl agent "$@"
fi
printf '%s\n' '{"schema":1,"ok":false,"verb":null,"version":"'"$VERSION"'","data":{},"error":{"code":"DEPENDENCY_MISSING","message":"douyin-dl is not on PATH and uvx is not available.","retryable":false,"action":{"kind":"install","command":"uv tool install douyin-dl"}},"warnings":[]}'
exit 7
