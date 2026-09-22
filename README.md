# Douyin Downloader

<p align="center">
  <img src="https://camo.githubusercontent.com/327409f4eab82516b28e9c67bd4831261917ee9a29ffd865a60996a9c53709cb/68747470733a2f2f736f6369616c6966792e6769742e63692f6a696a693236322f646f7579696e2d646f776e6c6f616465722f696d6167653f637573746f6d5f6465736372697074696f6e3d446f7579696e2b62617463682b646f776e6c6f61642b746f6f6c2532432b72656d6f76652b77617465726d61726b732532432b737570706f72742b62617463682b646f776e6c6f61642b6f662b766964656f732532432b67616c6c6572792532432b616e642b617574686f722b686f6d6570616765732e266465736372697074696f6e3d3126666f6e743d536f757263652b436f64652b50726f26666f726b733d31266f776e65723d31267061747465726e3d436972637569742b426f617264267374617267617a6572733d31267468656d653d4c69676874" alt="douyin-downloader" width="820" />
</p>

[简体中文](README.zh-CN.md) · **English**

Download Douyin videos and photos without watermarks. Use **Douzy** for a desktop interface, or the **Python CLI** for scripts.

## Douzy Desktop

A desktop app for **Douyin, TikTok, YouTube, Telegram, and X**. Paste links, track downloads, and browse your local archive.

**[Download for Windows / macOS](https://github.com/jiji262/douyin-downloader/releases)**

Platform features depend on enabled plugins or local components. Some batch and advanced features require activation.

| Platform | Content |
|:---|:---|
| Douyin | Videos, photos, profiles, collections; following, subscriptions, favorites, and likes |
| TikTok | Public videos, photos, and profiles |
| YouTube | Videos, Shorts, channels, and playlists; audio and subtitles |
| Telegram | Media from accessible chats, groups, and channels; requires setup and sign-in |
| X | Posts, profile media, your bookmarks and likes; requires account cookies |

| **Douyin** | **TikTok** | **YouTube** |
|:---:|:---:|:---:|
| [![Douzy Douyin download options](img/desktop/001.png)](img/desktop/001.png) | [![Douzy TikTok workspace](img/desktop/002.png)](img/desktop/002.png) | [![Douzy YouTube workspace](img/desktop/003.png)](img/desktop/003.png) |
| **Telegram setup** | **X** | **Platform switcher** |
| [![Douzy Telegram setup](img/desktop/004.png)](img/desktop/004.png) | [![Douzy X workspace](img/desktop/005.png)](img/desktop/005.png) | [![Douzy platform switcher](img/desktop/006.png)](img/desktop/006.png) |

_Screenshots: Douzy 0.11.6 on macOS. Telegram is shown before setup. Click an image to enlarge._

## CLI Status

The CLI supports **Douyin only**. It includes batch downloads, date filters, retries, download history, and optional comments, transcription, and notifications.

> **Download limitations:** Douyin's request verification currently blocks CLI downloads of individual videos/photos, collections, music, likes, and favorites. Profile posts can try the Playwright browser fallback, but success is not guaranteed. Use **Douzy** for everyday downloads.

Refreshing cookies or retrying does not fix this verification block. Live recording is experimental; HLS sources save a playlist, not a playable video.

## Quick Start

Requires **Python 3.9+** on Windows, macOS, or Linux. Read the CLI limitations above first.

### 1. Install

```bash
git clone https://github.com/jiji262/douyin-downloader.git
cd douyin-downloader
python -m pip install -r requirements.txt
python -m pip install playwright
python -m playwright install chromium
```

### 2. Configure and sign in

Copy [config.example.yml](config.example.yml) to `config.yml`:

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

### 3. Run

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

## Configuration

Full settings and examples: **[config.example.yml](config.example.yml)**.

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

Comments, transcription, notifications, and live recording are configured in [config.example.yml](config.example.yml).

</details>

## FAQ

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

[QQ group](https://qm.qq.com/q/8wrCzYyLHa) · [LINUX DO](https://linux.do/) · [MIT License](LICENSE)

<img src="img/qq-group.png" alt="Community QR code" width="200" />

For learning and personal data management. Respect copyright, privacy, and platform rules. You are responsible for how you use this tool; platform changes may affect availability.

## Star History

[View GitHub Star History](https://www.star-history.com/?repos=jiji262%2Fdouyin-downloader&type=date&legend=top-left)
