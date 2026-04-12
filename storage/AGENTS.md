<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-03-27 | Updated: 2026-03-27 -->

# storage

## Purpose
Data persistence and file management — SQLite database for download history, file system management for downloaded media, and metadata extraction/storage.

## Key Files

| File | Description |
|------|-------------|
| `__init__.py` | Exports `Database`, `FileManager`, `MetadataHandler` |
| `database.py` | Async SQLite wrapper — download history, latest aweme timestamps for incremental mode |
| `file_manager.py` | File path construction, directory creation, duplicate detection, file writing |
| `metadata_handler.py` | Extracts and stores metadata (author, description, timestamps) alongside media files |

## For AI Agents

### Working In This Directory
- `Database` is optional — enabled by `database: true` in config
- `Database.get_latest_aweme_time()` powers the incremental download feature
- `FileManager` handles path construction with author name, aweme ID, and content type
- All file I/O uses `aiofiles` for async operations

### Testing Requirements
- Tests: `tests/test_database.py`, `tests/test_file_manager.py`

### Common Patterns
- Async context manager for database connections
- `aiosqlite` for non-blocking SQLite access
- File naming: `{author}/{aweme_id}_{type}.{ext}`

## Dependencies

### Internal
- `utils/helpers.py` — timestamp parsing, size formatting
- `utils/validators.py` — filename sanitization

### External
- `aiosqlite` — async SQLite
- `aiofiles` — async file I/O

<!-- MANUAL: -->
