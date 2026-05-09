"""Tests for ``ConfigLoader.save()`` — settings persistence.

These cover the scenario fixed by the desktop settings-persistence bug:
the REST settings endpoint mutates the in-memory config, and on a real
``config.yml`` path that mutation must round-trip to disk so it survives
a sidecar (and app) restart.
"""

from __future__ import annotations

import yaml

from config import ConfigLoader


def test_save_without_config_path_is_noop(tmp_path):
    """ConfigLoader(None) has nothing to write to; save() must return False
    without raising so the server handler can stay oblivious."""
    loader = ConfigLoader(None)
    loader.update(path=str(tmp_path), thread=9)

    assert loader.save() is False


def test_save_writes_ui_keys_to_yaml(tmp_path):
    """After save() the target YAML must contain the UI-editable keys the
    user tweaked — this is the core guarantee the bug was violating."""
    config_path = tmp_path / "config.yml"

    loader = ConfigLoader(str(config_path))
    loader.update(path=str(tmp_path / "downloads"), thread=12, rate_limit=3.5)

    assert loader.save() is True
    assert config_path.exists()

    written = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert written["path"] == str(tmp_path / "downloads")
    assert written["thread"] == 12
    assert written["rate_limit"] == 3.5


def test_save_roundtrips_across_new_loader(tmp_path):
    """Persistence is only useful if a fresh ConfigLoader picks the values
    back up from disk — emulates the desktop app restarting the sidecar."""
    config_path = tmp_path / "config.yml"

    first = ConfigLoader(str(config_path))
    first.update(path=str(tmp_path / "downloads"), thread=7)
    first.save()

    second = ConfigLoader(str(config_path))
    assert second.get("thread") == 7
    assert second.get("path") == str(tmp_path / "downloads")


def test_save_preserves_unrelated_user_keys(tmp_path):
    """Users may have edited their config.yml by hand (e.g. ``link``,
    ``cookies``). save() must never drop those just because the UI doesn't
    know about them."""
    config_path = tmp_path / "config.yml"
    config_path.write_text(
        "link:\n  - https://www.douyin.com/video/123\n"
        "cookies:\n  sessionid_ss: abc\n"
        "thread: 2\n",
        encoding="utf-8",
    )

    loader = ConfigLoader(str(config_path))
    loader.update(thread=8)
    loader.save()

    written = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    # Unrelated user-authored keys survive…
    assert written["link"] == ["https://www.douyin.com/video/123"]
    assert written["cookies"] == {"sessionid_ss": "abc"}
    # …and the UI-driven update is reflected.
    assert written["thread"] == 8


def test_save_creates_parent_directory(tmp_path):
    """Desktop writes config to ``<userData>/config.yml``; on a fresh install
    that directory may not exist yet. save() must materialise it."""
    config_path = tmp_path / "nested" / "subdir" / "config.yml"

    loader = ConfigLoader(str(config_path))
    loader.update(thread=4)
    assert loader.save() is True
    assert config_path.exists()


def test_save_nested_sub_models(tmp_path):
    """Nested sub-models (comments/live/transcript/notifications) must be
    written as nested dicts, not flattened or dropped."""
    config_path = tmp_path / "config.yml"

    loader = ConfigLoader(str(config_path))
    loader.update(
        comments={"enabled": True, "max_comments": 100},
        notifications={"enabled": True, "providers": [{"type": "bark", "url": "https://x"}]},
    )
    loader.save()

    written = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert written["comments"]["enabled"] is True
    assert written["comments"]["max_comments"] == 100
    assert written["notifications"]["enabled"] is True
    assert written["notifications"]["providers"] == [
        {"type": "bark", "url": "https://x"}
    ]
