"""Unit + property tests for :mod:`core.audio_extraction`.

Covers Property 6 (stderr 内存有界), Property 7 (subprocess 永不
``shell=True``), Property 8 (可用性缓存正确性), plus the explicit
acceptance criteria around timeout, non-zero exit, empty output, and
platform-unsupported paths.

We intentionally do NOT spin up real ffmpeg processes here; that's
covered by the integration smoke step in tasks.md task 20. Mocking
``asyncio.create_subprocess_exec`` lets us exercise every error branch
deterministically and quickly.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional, Tuple
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from core.audio_extraction import (
    AudioExtractEmpty,
    FfmpegLocator,
    FfmpegNonZeroExit,
    FfmpegNotAvailable,
    FfmpegTimeout,
    extract_audio,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeStream:
    """Minimal asyncio.StreamReader stand-in for stderr drain testing."""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self._pos = 0

    async def read(self, n: int) -> bytes:
        if self._pos >= len(self._payload):
            return b""
        chunk = self._payload[self._pos : self._pos + n]
        self._pos += len(chunk)
        return chunk


class _FakeProcess:
    """asyncio.subprocess.Process stand-in.

    We stub the three attributes ``extract_audio`` actually touches:
    ``returncode``, ``stderr``, and ``wait()``/``kill()``.
    """

    def __init__(
        self,
        *,
        returncode: int = 0,
        stderr_payload: bytes = b"",
        wait_delay: float = 0.0,
        wait_exception: Optional[BaseException] = None,
    ) -> None:
        self.returncode = returncode
        self.stderr = _FakeStream(stderr_payload)
        self.pid = 12345  # arbitrary, only used in log messages
        self._wait_delay = wait_delay
        self._wait_exception = wait_exception
        self.killed = False
        # Track the rc the caller set; flip to None until wait() resolves
        # if you want to simulate a still-running process.

    async def wait(self) -> int:
        # After ``kill()``, subsequent ``wait()`` calls should resolve
        # promptly (the OS reaped the process). Without this short-circuit
        # ``_kill_and_reap`` would still sleep ``wait_delay`` seconds.
        if self.killed:
            return self.returncode
        if self._wait_delay:
            await asyncio.sleep(self._wait_delay)
        if self._wait_exception is not None:
            raise self._wait_exception
        return self.returncode

    def kill(self) -> None:
        self.killed = True


def _patch_subprocess(monkeypatch: pytest.MonkeyPatch, fake: _FakeProcess) -> MagicMock:
    """Patch ``asyncio.create_subprocess_exec`` so the next call returns
    ``fake``. Returns the mock so the test can inspect call args."""
    mock = AsyncMock(return_value=fake)
    # Patch in *both* places in case the module aliases.
    monkeypatch.setattr("core.audio_extraction.asyncio.create_subprocess_exec", mock)
    return mock


@pytest.fixture
def writable_tmp(tmp_path: Path) -> Tuple[Path, Path]:
    """Return ``(video_path, output_dir)`` and pre-create a fake video."""
    video = tmp_path / "源视频_with spaces.mp4"
    video.write_bytes(b"\x00" * 16)  # placeholder content
    out_dir = tmp_path / "out"
    return video, out_dir


@pytest.fixture(autouse=True)
def _reset_ffmpeg_locator():
    """Each test gets a clean FfmpegLocator singleton."""
    FfmpegLocator.reset_for_tests()
    yield
    FfmpegLocator.reset_for_tests()


@pytest.fixture
def mock_locator() -> FfmpegLocator:
    """A FfmpegLocator that yields a deterministic path without
    actually probing ffmpeg."""
    locator = FfmpegLocator()
    locator._available = True
    locator._path = "/fake/ffmpeg"
    locator._version = "ffmpeg version test"
    locator._cached_at = 999_999_999.0  # far future; never refreshes
    return locator


# ---------------------------------------------------------------------------
# extract_audio: success path
# ---------------------------------------------------------------------------


async def test_extract_audio_writes_mp3_when_ffmpeg_returns_zero(
    monkeypatch: pytest.MonkeyPatch,
    writable_tmp: Tuple[Path, Path],
    mock_locator: FfmpegLocator,
) -> None:
    video, out_dir = writable_tmp

    # We need the mock subprocess to also create the output file so
    # extract_audio's empty-output check passes.
    expected_out = out_dir / f"{video.stem}.mp3"

    fake = _FakeProcess(returncode=0, stderr_payload=b"ffmpeg: ok\n")

    async def fake_create(*args, **kwargs):
        # Side effect: pretend ffmpeg wrote the output file.
        out_dir.mkdir(parents=True, exist_ok=True)
        expected_out.write_bytes(b"\xff\xfb\x00fake-mp3-bytes")
        return fake

    monkeypatch.setattr("core.audio_extraction.asyncio.create_subprocess_exec", fake_create)

    result = await extract_audio(video, out_dir, locator=mock_locator)
    assert result == expected_out
    assert result.read_bytes() == b"\xff\xfb\x00fake-mp3-bytes"


async def test_extract_audio_passes_correct_ffmpeg_args(
    monkeypatch: pytest.MonkeyPatch,
    writable_tmp: Tuple[Path, Path],
    mock_locator: FfmpegLocator,
) -> None:
    """Property 7: subprocess always invoked with list args, never
    ``shell=True``. Also pins R1.2's exact codec flags."""
    video, out_dir = writable_tmp
    expected_out = out_dir / f"{video.stem}.mp3"

    fake = _FakeProcess(returncode=0)
    captured = {}

    async def fake_create(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        out_dir.mkdir(parents=True, exist_ok=True)
        expected_out.write_bytes(b"\xff\xfb\x00mp3")
        return fake

    monkeypatch.setattr("core.audio_extraction.asyncio.create_subprocess_exec", fake_create)

    await extract_audio(video, out_dir, locator=mock_locator)

    args = captured["args"]
    # First positional is ffmpeg path, then -y, then -i <video>, then the
    # codec flags, then output.
    assert args[0] == "/fake/ffmpeg"
    assert args[1] == "-y"
    assert args[2] == "-i"
    assert args[3] == str(video)
    # Required encoder flags (R1.2)
    for flag in ("-vn", "-ac", "1", "-ar", "16000", "-b:a", "32k", "-f", "mp3"):
        assert flag in args, f"missing required flag: {flag}"
    # Last positional is the output path
    assert args[-1] == str(expected_out)
    # No shell=True (Property 7 / R1.9)
    assert "shell" not in captured["kwargs"]


# ---------------------------------------------------------------------------
# extract_audio: failure paths
# ---------------------------------------------------------------------------


async def test_extract_audio_raises_nonzero_exit_with_stderr_tail(
    monkeypatch: pytest.MonkeyPatch,
    writable_tmp: Tuple[Path, Path],
    mock_locator: FfmpegLocator,
) -> None:
    video, out_dir = writable_tmp
    fake = _FakeProcess(returncode=1, stderr_payload=b"ffmpeg: invalid input\n")

    async def fake_create(*args, **kwargs):
        return fake

    monkeypatch.setattr("core.audio_extraction.asyncio.create_subprocess_exec", fake_create)

    with pytest.raises(FfmpegNonZeroExit) as excinfo:
        await extract_audio(video, out_dir, locator=mock_locator)

    msg = str(excinfo.value)
    assert msg.startswith("audio_extract_failed: nonzero_exit_code")
    assert "exit=1" in msg
    assert "ffmpeg: invalid input" in msg


async def test_extract_audio_raises_empty_when_output_zero_bytes(
    monkeypatch: pytest.MonkeyPatch,
    writable_tmp: Tuple[Path, Path],
    mock_locator: FfmpegLocator,
) -> None:
    video, out_dir = writable_tmp
    fake = _FakeProcess(returncode=0)

    async def fake_create(*args, **kwargs):
        out_dir.mkdir(parents=True, exist_ok=True)
        # Write a 0-byte output file, simulating libmp3lame edge case.
        (out_dir / f"{video.stem}.mp3").write_bytes(b"")
        return fake

    monkeypatch.setattr("core.audio_extraction.asyncio.create_subprocess_exec", fake_create)

    with pytest.raises(AudioExtractEmpty) as excinfo:
        await extract_audio(video, out_dir, locator=mock_locator)
    assert str(excinfo.value).startswith("audio_extract_failed: audio_extract_empty")


async def test_extract_audio_raises_timeout_and_kills_process(
    monkeypatch: pytest.MonkeyPatch,
    writable_tmp: Tuple[Path, Path],
    mock_locator: FfmpegLocator,
) -> None:
    video, out_dir = writable_tmp
    expected_out = out_dir / f"{video.stem}.mp3"

    fake = _FakeProcess(returncode=0, wait_delay=10.0)  # never resolves

    async def fake_create(*args, **kwargs):
        out_dir.mkdir(parents=True, exist_ok=True)
        # Pretend ffmpeg started writing output before timeout.
        expected_out.write_bytes(b"partial")
        return fake

    monkeypatch.setattr("core.audio_extraction.asyncio.create_subprocess_exec", fake_create)

    # Patch the timeout constant so the test resolves quickly.
    monkeypatch.setattr("core.audio_extraction._FFMPEG_TIMEOUT_SECONDS", 0.05)

    with pytest.raises(FfmpegTimeout) as excinfo:
        await extract_audio(video, out_dir, locator=mock_locator)

    assert str(excinfo.value).startswith("audio_extract_failed: audio_extract_timeout")
    assert fake.killed is True
    # Half-written output should have been removed.
    assert not expected_out.exists()


async def test_extract_audio_propagates_ffmpeg_not_available(
    writable_tmp: Tuple[Path, Path],
) -> None:
    """If ``locator.locate()`` raises, ``extract_audio`` must propagate."""
    video, out_dir = writable_tmp
    locator = FfmpegLocator()
    # locator hasn't probed; force a failure cache state directly.
    locator._available = False
    locator._cached_at = 9_999_999_999.0
    locator._last_error = "test: simulated"

    with pytest.raises(FfmpegNotAvailable) as excinfo:
        await extract_audio(video, out_dir, locator=locator)
    assert "test: simulated" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Property 6: stderr ring buffer is bounded
# ---------------------------------------------------------------------------


async def test_extract_audio_stderr_ring_buffer_keeps_only_tail(
    monkeypatch: pytest.MonkeyPatch,
    writable_tmp: Tuple[Path, Path],
    mock_locator: FfmpegLocator,
) -> None:
    """When ffmpeg dumps multi-MiB of stderr, the deque's ``maxlen`` keeps
    memory bounded and the error message contains only the tail bytes."""
    video, out_dir = writable_tmp

    # 2 MiB of unique stderr content; the last 4096 bytes should be a
    # well-known sentinel we can grep for.
    sentinel = b"SENTINEL_TAIL_PATTERN_XYZ_" * 100  # 2600 bytes, < 4096
    bulk = b"X" * (2 * 1024 * 1024 - len(sentinel))
    payload = bulk + sentinel
    fake = _FakeProcess(returncode=2, stderr_payload=payload)

    async def fake_create(*args, **kwargs):
        return fake

    monkeypatch.setattr("core.audio_extraction.asyncio.create_subprocess_exec", fake_create)

    with pytest.raises(FfmpegNonZeroExit) as excinfo:
        await extract_audio(video, out_dir, locator=mock_locator)

    msg = str(excinfo.value)
    # Tail sentinel must be present (proves the deque kept the end).
    assert b"SENTINEL_TAIL_PATTERN_XYZ_".decode() in msg
    # The original ``X`` bulk should NOT round-trip in full — message size
    # is bounded by 4096 bytes of tail (plus a small wrapping prefix).
    assert msg.count("X") < 4096


# ---------------------------------------------------------------------------
# Property 7: subprocess never invoked with shell=True (hypothesis)
# ---------------------------------------------------------------------------


@settings(deadline=None)
@given(
    raw_stem=st.text(
        alphabet=st.characters(
            min_codepoint=32,
            max_codepoint=0x1FFFF,
            blacklist_categories=("Cs",),  # exclude surrogates
        ),
        min_size=1,
        max_size=64,
    ).filter(lambda s: "/" not in s and "\\" not in s and "\x00" not in s),
)
def test_extract_audio_never_uses_shell_true_for_arbitrary_filenames(
    raw_stem: str,
) -> None:
    """Hypothesis property: any filename that survives FS encoding must
    still flow through ``create_subprocess_exec`` with list args, not
    ``shell=True``. Catches injection regressions if anyone ever
    refactors to ``f"ffmpeg -i {video}"``.

    Wrapped as a sync test driving its own asyncio loop because hypothesis
    + pytest-asyncio don't compose cleanly on async examples (each example
    needs an isolated loop). Uses ``tempfile`` directly — pytest tmp_path
    fixtures don't compose with ``@given`` either.
    """
    import tempfile

    with tempfile.TemporaryDirectory(prefix="hyp_") as tmp_str:
        tmp = Path(tmp_str)
        try:
            video = tmp / f"{raw_stem}.mp4"
            video.write_bytes(b"\x00")
        except (OSError, ValueError):
            return  # uninteresting; not a regression target.
        if not video.is_file():
            return  # Windows device names (for example CON.mp4) are not files.

        out_dir = tmp / "out"

        async def _run() -> None:
            locator = FfmpegLocator()
            locator._available = True
            locator._path = "/fake/ffmpeg"
            locator._version = "test"
            locator._cached_at = 9e9

            captured: dict = {}
            fake_proc = _FakeProcess(returncode=0)

            async def fake_create(*args, **kwargs):
                captured["args"] = args
                captured["kwargs"] = kwargs
                out_dir.mkdir(parents=True, exist_ok=True)
                Path(args[-1]).write_bytes(b"\xff\xfb\x00mp3")
                return fake_proc

            with patch(
                "core.audio_extraction.asyncio.create_subprocess_exec",
                side_effect=fake_create,
            ):
                await extract_audio(video, out_dir, locator=locator)

            assert "shell" not in captured.get("kwargs", {})
            assert all(isinstance(a, str) for a in captured["args"])

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# Property 8: FfmpegLocator availability cache (60s TTL)
# ---------------------------------------------------------------------------


async def test_locator_caches_probe_within_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """N calls to locate() within 60s should trigger only 1 probe."""
    locator = FfmpegLocator()

    probe_count = 0

    async def counting_probe():
        nonlocal probe_count
        probe_count += 1
        # Inline a successful state directly so we don't hit imageio.
        locator._available = True
        locator._path = "/fake/ffmpeg"
        locator._version = "ffmpeg version test"
        locator._last_error = None

    locator._probe = counting_probe  # type: ignore[assignment]

    for _ in range(5):
        path = await locator.locate()
        assert path == "/fake/ffmpeg"

    assert probe_count == 1


async def test_locator_re_probes_after_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After the 60s TTL, the next locate() must re-probe."""
    locator = FfmpegLocator()
    probe_count = 0
    fake_now = [1000.0]

    async def counting_probe():
        nonlocal probe_count
        probe_count += 1
        locator._available = True
        locator._path = "/fake/ffmpeg"
        locator._version = "v"
        locator._last_error = None

    locator._probe = counting_probe  # type: ignore[assignment]
    monkeypatch.setattr(
        "core.audio_extraction.time.monotonic",
        lambda: fake_now[0],
    )

    await locator.locate()  # probe 1
    fake_now[0] += 30  # within TTL
    await locator.locate()  # cache hit
    fake_now[0] += 31  # crosses TTL boundary (61s)
    await locator.locate()  # probe 2

    assert probe_count == 2


async def test_locator_diagnostic_returns_unavailable_on_imageio_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Platform-unsupported simulation: ``get_ffmpeg_exe()`` raises."""
    locator = FfmpegLocator()

    fake_imageio = MagicMock()
    fake_imageio.get_ffmpeg_exe = MagicMock(side_effect=RuntimeError("no binary on platform"))
    # We can't easily monkeypatch a stdlib import line, so we monkeypatch
    # the function via sys.modules.
    import sys

    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", fake_imageio)

    diag = await locator.diagnostic()
    assert diag == {
        "ffmpeg_available": False,
        "ffmpeg_path": "",
        "ffmpeg_version": None,
    }

    with pytest.raises(FfmpegNotAvailable) as excinfo:
        await locator.locate()
    assert "no binary on platform" in str(excinfo.value)


async def test_locator_diagnostic_returns_unavailable_on_version_nonzero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``ffmpeg -version`` exits non-zero ⇒ unavailable."""
    locator = FfmpegLocator()

    fake_path = tmp_path / "ffmpeg"
    fake_path.write_bytes(b"#!/bin/sh\nexit 1\n")
    fake_path.chmod(0o755)

    fake_imageio = MagicMock()
    fake_imageio.get_ffmpeg_exe = MagicMock(return_value=str(fake_path))
    import sys

    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", fake_imageio)

    fake = _FakeProcess(returncode=1, stderr_payload=b"")

    async def fake_create(*args, **kwargs):
        return fake

    # Stub stdout returned by communicate() — we rebuild a Process-ish
    # object since `_probe` calls communicate(), not .wait().
    class _CommunicableFake:
        returncode = 1

        async def communicate(self):
            return (b"", b"")

        def kill(self):
            pass

    async def fake_create_for_probe(*args, **kwargs):
        return _CommunicableFake()

    monkeypatch.setattr(
        "core.audio_extraction.asyncio.create_subprocess_exec",
        fake_create_for_probe,
    )

    diag = await locator.diagnostic()
    assert diag["ffmpeg_available"] is False
    assert diag["ffmpeg_version"] is None


# ---------------------------------------------------------------------------
# Sanity: AudioExtractError prefix contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "exc_cls,expected_cause",
    [
        (FfmpegNotAvailable, "ffmpeg_not_available"),
        (FfmpegTimeout, "audio_extract_timeout"),
        (FfmpegNonZeroExit, "nonzero_exit_code"),
        (AudioExtractEmpty, "audio_extract_empty"),
    ],
)
def test_audio_extract_error_message_prefix(exc_cls, expected_cause) -> None:
    """All AudioExtractError subclasses must produce
    ``audio_extract_failed: <cause>: <detail>`` per R6.2."""
    exc = exc_cls("some-detail")
    msg = str(exc)
    assert msg.startswith(f"audio_extract_failed: {expected_cause}")
    assert "some-detail" in msg
