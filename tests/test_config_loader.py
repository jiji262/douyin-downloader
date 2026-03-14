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


def test_config_loader_reads_auto_cookies_from_default_file(tmp_path):
    config_file = tmp_path / "config.yml"
    config_file.write_text(
        """
link:
  - https://www.douyin.com/note/1
path: ./Downloaded/
cookies: auto
"""
    )
    cookie_dir = tmp_path / "config"
    cookie_dir.mkdir()
    (cookie_dir / "cookies.json").write_text(
        """
{
  "ttwid": "auto-ttwid",
  "msToken": "auto-ms-token",
  "_waftokenid": "auto-waf-token"
}
""".strip(),
        encoding="utf-8",
    )

    loader = ConfigLoader(str(config_file))
    cookies = loader.get_cookies()

    assert cookies["ttwid"] == "auto-ttwid"
    assert cookies["msToken"] == "auto-ms-token"
    assert cookies["_waftokenid"] == "auto-waf-token"


def test_config_loader_reads_auto_cookies_for_nested_config_path(tmp_path, monkeypatch):
    workspace = tmp_path
    config_dir = workspace / "config"
    config_dir.mkdir()
    config_file = config_dir / "config.yml"
    config_file.write_text(
        """
link:
  - https://www.douyin.com/note/1
path: ./Downloaded/
cookies: auto
"""
    )
    (workspace / "config" / "cookies.json").write_text(
        """
{
  "ttwid": "nested-ttwid",
  "msToken": "nested-ms-token"
}
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.chdir(workspace)

    loader = ConfigLoader(str(config_file))
    cookies = loader.get_cookies()

    assert cookies["ttwid"] == "nested-ttwid"
    assert cookies["msToken"] == "nested-ms-token"


def test_config_loader_reads_auto_cookies_when_auto_cookie_enabled(
    tmp_path, monkeypatch
):
    workspace = tmp_path
    config_file = workspace / "config.yml"
    config_file.write_text(
        """
link:
  - https://www.douyin.com/note/1
path: ./Downloaded/
auto_cookie: true
"""
    )
    cookie_dir = workspace / "config"
    cookie_dir.mkdir()
    (cookie_dir / "cookies.json").write_text(
        """
{
  "ttwid": "auto-ttwid",
  "msToken": "auto-ms-token"
}
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.chdir(workspace)

    loader = ConfigLoader(str(config_file))
    cookies = loader.get_cookies()

    assert cookies["ttwid"] == "auto-ttwid"
    assert cookies["msToken"] == "auto-ms-token"


def test_config_loader_skips_auto_cookies_when_auto_cookie_disabled(
    tmp_path, monkeypatch
):
    workspace = tmp_path
    config_file = workspace / "config.yml"
    config_file.write_text(
        """
link:
  - https://www.douyin.com/note/1
path: ./Downloaded/
auto_cookie: false
"""
    )
    cookie_dir = workspace / "config"
    cookie_dir.mkdir()
    (cookie_dir / "cookies.json").write_text(
        """
{
  "ttwid": "auto-ttwid",
  "msToken": "auto-ms-token"
}
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.chdir(workspace)

    loader = ConfigLoader(str(config_file))

    assert loader.get_cookies() == {}


def test_config_loader_warns_for_non_object_auto_cookie_file(tmp_path, caplog):
    config_file = tmp_path / "config.yml"
    config_file.write_text(
        """
link:
  - https://www.douyin.com/note/1
path: ./Downloaded/
cookies: auto
"""
    )
    cookie_dir = tmp_path / "config"
    cookie_dir.mkdir()
    (cookie_dir / "cookies.json").write_text("[]", encoding="utf-8")

    loader = ConfigLoader(str(config_file))
    cookies = loader.get_cookies()

    assert cookies == {}
    assert any(
        "is not a JSON object" in record.message for record in caplog.records
    )


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


def test_config_loader_supports_proxy_from_env(tmp_path, monkeypatch):
    config_file = tmp_path / "config.yml"
    config_file.write_text(
        """
link:
  - https://www.douyin.com/video/1
path: ./Downloaded/
proxy: http://127.0.0.1:7890
"""
    )

    monkeypatch.setenv("DOUYIN_PROXY", "http://127.0.0.1:8899")

    loader = ConfigLoader(str(config_file))

    assert loader.get("proxy") == "http://127.0.0.1:8899"


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


@pytest.mark.parametrize(
    "number_cfg,increase_cfg,expected_mix_number,expected_mix_increase,expect_warning",
    [
        ({"mix": 9}, {"mix": True}, 9, True, False),
        ({"allmix": 7}, {"allmix": True}, 7, True, False),
        ({"mix": 8, "allmix": 8}, {"mix": False, "allmix": False}, 8, False, False),
        ({"mix": 5, "allmix": 3}, {"mix": False, "allmix": True}, 5, False, True),
        ({}, {}, 0, False, False),
    ],
)
def test_config_loader_normalizes_mix_aliases(
    tmp_path,
    caplog,
    number_cfg,
    increase_cfg,
    expected_mix_number,
    expected_mix_increase,
    expect_warning,
):
    config_file = tmp_path / "config.yml"
    config_file.write_text(
        f"""
link:
  - https://www.douyin.com/video/1
path: ./Downloaded/
number: {number_cfg}
increase: {increase_cfg}
"""
    )

    loader = ConfigLoader(str(config_file))
    number = loader.get("number", {})
    increase = loader.get("increase", {})

    assert number.get("mix") == expected_mix_number
    assert increase.get("mix") == expected_mix_increase
    # 内部统一后，allmix 与 mix 保持一致，避免后续使用双语义。
    assert number.get("allmix") == expected_mix_number
    assert increase.get("allmix") == expected_mix_increase

    warning_logs = [record.message for record in caplog.records if record.levelname == "WARNING"]
    if expect_warning:
        assert any("mix/allmix conflict" in message for message in warning_logs)
    else:
        assert not any("mix/allmix conflict" in message for message in warning_logs)
