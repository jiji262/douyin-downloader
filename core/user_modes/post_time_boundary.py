from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional


@dataclass(frozen=True)
class TimeBoundaryDecision:
    should_stop: bool = False
    boundary_reached: bool = False
    degraded_reason: Optional[str] = None


class PostTimeBoundary:
    def __init__(self, start_ts: Optional[int]):
        self._start_ts = start_ts
        self._enabled = start_ts is not None
        self._last_timestamp: Optional[int] = None
        self._boundary_seen = False

    def observe_page(
        self,
        items: List[Dict[str, Any]],
        *,
        is_pinned: Optional[Callable[[Dict[str, Any]], bool]] = None,
    ) -> TimeBoundaryDecision:
        if not self._enabled:
            return TimeBoundaryDecision()
        regular = [item for item in items if not self._is_pinned(item, is_pinned)]
        if not regular:
            reason = "confirmation_page_without_regular_items" if self._boundary_seen else None
            return self._degrade(reason) if reason else TimeBoundaryDecision()
        timestamps = self._timestamps(regular)
        if timestamps is None:
            return self._degrade("missing_or_invalid_create_time")
        if self._boundary_seen and any(value >= self._start_ts for value in timestamps):
            return self._degrade("time_range_reentry")
        if self._order_increased(timestamps):
            return self._degrade("time_order_increased")
        self._last_timestamp = timestamps[-1]
        if self._boundary_seen:
            return TimeBoundaryDecision(should_stop=True, boundary_reached=True)
        self._boundary_seen = any(value < self._start_ts for value in timestamps)
        return TimeBoundaryDecision(boundary_reached=self._boundary_seen)

    @staticmethod
    def _is_pinned(item, checker) -> bool:
        if checker is not None:
            return checker(item)
        value = item.get("is_top")
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    @staticmethod
    def _timestamps(items: List[Dict[str, Any]]) -> Optional[List[int]]:
        timestamps: List[int] = []
        for item in items:
            try:
                value = int(item.get("create_time"))
            except (TypeError, ValueError):
                return None
            if value <= 0:
                return None
            timestamps.append(value)
        return timestamps

    def _order_increased(self, timestamps: List[int]) -> bool:
        if any(current > previous for previous, current in zip(timestamps, timestamps[1:])):
            return True
        return self._last_timestamp is not None and timestamps[0] > self._last_timestamp

    def _degrade(self, reason: str) -> TimeBoundaryDecision:
        self._enabled = False
        return TimeBoundaryDecision(degraded_reason=reason)
