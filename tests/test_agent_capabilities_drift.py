"""能力表与共享代码的漂移检查：共享代码变了而 agent_mode/capabilities.py 没跟上就失败。"""

import ast
from pathlib import Path

from agent_mode import capabilities
from agent_mode.entry import build_parser

REPO_ROOT = Path(__file__).resolve().parent.parent
GATED_METHOD = "_request_json_gated"
SPEC_VERBS = {"doctor", "inspect", "hot", "search", "user", "login", "live", "transcribe"}


def _tree(relative):
    return ast.parse((REPO_ROOT / relative).read_text(encoding="utf-8"))


def _string_constants(node):
    return {
        n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)
    }


def _gated_paths():
    paths = set()
    for node in ast.walk(_tree("core/api_client.py")):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Attribute) and node.func.attr == GATED_METHOD):
            continue
        first = node.args[0]
        assert isinstance(first, ast.Constant) and isinstance(first.value, str), (
            "%s called with a non-literal path at line %d" % (GATED_METHOD, node.lineno)
        )
        paths.add(first.value)
    return paths


def _returned_url_types():
    """utils/validators.parse_url_type 所有 return 里出现的字符串。"""
    func = next(
        n
        for n in ast.walk(_tree("utils/validators.py"))
        if isinstance(n, ast.FunctionDef) and n.name == "parse_url_type"
    )
    found = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Return) and node.value is not None:
            found |= _result_strings(node.value)
    return found


def _result_strings(value):
    """return 表达式可能取到的字符串：`"a" if cond else None` 只看两个分支，不看条件。"""
    if isinstance(value, ast.IfExp):
        return _result_strings(value.body) | _result_strings(value.orelse)
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return {value.value}
    return set()


def _compared_url_types(relative):
    """形如 `url_type == "video"` 的比较里出现的类型名。"""
    found = set()
    for node in ast.walk(_tree(relative)):
        if not isinstance(node, ast.Compare):
            continue
        if isinstance(node.left, ast.Name) and node.left.id == "url_type":
            for comparator in node.comparators:
                found |= _string_constants(comparator)
    return found


def test_gated_endpoints_match_api_client():
    assert _gated_paths() == set(capabilities.GATED_ENDPOINTS)


def test_every_parser_url_type_is_in_the_table():
    produced = _returned_url_types()
    assert capabilities.UNRESOLVED_URL_TYPE in produced
    assert produced - {capabilities.UNRESOLVED_URL_TYPE} == set(capabilities.URL_TYPE_CAPABILITIES)


def test_url_parser_and_factory_types_are_known():
    from core.downloader_factory import UNSUPPORTED_URL_TYPE_DETAIL

    known = set(capabilities.URL_TYPE_CAPABILITIES) | {capabilities.UNRESOLVED_URL_TYPE}
    assert _compared_url_types("core/url_parser.py") <= known
    assert _compared_url_types("core/downloader_factory.py") <= known
    for url_type in UNSUPPORTED_URL_TYPE_DETAIL:
        assert capabilities.URL_TYPE_CAPABILITIES[url_type] == capabilities.UNSUPPORTED


def test_tables_are_internally_consistent():
    assert set(capabilities.URL_TYPE_CAPABILITIES.values()) <= set(capabilities.LEVELS)
    assert set(capabilities.VERB_CAPABILITIES.values()) <= set(capabilities.LEVELS)
    assert set(capabilities.URL_TYPE_NOTES) == set(capabilities.URL_TYPE_CAPABILITIES)
    for url_type, verb in capabilities.URL_TYPE_SUGGESTED_VERB.items():
        assert verb in capabilities.VERB_CAPABILITIES
        level = capabilities.URL_TYPE_CAPABILITIES[url_type]
        assert level not in (capabilities.ARGUS_GATED, capabilities.UNSUPPORTED)
    assert capabilities.url_type_level(None) == capabilities.UNSUPPORTED
    assert capabilities.url_type_level("brand-new-type") == capabilities.UNSUPPORTED


def test_all_spec_verbs_are_registered_with_handlers():
    parser = build_parser()
    assert set(parser.verb_parsers) == SPEC_VERBS == set(capabilities.VERB_CAPABILITIES)
    for verb, verb_parser in parser.verb_parsers.items():
        assert callable(verb_parser.get_default("handler")), verb


def test_gated_url_types_are_exactly_the_download_types():
    gated = {t for t, lv in capabilities.URL_TYPE_CAPABILITIES.items() if lv == "argus_gated"}
    assert gated == {"video", "gallery", "collection", "music"}
