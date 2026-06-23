<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-03-27 | Updated: 2026-03-27 -->

# tools

## Purpose
Standalone utility scripts — currently contains browser-based cookie fetching via Playwright.

## Key Files

| File | Description |
|------|-------------|
| `__init__.py` | Package docstring |
| `cookie_fetcher.py` | Launches Playwright browser to authenticate with Douyin and extract cookies |

## For AI Agents

### Working In This Directory
- `cookie_fetcher.py` requires the `[browser]` optional dependency (`playwright`)
- It automates browser login and exports cookies to JSON format
- This is a user-facing utility, not part of the core download pipeline

### Testing Requirements
- Tests: `tests/test_cookie_fetcher.py`
- Tests mock Playwright — do not launch real browsers

### Common Patterns
- Playwright async API for browser automation
- Cookie export as JSON dict for use with `ConfigLoader`

## Dependencies

### Internal
- `utils/cookie_utils.py` — cookie sanitization

### External
- `playwright` — browser automation (optional dependency)

<!-- MANUAL: -->
