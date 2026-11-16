"""skill 包与插件清单的静态校验：frontmatter、verb 引用、版本一致、脚本可执行。"""

import json
import os
import re
from pathlib import Path

import pytest
import yaml

from agent_mode.entry import build_parser

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_NAME = "douyin-dl"
SKILL_DIR = REPO_ROOT / "skills" / SKILL_NAME
SKILL_MD = SKILL_DIR / "SKILL.md"
CONTRACT_MD = SKILL_DIR / "references" / "contract.md"
TROUBLESHOOTING_MD = SKILL_DIR / "references" / "troubleshooting.md"
DY_SH = SKILL_DIR / "scripts" / "dy.sh"
PLUGIN_DIR = REPO_ROOT / ".claude-plugin"
PLUGIN_JSON = PLUGIN_DIR / "plugin.json"
MARKETPLACE_JSON = PLUGIN_DIR / "marketplace.json"

# 开放规范（agentskills.io/specification）允许的 frontmatter 字段与长度上限
OPEN_SPEC_FIELDS = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
NAME_MAX_CHARS = 64
DESCRIPTION_MAX_CHARS = 1024
COMPATIBILITY_MAX_CHARS = 500
SKILL_BODY_MAX_LINES = 150
DY_SH_MAX_LINES = 20
DESCRIPTION_KEYWORDS = ("抖音", "Douyin", "热榜", "搜索", "直播录制", "转写", "ARGUS_GATED")
_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.DOTALL)
_VERB_REF_RE = re.compile(r"douyin-dl agent\s+([a-z][a-z_-]*)")
_PYPROJECT_VERSION_RE = re.compile(r'^version\s*=\s*"([^"]+)"', re.MULTILINE)


def _split_skill():
    match = _FRONTMATTER_RE.match(SKILL_MD.read_text(encoding="utf-8"))
    assert match, "SKILL.md must start with a YAML frontmatter block"
    return yaml.safe_load(match.group(1)), match.group(2)


def _project_version() -> str:
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    project_table = text.split("[project]", 1)[1]
    return _PYPROJECT_VERSION_RE.search(project_table).group(1)


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_frontmatter_uses_only_open_spec_fields():
    meta, _ = _split_skill()
    assert set(meta) <= OPEN_SPEC_FIELDS
    assert meta["name"] == SKILL_NAME == SKILL_DIR.name
    assert len(meta["name"]) <= NAME_MAX_CHARS and _NAME_RE.match(meta["name"])
    assert meta["license"] == "MIT"


def test_description_and_compatibility_lengths():
    meta, _ = _split_skill()
    description = meta["description"]
    assert isinstance(description, str) and 0 < len(description) <= DESCRIPTION_MAX_CHARS
    for keyword in DESCRIPTION_KEYWORDS:
        assert keyword in description, keyword
    compatibility = meta["compatibility"]
    assert isinstance(compatibility, str) and 0 < len(compatibility) <= COMPATIBILITY_MAX_CHARS


def test_metadata_is_string_map_with_project_version():
    meta, _ = _split_skill()
    metadata = meta["metadata"]
    assert all(isinstance(k, str) and isinstance(v, str) for k, v in metadata.items())
    assert metadata["version"] == _project_version()


def test_skill_body_is_short_and_links_resolve():
    _, body = _split_skill()
    assert len(body.splitlines()) <= SKILL_BODY_MAX_LINES
    for relative in ("references/contract.md", "references/troubleshooting.md", "scripts/dy.sh"):
        assert relative in body
        assert (SKILL_DIR / relative).is_file()
    assert "douyin-dl agent doctor" in body
    assert "schema: 1" in CONTRACT_MD.read_text(encoding="utf-8")


@pytest.mark.parametrize("path", [SKILL_MD, CONTRACT_MD, TROUBLESHOOTING_MD], ids=lambda p: p.name)
def test_referenced_verbs_are_registered(path):
    registered = set(build_parser().verb_parsers)
    referenced = set(_VERB_REF_RE.findall(path.read_text(encoding="utf-8")))
    assert referenced, "expected at least one `douyin-dl agent <verb>` reference"
    assert referenced <= registered, sorted(referenced - registered)


def test_skill_and_contract_cover_every_verb():
    registered = set(build_parser().verb_parsers)
    for path in (SKILL_MD, CONTRACT_MD):
        referenced = set(_VERB_REF_RE.findall(path.read_text(encoding="utf-8")))
        assert registered <= referenced, (path.name, sorted(registered - referenced))


def test_plugin_manifests_parse_and_match_project_version():
    plugin = _load_json(PLUGIN_JSON)
    marketplace = _load_json(MARKETPLACE_JSON)
    version = _project_version()
    assert plugin["name"] == SKILL_NAME
    assert plugin["version"] == version
    assert plugin["license"] == "MIT"
    assert plugin["author"]["name"] == "jiji262"
    assert marketplace["version"] == version
    assert marketplace["owner"]["name"]
    entries = marketplace["plugins"]
    assert [entry["name"] for entry in entries] == [plugin["name"]]
    # 仓库根既是 marketplace 也是 plugin；entry 不重复声明 version，避免与 plugin.json 打架
    assert entries[0]["source"] == "./"
    assert "version" not in entries[0]


def test_dy_sh_is_executable_and_pinned():
    text = DY_SH.read_text(encoding="utf-8")
    assert os.access(str(DY_SH), os.X_OK)
    assert text.startswith("#!/bin/sh\n")
    assert len(text.splitlines()) <= DY_SH_MAX_LINES
    assert "set -eu" in text
    assert 'VERSION="%s"' % _project_version() in text
    assert "DEPENDENCY_MISSING" in text and "exit 7" in text
