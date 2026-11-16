"""agent 输出契约：envelope 结构、错误码表、入口的异常与取消处理。"""

import asyncio
import io
import json
import os
import shlex
import signal
import sys

import pytest

from agent_mode import entry, envelope, version
from agent_mode.envelope import AgentError

SPEC_EXIT_CODES = {
    "INTERNAL": 1,
    "USAGE": 2,
    "UNSUPPORTED": 2,
    "LOGIN_REQUIRED": 3,
    "ARGUS_GATED": 4,
    "UPSTREAM_EMPTY": 5,
    "PARTIAL": 6,
    "DEPENDENCY_MISSING": 7,
    "INTERRUPTED": 130,
}
ENVELOPE_KEYS = ["schema", "ok", "verb", "version", "data", "error", "warnings"]
FAKE_SECRET = "s3cr3t-session-value-0123456789"


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch, tmp_path):
    monkeypatch.setenv("DOUYIN_DL_HOME", str(tmp_path / "home"))


def _run_main(capsys, argv):
    code = entry.main(argv)
    captured = capsys.readouterr()
    lines = captured.out.splitlines()
    assert len(lines) == 1, captured.out
    assert captured.out.endswith("\n")
    return code, json.loads(lines[0]), captured.err


def _use_doctor_handler(monkeypatch, handler):
    monkeypatch.setattr("agent_mode.auth_cmds.run_doctor", handler)


def test_exit_code_table_matches_spec():
    assert {code: pair[0] for code, pair in envelope.ERROR_CODES.items()} == SPEC_EXIT_CODES
    retryable = [code for code, pair in envelope.ERROR_CODES.items() if pair[1]]
    assert retryable == ["UPSTREAM_EMPTY"]


def test_agent_error_defaults_and_overrides():
    login = AgentError("LOGIN_REQUIRED", "need login")
    assert login.exit_code == 3
    assert login.retryable is False
    assert login.action == {"kind": "run_command", "command": "douyin-dl agent login"}
    assert AgentError("ARGUS_GATED", "x").action["kind"] == "use_desktop"
    assert AgentError("UPSTREAM_EMPTY", "x").retryable is True
    assert AgentError("UPSTREAM_EMPTY", "x", retryable=False).retryable is False
    assert "action" not in AgentError("INTERNAL", "x").to_dict()


def test_agent_error_rejects_unknown_code_and_action_kind():
    with pytest.raises(ValueError):
        AgentError("NOPE", "x")
    with pytest.raises(ValueError):
        AgentError("USAGE", "x", action={"kind": "teleport"})


def test_build_envelope_success_shape():
    result = envelope.build_envelope("hot", data={"count": 0}, warnings=["w"])
    assert list(result) == ENVELOPE_KEYS
    assert result["schema"] == 1
    assert result["ok"] is True
    assert result["error"] is None
    assert result["data"] == {"count": 0}
    assert result["warnings"] == ["w"]
    assert result["version"] == version.get_version()
    assert envelope.exit_code_for(result) == 0


def test_build_envelope_error_carries_partial_data():
    error = AgentError("PARTIAL", "2 of 3", data={"count": 2})
    result = envelope.build_envelope("search", error=error)
    assert result["ok"] is False
    assert result["data"] == {"count": 2}
    assert result["error"] == {"code": "PARTIAL", "message": "2 of 3", "retryable": False}
    assert envelope.exit_code_for(result) == 6


def test_emit_writes_exactly_one_line_and_keeps_unicode():
    stream = io.StringIO()
    envelope.emit(envelope.build_envelope("hot", data={"word": "热\n榜 x"}), stream)
    text = stream.getvalue()
    assert text.endswith("\n")
    assert len(text.splitlines()) == 1
    assert "热" in text
    assert json.loads(text)["data"]["word"] == "热\n榜 x"


def test_clean_text_strips_control_chars_and_truncates():
    assert envelope.clean_text("a\x00b\x1b[31m\nc d") == "a b [31m c d"
    assert envelope.clean_text(None) == ""
    long_name = envelope.clean_text("名" * 100, envelope.NAME_MAX_CHARS)
    assert len(long_name) == envelope.NAME_MAX_CHARS + 1
    assert long_name.endswith("…")


def test_warnings_are_deduplicated_and_reset():
    envelope.reset_warnings()
    envelope.add_warning("one")
    envelope.add_warning("one")
    envelope.add_warning("")
    assert envelope.collect_warnings() == ["one"]
    envelope.reset_warnings()
    assert envelope.collect_warnings() == []


def test_install_action_uses_current_interpreter(monkeypatch):
    monkeypatch.setattr(envelope, "_pip_available", lambda: True)
    action = envelope.install_action("playwright", hint="then install chromium")
    assert action["kind"] == "install"
    assert action["command"] == "%s -m pip install playwright" % shlex.quote(sys.executable)
    assert action["hint"] == "then install chromium"


def test_install_action_uses_uv_when_env_has_no_pip(monkeypatch):
    # uv tool 环境没有 pip，`python -m pip` 会直接失败
    monkeypatch.setattr(envelope, "_pip_available", lambda: False)
    action = envelope.install_action("playwright", "openai-whisper")
    assert action["command"] == "uv pip install --python %s playwright openai-whisper" % (
        shlex.quote(sys.executable)
    )


def test_version_falls_back_from_pyproject_to_unknown(tmp_path, monkeypatch):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[build-system]\nversion = "0.0.1"\n[project]\nname = "x"\nversion = "9.8.7"\n',
        encoding="utf-8",
    )
    assert version._from_pyproject(pyproject) == "9.8.7"
    assert version._from_pyproject(tmp_path / "missing.toml") == ""
    monkeypatch.setattr(version, "_from_metadata", lambda: "")
    monkeypatch.setattr(version, "_from_pyproject", lambda: "")
    assert version.get_version() == version.UNKNOWN_VERSION


def test_handler_prints_go_to_stderr_and_warnings_reach_envelope(monkeypatch, capsys):
    async def handler(args):
        print("noise on stdout")
        envelope.add_warning("heads up")
        return {"fine": True}

    _use_doctor_handler(monkeypatch, handler)
    code, result, err = _run_main(capsys, ["doctor"])
    assert code == 0
    assert result["ok"] is True
    assert result["data"] == {"fine": True}
    assert result["warnings"] == ["heads up"]
    assert "noise on stdout" in err


def test_unknown_exception_becomes_internal_without_cookie_values(monkeypatch, capsys):
    from agent_mode import state

    state.write_cookies({"sessionid": FAKE_SECRET})

    async def handler(args):
        raise RuntimeError("request failed for sessionid=%s\x07" % FAKE_SECRET)

    _use_doctor_handler(monkeypatch, handler)
    code, result, err = _run_main(capsys, ["doctor"])
    assert code == 1
    assert result["error"]["code"] == "INTERNAL"
    assert result["error"]["message"].startswith("RuntimeError: ")
    assert FAKE_SECRET not in json.dumps(result)
    assert "Traceback" not in err


def test_verbose_writes_traceback_to_stderr_only(monkeypatch, capsys):
    async def handler(args):
        raise RuntimeError("boom")

    _use_doctor_handler(monkeypatch, handler)
    code, result, err = _run_main(capsys, ["doctor", "--verbose"])
    assert code == 1
    assert result["error"]["message"] == "RuntimeError: boom"
    assert "Traceback" in err


def test_non_dict_result_is_internal(monkeypatch, capsys):
    async def handler(args):
        return ["not", "a", "dict"]

    _use_doctor_handler(monkeypatch, handler)
    code, result, _ = _run_main(capsys, ["doctor"])
    assert code == 1
    assert result["error"]["code"] == "INTERNAL"


def test_cancelled_handler_becomes_interrupted(monkeypatch, capsys):
    async def handler(args):
        raise asyncio.CancelledError()

    _use_doctor_handler(monkeypatch, handler)
    code, result, _ = _run_main(capsys, ["doctor"])
    assert code == 130
    assert result["error"]["code"] == "INTERRUPTED"
    assert result["data"] == {}


def test_keyboard_interrupt_becomes_interrupted(monkeypatch, capsys):
    def fake_dispatch(args):
        raise KeyboardInterrupt()

    monkeypatch.setattr(entry, "_dispatch", fake_dispatch)
    code, result, _ = _run_main(capsys, ["doctor"])
    assert code == 130
    assert result["error"]["code"] == "INTERRUPTED"


@pytest.mark.skipif(sys.platform == "win32", reason="loop.add_signal_handler is POSIX-only")
@pytest.mark.parametrize("signame", ["SIGINT", "SIGTERM"])
def test_signal_cancels_handler_and_keeps_its_partial_data(monkeypatch, capsys, signame):
    async def handler(args):
        os.kill(os.getpid(), getattr(signal, signame))
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            raise AgentError("INTERRUPTED", "stopped early", data={"files": ["a.mp4"]})
        return {}

    _use_doctor_handler(monkeypatch, handler)
    code, result, _ = _run_main(capsys, ["doctor"])
    assert code == 130
    assert result["error"]["code"] == "INTERRUPTED"
    assert result["data"] == {"files": ["a.mp4"]}


@pytest.mark.skipif(sys.platform == "win32", reason="loop.add_signal_handler is POSIX-only")
def test_signal_without_handler_cleanup_is_plain_interrupted(monkeypatch, capsys):
    async def handler(args):
        os.kill(os.getpid(), signal.SIGTERM)
        await asyncio.sleep(30)
        return {}

    _use_doctor_handler(monkeypatch, handler)
    code, result, _ = _run_main(capsys, ["doctor"])
    assert code == 130
    assert result["error"]["code"] == "INTERRUPTED"


def test_help_is_an_ok_envelope(capsys):
    code, result, err = _run_main(capsys, ["live", "--help"])
    assert code == 0
    assert result["verb"] == "live"
    assert "--max-duration" in result["data"]["help"]
    assert "--max-duration" in err


def test_bounded_int_rejects_out_of_range_and_garbage(capsys):
    for bad in ("0", "101", "many"):
        code, result, _ = _run_main(capsys, ["hot", "--max", bad])
        assert code == 2
        assert result["verb"] == "hot"
        assert result["error"]["code"] == "USAGE"
        assert result["error"]["action"]["kind"] == "fix_usage"


def test_main_restores_logging_state(monkeypatch, capsys):
    import logging

    async def handler(args):
        assert logging.getLogger().level == logging.INFO
        return {}

    _use_doctor_handler(monkeypatch, handler)
    root = logging.getLogger()
    before = (root.level, list(root.handlers))
    code, _, _ = _run_main(capsys, ["doctor", "--verbose"])
    assert code == 0
    assert (root.level, list(root.handlers)) == before
