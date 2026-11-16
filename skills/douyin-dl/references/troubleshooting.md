# Troubleshooting

Start from `douyin-dl agent doctor`. Its `data.deps` and
`data.capabilities.verbs.<verb>.blocked_by` say what is missing. Show install commands to
the user and let them decide; do not install things silently.

## Installing the CLI

```bash
uv tool install douyin-dl
# before the PyPI release
uv tool install git+https://github.com/jiji262/douyin-downloader
# alternative
pipx install douyin-dl
```

- Python 3.10 or newer is required.
- Do not `pip install douyin-dl` into a shared environment. The package ships top-level
  modules named `utils`, `core` and `config`, which collide with other projects. `uv tool`
  and `pipx` give it an isolated environment.
- `douyin-dl: command not found` after installing: run `uv tool update-shell` (or
  `pipx ensurepath`) and open a new shell. Until then `scripts/dy.sh` can launch it.
- `scripts/dy.sh` pins `douyin-dl==2.1.0` for `uvx`. Before the PyPI release set
  `DOUYIN_DL_SPEC=git+https://github.com/jiji262/douyin-downloader`.
- A `version` in the envelope that differs from the skill's `metadata.version` means the
  CLI and the skill are out of step; upgrade with `uv tool upgrade douyin-dl`.

## Optional components

The `install` action in `doctor` targets the exact interpreter. It uses
`<python> -m pip install ...` when the environment has pip, and
`uv pip install --python <python> ...` when it does not, as in `uv tool` environments.
Packages added that way are dropped by `uv tool upgrade`. To keep them, use the
persistent forms:

| Needed for | uv tool | pipx |
|---|---|---|
| `login` (Playwright) | `uv tool install --force "douyin-dl[browser]"` | `pipx inject douyin-dl playwright` |
| `transcribe` (Whisper) | `uv tool install --force "douyin-dl[transcribe]"` | `pipx inject douyin-dl openai-whisper` |
| both | `uv tool install --force "douyin-dl[browser,transcribe]"` | both commands above |

### Playwright and Chromium

After the Python package is in place, download the browser with the interpreter that
`doctor` reports in `data.executable`:

```bash
"<data.executable>" -m playwright install chromium
```

- `login` needs a visible window. On Linux without `DISPLAY` or `WAYLAND_DISPLAY` it
  returns `USAGE`; the user exports cookies to a JSON file on their own machine and runs
  `douyin-dl agent login --from-file PATH`.
- Login timed out (`LOGIN_REQUIRED` with `retryable: true`): ask the user whether to try
  again, optionally with `--timeout 900`.
- `--from-file` returned `USAGE` and saved nothing: the file holds no Douyin cookies, or
  it has no `sessionid` and would replace a saved login. Ask the user to export the
  cookies again while logged in to douyin.com.
- On Linux, missing system libraries are fixed with
  `"<data.executable>" -m playwright install-deps chromium` (needs sudo; the user runs it).

### ffmpeg

`imageio-ffmpeg` is a regular dependency and bundles a static ffmpeg, so `deps.ffmpeg`
is normally available. If it is not, reinstall the CLI, or install a system ffmpeg
(`brew install ffmpeg`, `apt install ffmpeg`, `winget install ffmpeg`) and make sure it
is on PATH.

### Whisper

`openai-whisper` pulls in PyTorch, which is a large download. The first run of a model
also downloads its weights. Start with `--model base` on machines without a GPU; larger
models are slow on CPU. Pass `--language zh` for Mandarin when auto-detection picks the
wrong language.

## Login and cookies

- `search` returns `LOGIN_REQUIRED` although the user logged in before: the session
  expired. Check with `douyin-dl agent doctor --online`, then run the login handoff again.
- `warnings` mention file modes: the state directory should be 0700 and `cookies.json`
  0600. The warning contains the exact `chmod` command.
- Agent mode uses only `$DOUYIN_DL_HOME/cookies.json` (default `~/.douyin-dl`). Cookies
  from the human CLI's `config.yml` or `config/cookies.json` are not picked up.
- `ARGUS_GATED` is not a cookie problem. Logging in again will not change it.

## Network and proxy

- Set `DOUYIN_PROXY` for one call or the whole shell:
  `DOUYIN_PROXY=http://127.0.0.1:7890 douyin-dl agent hot`.
- Douyin is served from mainland China. An overseas exit node often gets empty lists or
  captcha pages, which show up as `UPSTREAM_EMPTY`. Try without the proxy, or with a
  mainland exit.
- `UPSTREAM_EMPTY` repeatedly: wait for `retry_after_s`, retry once, then stop. Rapid
  retries extend the block.
- Without an `msToken` cookie the CLI fetches a config file from
  `raw.githubusercontent.com`. If GitHub is unreachable it uses a bundled copy after a
  short timeout; this is slower on the first request but not an error.

## Windows

- Call `douyin-dl agent ...` directly from PowerShell or cmd. `scripts/dy.sh` needs a
  POSIX shell (Git Bash or WSL).
- `mode` fields are null and no permission warnings are produced; protect the profile
  directory with normal account permissions.
- stdout is written as UTF-8 bytes regardless of the console code page. Read it as
  UTF-8 rather than relying on what the console displays.
- Commands in `action.command` put paths with backslashes or spaces in double quotes.
  They run as-is in cmd and Git Bash; in PowerShell, put `& ` in front of the command.
  If the interpreter path contains `%`, `!`, `$`, a backtick or a quote character, the
  command uses plain `python` instead; run it with the interpreter shown in `doctor`'s
  `data.executable`.
- Ctrl+C is reported as `INTERRUPTED`, but `data` may be empty. Check the output
  directory for files recorded so far.
- Under WSL, `login` needs WSLg or an X server; otherwise use `--from-file`.

## Reporting a bug

Rerun the failing command with `--verbose` and attach the stderr output and the
envelope. The envelope never contains cookie values and tracebacks are redacted, but
`--verbose` also prints logs from the shared downloader, so check before sharing.
