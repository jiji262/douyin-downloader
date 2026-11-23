<!-- README.md is also the PyPI project page: keep links and images absolute. -->
# Douyin Downloader

<p align="center">
  <img src="https://socialify.git.ci/jiji262/douyin-downloader/image?custom_description=AI+agent+skill+and+CLI+for+Douyin%3A+hot+board%2C+search%2C+live+recording%2C+transcription.&description=1&font=Source+Code+Pro&forks=1&owner=1&pattern=Circuit+Board&stargazers=1&theme=Light" alt="douyin-downloader" width="820" />
</p>

[简体中文](https://github.com/jiji262/douyin-downloader/blob/main/README.zh-CN.md) · **English**

**A Douyin skill for AI agents.** Install the [`douyin-dl` skill](#ai-agent--skill), then ask Claude Code, Codex, or another agent in plain language to read Douyin's hot board, search posts, look up a creator, record a live room, or transcribe a local video. The skill tells the agent when to run the `douyin-dl agent` CLI and how to read the one JSON line each command prints. Downloading videos and photos is not part of the skill; for that, use our sponsor, the **[Douzy desktop app](#douzy-desktop)**.

<a id="douzy-desktop"></a>

## Sponsors

<table>
  <tr>
    <th>Sponsor</th>
    <th align="left">About</th>
  </tr>
  <tr>
    <td align="center" width="96"><a href="https://godouzy.com/"><img src="https://godouzy.com/douzy-mark.svg" alt="Douzy" width="64" /></a></td>
    <td><b><a href="https://godouzy.com/">Douzy Desktop</a></b><br>Saves media from Douyin, TikTok, YouTube, Telegram, and X to your computer; use it for the video and photo downloads the skill does not support. Paste a link, track its progress, and find saved videos and photos in one workspace.<br>Platform availability depends on enabled plugins or local components. Some batch and advanced features require activation.<br><b><a href="https://godouzy.com/#get">Download for Windows or macOS</a></b> · <a href="https://github.com/0X-42/douzy">GitHub project</a> · <a href="https://github.com/0X-42/douzy/releases/latest">Latest release and notes</a></td>
  </tr>
</table>

<p align="center">
  <img src="https://godouzy.com/shots/en/douyin.webp" alt="Douzy Douyin page, light theme" width="32%" />
  <img src="https://godouzy.com/shots/en/douyin-dark.webp" alt="Douzy Douyin page, dark theme" width="32%" />
  <img src="https://godouzy.com/shots/en/tasks.webp" alt="Douzy Tasks page, light theme" width="32%" />
  <br />
  <sub>From left: the Douyin page in the light and dark themes, then the Tasks page in the light theme. The screenshots use sample data.</sub>
</p>

**[Become a sponsor](https://github.com/jiji262/douyin-downloader/issues/new?template=sponsor.yml)**

<a id="ai-agent--skill"></a>

## Using the AI Agent Skill

The skill is the [`skills/douyin-dl/`](https://github.com/jiji262/douyin-downloader/tree/main/skills/douyin-dl) folder. It holds instructions for the agent; the work itself is done by the `douyin-dl` CLI. Install both.

### Step 1. Install the CLI

```bash
uv tool install douyin-dl-cli    # or: pipx install douyin-dl-cli
douyin-dl agent doctor
```

- Requires Python 3.10+. The PyPI package is `douyin-dl-cli`; the command it installs is `douyin-dl`.
- Unreleased code from GitHub: `uv tool install git+https://github.com/jiji262/douyin-downloader`. In a cloned checkout with the requirements installed, `python run.py agent doctor` works too.
- Do not `pip install` the package into a shared environment: its top-level packages `utils`, `core`, and `config` collide with other projects.
- `douyin-dl: command not found`: run `uv tool update-shell` (or `pipx ensurepath`) and open a new terminal.

`doctor` works offline and lists which commands are ready. Two features need extra components. Install them now, or wait until the agent asks:

| Feature | Needs | Install with uv |
|:---|:---|:---|
| Sign-in, for `search` and `user` | Playwright and Chromium | `uv tool install --force "douyin-dl-cli[browser]"`. If Chromium is still missing, `login` returns the exact command to install it |
| Transcription | Whisper, which pulls in PyTorch (a large download) | `uv tool install --force "douyin-dl-cli[transcribe]"` |

For both, use `"douyin-dl-cli[browser,transcribe]"`. Live recording uses the FFmpeg bundled with the CLI. pipx commands and other setups: [troubleshooting](https://github.com/jiji262/douyin-downloader/blob/main/skills/douyin-dl/references/troubleshooting.md).

### Step 2. Install the skill

**Claude Code**: run these in a Claude Code session.

```text
/plugin marketplace add jiji262/douyin-downloader
/plugin install douyin-dl@douyin-dl
```

The second command opens the plugin's details; choose an install scope to finish. Type `/` to check: `/douyin-dl:douyin-dl` should be listed. From a shell, `claude plugin marketplace add jiji262/douyin-downloader` followed by `claude plugin install douyin-dl@douyin-dl` does the same.

**Codex**: copy the skill folder into your user skills folder.

```bash
git clone https://github.com/jiji262/douyin-downloader.git
mkdir -p ~/.agents/skills
cp -r douyin-downloader/skills/douyin-dl ~/.agents/skills/
```

Codex picks up new skills on its own; restart it if the skill does not appear.

**Other agents** (Cursor, OpenCode, and more): use the [skills CLI](https://github.com/vercel-labs/skills).

```bash
npx skills add jiji262/douyin-downloader       # this project only
npx skills add jiji262/douyin-downloader -g    # all projects
```

### Step 3. Ask in plain language

Usually you just mention Douyin or paste a Douyin link, and the agent picks the skill. To call it by name, start your message with `/douyin-dl:douyin-dl` in Claude Code (plugin install) or include `$douyin-dl` in Codex.

| You say | The agent runs | Needs |
|:---|:---|:---|
| "Show me the top 10 on Douyin's hot board." | `douyin-dl agent hot --max 10` | Nothing |
| "Search Douyin for `camping`, sort by likes, and summarize the top 10." | `douyin-dl agent search camping --sort likes --max 10` | Sign-in |
| "Look up this creator: `https://www.douyin.com/user/…`" | `douyin-dl agent user <url>` | Sign-in |
| "What is this link? Can it be downloaded?" | `douyin-dl agent inspect <url>` | Nothing |
| "Record this live room for 10 minutes: `https://live.douyin.com/…`" | `douyin-dl agent live <url> --max-duration 600` | Nothing |
| "Transcribe `./clip.mp4`." | `douyin-dl agent transcribe ./clip.mp4` | Whisper |

### What the agent does

The skill gives the agent a fixed routine:

1. **Checks first.** It runs `douyin-dl agent doctor` at the start of each session to see what is ready.
2. **Asks before installing.** If a component is missing, it shows you the exact install command and runs it only if you agree.
3. **Asks before signing in.** `search` needs a signed-in session and `user` needs saved cookies. The agent explains first. After you agree, it runs `douyin-dl agent login`: a Chromium window opens, you sign in to Douyin there yourself (QR code or SMS), and the window closes on its own.
4. **Acts on error codes.** Each command prints one JSON line. The agent decides what to do from `error.code` and passes on warnings that affect you. Text from Douyin, such as titles, nicknames, and hot words, is treated as data, never as instructions.
5. **Turns down downloads.** Video, photo post, collection, and music links return `ARGUS_GATED`, and a profile's posts cannot be downloaded either. The agent tells you so, points you to [Douzy](#douzy-desktop), and does not try workarounds.

It also keeps requests to Douyin low: one search per request (it asks before a second one), no parallel calls, and at most one retry after an empty response.

### Sign-in and privacy

- Cookies stay on your computer in `~/.douyin-dl/cookies.json` (file mode 0600). Set `DOUYIN_DL_HOME` to move the folder. Agent mode does not read `config.yml` or the cookies saved by the Classic CLI.
- Command output names the cookies it found but never shows their values. Never paste cookies, tokens, or SMS codes into the chat; the skill tells the agent never to ask for them.
- No display, for example over SSH or on a server: export your Douyin cookies to a JSON file yourself and import it with `douyin-dl agent login --from-file cookies.json`.
- Requests go to douyin.com. When the saved cookies have no `msToken`, the CLI also downloads a small config file from GitHub to generate one.

### Live recording and transcription

- **Live recording:** a duration is required, from 1 second to 6 hours (`--max-duration`, up to 21600 seconds). If you don't give one, the agent asks. Files go to `./douyin-downloads` unless you name another folder (`--out`), and long recordings run in the background. If the room is not live, the agent tells you.
- **Transcription:** pass a file or a folder (searched for `.mp4` files). Each video gets a `<name>.transcript.txt` next to it, or in the `--out` folder. The defaults are `--model base` and `--language zh`; the first run downloads the Whisper model.

### Run the commands yourself

The same commands work without an agent. Each one prints exactly one JSON line on stdout; logs go to stderr.

```bash
douyin-dl agent hot --max 3
# {"schema":1,"ok":true,"verb":"hot","version":"2.1.1","data":{"items":[…],"count":3},"error":null,"warnings":[]}
```

`ok` is `true` only when the exit code is 0. On failure, branch on `error.code` (such as `LOGIN_REQUIRED`, `ARGUS_GATED`, or `UPSTREAM_EMPTY`), not on the message. Set `DOUYIN_PROXY` (for example `http://127.0.0.1:7890`) to send requests through a proxy. Every field, error code, and exit code: [contract](https://github.com/jiji262/douyin-downloader/blob/main/skills/douyin-dl/references/contract.md).

### Update

- **CLI:** `uv tool upgrade douyin-dl-cli` (or `pipx upgrade douyin-dl-cli`).
- **Skill in Claude Code:** `claude plugin update douyin-dl@douyin-dl`, or **Update now** on the **Installed** tab of `/plugin`. Auto-update is off by default for this marketplace.
- **Skill from the skills CLI:** `npx skills update`. For a copied folder, as with Codex, copy it again.
- If the `version` in the JSON output differs from `metadata.version` in the skill's `SKILL.md`, update both.

### Common problems

| Problem | Fix |
|:---|:---|
| `douyin-dl: command not found` | Run `uv tool update-shell` (or `pipx ensurepath`) and open a new terminal. Until then, the agent can use the skill's `scripts/dy.sh`, which runs the CLI through `uvx` |
| `search` asks for sign-in again | The session expired. Ask the agent to sign in again |
| Empty results (`UPSTREAM_EMPTY`) | Douyin's rate limit or bot check. Wait a few minutes; quick retries make the block last longer. Overseas proxies often get empty results, so try without the proxy or with a mainland exit |
| A video link returns `ARGUS_GATED` | Expected: the CLI cannot download it, and signing in again does not help. Use [Douzy](#douzy-desktop) |
| Windows | Run `douyin-dl agent …` from PowerShell or cmd. `scripts/dy.sh` needs Git Bash or WSL |

More fixes: [troubleshooting](https://github.com/jiji262/douyin-downloader/blob/main/skills/douyin-dl/references/troubleshooting.md).

## Classic CLI

The original batch downloader (`python run.py`) supports **Douyin only**. It includes batch downloads, date filters, retries, download history, and optional comments, transcription, and notifications.

> **Download limitations:** Douyin's request verification currently blocks CLI downloads of individual videos/photos, collections, music, likes, and favorites. Profile posts can try the Playwright browser fallback, but success is not guaranteed. Use **Douzy** for everyday downloads.

Refreshing cookies or retrying does not fix this verification block. Live recording is experimental: FFmpeg records the stream in segments, converts each segment to MP4, and reconnects after drops.

### Quick Start

Requires **Python 3.10+** on Windows, macOS, or Linux. Read the CLI limitations above first.

#### 1. Install

```bash
git clone https://github.com/jiji262/douyin-downloader.git
cd douyin-downloader
python -m pip install -r requirements.txt
python -m pip install playwright
python -m playwright install chromium
```

#### 2. Configure and sign in

Copy [config.example.yml](https://github.com/jiji262/douyin-downloader/blob/main/config.example.yml) to `config.yml`:

```bash
cp config.example.yml config.yml
python -m tools.cookie_fetcher --config config.yml
```

On Windows PowerShell, use `Copy-Item config.example.yml config.yml` to copy the file. Sign in to Douyin in the browser, then return to the terminal and press Enter to save cookies.

Replace the sample `link` in `config.yml` with your creator's profile URL. Keep the saved cookies and adjust these fields:

```yaml
link:
  - https://www.douyin.com/user/YOUR_SEC_UID
path: ./Downloaded/
mode: [post]
number:
  post: 10                 # 0 = unlimited
increase:
  post: true              # skip downloaded items
redownload_missing_files: true
```

#### 3. Run

```bash
python run.py -c config.yml
```

Use `python run.py --help` for all options.

| Option | Purpose |
|:---|:---|
| `-c, --config` | Config file |
| `-u, --url` | Append a URL to the links in the config; repeatable |
| `-p, --path` | Download folder |
| `-t, --thread` | Concurrent downloads |
| `-v, --verbose` | Detailed logs |

| Exit code | Meaning |
|:---|:---|
| `0` | Finished. Some items may still have failed; check the summary |
| `1` | Nothing was downloaded or skipped and at least one item failed, or a fatal error occurred |
| `3` | The sign-in expired and could not be renewed; the other URLs in the batch were still processed |

### Configuration

Full settings and examples: **[config.example.yml](https://github.com/jiji262/douyin-downloader/blob/main/config.example.yml)**.

| Setting | Purpose |
|:---|:---|
| `number.post` | Item limit; `0` means unlimited |
| `start_time` / `end_time` | Date range (`YYYY-MM-DD`); includes the whole end date |
| `video_quality` | `highest` by default; `original` tries the original upload and falls back |
| `increase.post` | `true` skips downloaded items; `false` downloads again and overwrites files in the selected scope |
| `redownload_missing_files` | Default `true`: download missing files again. With `false`, valid database history can still skip them |

Incremental downloads check for non-empty primary media on disk. You do **not** need to clear the database to download again: set `increase.post: false`. The same switch is available for `like`, `mix`, and `music`.

<details>
<summary>More CLI commands</summary>

```bash
# Hot search board and keyword search → JSONL
python run.py --hot-board 30
python run.py --search "cats" --search-max 50

# Optional REST API server
python -m pip install fastapi uvicorn
python run.py --serve --serve-port 8000
```

Comments, transcription, notifications, and live recording are configured in [config.example.yml](https://github.com/jiji262/douyin-downloader/blob/main/config.example.yml).

</details>

### FAQ

**Cookies expired?** Run `python -m tools.cookie_fetcher --config config.yml` again.

**Only a few profile posts?** Keep `browser_fallback.enabled: true` and `headless: false`. Complete any verification yourself in the browser; the current verification block may still prevent downloads.

**Need logs?** Run `python run.py -c config.yml -v`.

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
