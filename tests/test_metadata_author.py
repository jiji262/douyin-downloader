"""Tests for aweme author extraction helpers."""

from core.metadata import extract_author_nickname, extract_author_sec_uid


def test_extract_author_nickname_ok():
    aweme = {"author": {"nickname": " 刘小排 ", "sec_uid": "MS4wLjABAAAAxxx"}}
    assert extract_author_nickname(aweme) == "刘小排"
    assert extract_author_sec_uid(aweme) == "MS4wLjABAAAAxxx"


def test_extract_author_nickname_missing():
    assert extract_author_nickname({}) is None
    assert extract_author_nickname({"author": {}}) is None
    assert extract_author_nickname({"author": {"nickname": "  "}}) is None
    assert extract_author_nickname(None) is None
