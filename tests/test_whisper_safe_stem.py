from cli.whisper_transcribe import _safe_stem


def test_safe_stem_uses_the_shared_sanitizer_rules():
    # 转写文件要和视频同名:同一套规则,只处理真正不能落盘的字符
    assert _safe_stem("2024-01-01 #搞笑 a__b") == "2024-01-01 #搞笑 a__b"
    assert _safe_stem('bad/name?*') == "bad_name__"
    assert _safe_stem("x" * 200) == "x" * 150
