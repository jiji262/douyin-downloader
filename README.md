# Douyin Downloader V2.0

<p align="center">
  <img src="https://socialify.git.ci/jiji262/douyin-downloader/image?custom_description=Douyin+batch+download+tool%2C+remove+watermarks%2C+support+batch+download+of+videos%2C+gallery%2C+and+author+homepages.&description=1&font=Source+Code+Pro&forks=1&owner=1&pattern=Circuit+Board&stargazers=1&theme=Light" alt="douyin-downloader" width="820" />
</p>

<p align="center">
    <a href="https://linux.do" alt="LINUX DO">
        <img
            src="https://img.shields.io/badge/LINUX-DO-FFB003.svg?logo=data:image/svg%2bxml;base64,DQo8c3ZnIHhtbG5zPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwL3N2ZyIgd2lkdGg9IjEwMCIgaGVpZ2h0PSIxMDAiPjxwYXRoIGQ9Ik00Ni44Mi0uMDU1aDYuMjVxMjMuOTY5IDIuMDYyIDM4IDIxLjQyNmM1LjI1OCA3LjY3NiA4LjIxNSAxNi4xNTYgOC44NzUgMjUuNDV2Ni4yNXEtMi4wNjQgMjMuOTY4LTIxLjQzIDM4LTExLjUxMiA3Ljg4NS0yNS40NDUgOC44NzRoLTYuMjVxLTIzLjk3LTIuMDY0LTM4LjAwNC0yMS40M1EuOTcxIDY3LjA1Ni0uMDU0IDUzLjE4di02LjQ3M0MxLjM2MiAzMC43ODEgOC41MDMgMTguMTQ4IDIxLjM3IDguODE3IDI5LjA0NyAzLjU2MiAzNy41MjcuNjA0IDQ2LjgyMS0uMDU2IiBzdHlsZT0ic3Ryb2tlOm5vbmU7ZmlsbC1ydWxlOmV2ZW5vZGQ7ZmlsbDojZWNlY2VjO2ZpbGwtb3BhY2l0eToxIi8+PHBhdGggZD0iTTQ3LjI2NiAyLjk1N3EyMi41My0uNjUgMzcuNzc3IDE1LjczOGE0OS43IDQ5LjcgMCAwIDEgNi44NjcgMTAuMTU3cS00MS45NjQuMjIyLTgzLjkzIDAgOS43NS0xOC42MTYgMzAuMDI0LTI0LjM4N2E2MSA2MSAwIDAgMSA5LjI2Mi0xLjUwOCIgc3R5bGU9InN0cm9rZTpub25lO2ZpbGwtcnVsZTpldmVub2RkO2ZpbGw6IzE5MTkxOTtmaWxsLW9wYWNpdHk6MSIvPjxwYXRoIGQ9Ik03Ljk4IDcwLjkyNmMyNy45NzctLjAzNSA1NS45NTQgMCA4My45My4xMTNRODMuNDI2IDg3LjQ3MyA2Ni4xMyA5NC4wODZxLTE4LjgxIDYuNTQ0LTM2LjgzMi0xLjg5OC0xNC4yMDMtNy4wOS0yMS4zMTctMjEuMjYyIiBzdHlsZT0ic3Ryb2tlOm5vbmU7ZmlsbC1ydWxlOmV2ZW5vZGQ7ZmlsbDojZjlhZjAwO2ZpbGwtb3BhY2l0eToxIi8+PC9zdmc+" /></a>
</p>
中文文档 (Chinese): [README.zh-CN.md](./README.zh-CN.md)


A practical Douyin downloader supporting videos, image-notes, collections, music, favorites collections, and profile batch downloads, with progress display, retries, SQLite deduplication, download integrity checks, and browser fallback support.

> This document targets **V2.0 (`main` branch)**.  
> For the legacy version, switch to **V1.0**: `git fetch --all && git switch V1.0`

## Feature Overview

### Supported

| Feature | Description |
|---------|-------------|
| Single video download | `/video/{aweme_id}` |
| Single image-note download | `/note/{note_id}` and `/gallery/{note_id}` |
| Single collection download | `/collection/{mix_id}` and `/mix/{mix_id}` |
| Single music download | `/music/{music_id}` (prefers direct audio, fallback to first related aweme) |
| Short link parsing | `https://v.douyin.com/...` |
| Profile batch download | `/user/{sec_uid}` + `mode: [post, like, mix, music]` |
| Logged-in favorites collections | `/user/self?showTab=favorite_collection` + `mode: [collect, collectmix]` |
| No-watermark preferred | Automatically selects watermark-free video source |
| Extra assets | Cover, music, avatar, JSON metadata |
| Video transcription | Optional, using OpenAI Transcriptions API |
| Concurrent downloads | Configurable concurrency, default 5 |
| Retry with backoff | Exponential backoff (1s, 2s, 5s) |
| Rate limiting | Default 2 req/s |
| SQLite deduplication | Database + local file dual dedup |
| Incremental downloads | `increase.post/like/mix/music` |
| Time filters | `start_time` / `end_time` |
| Browser fallback | Launches browser when pagination is blocked, manual CAPTCHA supported |
| Download integrity check | Content-Length validation, auto-cleanup of incomplete files |
| Progress display | Rich progress bars, supports `progress.quiet_logs` quiet mode |
| Docker deployment | Dockerfile included |
| CI/CD | GitHub Actions for testing and linting |

### Current Limitations

- Browser fallback is fully validated for `post`; `like/mix/music` currently relies on API pagination
- `number.allmix` / `increase.allmix` are retained as compatibility aliases and normalized to `mix`
- `collect` / `collectmix` currently work for the account represented by the logged-in cookies only
- `collect` / `collectmix` must be used alone and cannot be combined with `post` / `like` / `mix` / `music`
- `increase` currently applies to `post` / `like` / `mix` / `music`; favorites collection modes do not support incremental stop

## Quick Start

### 1) Requirements

- Python 3.8+
- macOS / Linux / Windows

### 2) Install dependencies

```bash
pip install -r requirements.txt
```

For browser fallback and automatic cookie capture:

```bash
pip install playwright
python -m playwright install chromium
```

### 3) Copy config file

```bash
cp config.example.yml config.yml
```

### 4) Get cookies (recommended: automatic)

```bash
python -m tools.cookie_fetcher --config config.yml
```

After logging into Douyin, return to the terminal and press Enter. Cookies will be written to your config automatically.

### 5) Docker deployment (optional)

```bash
docker build -t douyin-downloader .
docker run -v $(pwd)/config.yml:/app/config.yml -v $(pwd)/Downloaded:/app/Downloaded douyin-downloader
```

## Minimal Working Config

```yaml
link:
  - https://www.douyin.com/user/MS4wLjABAAAAxxxx

path: ./Downloaded/
mode:
  - post

number:
  post: 0
  collect: 0
  collectmix: 0

thread: 5
retry_times: 3
proxy: ""
database: true
database_path: dy_downloader.db

progress:
  quiet_logs: true

cookies:
  msToken: ""
  ttwid: YOUR_TTWID
  odin_tt: YOUR_ODIN_TT
  passport_csrf_token: YOUR_CSRF_TOKEN
  sid_guard: ""

browser_fallback:
  enabled: true
  headless: false
  max_scrolls: 240
  idle_rounds: 8
  wait_timeout_seconds: 600

transcript:
  enabled: false
  model: gpt-4o-mini-transcribe
  output_dir: ""
  response_formats: ["txt", "json"]
  api_url: https://api.openai.com/v1/audio/transcriptions
  api_key_env: OPENAI_API_KEY
  api_key: ""
```

## Usage

### Run with a config file

```bash
python run.py -c config.yml
```

### Append CLI arguments

```bash
python run.py -c config.yml \
  -u "https://www.douyin.com/video/7604129988555574538" \
  -t 8 \
  -p ./Downloaded
```

### Arguments

| Argument | Description |
|----------|-------------|
| `-u, --url` | Append download link(s), can be repeated |
| `-c, --config` | Specify config file (default: `config.yml`) |
| `-p, --path` | Specify download directory |
| `-t, --thread` | Specify concurrency |
| `--show-warnings` | Show warning/error logs |
| `-v, --verbose` | Show info/warning/error logs |
| `--version` | Show version number |

## Typical Scenarios

### Download one video

```yaml
link:
  - https://www.douyin.com/video/7604129988555574538
```

### Download one image-note

```yaml
link:
  - https://www.douyin.com/note/7341234567890123456
```

### Download a collection

```yaml
link:
  - https://www.douyin.com/collection/7341234567890123456
```

### Download a music track

```yaml
link:
  - https://www.douyin.com/music/7341234567890123456
```

### Batch download a creator's posts

```yaml
link:
  - https://www.douyin.com/user/MS4wLjABAAAAxxxx
mode:
  - post
number:
  post: 50
```

### Batch download a creator's liked posts

```yaml
link:
  - https://www.douyin.com/user/MS4wLjABAAAAxxxx
mode:
  - like
number:
  like: 0    # 0 means download all
```

### Download multiple modes at once

```yaml
link:
  - https://www.douyin.com/user/MS4wLjABAAAAxxxx
mode:
  - post
  - like
  - mix
  - music
```

Cross-mode deduplication: the same aweme_id won't be downloaded twice across different modes.

### Download logged-in favorites collection items

```yaml
link:
  - https://www.douyin.com/user/self?showTab=favorite_collection
mode:
  - collect
number:
  collect: 0
```

### Download logged-in collected mixes

```yaml
link:
  - https://www.douyin.com/user/self?showTab=favorite_collection
mode:
  - collectmix
number:
  collectmix: 0
```

### Incremental download (only new items)

```yaml
increase:
  post: true
database: true    # incremental mode requires database
```

### Full crawl (no item limit)

```yaml
number:
  post: 0
```

## Optional Feature: Video Transcription (`transcript`)

Current behavior applies to **video items only** (image-note items do not generate transcripts).

### 1) Enable in config

```yaml
transcript:
  enabled: true
  model: gpt-4o-mini-transcribe
  output_dir: ""        # empty: same folder as video; non-empty: mirrored to target dir
  response_formats:
    - txt
    - json
  api_key_env: OPENAI_API_KEY
  api_key: ""           # can be set directly, or via environment variable
```

Recommended to provide key through environment variable:

```bash
export OPENAI_API_KEY="sk-xxxx"
```

### 2) Output files

When enabled, it generates:

- `xxx.transcript.txt`
- `xxx.transcript.json`

If `database: true`, job status is also recorded in SQLite table `transcript_job` (`success/failed/skipped`).

## Testing

Recommended:

```bash
python3 -m pytest -q
```

Plain `pytest` is also supported now:

```bash
pytest -q
```

## Key Config Fields

| Field | Description |
|-------|-------------|
| `mode` | Supports `post`/`like`/`mix`/`music`; logged-in favorites mode additionally supports standalone `collect`/`collectmix` |
| `number.post/like/mix/music/collect/collectmix` | Per-mode download limit, 0 = unlimited |
| `increase.post/like/mix/music` | Per-mode incremental toggle |
| `start_time` / `end_time` | Time filter (format: `YYYY-MM-DD`) |
| `folderstyle` | Create per-item subdirectories |
| `browser_fallback.*` | Browser fallback for `post` when pagination is restricted |
| `progress.quiet_logs` | Quiet logs during progress stage |
| `transcript.*` | Optional transcription after video download |
| `proxy` | HTTP/HTTPS proxy for API requests and media downloads, e.g. `http://127.0.0.1:7890` |
| `database` | Enable SQLite deduplication and history |
| `database_path` | SQLite path, default is `dy_downloader.db` in the current working directory |
| `thread` | Concurrent download count |
| `retry_times` | Retry count on failure |

## Output Structure

Default with `folderstyle: true` and `database_path: dy_downloader.db`:

```text
workspace/
├── config.yml
├── dy_downloader.db          # default location when database: true
└── Downloaded/
    ├── download_manifest.jsonl
    └── AuthorName/
        ├── post/
        │   └── 2024-02-07_Title_aweme_id/
        │       ├── ...mp4
        │       ├── ..._cover.jpg
        │       ├── ..._music.mp3
        │       ├── ..._data.json
        │       ├── ..._avatar.jpg
        │       ├── ...transcript.txt
        │       └── ...transcript.json
        ├── like/
        │   └── ...
        ├── mix/
        │   └── ...
        ├── music/
        │   └── ...
        ├── collect/
        │   └── ...
        └── collectmix/
            └── ...
```

## Re-downloading Content

The program uses a **database record + local file** dual check to decide whether to skip already-downloaded content. To force re-download, you need to clean up accordingly:

### Re-download a specific item

```bash
# Delete local files (folder name contains the aweme_id)
rm -rf Downloaded/AuthorName/post/*_<aweme_id>/

# Delete database record
sqlite3 dy_downloader.db "DELETE FROM aweme WHERE aweme_id = '<aweme_id>';"
```

### Re-download all items from a specific author

```bash
rm -rf Downloaded/AuthorName/
sqlite3 dy_downloader.db "DELETE FROM aweme WHERE author_name = 'AuthorName';"
```

### Full reset (re-download everything)

```bash
rm -rf Downloaded/
rm dy_downloader.db
```

> **Note:** Deleting only the database but keeping files will NOT trigger re-download — the program scans local filenames for aweme_id to detect existing downloads. Deleting only files but keeping the database WILL trigger re-download (the program treats "in DB but missing locally" as needing retry).

## FAQ

### 1) Why do I only get around 20 posts?

This is a common pagination risk-control behavior. Make sure:

- `browser_fallback.enabled: true`
- `browser_fallback.headless: false`
- complete verification manually in the browser popup, and do not close it too early

### 2) Why is the progress output noisy/repeated?

By default, `progress.quiet_logs: true` suppresses logs during progress stage.  
Use `--show-warnings` or `-v` temporarily when debugging.

### 3) What if cookies are expired?

Run:

```bash
python -m tools.cookie_fetcher --config config.yml
```

### 4) Why are transcript files not generated?

Check in order:

- whether `transcript.enabled` is `true`
- whether downloaded items are videos (image-notes are not transcribed)
- whether `OPENAI_API_KEY` (or `transcript.api_key`) is valid
- whether `response_formats` includes `txt` or `json`

### 5) How to view download history?

```bash
sqlite3 dy_downloader.db "SELECT aweme_id, title, author_name, datetime(download_time, 'unixepoch', 'localtime') FROM aweme ORDER BY download_time DESC LIMIT 20;"
```

## Legacy Version (V1.0)

If you prefer the legacy script style (V1.0):

```bash
git fetch --all
git switch V1.0
```

## Community Group

<img src="./img/fuye.jpg" alt="qun" width="360" />

## Disclaimer

This project is for technical research, learning, and personal data management only. Please use it legally and responsibly:

- Do not use it to infringe others' privacy, copyright, or other legal rights
- Do not use it for any illegal purpose
- Users are solely responsible for all risks and liabilities arising from usage
- If platform policies or interfaces change and features break, this is a normal technical risk

By continuing to use this project, you acknowledge and accept the statements above.

## License

This project is licensed under the MIT License. See [LICENSE](./LICENSE) for details.
