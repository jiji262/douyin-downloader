# Douyin Downloader V2.0

![douyin-downloader](https://socialify.git.ci/jiji262/douyin-downloader/image?custom_description=%E6%8A%96%E9%9F%B3%E6%89%B9%E9%87%8F%E4%B8%8B%E8%BD%BD%E5%B7%A5%E5%85%B7%EF%BC%8C%E5%8E%BB%E6%B0%B4%E5%8D%B0%EF%BC%8C%E6%94%AF%E6%8C%81%E8%A7%86%E9%A2%91%E3%80%81%E5%9B%BE%E9%9B%86%E3%80%81%E4%BD%9C%E8%80%85%E4%B8%BB%E9%A1%B5%E6%89%B9%E9%87%8F%E4%B8%8B%E8%BD%BD%E3%80%82&description=1&font=Jost&forks=1&logo=https%3A%2F%2Fraw.githubusercontent.com%2Fjiji262%2Fdouyin-downloader%2Frefs%2Fheads%2FV1.0%2Fimg%2Flogo.png&name=1&owner=1&pattern=Circuit+Board&pulls=1&stargazers=1&theme=Light)

中文文档 (Chinese): [README.zh-CN.md](./README.zh-CN.md)

A practical Douyin downloader for both single-item and profile batch downloads, with progress display, retries, SQLite deduplication, and browser fallback support.

> This document targets **V2.0 (`main` branch)**.  
> For the legacy version, switch to **V1.0**: `git fetch --all && git switch V1.0`

## Version Update Notice

> This project has been significantly upgraded to **V2.0**. Ongoing feature development and fixes are mainly on the `main` branch.  
> **V1.0 is still available**, but maintained with low frequency.

## Feature Overview

### Supported

- Single video download: `/video/{aweme_id}`
- Single image-note download: `/note/{note_id}`
- Automatic short-link parsing: `https://v.douyin.com/...`
- Profile batch download: `/user/{sec_uid}` + `mode: [post]`
- No-watermark preferred, plus cover/music/avatar/JSON metadata downloads
- Optional video transcription (`transcript`, using OpenAI Transcriptions API)
- Concurrent downloads, retry logic, and rate limiting
- SQLite deduplication and incremental download (`increase.post`)
- Time filters (`start_time` / `end_time`, currently for `post`)
- Browser fallback when pagination is blocked (manual verification supported)
- Progress bar display (supports `progress.quiet_logs` quiet mode)

### Not Yet Implemented (do not treat as supported)

- `mode: like` liked-content download
- `mode: mix` collection download
- `number.like` / `number.mix` / `increase.like` / `increase.mix`
- `collection/mix` links currently have no downloader (explicitly reported as unsupported)

## Quick Start

### 1) Requirements

- Python 3.8+
- macOS / Linux / Windows

### 2) Install dependencies

```bash
pip install -r requirements.txt
```

### 3) Copy config file

```bash
cp config.example.yml config.yml
```

### 4) Get cookies (recommended: automatic)

```bash
pip install playwright
python -m playwright install chromium
python -m tools.cookie_fetcher --config config.yml
```

After logging into Douyin, return to the terminal and press Enter. Cookies will be written to your config automatically.

## Minimal Working Config

```yaml
link:
  - https://www.douyin.com/user/MS4wLjABAAAAxxxx

path: ./Downloaded/
mode:
  - post

number:
  post: 0

thread: 5
retry_times: 3
database: true

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

Arguments:

- `-u, --url`: append download link(s), can be repeated
- `-c, --config`: specify config file
- `-p, --path`: specify download directory
- `-t, --thread`: specify concurrency
- `--show-warnings`: show warning/error logs
- `-v, --verbose`: show info/warning/error logs

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

### Batch download a creator profile

```yaml
link:
  - https://www.douyin.com/user/MS4wLjABAAAAxxxx
mode:
  - post
number:
  post: 50
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

## Key Config Fields (based on current effective code paths)

- `mode`: currently only `post` is effective
- `number`: currently only `number.post` is effective
- `increase`: currently only `increase.post` is effective
- `start_time/end_time`: currently used for `post` time filtering
- `folderstyle`: controls whether to create per-item subdirectories
- `browser_fallback.*`: used for `post` when pagination is restricted
- `progress.quiet_logs`: quiet logs during progress stage
- `transcript.*`: optional transcription after video download
- `auto_cookie`: reserved field, not used in main flow currently

## Output Structure

Default with `folderstyle: true`:

```text
Downloaded/
├── download_manifest.jsonl
└── AuthorName/
    └── post/
        └── 2024-02-07_Title_aweme_id/
            ├── ...mp4
            ├── ..._cover.jpg
            ├── ..._music.mp3
            ├── ..._data.json
            ├── ..._avatar.jpg
            ├── ...transcript.txt      # transcript.enabled=true and includes txt
            └── ...transcript.json     # transcript.enabled=true and includes json
```

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

## Legacy Version (V1.0)

If you prefer the legacy script style (V1.0):

```bash
git fetch --all
git switch V1.0
```

## Community Group

![qun](./img/fuye.jpg)

## Disclaimer

This project is for technical research, learning, and personal data management only. Please use it legally and responsibly:

- Do not use it to infringe others' privacy, copyright, or other legal rights
- Do not use it for any illegal purpose
- Users are solely responsible for all risks and liabilities arising from usage
- If platform policies or interfaces change and features break, this is a normal technical risk

By continuing to use this project, you acknowledge and accept the statements above.
