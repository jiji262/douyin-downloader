"""agent `live`：下载器和 API 全部用 fake，不录制、不联网。事件输出的单测在 test_agent_local_verbs.py。"""

import asyncio
import json
import os
from pathlib import Path

import pytest

from agent_mode import entry, media_live_recovery, state

try:
    import fcntl
except ImportError:  # Windows：没有 flock，录制锁整体不启用（锁的单测在 test_agent_live_lock.py）
    fcntl = None

LIVE_URL = "https://live.douyin.com/123456"
VIDEO_URL = "https://www.douyin.com/video/7300000000000000000"
HTTP_SHORT_URL = "http://v.douyin.com/AbCdEfG/"
RECORDING_ID = "rec0001"
ROOM_INFO = {
    "room": {"id_str": "777", "status": "2", "title": "晚间\n直播" + "x" * 100},
    "user": {"nickname": "主播A", "sec_uid": "MS4w"},
}


def _held_elsewhere(path):
    """另开一个 fd 试锁；flock 按打开的文件描述算，同一进程里也能测出别人持有。"""
    fd = os.open(str(path), os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    finally:
        os.close(fd)
    return False


class _FakeClient:
    resolved = []

    def __init__(self, cookies, proxy=None):
        self.cookies, self.proxy = cookies, proxy

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def get_live_room_info(self, room_id, **kwargs):
        return ROOM_INFO

    async def resolve_short_url(self, url):
        # 带登录态的 client 一旦被拿去解析短链，就会把 cookie 发给短链域名
        assert not self.cookies, "short links must be resolved without cookies"
        type(self).resolved.append(url)
        return LIVE_URL


class _Result:
    def __init__(self, **counts):
        self.total = counts.get("total", 1)
        self.success = counts.get("success", 0)
        self.failed = counts.get("failed", 0)
        self.skipped = counts.get("skipped", 0)


class _FakeDownloader:
    """按 mode 模拟 LiveDownloader 对 reporter、日志和文件的可见行为。"""

    mode = "ok"
    created = []

    def __init__(self, kwargs):
        self.kwargs = kwargs
        self.reporter = kwargs["progress_reporter"]
        type(self).created.append(self)

    def _write_journal(self, segments, reason):
        from storage.live_recordings import journal_root

        out = Path(self.kwargs["config"].get("path")) / "主播A" / "session"
        out.mkdir(parents=True)
        entries = {}
        for name, segment_state in segments:
            source, target = out / (name + ".ts"), out / (name + ".mp4")
            (target if segment_state == "indexed" else source).write_bytes(b"data")
            entries[name + ".ts"] = {
                "source": str(source),
                "target": str(target),
                "state": segment_state,
            }
        root = journal_root(self.kwargs["database"], self.kwargs["config"].get("path"))
        root.mkdir(parents=True, exist_ok=True)
        payload = {"recording_id": RECORDING_ID, "segments": entries, "reason": reason}
        (root / (RECORDING_ID + ".json")).write_text(json.dumps(payload), encoding="utf-8")
        return out

    def _progress(self, phase, segments):
        self.reporter.on_live_progress(
            recording_id=RECORDING_ID,
            phase=phase,
            recorded_seconds=12.34,
            bytes=4096,
            segments=segments,
            reconnects=0,
        )
        lock = state.state_dir() / ".live-recordings" / (RECORDING_ID + ".lock")
        self.lock_held = [_held_elsewhere(lock)] if lock.is_file() else []

    async def download(self, parsed):
        from core import item_reasons

        self.parsed = parsed
        if self.mode == "offline":
            reason = item_reasons.SKIP_LIVE_NOT_STREAMING
            self.reporter.advance_item("skipped", parsed["room_id"], reason=reason)
            return _Result(skipped=1)
        await self.kwargs["api_client"].get_live_room_info(parsed["room_id"])
        if self.mode == "cancel":
            self.out = self._write_journal([("part-000000", "pending_finalize")], "interrupted")
            self._progress("recording", 0)
            raise asyncio.CancelledError()
        self.out = self._write_journal(
            [("part-000000", "indexed"), ("part-000001", "indexed")], "stopped"
        )
        self._progress("recording", 1)
        self._progress("stopped", 2)
        self.reporter.advance_item("success", "live_seg_0")
        return _Result(total=2, success=2)


@pytest.fixture
def live(monkeypatch, tmp_path):
    monkeypatch.setenv("DOUYIN_DL_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("DOUYIN_PROXY", raising=False)
    monkeypatch.chdir(tmp_path)
    fake = type("FakeDownloader", (_FakeDownloader,), {"created": []})
    monkeypatch.setattr(_FakeClient, "resolved", [])
    recovered = []

    def create(url_type, config, api_client, **kwargs):
        assert url_type == "live"
        return fake(dict(kwargs, config=config, api_client=api_client))

    async def recover(root, **kwargs):
        recovered.append((Path(root), kwargs))

    monkeypatch.setattr("core.ffmpeg.resolve_ffmpeg_path", lambda: "/fake/ffmpeg")
    monkeypatch.setattr("core.api_client.DouyinAPIClient", _FakeClient)
    monkeypatch.setattr("core.downloader_factory.DownloaderFactory.create", staticmethod(create))
    monkeypatch.setattr("core.live_finalize.recover_recordings", recover)
    fake.recovered = recovered
    return fake


def _run(capsys, argv):
    code = entry.main(argv)
    captured = capsys.readouterr()
    assert captured.out.count("\n") == 1
    return code, json.loads(captured.out), captured.err


def _events(err):
    return [json.loads(line) for line in err.splitlines() if line.startswith('{"event"')]


def test_live_records_through_factory_and_reports_files(live, tmp_path, capsys):
    code, result, err = _run(capsys, ["live", LIVE_URL, "--max-duration", "30", "--out", "out"])
    assert code == 0
    downloader = live.created[0]
    data = result["data"]
    assert data["room"] == {"title": "晚间 直播" + "x" * 59 + "…", "nickname": "主播A"}
    assert data["files"] == [
        str(downloader.out / "part-000000.mp4"),
        str(downloader.out / "part-000001.mp4"),
    ]
    assert all(Path(path).is_absolute() and Path(path).is_file() for path in data["files"])
    assert (data["duration_s"], data["segments"]) == (12.3, 2)
    assert data["stopped_reason"] == "max_duration"
    kwargs = downloader.kwargs
    assert kwargs["config"].get("live")["max_duration_seconds"] == 30
    assert kwargs["config"].get("live")["segment_seconds"] == 3600
    assert kwargs["config"].get("path") == str(tmp_path / "out")
    assert kwargs["cookie_manager"].cookie_file == state.cookies_path()
    assert Path(kwargs["database"].db_path) == state.history_db_path()
    assert downloader.parsed["room_id"] == "123456"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["home", "out"]
    # 录制期间日志旁的锁文件被本进程 flock 着，录完即删
    assert downloader.lock_held == ([True] if fcntl else [])
    assert list(state.state_dir().glob(".live-recordings/*.lock")) == []
    assert _events(err) == []
    assert "stream" not in json.dumps(result).replace("stream_lost", "")


def test_live_default_out_and_recovery_of_earlier_recordings(live, tmp_path, capsys):
    journals = tmp_path / "home" / ".live-recordings"
    journals.mkdir(parents=True)
    (journals / "old.json").write_text("{}", encoding="utf-8")
    code, result, _ = _run(capsys, ["live", LIVE_URL, "--max-duration", "5"])
    assert code == 0
    assert result["data"]["files"][0].startswith(str(tmp_path / "douyin-downloads"))
    assert len(live.recovered) == 1
    root, kwargs = live.recovered[0]
    assert root == journals.resolve()
    assert kwargs["retry_deferred"] is False
    assert [p.name for p in kwargs["paths"]] == ["old.json"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["douyin-downloads", "home"]


def test_live_short_link_is_resolved_over_https_without_cookies(live, capsys):
    state.write_cookies({"sessionid": "fake-session-value-for-live-test"})
    argv = ["live", HTTP_SHORT_URL, "--max-duration", "5", "--out", "out"]
    code, result, _ = _run(capsys, argv)
    assert (code, result["error"]) == (0, None)
    assert _FakeClient.resolved == [HTTP_SHORT_URL.replace("http://", "https://")]
    assert live.created[0].kwargs["api_client"].cookies


@pytest.mark.parametrize("out", [[], ["--out", "new/nested"]])
def test_live_not_streaming_is_upstream_empty_not_retryable(live, capsys, out):
    live.mode = "offline"
    code, result, _ = _run(capsys, ["live", LIVE_URL, "--max-duration", "30"] + out)
    assert code == 5
    # 没录到东西时，本次新建的输出目录（含上级）不留在 cwd
    assert sorted(p.name for p in Path.cwd().iterdir()) == ["home"]
    assert result["error"]["code"] == "UPSTREAM_EMPTY"
    assert result["error"]["retryable"] is False
    assert result["data"]["files"] == []
    assert result["data"]["stopped_reason"] is None
    assert live.recovered == []


def test_live_interrupted_reports_recorded_files(live, monkeypatch, capsys):
    live.mode = "cancel"
    finalized = []

    async def finalize(path, database):
        finalized.append(path.name)

    monkeypatch.setattr(media_live_recovery, "_finalize_pending", finalize)
    code, result, _ = _run(capsys, ["live", LIVE_URL, "--max-duration", "30", "--out", "out"])
    assert code == 130
    assert result["error"]["code"] == "INTERRUPTED"
    assert result["data"]["files"] == [str(live.created[0].out / "part-000000.ts")]
    assert result["data"]["stopped_reason"] == "interrupted"
    assert result["data"]["room"]["nickname"] == "主播A"
    assert finalized == [RECORDING_ID + ".json"]
    assert any(".ts" in warning for warning in result["warnings"])


def test_live_events_go_to_stderr_as_ndjson(live, capsys):
    argv = ["live", LIVE_URL, "--max-duration", "30", "--out", "out", "--events"]
    code, _, err = _run(capsys, argv)
    assert code == 0
    seen = _events(err)
    assert [e["event"] for e in seen] == ["segment", "progress", "segment", "progress", "item"]
    assert seen[0] == {"event": "segment", "index": 1, "segments": 1, "recorded_s": 12.34}
    assert seen[3]["phase"] == "stopped"
    assert seen[4] == {"event": "item", "status": "success", "id": "live_seg_0"}


@pytest.mark.parametrize(
    "case",
    [
        ([], "USAGE"),
        ([LIVE_URL], "USAGE"),
        ([LIVE_URL, "--max-duration", "0"], "USAGE"),
        ([LIVE_URL, "--max-duration", "21601"], "USAGE"),
        ([VIDEO_URL, "--max-duration", "5"], "USAGE"),
        (["https://example.com/x", "--max-duration", "5"], "UNSUPPORTED"),
    ],
)
def test_live_rejects_bad_arguments(live, capsys, case):
    argv, error = case
    code, result, _ = _run(capsys, ["live"] + argv)
    assert (code, result["error"]["code"]) == (2, error)
    assert live.created == []
    assert not (Path.cwd() / "douyin-downloads").exists()


def test_live_without_ffmpeg_is_dependency_missing(live, monkeypatch, capsys):
    monkeypatch.setattr("core.ffmpeg.resolve_ffmpeg_path", lambda: None)
    code, result, _ = _run(capsys, ["live", LIVE_URL, "--max-duration", "5"])
    assert code == 7
    assert result["error"]["action"]["kind"] == "install"
    assert list(Path.cwd().iterdir()) == []
