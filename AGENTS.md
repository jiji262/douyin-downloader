<!-- Generated: 2026-03-27 | Updated: 2026-05-08 -->

# douyin-downloader

## Purpose
A Python-based Douyin (TikTok China) batch downloader that fetches videos, galleries, music, and user content without watermarks. Supports multiple download modes (user posts, likes, mixes, music), concurrent downloads with rate limiting, cookie-based authentication, and optional Whisper transcription. CLI-driven with YAML configuration.

## Key Files

| File | Description |
|------|-------------|
| `run.py` | Entry point — bootstraps `sys.path` and delegates to `cli.main:main()` |
| `__init__.py` | Legacy package marker, no longer read — the version source is `pyproject.toml` (`agent_mode/version.py`) |
| `pyproject.toml` | Build config (PyPI distribution `douyin-dl-cli`), dependencies, CLI entry point (`douyin-dl`), tool settings |
| `config.example.yml` | Example YAML config for users to copy and customize |
| `requirements.txt` | Pinned dependency list (mirrors pyproject.toml) |
| `Dockerfile` | Container build for the downloader |
| `PROJECT_SUMMARY.md` | Architecture overview document |

## Subdirectories

| Directory | Purpose |
|-----------|---------|
| `agent_mode/` | CLI-only `douyin-dl agent <verb>` mode — one JSON envelope per run, stable error and exit codes |
| `auth/` | Cookie and MS token management (see `auth/AGENTS.md`) |
| `cli/` | CLI argument parsing, main async loop, progress display (see `cli/AGENTS.md`) |
| `config/` | YAML config loading, env var overrides, defaults (see `config/AGENTS.md`) |
| `control/` | Concurrency control — rate limiter, retry handler, queue manager (see `control/AGENTS.md`) |
| `core/` | Business logic — API client, URL parser, downloaders, strategy pattern (see `core/AGENTS.md`) |
| `skills/` | CLI-only agent skill package `skills/douyin-dl/` (SKILL.md, references, launcher script) |
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

### Shared Logic With Desktop
- This project shares Python backend logic with `/Users/crimson/codes/douyin/douyin-downloader-desktop`.
- When fixing shared logic in `auth/`, `cli/`, `config/`, `control/`, `core/`, `storage/`, `tools/`, `utils/`, or shared tests, apply the equivalent fix in both projects unless the difference is explicitly desktop-only or CLI-only.
- Before finishing a shared-logic fix, compare the touched shared files against the sibling project and either keep them identical or document the intentional divergence.
- **Sync script:** `../douyin-downloader-desktop/scripts/sync-to-cli.sh` copies all shared files from the desktop project here. Run `--check` to detect drift.
- **Intentional divergences** (these files differ by design):
  - `cli/main.py` — CLI omits desktop-only `_verify_self_checksum()` and `_enforce_license_at_startup()`.
  - `run.py` — CLI is a simple bootstrap; desktop has sidecar startup + data-dir migration.
  - `server/app.py`, `server/jobs.py` — CLI server is a simplified subset; desktop adds license, SSE, overrides, cancel.
  - `control/__init__.py` — CLI doesn't export `ProgressReporter` classes (desktop UI only).
  - `core/retry_executor.py` — CLI retains an unwired legacy copy; the active desktop implementation depends on desktop-only platform routing and is not auto-synced.
  - `utils/proxy.py` — desktop-only policy for following the OS proxy when the app proxy setting is blank; CLI keeps explicit-only proxy semantics.
  - `core/page_bridge.py` (desktop-only, absent here) — Douyin's `ArgusSecurityPlugin` 403s non-page requests to `aweme/favorite`, `collects/*`, `aweme/listcollection`, `mix/listcollection` (since 2026-08), `mix/aweme` (since 2026-09-10), and `aweme/detail`, `aweme/post`, `mix/detail`, `mix/list`, `music/detail`, `music/aweme`, `music/list` (since 2026-09-14). Desktop signs them inside its hidden login window; the CLI always runs `DouyinAPIClient(page_bridge=None)`, so `_request_json_gated` falls back to aiohttp and these downloads cannot work in the CLI (profile posts only via the optional Playwright browser fallback, untested against the gate; see README "Current Limitations"). Shared files keep the bridge paths duck-typed (`page_bridge_code`) so they stay byte-identical — do not "fix" the 403 by adding query params or by removing the gated routing here.
  - Configuring `mode: [post, mix]` here downloads nothing: `mix/list` is behind the Argus gate
    above, so the collection scan always hard-fails, and `post` is deliberately skipped whenever
    collection membership is unconfirmed (members must not be misfiled as plain posts). Other
    selected modes such as `like` and `music` still run. This is an accepted trade-off of the
    shared routing, not a CLI bug — do not add a CLI-only branch to `core/user_downloader.py`
    or `core/user_modes/*`, it would break byte-identical sync. Use `mode: [post]` in the CLI.
  - Comments in synced shared files may point to `docs/spec/*`, `docs/research/*` or `core/my_content_service.py`; those live in the desktop repo only.
  - `storage/database.py` — desktop adds TikTok, Following, and My Content schema/helpers; only dependency-neutral database tests remain byte-identical.
  - `tests/test_database_platform.py`, `tests/test_database_desktop_schema.py`, `tests/test_retry_executor.py` — desktop-only tests and not present here.
  - Agent mode is CLI-only and absent from desktop (none of it is in the sync list): `agent_mode/`, `skills/`, `.claude-plugin/`, `.github/workflows/release-cli.yml`, `tests/test_agent_*.py`, `tests/test_cli_exit_codes.py`, `tests/test_skill_package.py`.
  - `run.py` — CLI additionally skips `os.chdir` when `argv[1] == "agent"`, so agent paths resolve against the caller's cwd.
  - `cli/main.py` — CLI additionally dispatches `douyin-dl agent ...` to `agent_mode.entry.main()` before argparse, and sets exit codes: 3 when a `LoginRequiredError` cannot be recovered (the rest of a URL batch still runs, without further relogin attempts, and the summary is still printed), 1 when nothing in the batch succeeded or was skipped and at least one URL failed.

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
