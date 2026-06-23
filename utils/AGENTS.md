<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-03-27 | Updated: 2026-03-27 -->

# utils

## Purpose
Shared utility modules — logging setup, URL/filename validation, date/size helpers, cookie parsing, and Douyin anti-bot signature generation (X-Bogus, A-Bogus).

## Key Files

| File | Description |
|------|-------------|
| `__init__.py` | Exports `setup_logger`, `validate_url`, `sanitize_filename`, `parse_timestamp`, `format_size`, `generate_x_bogus`, `XBogus` |
| `logger.py` | Configures per-module loggers with Rich handler; `set_console_log_level()` for runtime control |
| `validators.py` | URL validation and filename sanitization (removes illegal characters) |
| `helpers.py` | `parse_timestamp()` for Douyin timestamps, `format_size()` for human-readable byte sizes |
| `cookie_utils.py` | `parse_cookie_header()` string→dict, `sanitize_cookies()` value cleaning |
| `xbogus.py` | X-Bogus signature generator for Douyin API request signing |
| `abogus.py` | A-Bogus signature generator using SM3 crypto (newer anti-bot mechanism) |

## For AI Agents

### Working In This Directory
- `setup_logger(name)` is called in every module — returns a named logger with Rich formatting
- `set_console_log_level()` is used by CLI to quiet logs during progress display
- Anti-bot modules (`xbogus.py`, `abogus.py`) implement Douyin's request signing — changes here require careful testing as they affect all API calls
- `cookie_utils.py` is shared by both `auth/` and `config/`

### Testing Requirements
- Tests: `tests/test_xbogus.py`, `tests/test_cookie_utils.py`
- Anti-bot signature tests should verify output format, not exact values (signatures change with input)

### Common Patterns
- Singleton-style logger per module via `setup_logger()`
- SM3 hashing via `gmssl` for A-Bogus signatures
- Cookie sanitization strips whitespace and empty values

## Dependencies

### External
- `rich` — log handler formatting
- `gmssl` — SM3/SM4 Chinese crypto standard (for `abogus.py`)
- `python-dateutil` — date parsing in `helpers.py`

<!-- MANUAL: -->
