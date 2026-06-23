<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-03-27 | Updated: 2026-03-27 -->

# config

## Purpose
YAML configuration loading with environment variable overrides, default values, cookie resolution, and config validation. Handles the `mix`/`allmix` alias normalization system.

## Key Files

| File | Description |
|------|-------------|
| `__init__.py` | Exports `ConfigLoader` |
| `config_loader.py` | Main config class — loads YAML, merges env overrides, normalizes aliases, validates |
| `default_config.py` | `DEFAULT_CONFIG` dict with all default values |
| `cookies.json` | Auto-cookie file — loaded when `cookie: auto` or `auto_cookie: true` |

## For AI Agents

### Working In This Directory
- `ConfigLoader` merges: defaults → YAML file → env vars (`DOUYIN_*` prefix)
- The `mix`/`allmix` alias system keeps both keys in sync across `number` and `increase` sections
- Cookie resolution order: explicit string → dict → `"auto"` keyword → `auto_cookie` flag → fallback JSON files
- Network config supports TLS customization: `verify`, `trust_env`, `ca_file`, `ca_dir`
- `validate()` coerces types and clears invalid date formats

### Testing Requirements
- Tests: `tests/test_config_loader.py`, `tests/test_config_validation.py`

### Common Patterns
- Deep-merge for nested dicts (`_merge_config`)
- Explicit-source tracking for alias conflict resolution (`_is_key_explicit_in_sources`)
- Boolean env var parsing with strict true/false value sets

## Dependencies

### Internal
- `utils/cookie_utils.py` — `parse_cookie_header()`, `sanitize_cookies()`

### External
- `pyyaml` — YAML parsing

<!-- MANUAL: -->
