<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-03-27 | Updated: 2026-03-27 -->

# control

## Purpose
Concurrency and reliability primitives — rate limiting, retry with backoff, and async queue-based worker pool for parallel downloads.

## Key Files

| File | Description |
|------|-------------|
| `__init__.py` | Exports `RateLimiter`, `RetryHandler`, `QueueManager` |
| `rate_limiter.py` | Token-bucket rate limiter with configurable requests/second |
| `retry_handler.py` | Exponential backoff retry with max attempts |
| `queue_manager.py` | Async queue with configurable worker count for concurrent downloads |

## For AI Agents

### Working In This Directory
- All three classes are instantiated in `cli/main.py` per download session
- `RateLimiter.acquire()` is awaited before every API call in strategies and downloaders
- `RetryHandler` wraps download operations with configurable `max_retries`
- `QueueManager` manages the worker pool (`max_workers` from `thread` config)

### Testing Requirements
- Tests: `tests/test_rate_limiter.py`, `tests/test_retry_handler.py`

### Common Patterns
- All classes are async-first (use `asyncio` primitives)
- Config values come from `ConfigLoader`: `rate_limit`, `retry_times`, `thread`

## Dependencies

### Internal
- None — self-contained async primitives

### External
- `asyncio` (stdlib)

<!-- MANUAL: -->
