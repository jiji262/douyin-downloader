# Feature: desktop-workflow-polish, Property C: Proxy_Probe parameter validation parity with _is_valid_proxy
"""Hypothesis property tests for ``server.app._is_valid_proxy`` parity.

Validates Property C: Proxy_Probe parameter validation parity with
``_is_valid_proxy``
Validates: Requirements 8.2, 8.9

This file is the **Python half** of a bit-for-bit parity contract between
the desktop renderer's ``isValidProxy`` (TypeScript) and the sidecar's
``_is_valid_proxy`` (Python). The two must agree for every input so that
the renderer can disable the "测试代理" button for strings the server will
reject with HTTP 400 — no round trip required.

Two sources of samples are combined:

1. A static fixture ``tests/fixtures/proxy_samples.json`` that both sides
   replay. If the fixture is missing the test gracefully skips the fixture
   replay but keeps the Hypothesis generator portion running (useful during
   iterative development before task 7.2 lands).
2. Hypothesis-generated arbitrary strings, biased with a weighted sample
   pool so the shrinker explores representative edge cases — every legal
   scheme, non-whitelisted schemes, empty host, whitespace-only host, mixed
   case, and random unicode.

Kept Python 3.8 compatible (no walrus, no ``match`` statements, no PEP-604
union syntax). The file is shared with the CLI project per task 7.1, so the
imports must stay portable.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

import pytest
from hypothesis import given
from hypothesis import settings as hyp_settings
from hypothesis import strategies as st

# Sidecar-only symbols. In environments that ship a stripped-down
# `server/app.py` (e.g. the CLI project before the proxy-validator
# feature lands there), the import would break collection. Downgrade
# that to a module-level skip so `pytest tests/` still runs the rest
# of the suite cleanly.
try:
    from server.app import _PROXY_ALLOWED_SCHEMES, _is_valid_proxy
except ImportError as _exc:
    pytest.skip(
        f"server.app proxy validator symbols unavailable in this worktree: {_exc}",
        allow_module_level=True,
    )

_FIXTURE_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "proxy_samples.json")


def _load_fixture_samples() -> List[Dict[str, Any]]:
    """Return the samples array from the parity fixture, or [] if missing.

    The fixture is shared with the TypeScript half of Property C via task
    7.1's sync script. During iterative development (or on a stripped-down
    worktree) the fixture may not exist yet; in that case we silently fall
    back to an empty list so the Hypothesis portion of this file still runs.
    """
    if not os.path.exists(_FIXTURE_PATH):
        return []
    try:
        with open(_FIXTURE_PATH, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, ValueError):
        return []
    samples = payload.get("samples")
    if not isinstance(samples, list):
        return []
    return samples


_FIXTURE_SAMPLES = _load_fixture_samples()


# ---------------------------------------------------------------------------
# Fixture replay — one parametrised test per row
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _FIXTURE_SAMPLES,
    reason=(
        "tests/fixtures/proxy_samples.json missing; skip fixture replay "
        "(task 7.2 creates the fixture)."
    ),
)
@pytest.mark.parametrize(
    "sample",
    _FIXTURE_SAMPLES,
    ids=[
        # Avoid huge / unreadable ids: just expose the input string (truncated)
        # and the expected validity. The shape is ``{input, expected: {isValid}}``.
        "[{}]{}".format(
            "OK " if s.get("expected", {}).get("isValid") else "NG ",
            (s.get("input", "") or "<empty>")[:40],
        )
        for s in _FIXTURE_SAMPLES
    ],
)
def test_is_valid_proxy_matches_fixture(sample: Dict[str, Any]) -> None:
    """Every fixture sample's ``expected.isValid`` must match ``_is_valid_proxy``.

    The parity contract is bit-for-bit: if ``isValid=true`` the Python half
    accepts the string; if ``isValid=false`` the Python half rejects it.
    Schema: ``{input: string, expected: {isValid: bool, scheme?: string}}``.
    """
    input_value = sample.get("input")
    expected = sample.get("expected") or {}
    expected_valid = bool(expected.get("isValid"))

    assert isinstance(input_value, str), "fixture row must carry a string `input`; got {!r}".format(
        input_value
    )

    actual_valid = _is_valid_proxy(input_value)
    assert actual_valid == expected_valid, "parity break: input={!r} expected={} got={}".format(
        input_value, expected_valid, actual_valid
    )


# ---------------------------------------------------------------------------
# Hypothesis generator — weighted sample pool + arbitrary strings
# ---------------------------------------------------------------------------


_LEGAL_SCHEMES = list(_PROXY_ALLOWED_SCHEMES)
_ILLEGAL_SCHEMES = ["ftp", "ws", "wss", "socks", "socks4", "tcp", "rsync", "file"]


def _scheme_with_host_strategy() -> st.SearchStrategy[str]:
    """Generate ``<legal_scheme>://<host>`` strings with non-blank hosts.

    Hosts can be IPv4/IPv6/hostnames/with-ports/with-auth; we just need a
    post-scheme remainder with at least one non-whitespace byte so
    ``_is_valid_proxy`` accepts them.
    """
    scheme = st.sampled_from(_LEGAL_SCHEMES)
    # Restrict host to printable ASCII so we can reason about "non-whitespace
    # after scheme" without unicode ambiguity. Exclude control chars.
    host = st.text(
        alphabet=st.characters(
            min_codepoint=33,
            max_codepoint=126,  # printable ASCII, no space
        ),
        min_size=1,
        max_size=60,
    )
    return st.builds(lambda s, h: "{}://{}".format(s, h), scheme, host)


def _illegal_scheme_strategy() -> st.SearchStrategy[str]:
    """Generate strings with a non-whitelisted scheme."""
    scheme = st.sampled_from(_ILLEGAL_SCHEMES)
    host = st.text(
        alphabet=st.characters(min_codepoint=33, max_codepoint=126),
        min_size=1,
        max_size=40,
    )
    return st.builds(lambda s, h: "{}://{}".format(s, h), scheme, host)


def _empty_or_blank_host_strategy() -> st.SearchStrategy[str]:
    """Generate ``<legal_scheme>://`` + optional whitespace-only tail."""
    scheme = st.sampled_from(_LEGAL_SCHEMES)
    ws = st.text(alphabet=" \t\n\r", min_size=0, max_size=8)
    return st.builds(lambda s, w: "{}://{}".format(s, w), scheme, ws)


def _whitespace_only_strategy() -> st.SearchStrategy[str]:
    """Generate pure-whitespace strings (including empty)."""
    return st.text(alphabet=" \t\n\r", min_size=0, max_size=8)


def _mixed_case_scheme_strategy() -> st.SearchStrategy[str]:
    """Generate schemes with uppercase letters to exercise case sensitivity.

    ``_PROXY_RE`` is case-sensitive, so ``HTTP://example.com`` must be
    rejected even though the scheme name is a whitelisted one.
    """
    # Hand-crafted mixed-case variants — Hypothesis sampled_from keeps the
    # search space tight and the shrinker well-behaved.
    variants = [
        "HTTP",
        "HTTPS",
        "SOCKS5",
        "SOCKS5H",
        "Http",
        "HtTp",
        "HTtPs",
        "Socks5",
        "Socks5H",
    ]
    scheme = st.sampled_from(variants)
    host = st.text(
        alphabet=st.characters(min_codepoint=33, max_codepoint=126),
        min_size=1,
        max_size=40,
    )
    return st.builds(lambda s, h: "{}://{}".format(s, h), scheme, host)


_WEIGHTED_POOL = st.one_of(
    _scheme_with_host_strategy(),
    _illegal_scheme_strategy(),
    _empty_or_blank_host_strategy(),
    _whitespace_only_strategy(),
    _mixed_case_scheme_strategy(),
    # Fully arbitrary text to catch cases the hand-crafted pools miss.
    st.text(max_size=120),
)


def _expected_is_valid(s: str) -> bool:
    """Reference oracle mirroring the exact contract of ``_is_valid_proxy``.

    The oracle is derived from :data:`server.app._PROXY_RE` and the docstring
    on :func:`server.app._is_valid_proxy`:

    - Empty string → valid (means "no proxy").
    - Otherwise must match ``^(https?|socks5h?)://(.+)$`` (case-sensitive)
      AND the post-scheme host portion must contain at least one
      non-whitespace character.
    """
    if s == "":
        return True
    # Case-sensitive scheme check.
    for scheme in ("http", "https", "socks5h", "socks5"):
        # Order matters: match ``socks5h`` before ``socks5`` so the longer
        # prefix wins.
        prefix = scheme + "://"
        if s.startswith(prefix):
            host = s[len(prefix) :]
            return host.strip() != ""
    return False


# ---------------------------------------------------------------------------
# Property: oracle / implementation agreement
# ---------------------------------------------------------------------------


@given(value=_WEIGHTED_POOL)
@hyp_settings(max_examples=100)
def test_is_valid_proxy_matches_oracle(value: str) -> None:
    """Across all generated inputs the oracle and the implementation agree.

    The oracle mirrors the documented contract on :func:`_is_valid_proxy`.
    If this test fails, either the regex in ``_PROXY_RE`` drifted from its
    docstring, or the oracle above needs updating — both require a code
    review of the parity fixture too (task 7.2).
    """
    actual = _is_valid_proxy(value)
    expected = _expected_is_valid(value)
    assert actual == expected, "parity break: value={!r} oracle={} actual={}".format(
        value, expected, actual
    )


# ---------------------------------------------------------------------------
# Property: whitelist invariant
# ---------------------------------------------------------------------------


@given(value=_WEIGHTED_POOL)
@hyp_settings(max_examples=100)
def test_non_empty_valid_proxy_has_whitelisted_scheme(value: str) -> None:
    """Non-empty inputs that pass validation must start with a legal scheme.

    Complements the oracle test: proves that ``_is_valid_proxy`` never
    "silently accepts" a scheme that the ``POST /api/v1/network/test-proxy``
    handler would subsequently 400 on (Requirement 8.9). Empty string is
    the well-known exception (means "no proxy").
    """
    if not _is_valid_proxy(value):
        return
    if value == "":
        return
    scheme_prefix: Optional[str] = None
    for scheme in ("socks5h", "socks5", "https", "http"):
        candidate = scheme + "://"
        if value.startswith(candidate):
            scheme_prefix = scheme
            break
    assert scheme_prefix is not None, (
        "value passed _is_valid_proxy but has no whitelisted scheme prefix: {!r}".format(value)
    )
    assert scheme_prefix in _PROXY_ALLOWED_SCHEMES
