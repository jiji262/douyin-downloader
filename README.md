<!-- README.md is also the PyPI project page: keep links and images absolute. -->
# Douyin Downloader

<p align="center">
  <img src="https://socialify.git.ci/jiji262/douyin-downloader/image?custom_description=Free+Douyin+video+downloader+with+CLI+and+AI+Agent+Skill%3A+batch+download%2C+hot+board%2C+search%2C+live+recording%2C+transcription.&description=1&font=Source+Code+Pro&forks=1&owner=1&pattern=Circuit+Board&stargazers=1&theme=Light" alt="douyin-downloader" width="820" />
</p>

[简体中文](https://github.com/jiji262/douyin-downloader/blob/main/README.zh-CN.md) · **English**

**Douyin Downloader** is a free, open-source tool for downloading Douyin (TikTok China) videos, photos, and media data. It provides both a powerful **CLI version** and an **AI Agent Skill version** to fit diverse personal and development workflows:

- 🚀 **CLI Version (Free Batch Downloader)**: Dedicated to **free batch downloading of Douyin videos and photos**. Supports creator profile downloads, single/batch post downloads, collections/mixes, favorites/likes, and music. Built-in incremental downloads (skips previously saved files), date range filtering, automatic failure retries, history deduplication, local Whisper subtitle transcription, and bounded live recording.
- 🤖 **AI Agent Skill Version (Agent-Driven)**: Built for AI agents including Claude Code, Codex, Cursor, and OpenCode. Enables agents to query Douyin's real-time hot board, search posts by keyword, look up creator statistics, record live streams, and transcribe local media files directly through natural language conversations.

<a id="douzy-desktop"></a>

## Sponsors

<table>
  <tr>
    <th>Sponsor</th>
    <th align="left">About</th>
  </tr>
  <tr>
    <td align="center" width="96"><a href="https://godouzy.com/"><img src="https://godouzy.com/douzy-mark.svg" alt="Douzy" width="64" /></a></td>
    <td><b><a href="https://godouzy.com/">Douzy Desktop</a></b><br>Saves media from Douyin, TikTok, YouTube, Telegram, and X to your computer. Paste a link, track progress, and organize saved media in one workspace.<br>Platform availability depends on enabled plugins or local components. Some batch and advanced features require activation.<br><b><a href="https://godouzy.com/#get">Download for Windows or macOS</a></b> · <a href="https://github.com/0X-42/douzy">GitHub project</a> · <a href="https://github.com/0X-42/douzy/releases/latest">Latest release and notes</a></td>
  </tr>
</table>

<table>
  <tr>
    <td width="33.3%" align="center">
      <img src="https://godouzy.com/shots/en/douyin.webp" alt="Douzy Douyin page, light theme" width="100%" /><br />
      <sub>Douyin page (light)</sub>
    </td>
    <td width="33.3%" align="center">
      <img src="https://godouzy.com/shots/en/douyin-dark.webp" alt="Douzy Douyin page, dark theme" width="100%" /><br />
      <sub>Douyin page (dark)</sub>
    </td>
    <td width="33.3%" align="center">
      <img src="https://godouzy.com/shots/en/tasks.webp" alt="Douzy Tasks page, light theme" width="100%" /><br />
      <sub>Tasks page (light)</sub>
    </td>
  </tr>
</table>

**[Become a sponsor](https://github.com/jiji262/douyin-downloader/issues/new?template=sponsor.yml)**

---

## Edition Overview

| Feature / Scenario | 🚀 CLI Version | 🤖 AI Agent Skill Version |
|:---|:---:|:---:|
| **Target Use Case** | Developers / Batch Archive / Automation | AI Agent Conversations / Agentic Workflows |
| **Douyin Video/Photo Batch Download** | ✅ **Free Supported** (Profile/Mix/Single) | ❌ Metadata query only (use CLI for download) |
| **Incremental Download** | ✅ Supported (Skip existing files by date/history) | - |
| **Real-time Hot Search Board** | ✅ `douyin-dl agent hot` | ✅ Natural language: "Show Douyin's hot board" |
| **Keyword Post Search** | ✅ `douyin-dl agent search` | ✅ Natural language: "Search camping and summarize" |
| **Creator Profile Lookup** | ✅ `douyin-dl agent user` | ✅ Natural language: "Look up creator profile" |
| **Live Stream Recording** | ✅ Supported (Segmented to MP4 via FFmpeg) | ✅ Natural language: "Record live room 10 mins" |
| **Local Subtitle Transcription** | ✅ Supported (Local Whisper model) | ✅ Natural language: "Transcribe local video" |
| **License & Cost** | **Completely Free / Open Source (MIT)** | **Completely Free / Open Source (MIT)** |

---

<a id="cli-version"></a>

## Using the CLI Version (Free Batch Downloader)

The CLI version is the core download engine of this project, offering automated batch downloads, configurable YAML rules, and rich options.

### 1. Installation

Requires **Python 3.10+** on Windows, macOS, or Linux.

**Recommended: Install via uv or pipx**
```bash
uv tool install douyin-dl-cli    # or: pipx install douyin-dl-cli
```

**Or clone from GitHub:**
```bash
git clone https://github.com/jiji262/douyin-downloader.git
cd douyin-downloader
python -m pip install -r requirements.txt
python -m pip install playwright
python -m playwright install chromium
```

### 2. Configure and Sign In

Copy [config.example.yml](https://github.com/jiji262/douyin-downloader/blob/main/config.example.yml) to `config.yml`, and fetch sign-in cookies:

```bash
cp config.example.yml config.yml
python -m tools.cookie_fetcher --config config.yml
```
*(On Windows PowerShell, use `Copy-Item config.example.yml config.yml`)*

Sign in to Douyin in the browser window, then return to the terminal and press Enter to save cookies.

### 3. Run Batch Downloads

Edit `config.yml` to specify your target creator profile URL or post URL:

```yaml
link:
  - https://www.douyin.com/user/YOUR_SEC_UID
path: ./Downloaded/
mode: [post]             # post(posts), like(likes), mix(collections), music(music)
number:
  post: 10               # 0 = unlimited
increase:
  post: true             # Incremental: automatically skip existing files on disk
redownload_missing_files: true
```

Run the downloader:
```bash
# Run with python
python run.py -c config.yml

# Or run with the globally installed command
douyin-dl -c config.yml -u "https://www.douyin.com/user/..."
```

| Option | Purpose |
|:---|:---|
| `-c, --config` | Path to YAML config file |
| `-u, --url` | Append a URL to the links in the config (repeatable) |
| `-p, --path` | Download folder destination |
| `-t, --thread` | Concurrent download threads |
| `-v, --verbose` | Detailed logs |

### 4. Configuration and Settings

Full settings and examples: **[config.example.yml](https://github.com/jiji262/douyin-downloader/blob/main/config.example.yml)**.

| Setting | Purpose |
|:---|:---|
| `number.post` | Item limit; `0` means unlimited |
| `start_time` / `end_time` | Date range (`YYYY-MM-DD`); includes the whole end date |
| `video_quality` | Video quality: `highest` (default) or `original` |
| `increase.post` | `true` skips downloaded items; `false` overwrites files in scope |
| `redownload_missing_files` | `true` (default): download missing files again; `false`: skip if in database |

> **Tip:** Incremental downloads check for non-empty primary media on disk. You do **not** need to clear the database to resume or backfill downloads. The same switch is available for `like`, `mix`, and `music`.

<details>
<summary>More CLI commands (Hot search, keyword search, REST server)</summary>

```bash
# Hot search board (top 30) -> JSONL
python run.py --hot-board 30

# Keyword search -> JSONL
python run.py --search "cats" --search-max 50

# Optional REST API server
python -m pip install fastapi uvicorn
python run.py --serve --serve-port 8000
```

Comments, transcription, notifications, and live recording can be configured in [config.example.yml](https://github.com/jiji262/douyin-downloader/blob/main/config.example.yml).
</details>

---

<a id="ai-agent--skill"></a>

## Using the AI Agent Skill

The skill is located in the [`skills/douyin-dl/`](https://github.com/jiji262/douyin-downloader/tree/main/skills/douyin-dl) directory. It provides structured instructions for agents; execution is powered by the underlying `douyin-dl` CLI.

### Step 1. Install the CLI

```bash
uv tool install douyin-dl-cli    # or: pipx install douyin-dl-cli
douyin-dl agent doctor
```

- Requires Python 3.10+. The PyPI package is `douyin-dl-cli`; the command is `douyin-dl`.
- `doctor` works offline and lists which verbs are ready in your environment.
- For search/user lookup or transcription, install optional dependencies:
  - Browser (for sign-in): `uv tool install --force "douyin-dl-cli[browser]"`
  - Transcription (Whisper): `uv tool install --force "douyin-dl-cli[transcribe]"`

### Step 2. Install the Skill

**Claude Code**: In a Claude Code session:
```text
/plugin marketplace add jiji262/douyin-downloader
/plugin install douyin-dl@douyin-dl
```

**Codex**: Copy the skill folder into your user skills directory:
```bash
git clone https://github.com/jiji262/douyin-downloader.git
mkdir -p ~/.agents/skills
cp -r douyin-downloader/skills/douyin-dl ~/.agents/skills/
```

**Other Agents (Cursor, OpenCode, etc.)**: Use the [skills CLI](https://github.com/vercel-labs/skills):
```bash
npx skills add jiji262/douyin-downloader       # this project only
npx skills add jiji262/douyin-downloader -g    # all projects
```

### Step 3. Ask in Plain Language

Once installed, mention Douyin or paste a Douyin link in your prompt:

| You say | The agent runs | Purpose |
|:---|:---|:---|
| "Show me the top 10 on Douyin's hot board." | `douyin-dl agent hot --max 10` | Hot search board |
| "Search Douyin for `camping`, sort by likes, and summarize the top 10." | `douyin-dl agent search camping --sort likes --max 10` | Keyword search (needs sign-in) |
| "Look up this creator: `https://www.douyin.com/user/…`" | `douyin-dl agent user <url>` | Creator profile data (needs cookies) |
| "What is this link? Can it be inspected?" | `douyin-dl agent inspect <url>` | URL classification |
| "Record this live room for 10 minutes: `https://live.douyin.com/…`" | `douyin-dl agent live <url> --max-duration 600` | Live stream recording |
| "Transcribe `./clip.mp4`." | `douyin-dl agent transcribe ./clip.mp4` | Local Whisper transcription |

> **Note on Downloads:**
> The Skill version is designed as a lightweight, safe metadata query channel (avoiding massive media transfers inside LLM chats). For downloading video or photo files in batch, use the **[CLI Version](#using-the-cli-version-free-batch-downloader)**.

### Run Without an Agent (JSON Output)

The same commands can be executed directly in your terminal, outputting one JSON line on stdout:

```bash
douyin-dl agent hot --max 3
# {"schema":1,"ok":true,"verb":"hot","version":"2.1.1","data":{"items":[...],"count":3},"error":null,"warnings":[]}
```

---

## Common Problems

| Problem | Fix |
|:---|:---|
| `douyin-dl: command not found` | Run `uv tool update-shell` (or `pipx ensurepath`) and open a new terminal window. |
| Cookies expired / login required | Run `python -m tools.cookie_fetcher --config config.yml` or `douyin-dl agent login` to sign in again. |
| Empty results (`UPSTREAM_EMPTY`) | Douyin rate limit or bot check. Wait a few minutes; avoid overseas proxies that trigger anti-bot blocks. |
| Only a few profile posts fetched | Ensure `browser_fallback.enabled: true` in config and solve the slider verification in the browser window. |
| Windows usage | Run `douyin-dl` directly from PowerShell or cmd; `scripts/dy.sh` requires Git Bash or WSL. |

More fixes: [troubleshooting guide](https://github.com/jiji262/douyin-downloader/blob/main/skills/douyin-dl/references/troubleshooting.md).

---

## Development

```bash
python -m pip install -e ".[dev]"
python -m pytest tests/
ruff check .
```

## Community & License

[QQ group](https://qm.qq.com/q/8wrCzYyLHa) · [LINUX DO](https://linux.do/) · [MIT License](https://github.com/jiji262/douyin-downloader/blob/main/LICENSE)

<img src="https://raw.githubusercontent.com/jiji262/douyin-downloader/main/img/qq-group.png" alt="Community QR code" width="200" />

For learning and personal data management. Respect copyright, privacy, and platform rules. You are responsible for how you use this tool; platform changes may affect availability.

## Star History

[View GitHub Star History](https://www.star-history.com/?repos=jiji262%2Fdouyin-downloader&type=date&legend=top-left)
