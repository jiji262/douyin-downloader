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


# ---------------------------------------------------------------------------
# 只处理真正不能落盘的部分;合法字符原样保留
# ---------------------------------------------------------------------------


def test_sanitize_filename_replaces_only_windows_illegal_chars():
    assert sanitize_filename('a<b>c:d"e/f\\g|h?i*j') == "a_b_c_d_e_f_g_h_i_j"


def test_sanitize_filename_replaces_control_chars_and_newlines():
    assert sanitize_filename("a\tb\x01c") == "a_b_c"
    assert sanitize_filename("line1\nline2\r\nline3") == "line1 line2  line3"


@pytest.mark.parametrize(
    "legal",
    [
        "#搞笑 #日常",
        "a__b___c",
        "hello   world",
        "_leading_and_trailing_",
        "-dash-",
        "a,b;c'd(e)f[g]h{i}j!k@l$m%n^o&p+q=r~s`t",
    ],
)
def test_sanitize_filename_keeps_legal_content_untouched(legal):
    assert sanitize_filename(legal) == legal


def test_sanitize_filename_strips_only_dots_and_spaces_at_the_edges():
    # Windows 会静默去掉结尾的 . 和空格;开头的 . 在 macOS / Linux 上是隐藏文件
    assert sanitize_filename("  ..name.. ") == "name"
    assert sanitize_filename("_-name-_") == "_-name-_"


def test_sanitize_filename_truncates_and_re_strips_trailing_dots():
    assert sanitize_filename("x" * 79 + ". tail", max_length=80) == "x" * 79


def test_sanitize_filename_falls_back_to_untitled_only_when_nothing_is_left():
    assert sanitize_filename("") == "untitled"
    assert sanitize_filename(" . . ") == "untitled"
    assert sanitize_filename("///???") == "______"
