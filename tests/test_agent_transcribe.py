"""agent `transcribe`：Whisper 和 transcribe_file 全部用 fake，不跑真模型。"""

import asyncio
import json
import os
import shlex
import sys
import threading
import types
from pathlib import Path

import pytest

from agent_mode import entry, media_transcribe

THREAD_WAIT_S = 10


def _run(capsys, argv):
    code = entry.main(argv)
    captured = capsys.readouterr()
    assert captured.out.count("\n") == 1
    return code, json.loads(captured.out), captured.err


def _events(err):
    return [json.loads(line) for line in err.splitlines() if line.startswith('{"event"')]


@pytest.fixture
def whisper(monkeypatch, tmp_path):
    monkeypatch.setenv("DOUYIN_DL_HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    calls = []
    module = types.ModuleType("whisper")
    module.available_models = lambda: ["tiny", "base"]
    module.load_model = lambda name: "model:" + name

    def transcribe_file(video, *rest):
        model, ffmpeg, formats, language, _converter, output_dir = rest
        calls.append((Path(video).name, model, ffmpeg, sorted(formats), language, output_dir))
        if "boom" in Path(video).name:
            raise RuntimeError("decoder\nexploded")
        if "silent" in Path(video).name:
            return False
        target = Path(output_dir) / (Path(video).stem + ".transcript.txt")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("你好", encoding="utf-8")
        return True

    monkeypatch.setitem(sys.modules, "whisper", module)
    monkeypatch.setattr("cli.whisper_transcribe.find_ffmpeg", lambda: "/fake/ffmpeg")
    monkeypatch.setattr("cli.whisper_transcribe.transcribe_file", transcribe_file)
    media = tmp_path / "media"
    (media / "sub").mkdir(parents=True)
    return calls, media


def test_transcribe_single_file_writes_next_to_input(whisper, tmp_path, capsys):
    calls, media = whisper
    (media / "clip.mp4").write_bytes(b"v")
    code, result, _ = _run(capsys, ["transcribe", "media/clip.mp4"])
    assert code == 0
    transcript = media / "clip.transcript.txt"
    assert result["data"] == {
        "items": [{"input": str(media / "clip.mp4"), "transcript_path": str(transcript)}],
        "count": 1,
        "failed": 0,
        "failed_items": [],
    }
    assert transcript.is_file()
    work_dir = Path(calls[0][-1])
    assert calls == [("clip.mp4", "model:base", "/fake/ffmpeg", ["txt"], "zh", str(work_dir))]
    # 输出目录可能被别人写（会把里面的目录换掉），transcribe_file 只写系统临时目录下的私有目录
    assert media not in work_dir.parents and tmp_path not in work_dir.parents
    assert not work_dir.exists()
    assert sorted(p.name for p in media.iterdir()) == ["clip.mp4", "clip.transcript.txt", "sub"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["media"]


def test_transcribe_directory_partial_failure(whisper, tmp_path, capsys):
    calls, media = whisper
    for name in ("a.mp4", "sub/boom.mp4", "sub/silent.mp4", "notes.txt"):
        (media / name).write_bytes(b"v")
    argv = ["transcribe", "media", "--out", "texts", "--model", "tiny", "--language", "en"]
    code, result, err = _run(capsys, argv + ["--events"])
    assert code == 6
    assert result["error"]["code"] == "PARTIAL"
    data = result["data"]
    assert data["items"] == [
        {
            "input": str(media / "a.mp4"),
            "transcript_path": str(tmp_path / "texts" / "a.transcript.txt"),
        }
    ]
    assert (data["count"], data["failed"]) == (1, 2)
    assert [item["input"] for item in data["failed_items"]] == [
        str(media / "sub" / "boom.mp4"),
        str(media / "sub" / "silent.mp4"),
    ]
    assert data["failed_items"][0]["reason"] == "RuntimeError: decoder exploded"
    assert {call[1] for call in calls} == {"model:tiny"}
    assert {call[4] for call in calls} == {"en"}
    kinds = [e["event"] for e in _events(err)]
    assert kinds == ["step", "progress", "step", "item", "step", "item", "step", "item"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["media", "texts"]


def test_transcribe_without_whisper_is_dependency_missing(whisper, monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "whisper", None)
    monkeypatch.setattr("agent_mode.envelope._pip_available", lambda: True)
    code, result, _ = _run(capsys, ["transcribe", "media"])
    assert code == 7
    action = result["error"]["action"]
    assert action == {
        "kind": "install",
        "command": "%s -m pip install openai-whisper" % shlex.quote(sys.executable),
    }


@pytest.mark.parametrize(
    "argv",
    [
        ["transcribe", "missing.mp4"],
        ["transcribe", "media"],
        ["transcribe", "media", "--model", "gigantic"],
    ],
)
def test_transcribe_usage_errors(whisper, capsys, argv):
    calls, media = whisper
    if "--model" in argv:
        (media / "clip.mp4").write_bytes(b"v")
    code, result, _ = _run(capsys, argv)
    assert (code, result["error"]["code"]) == (2, "USAGE")
    assert calls == []
    assert sorted(p.name for p in Path.cwd().iterdir()) == ["media"]


@pytest.mark.skipif(not hasattr(Path, "symlink_to"), reason="needs symlinks")
def test_transcript_never_follows_a_planted_symlink(whisper, tmp_path, capsys):
    _, media = whisper
    (media / "clip.mp4").write_bytes(b"v")
    victim = tmp_path / "victim.txt"
    victim.write_text("important", encoding="utf-8")
    transcript = media / "clip.transcript.txt"
    try:
        transcript.symlink_to(victim)
    except OSError:
        pytest.skip("symlinks are not available here")
    code, result, _ = _run(capsys, ["transcribe", "media/clip.mp4"])
    assert code == 0
    assert result["data"]["items"][0]["transcript_path"] == str(transcript)
    assert victim.read_text(encoding="utf-8") == "important"
    assert not transcript.is_symlink()
    assert transcript.read_text(encoding="utf-8") == "你好"
    assert sorted(p.name for p in media.iterdir()) == ["clip.mp4", "clip.transcript.txt", "sub"]


@pytest.mark.skipif(not hasattr(os, "link"), reason="needs hard links")
def test_transcript_never_writes_into_a_planted_hard_link(whisper, tmp_path, capsys):
    _, media = whisper
    (media / "clip.mp4").write_bytes(b"v")
    victim = tmp_path / "victim.txt"
    victim.write_text("important", encoding="utf-8")
    os.link(str(victim), str(media / "clip.transcript.txt"))
    assert _run(capsys, ["transcribe", "media/clip.mp4"])[0] == 0
    assert victim.read_text(encoding="utf-8") == "important"
    assert (media / "clip.transcript.txt").read_text(encoding="utf-8") == "你好"


@pytest.mark.skipif(not hasattr(Path, "symlink_to"), reason="needs symlinks")
def test_publishing_never_reuses_an_entry_planted_at_the_staging_name(
    whisper, monkeypatch, capsys
):
    # 临时文件名是随机的；就算被猜中并预先放了链接，也只会失败，不会写穿
    _, media = whisper
    (media / "clip.mp4").write_bytes(b"v")
    victim = media.parent / "victim.txt"
    victim.write_text("important", encoding="utf-8")
    monkeypatch.setattr(media_transcribe.secrets, "token_hex", lambda _n: "guessed")
    try:
        (media / ".douyin-dl-tmp-guessed.txt").symlink_to(victim)
    except OSError:
        pytest.skip("symlinks are not available here")
    code, result, _ = _run(capsys, ["transcribe", "media/clip.mp4"])
    assert (code, result["error"]["code"]) == (6, "PARTIAL")
    assert victim.read_text(encoding="utf-8") == "important"
    assert not (media / "clip.transcript.txt").exists()


def test_transcript_blocked_by_a_directory_is_a_failed_item(whisper, capsys):
    _, media = whisper
    (media / "clip.mp4").write_bytes(b"v")
    (media / "clip.transcript.txt").mkdir()
    code, result, _ = _run(capsys, ["transcribe", "media/clip.mp4"])
    assert code == 6
    assert result["data"]["failed_items"][0]["input"] == str(media / "clip.mp4")
    assert [p.name for p in media.iterdir() if p.name.startswith(".douyin-dl-tmp-")] == []


def _interrupt_while_transcribing(monkeypatch, blocked_name):
    """fake transcribe_file 处理到 blocked_name 时卡住，此时 cancel 掉 verb。"""
    real = sys.modules["cli.whisper_transcribe"].transcribe_file
    entered, release = threading.Event(), threading.Event()

    def transcribe_file(video, *rest):
        if Path(video).name != blocked_name:
            return real(video, *rest)
        entered.set()
        release.wait(THREAD_WAIT_S)
        return real(video, *rest)

    monkeypatch.setattr("cli.whisper_transcribe.transcribe_file", transcribe_file)
    real_handler = media_transcribe.transcribe_path

    async def handler(args):
        task = asyncio.ensure_future(real_handler(args))
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, entered.wait, THREAD_WAIT_S)
        task.cancel()
        return await task

    monkeypatch.setattr(media_transcribe, "transcribe_path", handler)
    return release


def test_interrupted_data_lists_done_items_and_the_current_input(whisper, monkeypatch, capsys):
    _, media = whisper
    for name in ("a.mp4", "b.mp4", "c.mp4"):
        (media / name).write_bytes(b"v")
    release = _interrupt_while_transcribing(monkeypatch, "b.mp4")
    try:
        code, result, _ = _run(capsys, ["transcribe", "media"])
        # 真实进程此时就退出了；要在放行被丢下的线程之前看目录
        left_behind = sorted(p.name for p in media.iterdir())
    finally:
        release.set()
    assert (code, result["error"]["code"]) == (130, "INTERRUPTED")
    assert result["data"] == {
        "items": [
            {
                "input": str(media / "a.mp4"),
                "transcript_path": str(media / "a.transcript.txt"),
            }
        ],
        "count": 1,
        "failed": 0,
        "failed_items": [],
        "interrupted_input": str(media / "b.mp4"),
    }
    # 被打断的文件没有发布，临时目录也清掉了
    assert left_behind == ["a.mp4", "a.transcript.txt", "b.mp4", "c.mp4", "sub"]


def test_interrupt_does_not_wait_for_the_worker_thread(whisper, monkeypatch, capsys):
    _, media = whisper
    (media / "a.mp4").write_bytes(b"v")
    release = _interrupt_while_transcribing(monkeypatch, "a.mp4")
    started = threading.Event()
    threading.Timer(THREAD_WAIT_S / 2, lambda: (started.set(), release.set())).start()
    try:
        code, result, _ = _run(capsys, ["transcribe", "media"])
        # 线程还卡着的时候 main() 就已经返回
        assert not started.is_set()
    finally:
        release.set()
    assert code == 130
    assert result["data"]["interrupted_input"] == str(media / "a.mp4")
