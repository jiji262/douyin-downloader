"""agent 状态目录：路径、权限、cookie 的原子写入与只报键名。"""

import json
import os
import shlex
import stat
import sys

import pytest

from agent_mode import envelope, state
from agent_mode.envelope import AgentError

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
FAKE_SESSION = "fake-session-value-for-state-test"


@pytest.fixture
def home(monkeypatch, tmp_path):
    path = tmp_path / "home"
    monkeypatch.setenv("DOUYIN_DL_HOME", str(path))
    monkeypatch.chdir(tmp_path)
    envelope.reset_warnings()
    return path


def _mode(path):
    return stat.S_IMODE(os.stat(str(path)).st_mode)


def test_state_dir_follows_env_and_defaults_to_home(home, monkeypatch, tmp_path):
    assert state.state_dir() == home
    assert state.cookies_path() == home / "cookies.json"
    assert state.history_db_path() == home / "history.db"
    monkeypatch.setenv("DOUYIN_DL_HOME", "relative-home")
    assert state.state_dir() == tmp_path / "relative-home"
    monkeypatch.delenv("DOUYIN_DL_HOME")
    monkeypatch.setenv("HOME", str(tmp_path / "fake-user"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "fake-user"))
    assert state.state_dir() == tmp_path / "fake-user" / ".douyin-dl"


def test_read_cookies_missing_file_is_empty_and_creates_nothing(home):
    assert state.read_cookies() == {}
    assert not home.exists()
    assert state.login_status({}) == "none"


@posix_only
def test_write_cookies_is_private_and_atomic(home):
    target = state.write_cookies({"sessionid": FAKE_SESSION, "ttwid": "t", "empty": ""})
    assert target == home / "cookies.json"
    assert _mode(home) == 0o700
    assert _mode(target) == 0o600
    assert [p.name for p in home.iterdir()] == ["cookies.json"]
    assert state.read_cookies() == {"sessionid": FAKE_SESSION, "ttwid": "t"}
    assert state.permission_warnings() == []


def test_existing_state_dir_keeps_its_mode_and_is_reported(home):
    # DOUYIN_DL_HOME 可能指向用户已有目录：只报告过宽权限，不擅自 chmod
    home.mkdir(mode=0o755)
    os.chmod(str(home), 0o755)
    target = state.write_cookies({"sessionid": FAKE_SESSION})
    assert _mode(home) == 0o755
    assert _mode(target) == 0o600
    warnings = state.permission_warnings()
    assert len(warnings) == 1 and str(home) in warnings[0]


def test_state_dir_pointing_at_a_file_is_usage_error(home):
    home.write_text("not a directory", encoding="utf-8")
    with pytest.raises(AgentError) as info:
        state.ensure_state_dir()
    assert info.value.code == "USAGE"


def test_permission_hint_quotes_the_path_for_the_shell(tmp_path, monkeypatch):
    # 路径里带引号和 shell 片段时，给人复制的 chmod 命令不能被注入
    home = tmp_path / "dy'; echo INJECTED; #"
    monkeypatch.setenv("DOUYIN_DL_HOME", str(home))
    home.mkdir()
    os.chmod(str(home), 0o755)
    (warning,) = state.permission_warnings()
    command = warning.split("(run: ", 1)[1].rstrip(")")
    assert shlex.split(command) == ["chmod", "700", str(home)]


def test_write_cookies_replaces_previous_content(home):
    state.write_cookies({"sessionid": "old", "stale": "x"})
    state.write_cookies({"sessionid_ss": "new"})
    assert state.read_cookies() == {"sessionid_ss": "new"}


def test_failed_write_keeps_old_file_and_leaves_no_temp(home, monkeypatch):
    state.write_cookies({"sessionid": "old"})

    def broken_replace(src, dst):
        raise OSError("disk full")

    with monkeypatch.context() as patch:
        patch.setattr(state.os, "replace", broken_replace)
        with pytest.raises(OSError):
            state.write_cookies({"sessionid": "new"})
    assert [p.name for p in home.iterdir()] == ["cookies.json"]
    assert json.loads((home / "cookies.json").read_text(encoding="utf-8")) == {"sessionid": "old"}


@pytest.mark.parametrize("content", ["{not json", "[1, 2]", '"text"'])
def test_corrupt_cookie_file_reads_as_empty_with_warning(home, content):
    home.mkdir()
    (home / "cookies.json").write_text(content, encoding="utf-8")
    assert state.read_cookies() == {}
    warnings = envelope.collect_warnings()
    assert len(warnings) == 1
    assert "cookies.json" in warnings[0]


def test_keys_present_only_lists_whitelisted_non_empty_names():
    cookies = {"ttwid": "a", "sessionid": "b", "msToken": "", "secret_other": "c"}
    assert state.keys_present(cookies) == ["sessionid", "ttwid"]


@pytest.mark.parametrize(
    "cookies, expected",
    [
        ({}, "none"),
        ({"ttwid": "a"}, "device_only"),
        ({"ttwid": "a", "sessionid": ""}, "device_only"),
        ({"sessionid": "x"}, "logged_in"),
        ({"sessionid_ss": "x"}, "logged_in"),
    ],
)
def test_login_status(cookies, expected):
    assert state.login_status(cookies) == expected


@posix_only
def test_permission_warnings_flag_group_or_world_access(home):
    state.write_cookies({"sessionid": "x"})
    os.chmod(str(home), 0o755)
    os.chmod(str(home / "cookies.json"), 0o644)
    warnings = state.permission_warnings()
    assert len(warnings) == 2
    assert "0755" in warnings[0] and "0700" in warnings[0]
    assert "0644" in warnings[1] and "0600" in warnings[1]


def test_build_config_never_points_into_cwd(home, monkeypatch, tmp_path):
    monkeypatch.setenv("DOUYIN_COOKIE", "sessionid=%s" % FAKE_SESSION)
    monkeypatch.setenv("DOUYIN_PATH", "./from-env")
    config = state.build_config()
    assert config.get("database_path") == str(home / "history.db")
    assert config.get("path") == str(tmp_path / "douyin-downloads")
    assert config.get("auto_cookie") is False
    assert config.get_cookies() == {}
    assert FAKE_SESSION not in json.dumps(config.config, default=str)
    custom = state.build_config(download_path="out/dir")
    assert custom.get("path") == str(tmp_path / "out" / "dir")
    assert sorted(p.name for p in tmp_path.iterdir()) == []
