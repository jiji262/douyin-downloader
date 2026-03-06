import os
import pytest
from config import ConfigLoader


def test_validate_normalizes_invalid_thread(tmp_path):
    config_file = tmp_path / "config.yml"
    config_file.write_text("link:\n  - https://www.douyin.com/video/123\npath: ./out\nthread: -1\n")
    loader = ConfigLoader(str(config_file))
    assert loader.validate() is True
    assert loader.get("thread") == 5


def test_validate_normalizes_invalid_start_time(tmp_path):
    config_file = tmp_path / "config.yml"
    config_file.write_text("link:\n  - https://www.douyin.com/video/123\npath: ./out\nstart_time: not-a-date\n")
    loader = ConfigLoader(str(config_file))
    assert loader.validate() is True
    assert loader.get("start_time") == ""


def test_env_thread_invalid_ignored(tmp_path, monkeypatch):
    config_file = tmp_path / "config.yml"
    config_file.write_text("link:\n  - https://www.douyin.com/video/123\npath: ./out\n")
    monkeypatch.setenv("DOUYIN_THREAD", "not_a_number")
    loader = ConfigLoader(str(config_file))
    assert loader.get("thread") == 5
