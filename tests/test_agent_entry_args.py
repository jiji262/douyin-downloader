"""agent 入口与 envelope 的边界：参数缩写、traceback 脱敏、shell 引号、stdout 关闭（不联网）。"""

import json
import shlex
import sys

import pytest

from agent_mode import entry, envelope, state

FAKE_SESSION = "S" * 32


@pytest.fixture
def home(monkeypatch, tmp_path):
    path = tmp_path / "home"
    monkeypatch.setenv("DOUYIN_DL_HOME", str(path))
    return path


def _run(capsys, argv):
    code = entry.main(argv)
    captured = capsys.readouterr()
    return code, json.loads(captured.out), captured.err


@pytest.mark.parametrize("argv", [["hot", "--ma", "5"], ["--verb", "doctor"], ["doctor", "--onl"]])
def test_abbreviated_options_are_rejected(home, capsys, argv):
    # 缩写会被 argparse 静默展开；契约要求原样拒绝，避免参数被误读
    code, result, _ = _run(capsys, argv)
    assert code == 2
    assert result["error"]["code"] == "USAGE"


def test_verbose_traceback_is_redacted(home, capsys, monkeypatch):
    state.write_cookies({"sessionid": FAKE_SESSION})

    def boom(_args):
        raise RuntimeError("upstream echoed %s" % FAKE_SESSION)

    monkeypatch.setattr(entry, "_dispatch", boom)
    code, result, err = _run(capsys, ["--verbose", "doctor"])
    assert code == 1
    assert result["error"]["code"] == "INTERNAL"
    assert "Traceback" in err
    assert FAKE_SESSION not in err
    assert FAKE_SESSION not in json.dumps(result)


def test_shell_quote_neutralises_shell_syntax(monkeypatch):
    # 解释器路径里的 `$(...)` 不能在给人复制的命令里被执行
    monkeypatch.setattr(sys, "executable", "/tmp/$(touch pwned)/python")
    command = envelope.python_command("-m", "playwright", "install", "chromium")
    assert shlex.split(command) == [
        "/tmp/$(touch pwned)/python", "-m", "playwright", "install", "chromium"
    ]


@pytest.mark.parametrize(
    "path, expected",
    [
        ("imageio-ffmpeg==0.6.0", "imageio-ffmpeg==0.6.0"),
        # 反斜杠不加引号会被 Git Bash 吃掉；放进双引号后 cmd、Git Bash 都原样保留
        (r"C:\Users\张三\Python312\python.exe", r'"C:\Users\张三\Python312\python.exe"'),
        (r"C:\Program Files\Python312\python.exe", r'"C:\Program Files\Python312\python.exe"'),
        # cmd 的 & 只有在双引号外才分隔命令
        (r"C:\dy&calc&rem\python.exe", r'"C:\dy&calc&rem\python.exe"'),
    ],
)
def test_windows_quoting_keeps_metacharacters_inside_double_quotes(monkeypatch, path, expected):
    monkeypatch.setattr(envelope, "_ON_WINDOWS", True)
    assert envelope.shell_quote(path) == expected


@pytest.mark.parametrize(
    "path",
    [
        r"C:\$(calc)\python.exe",  # PowerShell 在双引号里也会执行 $(...)
        "C:\\a`b\\python.exe",
        r"C:\a%PATH%b\python.exe",  # cmd 在双引号里也会展开 %VAR%
        r"C:\a!x!\python.exe",
        "C:\\a\u201c;calc;\u201c\\python.exe",  # PowerShell 把智能引号当成双引号
    ],
)
def test_windows_unquotable_interpreter_falls_back_to_plain_python(monkeypatch, path):
    monkeypatch.setattr(envelope, "_ON_WINDOWS", True)
    monkeypatch.setattr(sys, "executable", path)
    monkeypatch.setattr(envelope, "_pip_available", lambda: False)
    with pytest.raises(envelope.UnquotableArgument):
        envelope.shell_quote(path)
    assert envelope.python_command("-m", "playwright") == "python -m playwright"
    assert envelope.install_action("pkg")["command"] == "uv pip install --python python pkg"


def test_emit_tolerates_closed_or_missing_stdout():
    class Broken:
        def write(self, _text):
            raise BrokenPipeError()

        def flush(self):
            raise BrokenPipeError()

    envelope.emit(envelope.build_envelope("doctor", data={}), None)
    envelope.emit(envelope.build_envelope("doctor", data={}), Broken())
