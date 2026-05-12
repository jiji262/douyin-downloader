# Feature: desktop-workflow-polish, Property D: Top_Authors_Endpoint invariants
"""Property-based test for ``Database.get_top_authors``.

**Validates Property D: Top_Authors_Endpoint invariants**

**Validates: Requirements 5.2, 5.3, 5.4, 5.5, 5.12**

For any random population of ``aweme`` rows (where ``author_sec_uid`` may be
``None`` / ``""`` / non-empty, ``author_name`` may be ``None`` / ``""`` /
non-empty, and ``create_time`` / ``download_time`` are arbitrary unix seconds)
and any ``(days, limit)`` with ``1 <= days <= 365`` and ``1 <= limit <= 20``,
``get_top_authors(days=days, limit=limit)`` must satisfy:

1. ``len(result) <= limit``
2. Every ``a.sec_uid`` is non-empty and not ``None``
3. Every ``a.download_count >= 1``
4. Sorted by ``(-a.download_count, a.sec_uid)`` (stable tie-break)
5. All ``sec_uid`` values in the result are unique
6. Each ``sec_uid`` in the result has at least one row with
   ``create_time >= now - days*86400`` in the original data
7. Each ``a.author_name`` is either the latest non-empty ``author_name`` for
   that ``sec_uid`` (by ``download_time DESC``) or ``"未知作者"`` when no
   non-empty name exists for that ``sec_uid``.

The test also serves as regression coverage for the stable ordering required
by the design doc (Property D explicitly requires stable sort to avoid flaky
property tests on ties).
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from datetime import datetime
from typing import Any, Dict, List, Optional

from hypothesis import HealthCheck, given
from hypothesis import settings as hyp_settings
from hypothesis import strategies as st

from storage.database import Database

# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

# Small, reusable pool for ``author_sec_uid``. Keeping the pool tiny makes
# grouping interesting (we actually get rows that share a sec_uid) instead of
# generating mostly-unique strings that degenerate into groups of size 1.
# Empty / null values exercise the "must be filtered out" branch (R5.4).
_SEC_UID_POOL = st.sampled_from(
    ["", None, "uid_a", "uid_b", "uid_c", "uid_d", "uid_e"]
)

# Tiny pool for ``author_name``. Allow empty string + None to exercise the
# fallback path to ``"未知作者"`` (R5.5).
_AUTHOR_NAME_POOL = st.one_of(
    st.none(),
    st.just(""),
    st.sampled_from(["Alice", "Bob", "Charlie", "Diana"]),
)

# ``create_time_offset_seconds`` lets us place rows inside or outside any
# cutoff window. Range covers roughly [-400 days, +30 days]: plenty of rows
# land inside the in-window region and plenty land outside it for any
# ``days ∈ [1, 365]``.
_CREATE_OFFSET = st.integers(
    min_value=-400 * 86400, max_value=30 * 86400
)

# ``download_time_offset_seconds`` is a non-negative offset into the past.
# Distinct offsets ⇒ distinct ``download_time`` values for most rows, which
# lets us meaningfully assert "latest non-empty author_name is selected".
_DOWNLOAD_OFFSET = st.integers(min_value=0, max_value=365 * 86400)

_aweme_row_strategy = st.fixed_dictionaries(
    {
        "author_sec_uid": _SEC_UID_POOL,
        "author_name": _AUTHOR_NAME_POOL,
        "create_time_offset_seconds": _CREATE_OFFSET,
        "download_time_offset_seconds": _DOWNLOAD_OFFSET,
    }
)


# ---------------------------------------------------------------------------
# Async helpers
# ---------------------------------------------------------------------------


async def _populate_and_query(
    rows: List[Dict[str, Any]],
    *,
    days: int,
    limit: int,
) -> Dict[str, Any]:
    """Insert generated rows into a fresh DB and call ``get_top_authors``.

    Returns a dict containing the query ``result`` plus the ``now_before`` /
    ``now_after`` timestamps straddling the query call so the caller can
    reason about the method's wall-clock cutoff without being flaky on the
    second boundary.
    """
    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "test.db")
        db = Database(db_path=db_path)
        try:
            await db.initialize()
            # We use the private conn here so we can insert with an explicit
            # ``download_time`` value. ``add_aweme`` / ``add_aweme_batch``
            # both hard-code ``datetime.now()`` which would collapse all
            # download_times to the same value, making the "latest
            # author_name" assertion trivially satisfied.
            conn = await db._get_conn()
            now_ref = int(datetime.now().timestamp())
            # ``now_ref`` is the "test time origin" we use to translate
            # per-row offsets into absolute timestamps. The DB method uses
            # ``datetime.now()`` internally; we capture ``now_before`` right
            # before the call so we can bound the method's cutoff.
            for idx, row in enumerate(rows):
                create_time = now_ref + row["create_time_offset_seconds"]
                download_time = now_ref - row["download_time_offset_seconds"]
                await conn.execute(
                    """
                    INSERT INTO aweme (
                        aweme_id, aweme_type, title, author_id,
                        author_name, author_sec_uid, create_time,
                        download_time, file_path, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"id_{idx}",
                        "video",
                        None,
                        None,
                        row["author_name"],
                        row["author_sec_uid"],
                        create_time,
                        download_time,
                        None,
                        None,
                    ),
                )
            await conn.commit()

            now_before = int(datetime.now().timestamp())
            result = await db.get_top_authors(days=days, limit=limit)
            now_after = int(datetime.now().timestamp())
            return {
                "result": result,
                "rows": rows,
                "now_ref": now_ref,
                "now_before": now_before,
                "now_after": now_after,
            }
        finally:
            await db.close()


# ---------------------------------------------------------------------------
# Assertion helpers
# ---------------------------------------------------------------------------


def _absolute_create_time(row: Dict[str, Any], now_ref: int) -> int:
    return now_ref + int(row["create_time_offset_seconds"])


def _absolute_download_time(row: Dict[str, Any], now_ref: int) -> int:
    return now_ref - int(row["download_time_offset_seconds"])


def _latest_nonempty_names_for(
    sec_uid: str,
    rows: List[Dict[str, Any]],
    now_ref: int,
) -> Optional[set]:
    """Return the set of ``author_name`` values tied for the max download_time
    among rows whose ``author_sec_uid == sec_uid`` and whose ``author_name``
    is non-empty / non-null. Returns ``None`` if no such row exists.

    We return a set (rather than a single value) because SQLite's
    ``ORDER BY ... LIMIT 1`` is not deterministic on ties.
    """
    candidates = [
        (_absolute_download_time(r, now_ref), r["author_name"])
        for r in rows
        if r["author_sec_uid"] == sec_uid
        and r["author_name"] is not None
        and r["author_name"] != ""
    ]
    if not candidates:
        return None
    max_dt = max(dt for dt, _ in candidates)
    return {name for dt, name in candidates if dt == max_dt}


def _assert_invariants(ctx: Dict[str, Any], *, days: int, limit: int) -> None:
    result: List[Dict[str, Any]] = ctx["result"]
    rows: List[Dict[str, Any]] = ctx["rows"]
    now_ref: int = ctx["now_ref"]
    now_before: int = ctx["now_before"]

    # Invariant 1: length bounded by limit.
    assert len(result) <= limit, (
        f"result length {len(result)} exceeds limit {limit}"
    )

    # Invariant 5: all sec_uid values in result are unique.
    sec_uids = [a["sec_uid"] for a in result]
    assert len(set(sec_uids)) == len(sec_uids), (
        f"duplicate sec_uid in result: {sec_uids}"
    )

    # Invariant 4: sorted by (-download_count, sec_uid).
    sort_keys = [(-a["download_count"], a["sec_uid"]) for a in result]
    assert sort_keys == sorted(sort_keys), (
        f"result is not sorted by (-download_count, sec_uid): {sort_keys}"
    )

    # Bound the db method's internal cutoff. The method uses
    # ``datetime.now()`` once inside; that ``now`` is in
    # ``[now_before, now_after]``. Therefore ``cutoff_db`` is in
    # ``[now_before - days*86400, now_after - days*86400]``.
    # A necessary condition for a row to have been included is
    # ``create_time >= cutoff_db``, which implies
    # ``create_time >= now_before - days*86400`` (because cutoff_db is at
    # least that value).
    necessary_cutoff_lower_bound = now_before - days * 86400

    for a in result:
        # Invariant 2: sec_uid must be non-empty / non-null.
        assert a["sec_uid"] is not None, "sec_uid is None in result"
        assert a["sec_uid"] != "", "sec_uid is empty string in result"

        # Invariant 3: download_count >= 1.
        assert a["download_count"] >= 1, (
            f"download_count {a['download_count']} < 1 for sec_uid "
            f"{a['sec_uid']}"
        )

        sec_uid = a["sec_uid"]
        matching_rows = [
            r for r in rows if r["author_sec_uid"] == sec_uid
        ]

        # Invariant 6: at least one row for this sec_uid has
        # ``create_time >= cutoff_db``, which is necessarily
        # ``>= necessary_cutoff_lower_bound``.
        in_window_rows = [
            r for r in matching_rows
            if _absolute_create_time(r, now_ref)
            >= necessary_cutoff_lower_bound
        ]
        assert in_window_rows, (
            f"sec_uid {sec_uid!r} appeared in result but has no row with "
            f"create_time >= {necessary_cutoff_lower_bound} "
            f"(now_before={now_before}, days={days})"
        )

        # Invariant 7: author_name is either the latest non-empty name for
        # this sec_uid (ties allowed) or the placeholder "未知作者".
        latest_names = _latest_nonempty_names_for(sec_uid, rows, now_ref)
        if latest_names is None:
            assert a["author_name"] == "未知作者", (
                f"sec_uid {sec_uid!r} has no non-empty author_name rows, "
                f"expected '未知作者' but got {a['author_name']!r}"
            )
        else:
            assert a["author_name"] in latest_names, (
                f"sec_uid {sec_uid!r} author_name {a['author_name']!r} not "
                f"in tied latest set {latest_names!r}"
            )


# ---------------------------------------------------------------------------
# Property test
# ---------------------------------------------------------------------------


@given(
    rows=st.lists(_aweme_row_strategy, max_size=200),
    days=st.integers(min_value=1, max_value=365),
    limit=st.integers(min_value=1, max_value=20),
)
@hyp_settings(
    deadline=None,
    max_examples=100,
    # ``tempfile.TemporaryDirectory`` + async roundtrip is a little slow and
    # may trigger ``too_slow`` on busy CI nodes; we opt out since 100
    # iterations is the explicit contract from the task list.
    suppress_health_check=[
        HealthCheck.function_scoped_fixture,
        HealthCheck.too_slow,
    ],
)
def test_top_authors_invariants(rows, days, limit):
    """Property D — Top_Authors_Endpoint invariants."""
    ctx = asyncio.run(_populate_and_query(rows, days=days, limit=limit))
    _assert_invariants(ctx, days=days, limit=limit)
