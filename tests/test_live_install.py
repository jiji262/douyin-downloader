import errno

import pytest

from core.live_finalize import _install


def test_install_falls_back_on_filesystem_without_hardlinks(tmp_path, monkeypatch):
    source, target = tmp_path / "pending.mp4", tmp_path / "final.mp4"
    source.write_bytes(b"complete-media")

    def unsupported(*args):
        raise OSError(errno.ENOTSUP, "hardlinks unsupported")

    monkeypatch.setattr("core.live_finalize.os.link", unsupported)
    _install(source, target)
    assert target.read_bytes() == b"complete-media"
    assert not source.exists()


def test_install_does_not_overwrite_existing_output(tmp_path):
    source, target = tmp_path / "pending.mp4", tmp_path / "final.mp4"
    source.write_bytes(b"new")
    target.write_bytes(b"original")
    with pytest.raises(FileExistsError):
        _install(source, target)
    assert target.read_bytes() == b"original"


@pytest.mark.parametrize("winerror", [1, 50])
def test_install_falls_back_on_windows_volume_without_hardlinks(tmp_path, monkeypatch, winerror):
    source, target = tmp_path / "pending.mp4", tmp_path / "final.mp4"
    source.write_bytes(b"complete-media")

    def unsupported(*args):
        exc = OSError(errno.EINVAL, "hardlinks unsupported")
        exc.winerror = winerror
        raise exc

    monkeypatch.setattr("core.live_finalize.os.link", unsupported)
    _install(source, target)
    assert target.read_bytes() == b"complete-media"
    assert not source.exists()


def test_install_does_not_treat_every_invalid_argument_as_unsupported(tmp_path, monkeypatch):
    source, target = tmp_path / "pending.mp4", tmp_path / "final.mp4"
    source.write_bytes(b"complete-media")

    def invalid(*args):
        raise OSError(errno.EINVAL, "invalid")

    monkeypatch.setattr("core.live_finalize.os.link", invalid)
    with pytest.raises(OSError):
        _install(source, target)
    assert not target.exists() and source.exists()
