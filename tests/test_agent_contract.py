"""子进程契约测试：stdout 恰好一行 JSON、退出码正确、cwd 不留任何文件。"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TIMEOUT_S = 60
FAKE_SESSION = "fake-session-value-for-contract-test"
ENV_TO_DROP = ("DOUYIN_COOKIE", "DOUYIN_PATH", "DOUYIN_THREAD", "DOUYIN_PROXY")
VIDEO_URL = "https://www.douyin.com/video/7300000000000000000"


class _Sandbox:
    def __init__(self, tmp_path):
        self.cwd = tmp_path / "cwd"
        self.home = tmp_path / "home"
        self.cwd.mkdir()

    def _env(self):
        env = {k: v for k, v in os.environ.items() if k not in ENV_TO_DROP}
        env["PYTHONPATH"] = str(REPO_ROOT)
        env["DOUYIN_DL_HOME"] = str(self.home)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return env

    def run(self, *args, launcher=("-m", "agent_mode")):
        proc = subprocess.run(
            [sys.executable, *launcher, *args],
            cwd=str(self.cwd),
            env=self._env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=TIMEOUT_S,
        )
        out = proc.stdout.decode("utf-8")
        assert out.endswith("\n"), out
        assert out.count("\n") == 1, out
        assert list(self.cwd.iterdir()) == [], "agent verb left files in cwd"
        return proc.returncode, json.loads(out), proc.stderr.decode("utf-8", "replace")


@pytest.fixture
def sandbox(tmp_path):
    return _Sandbox(tmp_path)


@pytest.mark.parametrize(
    "argv, verb",
    [
        (["--bogus"], None),
        ([], None),
        (["frobnicate"], None),
        (["hot", "--max", "0"], "hot"),
        (["live", "https://live.douyin.com/123"], "live"),
        (["inspect"], "inspect"),
    ],
)
def test_usage_errors_are_enveloped(sandbox, argv, verb):
    code, result, _ = sandbox.run(*argv)
    assert code == 2
    assert result["ok"] is False
    assert result["verb"] == verb
    assert result["error"]["code"] == "USAGE"
    assert result["error"]["retryable"] is False
    assert result["error"]["action"]["kind"] == "fix_usage"


def test_help_exits_zero_with_one_line(sandbox):
    code, result, err = sandbox.run("--help")
    assert code == 0
    assert result["ok"] is True
    for verb in ("doctor", "inspect", "hot", "search", "user", "login", "live", "transcribe"):
        assert verb in result["data"]["help"]
    assert "usage:" in err


@pytest.mark.parametrize(
    "case",
    [
        (VIDEO_URL, "video", "aweme_id"),
        ("https://www.douyin.com/note/7300000000000000001", "gallery", "note_id"),
        ("https://www.douyin.com/collection/7300000000000000002", "collection", "mix_id"),
        ("https://www.douyin.com/music/7300000000000000003", "music", "music_id"),
    ],
)
def test_inspect_gated_url_exits_4_with_classification(sandbox, case):
    url, url_type, id_key = case
    code, result, _ = sandbox.run("inspect", url)
    assert code == 4
    assert result["ok"] is False
    assert result["error"]["code"] == "ARGUS_GATED"
    assert result["error"]["action"]["kind"] == "use_desktop"
    data = result["data"]
    assert data["input"] == url
    assert data["url_type"] == url_type
    assert data["capability"] == "argus_gated"
    assert data["suggested_verb"] is None
    assert data["ids"][id_key].isdigit()


@pytest.mark.parametrize(
    "url, url_type",
    [
        ("https://www.douyin.com/lvdetail/7300000000000000004", "lvdetail"),
        ("https://www.douyin.com/vsdetail/7300000000000000005", "live_replay"),
        ("https://example.com/video/1", None),
        ("not a url at all", None),
    ],
)
def test_inspect_unsupported_url_exits_2(sandbox, url, url_type):
    code, result, _ = sandbox.run("inspect", url)
    assert code == 2
    assert result["error"]["code"] == "UNSUPPORTED"
    assert result["data"]["url_type"] == url_type
    assert result["data"]["capability"] == "unsupported"


@pytest.mark.parametrize(
    "case",
    [
        ("https://www.douyin.com/user/MS4wLjABAAAAabc_-123", "user", "cookie", "user"),
        ("https://live.douyin.com/123456789", "live", "anonymous", "live"),
        ("www.douyin.com/user/MS4wLjABAAAAabc", "user", "cookie", "user"),
    ],
)
def test_inspect_supported_url_exits_0(sandbox, case):
    url, url_type, capability, verb = case
    code, result, _ = sandbox.run("inspect", url)
    assert code == 0
    assert result["ok"] is True
    assert result["error"] is None
    assert result["data"]["url_type"] == url_type
    assert result["data"]["capability"] == capability
    assert result["data"]["suggested_verb"] == verb
    assert result["data"]["note"]


def test_inspect_takes_the_url_out_of_share_text(sandbox):
    code, result, _ = sandbox.run("inspect", "看看这个 %s 复制此链接" % VIDEO_URL)
    assert code == 4
    assert result["data"]["resolved_url"] == VIDEO_URL


def test_doctor_without_state_is_offline_and_creates_nothing(sandbox):
    code, result, _ = sandbox.run("doctor")
    assert code == 0
    assert result["ok"] is True
    data = result["data"]
    assert data["version"] == result["version"]
    assert data["executable"] == sys.executable
    assert data["state_dir"] == {"path": str(sandbox.home), "exists": False, "mode": None}
    assert data["cookies"]["exists"] is False
    assert data["cookies"]["keys_present"] == []
    assert data["cookies"]["login_status"] == "none"
    assert set(data["deps"]) == {"ffmpeg", "playwright", "whisper"}
    assert data["online_check"] is None
    verbs = data["capabilities"]["verbs"]
    assert verbs["search"] == {"level": "login", "ready": False, "blocked_by": ["login"]}
    assert verbs["user"]["blocked_by"] == ["cookies"]
    assert verbs["hot"]["ready"] is True
    assert not sandbox.home.exists()


def test_doctor_reports_cookie_key_names_but_never_values(sandbox):
    sandbox.home.mkdir()
    cookie_file = sandbox.home / "cookies.json"
    cookie_file.write_text(
        json.dumps({"sessionid": FAKE_SESSION, "ttwid": "fake-ttwid", "x_private": "zzz"}),
        encoding="utf-8",
    )
    if sys.platform != "win32":
        cookie_file.chmod(0o644)
    code, result, err = sandbox.run("doctor")
    assert code == 0
    cookies = result["data"]["cookies"]
    assert cookies["exists"] is True
    assert cookies["keys_present"] == ["sessionid", "ttwid"]
    assert cookies["login_status"] == "logged_in"
    assert cookies["age_days"] == 0.0
    assert result["data"]["capabilities"]["verbs"]["search"]["ready"] is True
    assert FAKE_SESSION not in json.dumps(result)
    assert FAKE_SESSION not in err
    assert "x_private" not in json.dumps(result)
    if sys.platform != "win32":
        assert cookies["mode"] == "0644"
        assert any("cookies.json" in w and "0600" in w for w in result["warnings"])


def test_run_py_agent_keeps_caller_cwd(sandbox):
    code, result, _ = sandbox.run("doctor", launcher=(str(REPO_ROOT / "run.py"), "agent"))
    assert code == 0
    assert result["verb"] == "doctor"
    assert result["data"]["state_dir"]["path"] == str(sandbox.home)


def test_cli_entrypoint_dispatches_agent_before_argparse(sandbox):
    code, result, _ = sandbox.run(
        "inspect", VIDEO_URL, launcher=("-c", "from cli.main import main; main()", "agent")
    )
    assert code == 4
    assert result["verb"] == "inspect"
