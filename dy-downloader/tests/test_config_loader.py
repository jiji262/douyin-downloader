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

    monkeypatch.setenv('DOUYIN_THREAD', '8')

    loader = ConfigLoader(str(config_file))

    # Environment variable should override file
    assert loader.get('thread') == 8
    # File values should override defaults
    assert loader.get('path') == './Custom/'
    # Links should be normalized to list
    assert loader.get_links() == ['https://www.douyin.com/video/1']


def test_config_validation_requires_links_and_path(tmp_path):
    config_file = tmp_path / "config.yml"
    config_file.write_text("{}")

    loader = ConfigLoader(str(config_file))
    assert not loader.validate()

    loader.update(link=['https://www.douyin.com/video/1'], path='./Downloaded/')
    assert loader.validate() is True
