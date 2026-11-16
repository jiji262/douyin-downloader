"""agent 入口的信号处理：第一次信号取消 verb，之后的信号忽略，envelope 一定写出来。"""

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agent_mode import entry
from agent_mode.envelope import AgentError

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX signals only")

REPO_ROOT = Path(__file__).resolve().parent.parent
FAKE_WHISPER = """
import os, time
def available_models():
    return ["base"]
def load_model(name):
    open(os.environ["FAKE_WHISPER_MARKER"], "w").close()
    time.sleep(30)
"""
STARTUP_TIMEOUT_S = 30
SECOND_SIGNAL_DELAY_S = 0.3
EXIT_DEADLINE_S = 5
CLEANUP_S = 0.3
INTERRUPTED_EXIT_CODE = 130


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch, tmp_path):
    monkeypatch.setenv("DOUYIN_DL_HOME", str(tmp_path / "home"))


def _spawn_transcribe(tmp_path):
    package = tmp_path / "fake" / "whisper"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(FAKE_WHISPER, encoding="utf-8")
    ffmpeg = tmp_path / "bin" / "ffmpeg"
    ffmpeg.parent.mkdir()
    ffmpeg.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    ffmpeg.chmod(0o755)
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"v")
    marker = tmp_path / "model-loading"
    env = dict(
        os.environ,
        PYTHONPATH=os.pathsep.join([str(package.parent), str(REPO_ROOT)]),
        PATH=os.pathsep.join([str(ffmpeg.parent), os.environ.get("PATH", "")]),
        DOUYIN_DL_HOME=str(tmp_path / "home"),
        FAKE_WHISPER_MARKER=str(marker),
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "agent_mode", "transcribe", str(video)],
        cwd=str(tmp_path),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return process, marker


def _wait_for(marker, process):
    deadline = time.monotonic() + STARTUP_TIMEOUT_S
    while not marker.exists():
        assert process.poll() is None, process.stderr.read().decode()
        assert time.monotonic() < deadline, "the fake model never started loading"
        time.sleep(0.05)


def test_two_sigterms_still_yield_one_interrupted_envelope(tmp_path):
    process, marker = _spawn_transcribe(tmp_path)
    try:
        _wait_for(marker, process)
        started = time.monotonic()
        process.send_signal(signal.SIGTERM)
        time.sleep(SECOND_SIGNAL_DELAY_S)
        process.send_signal(signal.SIGTERM)
        out, err = process.communicate(timeout=EXIT_DEADLINE_S)
    finally:
        process.kill()
        process.wait()
    assert time.monotonic() - started < EXIT_DEADLINE_S
    assert process.returncode == INTERRUPTED_EXIT_CODE, err.decode()
    lines = out.decode().splitlines()
    assert len(lines) == 1, out
    result = json.loads(lines[0])
    assert result["error"]["code"] == "INTERRUPTED"
    assert result["data"]["items"] == []
    assert result["data"]["interrupted_input"] is None


def _run_main(capsys, argv):
    code = entry.main(argv)
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    return code, json.loads(lines[0])


@pytest.mark.parametrize("signame", ["SIGINT", "SIGTERM"])
def test_only_the_first_signal_cancels(monkeypatch, capsys, signame):
    signum = getattr(signal, signame)
    cancels = []

    async def cleanup():
        # 收尾期间又来两个信号：不能再被 cancel
        os.kill(os.getpid(), signum)
        os.kill(os.getpid(), signal.SIGTERM)
        try:
            await asyncio.sleep(CLEANUP_S)
        except asyncio.CancelledError:
            cancels.append("cleanup")
            raise

    async def handler(args):
        os.kill(os.getpid(), signum)
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancels.append("verb")
            await cleanup()
            raise AgentError("INTERRUPTED", "stopped", data={"finalized": True}) from None
        return {}

    monkeypatch.setattr("agent_mode.auth_cmds.run_doctor", handler)
    code, result = _run_main(capsys, ["doctor"])
    assert cancels == ["verb"]
    assert code == INTERRUPTED_EXIT_CODE
    assert result["data"] == {"finalized": True}


def test_signal_after_the_verb_finished_does_not_kill_the_process(monkeypatch, capsys):
    async def handler(args):
        return {"fine": True}

    def dispatch_then_signal(args, _real=entry._dispatch):
        data = _real(args)
        os.kill(os.getpid(), signal.SIGTERM)
        return data

    monkeypatch.setattr("agent_mode.auth_cmds.run_doctor", handler)
    monkeypatch.setattr(entry, "_dispatch", dispatch_then_signal)
    code, result = _run_main(capsys, ["doctor"])
    assert (code, result["data"]) == (0, {"fine": True})


def test_main_restores_previous_signal_handlers(monkeypatch, capsys):
    def sentinel(signum, frame):
        raise AssertionError("must not run")

    during = {}

    async def handler(args):
        during.update({s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)})
        return {}

    monkeypatch.setattr("agent_mode.auth_cmds.run_doctor", handler)
    previous = {s: signal.signal(s, sentinel) for s in (signal.SIGINT, signal.SIGTERM)}
    try:
        code, _ = _run_main(capsys, ["doctor"])
        after = {s: signal.getsignal(s) for s in previous}
    finally:
        for signum, old in previous.items():
            signal.signal(signum, old)
    assert code == 0
    assert after == {signal.SIGINT: sentinel, signal.SIGTERM: sentinel}
    assert sentinel not in during.values()
    assert signal.SIG_DFL not in during.values()
