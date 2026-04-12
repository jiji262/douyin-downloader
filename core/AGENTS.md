<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-03-27 | Updated: 2026-03-27 -->

# core

## Purpose
Core business logic — Douyin API client, URL parsing, download orchestration, and specialized downloaders for videos, users, mixes, and music. Uses factory and strategy patterns for extensibility.

## Key Files

| File | Description |
|------|-------------|
| `__init__.py` | Exports `DouyinAPIClient`, `URLParser`, `DownloaderFactory`, `MixDownloader`, `MusicDownloader` |
| `api_client.py` | Async HTTP client for Douyin API — fetches video details, user posts, mix lists, music |
| `url_parser.py` | Regex-based URL classifier — detects video, user, gallery, collection, music URL types |
| `downloader_base.py` | `BaseDownloader` ABC and `DownloadResult` dataclass — shared download logic |
| `downloader_factory.py` | Factory mapping URL types to downloader classes |
| `video_downloader.py` | Downloads single videos and gallery images (handles both `video` and `gallery` types) |
| `user_downloader.py` | Downloads all content for a user — delegates to mode strategies via registry |
| `mix_downloader.py` | Downloads Douyin "mix" (collection) content |
| `music_downloader.py` | Downloads music-related content |
| `user_mode_registry.py` | Auto-discovers and registers user mode strategies from `user_modes/` |
| `transcript_manager.py` | Manages Whisper transcription for downloaded audio |

## Subdirectories

| Directory | Purpose |
|-----------|---------|
| `user_modes/` | Strategy pattern implementations for user download modes (see `user_modes/AGENTS.md`) |

## For AI Agents

### Working In This Directory
- `DownloaderFactory.create()` maps URL type strings to downloader instances
- All downloaders share the same constructor signature (config, api_client, file_manager, cookie_manager, database, rate_limiter, retry_handler, queue_manager, progress_reporter)
- `UserDownloader` uses `UserModeRegistry` to discover strategies and runs enabled modes
- `VideoDownloader` handles both `video` and `gallery` URL types
- API client uses anti-bot signatures from `utils/xbogus.py` and `utils/abogus.py`
- Gallery downloads prefer `download_url_list` over `url_list` for higher quality

### Testing Requirements
- Tests: `tests/test_api_client.py`, `tests/test_url_parser.py`, `tests/test_downloader_factory.py`, `tests/test_video_downloader.py`, `tests/test_user_downloader.py`, `tests/test_mix_downloader.py`, `tests/test_music_downloader.py`, `tests/test_user_mode_registry.py`, `tests/test_user_downloader_modes.py`

### Common Patterns
- Factory pattern: `DownloaderFactory` → concrete downloader
- Strategy pattern: `BaseUserModeStrategy` → concrete mode strategies
- Registry pattern: `UserModeRegistry` auto-discovers strategies
- Paged API collection with cursor-based pagination and stall detection
- `DownloadResult` tracks total/success/failed/skipped counts

## Dependencies

### Internal
- `auth/` — `CookieManager` for authenticated requests
- `config/` — `ConfigLoader` for download settings
- `control/` — rate limiting, retry, queue management
- `storage/` — `Database` for history, `FileManager` for file I/O
- `utils/` — logging, anti-bot signatures, helpers

### External
- `aiohttp` — async HTTP
- `aiofiles` — async file writes

<!-- MANUAL: -->
