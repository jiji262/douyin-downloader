"""NDJSON 进度事件：`live` / `transcribe` 加 `--events` 时每行一个 JSON 写 stderr。

事件种类::

    {"event":"step","step":"...","detail":"..."}
    {"event":"progress","total":N,"detail":"..."}                 # 条目总数
    {"event":"progress","phase":"recording","recorded_s":..,...}  # 直播录制进度
    {"event":"item","status":"success|failed|skipped","id":"...","reason":"..."}
    {"event":"segment","index":N,"segments":N,"recorded_s":..}    # 直播每封装完一段

stdout 仍然只有最终的 envelope。`--verbose` 的日志也写 stderr，消费方只解析
以 `{"event"` 开头的行。事件里的文本来自平台，都经过 `clean_text`。
"""

import json
import sys
import time
from typing import Any, Callable, Dict, Optional

from agent_mode.envelope import DESC_MAX_CHARS, clean_text

EVENT_STEP = "step"
EVENT_PROGRESS = "progress"
EVENT_ITEM = "item"
EVENT_SEGMENT = "segment"
# 直播进度上游每秒回调一次；同一阶段内按这个间隔节流，阶段切换时立即输出
LIVE_PROGRESS_INTERVAL_S = 5.0


def write_event(event: str, stream: Any = None, **fields: Any) -> None:
    """写一行事件。ensure_ascii 保证一行里不会出现任何裸换行类字符。"""
    target = stream if stream is not None else sys.stderr
    payload: Dict[str, Any] = {"event": event}
    payload.update(fields)
    try:
        target.write(json.dumps(payload, ensure_ascii=True, default=str) + "\n")
        target.flush()
    except (OSError, ValueError):
        # stderr 被关掉不该让录制或转写失败
        return


class SilentReporter:
    """没有 `--events` 时用：实现同样的接口，什么都不输出。"""

    def update_step(self, step: str, detail: str = "") -> None:
        return None

    def set_item_total(self, total: int, detail: str = "") -> None:
        return None

    def advance_item(self, status: str, detail: str = "", reason: str = "") -> None:
        return None

    def on_live_progress(self, **fields: Any) -> None:
        return None


class NdjsonReporter:
    """`core.downloader_base.ProgressReporter` 的 NDJSON 实现，外加直播进度回调。"""

    def __init__(self, stream: Any = None, clock: Callable[[], float] = time.monotonic):
        self._stream = stream
        self._clock = clock
        self._phase = ""
        self._progress_at: Optional[float] = None
        self._segments = 0

    def _emit(self, event: str, **fields: Any) -> None:
        write_event(event, self._stream, **fields)

    def update_step(self, step: str, detail: str = "") -> None:
        self._emit(EVENT_STEP, step=clean_text(step), detail=clean_text(detail))

    def set_item_total(self, total: int, detail: str = "") -> None:
        self._emit(EVENT_PROGRESS, total=int(total), detail=clean_text(detail))

    def advance_item(self, status: str, detail: str = "", reason: str = "") -> None:
        fields: Dict[str, Any] = {"status": clean_text(status), "id": clean_text(detail)}
        if reason:
            fields["reason"] = clean_text(reason, DESC_MAX_CHARS)
        self._emit(EVENT_ITEM, **fields)

    def on_live_progress(self, **fields: Any) -> None:
        """`core.live_session.LiveSession.emit` 的回调（全部是关键字参数）。"""
        segments = int(fields.get("segments") or 0)
        recorded_s = fields.get("recorded_seconds") or 0
        for index in range(self._segments + 1, segments + 1):
            self._emit(EVENT_SEGMENT, index=index, segments=segments, recorded_s=recorded_s)
        self._segments = max(self._segments, segments)
        phase = str(fields.get("phase") or "")
        now = self._clock()
        throttled = (
            phase == self._phase
            and self._progress_at is not None
            and now - self._progress_at < LIVE_PROGRESS_INTERVAL_S
        )
        if throttled:
            return
        self._phase, self._progress_at = phase, now
        self._emit(
            EVENT_PROGRESS,
            phase=clean_text(phase),
            recorded_s=recorded_s,
            bytes=int(fields.get("bytes") or 0),
            segments=segments,
            reconnects=int(fields.get("reconnects") or 0),
        )


def make_reporter(enabled: bool) -> Any:
    """`--events` 打开时返回 NDJSON reporter，否则返回静默的。"""
    return NdjsonReporter() if enabled else SilentReporter()
