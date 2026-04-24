<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-03-27 | Updated: 2026-03-27 -->

# user_modes

## Purpose
Strategy pattern implementations for user download modes. Each strategy defines how to collect and filter content for a specific mode (posts, likes, mixes, music). Auto-discovered by `UserModeRegistry`.

## Key Files

| File | Description |
|------|-------------|
| `__init__.py` | Package marker |
| `base_strategy.py` | `BaseUserModeStrategy` ABC — paged collection, cursor pagination, filtering, metadata expansion |
| `post_strategy.py` | Downloads a user's published posts (`mode_name="post"`) |
| `like_strategy.py` | Downloads a user's liked videos (`mode_name="like"`) |
| `mix_strategy.py` | Downloads a user's mixes/collections (`mode_name="mix"`) — expands metadata items to aweme lists |
| `music_strategy.py` | Downloads a user's music-related content (`mode_name="music"`) — expands metadata items |

## For AI Agents

### Working In This Directory
- Each strategy sets `mode_name` and `api_method_name` class attributes
- `BaseUserModeStrategy._collect_paged_aweme()` handles cursor-based pagination with stall detection
- Mix and music strategies override `collect_items()` to use `_expand_metadata_items()` for two-level fetching
- `apply_filters()` chains time-range filtering and count limiting from config
- The `number` and `increase` config sections control per-mode limits and incremental behavior
- New modes: create a new `*_strategy.py` with a class inheriting `BaseUserModeStrategy`, set `mode_name` and `api_method_name`, and it will be auto-discovered

### Testing Requirements
- Tests: `tests/test_user_mode_strategies.py`, `tests/test_user_downloader_modes.py`

### Common Patterns
- Strategy pattern: each mode is a pluggable strategy class
- Registry auto-discovery: `UserModeRegistry` scans this directory for `BaseUserModeStrategy` subclasses
- Two-level fetching: metadata items → expanded aweme lists (mix/music strategies)
- Page normalization: `_normalize_page_data()` handles both `items` and `aweme_list` API response formats

## Dependencies

### Internal
- `core/downloader_base.py` — `DownloadResult` for return values
- `core/user_downloader.py` — `UserDownloader` reference (TYPE_CHECKING only)
- `utils/logger.py` — logging

<!-- MANUAL: -->
