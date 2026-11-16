"""agent `live` 的录制锁：flock 判断持有者、清理过期锁、不写穿预先放好的符号链接。"""

import errno
import os
import stat
import threading

import pytest

from agent_mode import envelope, media_live_recovery

try:
    import fcntl
except ImportError:  # Windows 没有 flock，录制锁不启用
    fcntl = None

pytestmark = pytest.mark.skipif(fcntl is None, reason="flock is POSIX-only")
HANG_TIMEOUT_S = 5


def _finishes(function):
    """在 daemon 线程里跑，卡住（比如阻塞在 FIFO 的 open 上）就判失败。"""
    worker = threading.Thread(target=function, daemon=True)
    worker.start()
    worker.join(HANG_TIMEOUT_S)
    return not worker.is_alive()


def test_lock_file_is_never_visible_before_it_is_locked(tmp_path, monkeypatch):
    # 先在随机名上加锁再改名：恢复方不可能看到「已存在但还没锁上」的锁文件并把它删掉
    final = tmp_path / "rec.lock"
    seen = []
    real_flock = fcntl.flock
    monkeypatch.setattr(
        fcntl, "flock", lambda fd, op: (seen.append(final.exists()), real_flock(fd, op))[1]
    )
    lock = media_live_recovery.RecordingLock()
    lock.claim(tmp_path / "rec.json")
    try:
        assert seen == [False]
        assert stat.S_ISREG(os.lstat(str(final)).st_mode)
        assert media_live_recovery._held_by_running_process(final)
    finally:
        lock.release()
    assert list(tmp_path.iterdir()) == []


def test_claim_replaces_a_planted_fifo_instead_of_opening_it(tmp_path):
    final = tmp_path / "rec.lock"
    os.mkfifo(str(final))
    lock = media_live_recovery.RecordingLock()
    assert _finishes(lambda: lock.claim(tmp_path / "rec.json"))
    try:
        assert stat.S_ISREG(os.lstat(str(final)).st_mode)
        assert media_live_recovery._held_by_running_process(final)
    finally:
        lock.release()


def test_recovery_skips_a_lock_it_cannot_check_without_hanging(tmp_path):
    os.mkfifo(str(tmp_path / "odd.lock"))
    (tmp_path / "odd.json").write_text("{}", encoding="utf-8")
    envelope.reset_warnings()
    found = []
    assert _finishes(lambda: found.append(media_live_recovery._recoverable_journals(tmp_path)))
    assert found == [[]]
    assert len(envelope.collect_warnings()) == 1
    assert (tmp_path / "odd.lock").exists()


def test_probe_needs_no_writable_fd_so_it_works_on_nfs(tmp_path, monkeypatch):
    # NFS 把 flock 模拟成字节范围锁：只读 fd 加不了排他锁（EBADF），共享锁可以
    real_flock = fcntl.flock

    def nfs_like(fd, op):
        read_only = (fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE) == os.O_RDONLY
        if op & fcntl.LOCK_EX and read_only:
            raise OSError(errno.EBADF, "exclusive lock needs a writable fd")
        return real_flock(fd, op)

    monkeypatch.setattr(fcntl, "flock", nfs_like)
    lock = media_live_recovery.RecordingLock()
    lock.claim(tmp_path / "rec.json")
    try:
        assert media_live_recovery._held_by_running_process(tmp_path / "rec.lock")
    finally:
        lock.release()


def test_recovery_skips_journals_locked_by_a_running_process(tmp_path):
    for name in ("busy", "dead", "orphan"):
        (tmp_path / (name + ".lock")).touch()
        if name != "orphan":
            (tmp_path / (name + ".json")).write_text("{}", encoding="utf-8")
    # 另开 fd 锁住 busy，模拟另一个正在录的 live；dead 的持有者已退出，pid 复用也不影响判断
    busy = os.open(str(tmp_path / "busy.lock"), os.O_RDONLY)
    fcntl.flock(busy, fcntl.LOCK_EX)
    try:
        paths = media_live_recovery._recoverable_journals(tmp_path)
    finally:
        os.close(busy)
    assert [p.name for p in paths] == ["dead.json"]
    assert sorted(p.name for p in tmp_path.glob("*.lock")) == ["busy.lock"]


def test_lock_is_held_while_recording_and_removed_on_release(tmp_path):
    journal = tmp_path / "rec.json"
    lock = media_live_recovery.RecordingLock()
    lock.claim(journal)
    assert media_live_recovery._held_by_running_process(journal.with_suffix(".lock"))
    lock.release()
    assert list(tmp_path.iterdir()) == []


def test_recording_lock_never_writes_through_a_planted_symlink(tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("important", encoding="utf-8")
    journal = tmp_path / "rec.json"
    journal.with_suffix(".lock").symlink_to(victim)
    lock = media_live_recovery.RecordingLock()
    lock.claim(journal)
    # 预先放的链接被已经锁好的普通文件整个替换掉，链接目标从不被打开
    assert stat.S_ISREG(os.lstat(str(journal.with_suffix(".lock"))).st_mode)
    lock.release()
    assert victim.read_text(encoding="utf-8") == "important"
