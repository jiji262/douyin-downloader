# Feature: desktop-workflow-polish, Property F: stderr / config redactor never leaks credentials
"""Hypothesis property tests for ``utils.notifier`` credential redactors.

Validates Property F: stderr / config redactor never leaks credentials
Validates: Requirements 3.14, 7.10

The two helpers under test are:

- ``_mask_credential(value)`` — masks an individual token.
- ``_masked_config_for_log(type, config)`` — returns a deep-copied provider
  config with ``bark.device_key`` / ``telegram.bot_token`` / webhook URL
  query-string values masked.

Properties covered for ``_mask_credential``:

    For any token ``v`` with ``len(v) >= 8``:
      - the middle portion ``v[4:-4]`` is NOT a substring of the masked output
      - the masked output starts with ``v[:4]`` and ends with ``v[-4:]``
      - the masked output contains the literal ``***``
      - the masked output does not grow pathologically
        (``len(out) < len(v) + 16``)

Properties covered for ``_masked_config_for_log`` (JSON-serialized output):

    For each provider type (bark / telegram / webhook):
      - the original secret's middle portion never appears in
        ``json.dumps(masked)`` — i.e. the secret cannot be recovered via a
        JSON config dump that callers use for log redaction.
"""

from __future__ import annotations

import json
from urllib.parse import quote

from hypothesis import given
from hypothesis import settings as hyp_settings
from hypothesis import strategies as st

from utils.notifier import _mask_credential, _masked_config_for_log

# The literal mask sentinel emitted by ``_mask_credential``. Any middle-leak
# assertion must tolerate substrings of this marker — otherwise a generator
# that happens to produce a token whose middle is only ``*`` characters will
# falsely flag the mask itself as a leak (e.g. token='0000*0000' produces
# masked='0000***0000'; the lone '*' in the middle is indistinguishable from
# the sentinel and is NOT a credential leak).
_MASK_SENTINEL = "***"


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------

# Arbitrary unicode text — exercises that the redactor handles any byte-safe
# credential (Bark device keys, Telegram bot tokens, and webhook query values
# may in practice contain only ASCII, but we are defensive in depth).
_token_strategy = st.text(min_size=8, max_size=200)

# Token alphabet used specifically for the webhook-URL test. Hex chars let
# us fuzz byte-like credentials while staying disjoint from the lowercase
# ASCII we use for host / path / param names, so the middle-leak assertion
# measures what it's supposed to — an actual leak of the middle token bytes
# — instead of an accidental substring match against the URL structure.
_webhook_token_strategy = st.text(
    alphabet="0123456789abcdef",
    min_size=16,
    max_size=200,
)

# Safe alphabet for query param names + URL paths/hosts so that a generated
# URL is guaranteed to round-trip through ``urlsplit`` / ``urlencode`` without
# the helper choosing to encode special characters inside the masked value.
# Intentionally excludes hex digits (0-9a-f) so `_webhook_token_strategy` and
# these never collide in a way that would produce spurious failures.
_url_safe_alphabet = "ghijklmnopqrstuvwxyzGHIJKLMNOPQRSTUVWXYZ-_."
_url_safe_text = st.text(alphabet=_url_safe_alphabet, min_size=1, max_size=20)


# ---------------------------------------------------------------------------
# Property: _mask_credential
# ---------------------------------------------------------------------------


@given(value=_token_strategy)
@hyp_settings(max_examples=100)
def test_mask_credential_never_leaks_middle(value: str) -> None:
    """For any ``v`` with ``len(v) >= 8`` the middle ``v[4:-4]`` is scrubbed.

    Additionally: the masked output keeps the 4-char prefix and 4-char suffix,
    contains ``'***'``, and does not expand by more than 16 characters.
    """
    out = _mask_credential(value)

    assert isinstance(out, str)
    assert "***" in out
    assert out.startswith(value[:4])
    assert out.endswith(value[-4:])

    middle = value[4:-4]
    # Skip the middle-leak assertion when the middle is unavoidably a
    # substring of (a) the preserved 4+4 edges, or (b) the mask sentinel
    # ``***`` itself. Case (a) covers e.g. value='000000000' where
    # middle='0' trivially appears in preserved='00000000'; case (b) covers
    # middles consisting only of '*' characters (e.g. value='0000*0000')
    # which are indistinguishable from the sentinel the masker emits.
    middle_in_preserved = len(middle) > 0 and (middle in value[:4] or middle in value[-4:])
    middle_in_sentinel = len(middle) > 0 and middle in _MASK_SENTINEL
    if len(middle) > 0 and not middle_in_preserved and not middle_in_sentinel:
        # The whole middle portion must be gone. Note: a *prefix* or *suffix*
        # of the middle can legally still appear if it happens to match the
        # first/last 4 preserved characters, but the full span cannot.
        assert middle not in out, f"Middle portion {middle!r} leaked into masked output {out!r}"

    # No pathological expansion: ``first4 + '***' + last4`` is 11 chars; we
    # allow some slack but refuse the redactor growing unboundedly.
    assert len(out) < len(value) + 16


@given(value=st.text(max_size=7))
@hyp_settings(max_examples=100)
def test_mask_credential_short_inputs_collapse_to_stars(value: str) -> None:
    """Inputs shorter than 8 chars must collapse to exactly ``'***'``.

    This proves that partial redaction never accidentally reveals a short
    secret such as a 4-char PIN.
    """
    assert _mask_credential(value) == "***"


# ---------------------------------------------------------------------------
# Property: _masked_config_for_log — bark
# ---------------------------------------------------------------------------


@given(device_key=_token_strategy)
@hyp_settings(max_examples=100)
def test_masked_config_bark_device_key_never_leaks(device_key: str) -> None:
    """Bark ``device_key`` middle portion must not appear in serialized output."""
    config = {
        "type": "bark",
        "device_key": device_key,
        "sound": "bell",
    }
    masked = _masked_config_for_log("bark", config)
    serialized = json.dumps(masked, ensure_ascii=False)

    # The original config must not have been mutated in place.
    assert config["device_key"] == device_key

    middle = device_key[4:-4]
    # Skip when the middle is unavoidably present in either the preserved
    # 4+4 edges or the mask sentinel itself (see `_MASK_SENTINEL` comment
    # at top of file for rationale).
    middle_in_preserved = len(middle) > 0 and (
        middle in device_key[:4] or middle in device_key[-4:]
    )
    middle_in_sentinel = len(middle) > 0 and middle in _MASK_SENTINEL
    if len(middle) > 0 and not middle_in_preserved and not middle_in_sentinel:
        # Check the masked field value directly, not the full JSON blob:
        # incidental bytes in other fields (e.g. `"sound": "bell"`) could
        # coincidentally contain the middle character and defeat the
        # assertion without representing a credential leak.
        assert middle not in masked["device_key"], (
            f"device_key middle {middle!r} leaked into masked field {masked['device_key']!r}"
        )
    # Sanity: the serialized JSON exists and contains the mask marker.
    assert "***" in serialized

    # The masked representation must still carry a marker and the short
    # prefix so operators can recognise which key was configured.
    assert "***" in masked["device_key"]
    assert masked["device_key"].startswith(device_key[:4])
    assert masked["device_key"].endswith(device_key[-4:])

    # Non-sensitive fields untouched.
    assert masked["sound"] == "bell"


# ---------------------------------------------------------------------------
# Property: _masked_config_for_log — telegram
# ---------------------------------------------------------------------------


@given(bot_token=_token_strategy)
@hyp_settings(max_examples=100)
def test_masked_config_telegram_bot_token_never_leaks(bot_token: str) -> None:
    """Telegram ``bot_token`` middle portion must not appear in serialized output."""
    config = {
        "type": "telegram",
        "bot_token": bot_token,
        # Distinct suffix so `chat_id` can never spuriously appear in
        # `bot_token[4:-4]` and defeat the leak assertion.
        "chat_id": "CHAT_ID_PLACEHOLDER",
    }
    masked = _masked_config_for_log("telegram", config)

    assert config["bot_token"] == bot_token  # no in-place mutation

    middle = bot_token[4:-4]
    # Skip when the middle is unavoidably present in either the preserved
    # 4+4 edges or the mask sentinel itself (see `_MASK_SENTINEL` comment
    # at top of file for rationale).
    middle_in_preserved = len(middle) > 0 and (middle in bot_token[:4] or middle in bot_token[-4:])
    middle_in_sentinel = len(middle) > 0 and middle in _MASK_SENTINEL
    if len(middle) > 0 and not middle_in_preserved and not middle_in_sentinel:
        # Check masked field value only — see bark test for rationale.
        assert middle not in masked["bot_token"], (
            f"bot_token middle {middle!r} leaked into masked field {masked['bot_token']!r}"
        )

    assert "***" in masked["bot_token"]
    assert masked["chat_id"] == "CHAT_ID_PLACEHOLDER"


# ---------------------------------------------------------------------------
# Property: _masked_config_for_log — webhook url query-string
# ---------------------------------------------------------------------------


@given(
    token=_webhook_token_strategy,
    host=_url_safe_text,
    path=_url_safe_text,
    param=_url_safe_text,
)
@hyp_settings(max_examples=100)
def test_masked_config_webhook_url_query_never_leaks(
    token: str, host: str, path: str, param: str
) -> None:
    """Webhook URL query values are masked; path/host are preserved.

    The token is URL-quoted before being placed into the query so that the
    generated URL is always well-formed and the value inside the query string
    is exactly the original token. After masking we check that the original
    token's middle portion cannot be recovered from either the URL itself or
    the JSON-serialized config.
    """
    url = "https://{host}/{path}?{param}={value}".format(
        host=host, path=path, param=param, value=quote(token, safe="")
    )
    config = {"type": "webhook", "url": url}
    masked = _masked_config_for_log("webhook", config)
    serialized = json.dumps(masked, ensure_ascii=False)

    # Original config untouched.
    assert config["url"] == url

    middle = token[4:-4]
    # Same repetitive-middle caveat as the bark / telegram tests above.
    middle_in_preserved = len(middle) > 0 and (middle in token[:4] or middle in token[-4:])
    if len(middle) > 0 and not middle_in_preserved:
        assert middle not in masked["url"], (
            f"token middle {middle!r} leaked into masked URL {masked['url']!r}"
        )
        assert middle not in serialized

    # Host + path must be preserved exactly so operators can still see which
    # endpoint was configured.
    assert host in masked["url"]
    assert path in masked["url"]
    # The param name is preserved; only its value is masked.
    assert param + "=" in masked["url"]


# ---------------------------------------------------------------------------
# Property: unknown provider types leave config untouched
# ---------------------------------------------------------------------------


@given(secret=_token_strategy)
@hyp_settings(max_examples=100)
def test_masked_config_unknown_type_returns_deep_copy(secret: str) -> None:
    """Unknown provider types do not crash and return a deep copy.

    This guarantees callers can route any provider type through the helper
    without special-casing. For an unknown type we do not attempt to guess
    which fields are sensitive; the unmodified-but-deep-copied payload is
    returned so the caller can log or discard at its discretion.
    """
    config = {"type": "unknown", "secret": secret}
    masked = _masked_config_for_log("unknown", config)

    # Deep copy: mutating one must not affect the other.
    masked["secret"] = "CHANGED"
    assert config["secret"] == secret
