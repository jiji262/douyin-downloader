import os

import pytest
from config import ConfigLoader


def test_config_loader_merges_file_and_defaults(tmp_path, monkeypatch):
    config_file = tmp_path / "config.yml"
    config_file.write_text(
        """
link:
  - https://www.douyin.com/video/1
path: ./Custom/
thread: 3
"""
    )

    monkeypatch.setenv("DOUYIN_THREAD", "8")

    loader = ConfigLoader(str(config_file))

    # Environment variable should override file
    assert loader.get("thread") == 8
    # File values should override defaults
    assert loader.get("path") == "./Custom/"
    # Links should be normalized to list
    assert loader.get_links() == ["https://www.douyin.com/video/1"]


def test_config_validation_requires_links_and_path(tmp_path):
    config_file = tmp_path / "config.yml"
    config_file.write_text("{}")

    loader = ConfigLoader(str(config_file))
    assert not loader.validate()

    loader.update(link=["https://www.douyin.com/video/1"], path="./Downloaded/")
    assert loader.validate() is True


def test_config_loader_sanitizes_invalid_cookie_keys(tmp_path):
    config_file = tmp_path / "config.yml"
    config_file.write_text(
        """
link:
  - https://www.douyin.com/video/1
path: ./Downloaded/
cookies:
  "": douyin.com
  ttwid: abc
  msToken: token
"""
    )

    loader = ConfigLoader(str(config_file))
    cookies = loader.get_cookies()

    assert "" not in cookies
    assert cookies["ttwid"] == "abc"
    assert cookies["msToken"] == "token"


def test_progress_quiet_logs_default_enabled(tmp_path):
    config_file = tmp_path / "config.yml"
    config_file.write_text(
        """
link:
  - https://www.douyin.com/video/1
path: ./Downloaded/
"""
    )

    loader = ConfigLoader(str(config_file))
    progress = loader.get("progress", {})

    assert isinstance(progress, dict)
    assert progress.get("quiet_logs") is True


def test_progress_quiet_logs_can_be_overridden(tmp_path):
    config_file = tmp_path / "config.yml"
    config_file.write_text(
        """
link:
  - https://www.douyin.com/video/1
path: ./Downloaded/
progress:
  quiet_logs: false
"""
    )

    loader = ConfigLoader(str(config_file))
    progress = loader.get("progress", {})

    assert isinstance(progress, dict)
    assert progress.get("quiet_logs") is False


def test_nested_defaults_do_not_leak_between_loader_instances(tmp_path):
    config_file = tmp_path / "config.yml"
    config_file.write_text(
        """
link:
  - https://www.douyin.com/video/1
path: ./Downloaded/
"""
    )

    loader_a = ConfigLoader(str(config_file))
    loader_a.update(progress={"quiet_logs": False})

    loader_b = ConfigLoader(str(config_file))
    assert loader_b.get("progress", {}).get("quiet_logs") is True
