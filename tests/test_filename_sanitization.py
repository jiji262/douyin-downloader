import pytest

from utils.validators import sanitize_filename


@pytest.mark.parametrize(
    "reserved",
    [
        "CON",
        "con.txt",
        "PRN",
        "aux.log",
        "NUL",
        "com1.mp4",
        "COM9",
        "lpt1.json",
        "LPT9",
    ],
)
def test_sanitize_filename_neutralizes_windows_device_names(reserved):
    cleaned = sanitize_filename(reserved)

    assert cleaned.startswith("_")
    assert cleaned[1:] == reserved


def test_sanitize_filename_keeps_non_reserved_lookalikes():
    assert sanitize_filename("COM10.txt") == "COM10.txt"
    assert sanitize_filename("console.mp4") == "console.mp4"
