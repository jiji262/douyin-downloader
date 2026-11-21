import datetime as dt
from datetime import datetime

import pytest

from config import ConfigLoader


def _loader_with(tmp_path, body):
    config_file = tmp_path / "config.yml"
    config_file.write_text(body, encoding="utf-8")
    return ConfigLoader(str(config_file))


def test_validate_normalizes_invalid_thread(tmp_path):
    config_file = tmp_path / "config.yml"
    config_file.write_text("link:\n  - https://www.douyin.com/video/123\npath: ./out\nthread: -1\n")
    loader = ConfigLoader(str(config_file))
    assert loader.validate() is True
    assert loader.get("thread") == 5


def test_validate_normalizes_invalid_start_time(tmp_path):
    config_file = tmp_path / "config.yml"
    config_file.write_text(
        "link:\n  - https://www.douyin.com/video/123\npath: ./out\nstart_time: not-a-date\n"
    )
    loader = ConfigLoader(str(config_file))
    assert loader.validate() is True
    assert loader.get("start_time") == ""


def test_env_thread_invalid_ignored(tmp_path, monkeypatch):
    config_file = tmp_path / "config.yml"
    config_file.write_text("link:\n  - https://www.douyin.com/video/123\npath: ./out\n")
    monkeypatch.setenv("DOUYIN_THREAD", "not_a_number")
    loader = ConfigLoader(str(config_file))
    assert loader.get("thread") == 5


@pytest.mark.parametrize(
    "raw,expected",
    [
        # PyYAML parses an unquoted ISO date/timestamp into a date/datetime
        # object. validate() must normalize it back to the documented
        # "YYYY-MM-DD" string instead of leaking the object downstream.
        ("2024-01-01", "2024-01-01"),
        ("2024-01-01 12:30:00", "2024-01-01"),
        # strftime("%Y") drops the leading zeros for years below 1000 on
        # Linux; isoformat() keeps the zero-padded YYYY-MM-DD form that the
        # downstream strptime() contract requires.
        ("0001-01-01", "0001-01-01"),
        # Any other non-string YAML scalar/collection is invalid and must be
        # cleared rather than left in config for datetime.strptime to choke on.
        ("20240101", ""),
        ("true", ""),
        ("[1, 2]", ""),
        # A quoted valid date keeps working; a malformed string is still cleared.
        ('"2024-01-01"', "2024-01-01"),
        ("not-a-date", ""),
    ],
)
def test_validate_normalizes_date_field_types(tmp_path, raw, expected):
    loader = _loader_with(
        tmp_path,
        f"link:\n  - https://www.douyin.com/video/123\npath: ./out\nstart_time: {raw}\n",
    )

    assert loader.validate() is True
    value = loader.get("start_time")
    assert value == expected
    assert isinstance(value, str)
    # The value left in config must satisfy the downstream contract
    # datetime.strptime(value, "%Y-%m-%d") without raising TypeError.
    if value:
        datetime.strptime(value, "%Y-%m-%d")


def test_validate_preserves_leading_zeros_for_early_years(tmp_path, monkeypatch):
    """strftime('%Y') is platform-dependent: on Linux a year below 1000
    loses its leading zeros ('0001-01-01' -> '1-01-01'). isoformat() is not,
    so normalization must not go through strftime. Simulate that platform
    here so the regression is reproducible on Windows too."""
    class LinuxLikeDate(dt.date):
        def strftime(self, fmt):
            return "1-01-01"

    loader = _loader_with(
        tmp_path, "link:\n  - https://www.douyin.com/video/123\npath: ./out\n"
    )
    loader.config["start_time"] = LinuxLikeDate(1, 1, 1)

    assert loader.validate() is True
    assert loader.get("start_time") == "0001-01-01"


@pytest.mark.parametrize("field", ["start_time", "end_time"])
def test_validate_normalizes_date_object_for_both_fields(tmp_path, field):
    """Both date fields route through the shared validation path. An
    unquoted YAML date used to survive validate() as a datetime.date and
    crash _time_range_bounds() in core/downloader_base.py."""
    loader = _loader_with(
        tmp_path,
        f"link:\n  - https://www.douyin.com/video/123\npath: ./out\n{field}: 2024-03-15\n",
    )

    assert loader.validate() is True
    assert loader.get(field) == "2024-03-15"
    assert isinstance(loader.get(field), str)

