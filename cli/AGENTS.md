<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-03-27 | Updated: 2026-03-27 -->

# cli

## Purpose
Command-line interface — argument parsing, main async download loop, progress display with Rich, and optional Whisper transcription integration.

## Key Files

| File | Description |
|------|-------------|
| `__init__.py` | Package marker |
| `main.py` | CLI entry point: `main()` → `main_async()` → per-URL `download_url()` orchestration |
| `progress_display.py` | Rich-based terminal UI — banners, progress bars, step tracking, result summaries |
| `whisper_transcribe.py` | Optional audio transcription via OpenAI Whisper |

## For AI Agents

### Working In This Directory
- `main.py` is the orchestration hub — it wires together config, auth, storage, control, and core
- `download_url()` is the per-URL pipeline: resolve short URL → parse → factory → download → record history
- Progress display quiets console logs during download to avoid Rich redraws; restores after
- The `douyin-dl` CLI entry point (from pyproject.toml) maps to `cli.main:main`

### Testing Requirements
- Tests: `tests/test_progress_display.py`
- `main.py` is tested indirectly through integration; mock `asyncio.run` for unit tests

### Common Patterns
- `argparse` for CLI args with `-u`, `-c`, `-p`, `-t` flags
- Chinese-language step labels in progress display (初始化, 解析链接, etc.)
- `DownloadResult` aggregation for multi-URL summary

## Dependencies

### Internal
- `config/` — `ConfigLoader` for YAML config
- `auth/` — `CookieManager` for authentication
- `storage/` — `Database`, `FileManager`
- `control/` — `QueueManager`, `RateLimiter`, `RetryHandler`
- `core/` — `DouyinAPIClient`, `URLParser`, `DownloaderFactory`
- `utils/logger` — `setup_logger`, `set_console_log_level`

### External
- `rich` — terminal UI rendering
- `openai-whisper` — optional transcription (behind `[transcribe]` extra)

<!-- MANUAL: -->
