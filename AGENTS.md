<!-- Generated: 2026-03-27 | Updated: 2026-03-27 -->

# douyin-downloader

## Purpose
A Python-based Douyin (TikTok China) batch downloader that fetches videos, galleries, music, and user content without watermarks. Supports multiple download modes (user posts, likes, mixes, music), concurrent downloads with rate limiting, cookie-based authentication, and optional Whisper transcription. CLI-driven with YAML configuration.

## Key Files

| File | Description |
|------|-------------|
| `run.py` | Entry point — bootstraps `sys.path` and delegates to `cli.main:main()` |
| `__init__.py` | Package version (`2.0.0`) |
| `pyproject.toml` | Build config, dependencies, CLI entry point (`douyin-dl`), tool settings |
| `config.example.yml` | Example YAML config for users to copy and customize |
| `requirements.txt` | Pinned dependency list (mirrors pyproject.toml) |
| `Dockerfile` | Container build for the downloader |
| `PROJECT_SUMMARY.md` | Architecture overview document |

## Subdirectories

| Directory | Purpose |
|-----------|---------|
| `auth/` | Cookie and MS token management (see `auth/AGENTS.md`) |
| `cli/` | CLI argument parsing, main async loop, progress display (see `cli/AGENTS.md`) |
| `config/` | YAML config loading, env var overrides, defaults (see `config/AGENTS.md`) |
| `control/` | Concurrency control — rate limiter, retry handler, queue manager (see `control/AGENTS.md`) |
| `core/` | Business logic — API client, URL parser, downloaders, strategy pattern (see `core/AGENTS.md`) |
| `storage/` | SQLite database, file management, metadata handling (see `storage/AGENTS.md`) |
| `tests/` | Pytest test suite with 23 test modules (see `tests/AGENTS.md`) |
| `tools/` | Standalone utilities like browser-based cookie fetching (see `tools/AGENTS.md`) |
| `utils/` | Shared helpers — logging, validation, anti-bot signatures (see `utils/AGENTS.md`) |

## For AI Agents

### Working In This Directory
- Python 3.8+ compatibility required — avoid walrus operator, `match` statements, and `type` aliases
- All I/O is async (`aiohttp`, `aiofiles`, `aiosqlite`) — never use blocking I/O in core paths
- Entry point is `cli.main:main()` which calls `asyncio.run(main_async(args))`
- Config is YAML-based with env var overrides (`DOUYIN_*` prefix)
- The `mix`/`allmix` config alias system requires special handling (see `config/config_loader.py`)

### Testing Requirements
- Run: `python -m pytest tests/`
- Async tests use `pytest-asyncio` with `asyncio_mode = "auto"`
- Linting: `ruff check .` (target Python 3.8, line-length 100)

### Common Patterns
- Factory pattern for downloaders (`DownloaderFactory.create()`)
- Strategy pattern for user download modes (`core/user_modes/`)
- Registry pattern for mode discovery (`UserModeRegistry`)
- All downloaders inherit from `BaseDownloader` with shared `_download_mode_items()`
- Logging via `utils.logger.setup_logger(name)` — one logger per module

## Dependencies

### External
- `aiohttp` — async HTTP client for API calls and downloads
- `aiofiles` — async file I/O
- `aiosqlite` — async SQLite for download history
- `rich` — terminal UI (progress bars, tables, styled output)
- `pyyaml` — YAML config parsing
- `python-dateutil` — date/time parsing for time-range filters
- `gmssl` — Chinese SM3/SM4 crypto for anti-bot signatures

### Optional
- `playwright` — browser automation for cookie fetching
- `openai-whisper` — audio transcription

<!-- MANUAL: -->
