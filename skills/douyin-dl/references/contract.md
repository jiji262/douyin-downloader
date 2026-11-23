# Agent mode contract (douyin-dl)

Fields follow `schema: 1`. If the `schema` number in an envelope is not 1, do not rely
on this document; run `douyin-dl agent --help` and re-check.

## Invocation

```text
douyin-dl agent [--verbose] <verb> [arguments]
```

- stdout: exactly one line of UTF-8 JSON (the envelope), ending in `\n`.
- stderr: logs (ERROR by default, INFO with `--verbose`), tracebacks (only with
  `--verbose`), and NDJSON progress events when a verb is given `--events`.
- `--verbose` may be written before or after the verb.
- `--help` returns an `ok: true` envelope with the help text in `data.help`.
- State lives in `$DOUYIN_DL_HOME` (default `~/.douyin-dl`): `cookies.json` and
  `history.db`. Agent mode does not read `config.yml` or any cookie file elsewhere, and
  creates no files outside the state directory and an explicit output directory.
- Relative paths are resolved against the caller's current directory.
- `DOUYIN_PROXY` (for example `http://127.0.0.1:7890`) is honoured.

## Envelope

```json
{"schema":1,"ok":true,"verb":"hot","version":"2.1.1","data":{},"error":null,"warnings":[]}
```

| Field | Type | Meaning |
|---|---|---|
| `schema` | int | Contract version, currently `1` |
| `ok` | bool | True only when the exit code is 0 |
| `verb` | string or null | The verb that ran; null when arguments could not be parsed |
| `version` | string | CLI version |
| `data` | object | Result. May be non-empty on failure (partial results, classification) |
| `error` | object or null | Present when `ok` is false |
| `warnings` | string[] | Non-fatal notices, for example loose file permissions |

`error` is `{"code", "message", "retryable", "action"?}`.

`error.action` is `{"kind", "command"?, "hint"?, "retry_after_s"?}`:

| `kind` | Meaning |
|---|---|
| `run_command` | A person should run `command` (used for login) |
| `install` | A dependency is missing; `command` is the exact install command |
| `use_desktop` | Gated; point the user to the Douzy desktop app |
| `retry_later` | Retry after `retry_after_s` seconds |
| `fix_usage` | Correct the arguments; see `hint` |

## Error codes and exit codes

| `error.code` | Exit | `retryable` default | Meaning |
|---|---|---|---|
| (success) | 0 | n/a | `ok: true` |
| `INTERNAL` | 1 | false | Unexpected exception. `message` is the exception type plus a short detail |
| `USAGE` | 2 | false | Bad arguments or path, or login requested without a display |
| `UNSUPPORTED` | 2 | false | Unknown URL, cinema (lvdetail) content, live replays |
| `LOGIN_REQUIRED` | 3 | false | Default action: `run_command` with the login command. True `retryable` means a login attempt timed out |
| `ARGUS_GATED` | 4 | false | Deterministic gate. Action `use_desktop` |
| `UPSTREAM_EMPTY` | 5 | true | Empty response, rate limit, anti-bot, or a room that is not live (then `retryable` is false) |
| `PARTIAL` | 6 | false | Some items succeeded; `data` carries results and counts |
| `DEPENDENCY_MISSING` | 7 | false | Action `install` |
| `INTERRUPTED` | 130 | false | SIGINT or SIGTERM. Only the first signal counts; later ones are ignored until the envelope is written. `data` may list finished files |

## Capability levels

| Level | Meaning |
|---|---|
| `anonymous` | No cookies needed |
| `cookie` | Needs any saved cookie file |
| `login` | Needs `sessionid` or `sessionid_ss` |
| `local` | No network |
| `argus_gated` | Behind the Argus gate; not available |
| `unsupported` | Not offered |

Verbs: `doctor` local, `inspect` anonymous, `hot` anonymous, `search` login, `user`
cookie, `login` local, `live` anonymous, `transcribe` local.

URL types: `video`, `gallery`, `collection`, `music` are `argus_gated`; `user` is
`cookie`; `live` is `anonymous`; `live_replay`, `lvdetail` and anything unrecognised are
`unsupported`.

## Text fields

Text from the platform has control characters removed and whitespace collapsed, and is
truncated with a trailing `…`: `desc` and `signature` at 200 characters, `nickname` and
`title` at 64. `data` holds projected fields only, never raw API responses, and never a
media URL (`play_addr` or any direct link).

## Verbs

### `douyin-dl agent doctor [--online]`

Always exits 0 unless `INTERNAL`.

| `data` field | Type | Notes |
|---|---|---|
| `version`, `python`, `platform`, `executable` | string | `executable` is the interpreter path |
| `state_dir` | `{path, exists, mode}` | `mode` like `"0700"`; null on Windows or when missing |
| `cookies` | `{path, exists, mode, mtime, age_days, keys_present, login_status}` | `mtime` ISO 8601 UTC or null; `age_days` number or null; `keys_present` lists known key names only, never values; `login_status` is `none`, `device_only` or `logged_in` |
| `deps.ffmpeg` | `{available, path, install?}` | `install` is an action object, present only when unavailable |
| `deps.playwright`, `deps.whisper` | `{available, install?}` | |
| `capabilities.verbs.<verb>` | `{level, ready, blocked_by}` | `blocked_by` items: `cookies`, `login`, `playwright`, `ffmpeg`, `whisper` |
| `capabilities.url_types` | `{<url_type>: <level>}` | |
| `online_check` | object or null | Null without `--online`. Otherwise `{endpoint, status, logged_in, detail}`; `status` is `logged_in`, `login_required`, `empty`, `error` or `skipped` |

`--online` sends at most one request (own-profile lookup) and none when no session
cookie is saved (`status: skipped`).

### `douyin-dl agent inspect <url>`

Accepts a URL, a short link, or share text containing one. Only a short link triggers a
network call (one redirect lookup); no API is called.

Short links (here and in `user` and `live`) are resolved without cookies and always over
`https://`; an `http://` short link is upgraded before the request. Only Douyin short-link
hosts (`v.douyin.com`, `v.iesdouyin.com`, `iesdouyin.com`) are followed. Any other
shortener is not resolved and is classified as it stands.

| `data` field | Type | Notes |
|---|---|---|
| `input` | string | What was passed |
| `resolved_url` | string | After short-link resolution |
| `url_type` | string or null | `video`, `gallery`, `collection`, `music`, `user`, `live`, `live_replay`, `lvdetail`, or null |
| `ids` | object | String ids found by the parser, for example `aweme_id`, `sec_uid`, `room_id` |
| `capability` | string | A capability level |
| `suggested_verb` | string or null | `user`, `live`, or null |
| `note` | string | Short explanation |

Errors: `ARGUS_GATED` and `UNSUPPORTED` keep the full classification in `data`.
`UPSTREAM_EMPTY` (short link could not be resolved) has `data` of
`{input, resolved_url: null}` and `retry_after_s`.

### `douyin-dl agent hot [--max N]`

`N` is 1 to 100, default 50.

`data`: `items[]` of `{rank, word, hot_value, label?, video_count?, search_url}`, and
`count`. `rank` is the 1-based output order. `label` is Douyin's numeric badge code and is
omitted when 0; `video_count` is omitted when 0; `hot_value` may be `null`.

Errors: `UPSTREAM_EMPTY`; `LOGIN_REQUIRED` if the server ever demands a session.

### `douyin-dl agent search <keyword> [--max N] [--sort S] [--publish-days D]`

`N` is 1 to 50, default 20. `S` is `general` (default), `likes` or `latest`. `D` is `0`
(any time, default), `1`, `7` or `182`.

`data`: `keyword`, `count`, `has_more`, and `items[]` of:

| Field | Type |
|---|---|
| `aweme_id` | string |
| `desc` | string |
| `author` | `{nickname, sec_uid}` |
| `stats` | `{digg, comment, share, collect}` |
| `create_time` | ISO 8601 UTC string, or `null` |
| `duration_s` | seconds (1 decimal), or `null` (usually for image posts) |
| `media_type` | `video` or `gallery` |
| `share_url` | `/video/<id>` or `/note/<id>` page link, never a media link |

Numbers in `stats` are `null` when Douyin omits them; `null` never means 0.

A keyword with no matches is a success: `ok: true` with `items: []`, `count: 0` and
`has_more: false`. Do not retry it.

Errors:
- `LOGIN_REQUIRED`: returned without sending a request when no session cookie is saved.
  Also returned when the server reports the session as logged out, with any items
  already collected in `data`.
- `UPSTREAM_EMPTY`: the first page failed: no response body, a non-zero status code, or
  an anti-bot verification marker. `retry_after_s` is 120.
- `PARTIAL`: failed midway. `data` holds what was collected, and `action` is
  `retry_later` with `retry_after_s`. Do not loop.

### `douyin-dl agent user <url-or-sec_uid>`

The argument can be a profile URL (short links included) or a bare `sec_uid`. A
`sec_uid` starts with `MS4wLjAB`; a Douyin ID (抖音号) or numeric uid is not accepted.

`data`: `sec_uid`, `uid`, `unique_id`, `nickname`, `signature`, `follower_count`,
`following_count`, `aweme_count`, `total_favorited`, `ip_location?`, `note`. The `note`
states that downloading the user's posts is gated. Counts are `null` when missing.
`unique_id` falls back to the short id.

Errors:
- `LOGIN_REQUIRED`: no cookie file. No request is sent, not even to resolve a short link.
- `USAGE`: the URL is not a profile, or a bare id does not start with `MS4wLjAB`. No
  request is sent. `action.hint` says to pass the profile URL or a `sec_uid`.
- `UPSTREAM_EMPTY`: Douyin returned no profile.

### `douyin-dl agent login [--timeout S] [--from-file PATH]`

`S` is 1 to 900, default 300. Opens a visible Chromium window at douyin.com and waits
for the user to log in. `--from-file` instead imports a JSON file the user exported:
either a `{name: value}` object or a list of `{name, value, ...}` objects (max 1 MB).
List entries whose `domain` is not a Douyin domain are skipped with a warning.

`data`: `saved_to` (path), `keys_present` (names only), `login_status`.

Errors: `DEPENDENCY_MISSING` (Playwright or Chromium), `LOGIN_REQUIRED` with
`retryable: true` (timed out), `USAGE` (Linux without `DISPLAY` or `WAYLAND_DISPLAY`;
the hint points to `--from-file`).

`--from-file` also returns `USAGE` and changes nothing when the file has no known Douyin
cookie name with a value, or when it has no `sessionid` while a logged-in session is
already saved. A file with only device cookies is accepted when no session is saved,
with a warning.

### `douyin-dl agent live <url> --max-duration S [--out DIR] [--events]`

`S` is required, 1 to 21600. `--out` defaults to `<cwd>/douyin-downloads`. When nothing
was recorded, an output directory created by this run is removed again.

`<url>` is a live-room URL or a Douyin short link to one. The short link is resolved as
described under `inspect` (no cookies, `https://` only); saved cookies are used only for
the room lookup and the recording itself.

`data`: `room` (`{title?, nickname?}`), `files[]` (absolute paths), `duration_s`,
`segments`, `stopped_reason`.

`stopped_reason` is one of:

| Value | Meaning |
|---|---|
| `max_duration` | The `--max-duration` limit was reached |
| `ended` | The streamer went offline |
| `stream_lost` | The stream dropped past the reconnect grace period |
| `interrupted` | A signal stopped the recording |
| `null` | Recording never started |

`files` may still contain a `.ts` that could not be remuxed; a warning is added when it
does.

| Error | When |
|---|---|
| `UPSTREAM_EMPTY`, `retryable: false` | The room is not live |
| `UPSTREAM_EMPTY`, `retry_after_s: 60` | Room info or stream data unavailable, or the short link could not be resolved |
| `PARTIAL` | A segment failed to finalize |
| `INTERRUPTED` | Stopped by a signal. Recording is finalized for up to 60 s; `data` has the same shape |
| `DEPENDENCY_MISSING` | ffmpeg is missing |
| `USAGE` | Not a live URL |
| `UNSUPPORTED` | Replay or unknown type |

### `douyin-dl agent transcribe <path> [--model NAME] [--language CODE] [--out DIR] [--events]`

`path` is a media file or a directory; directories are searched recursively for `.mp4`.
Transcripts are written as `<stem>.transcript.txt` next to each input unless `--out` is
given. `--model` defaults to `base` and `--language` to `zh`. The first run downloads
the Whisper model into `~/.cache/whisper`.

`data`: `items[]` of `{input, transcript_path}`, `count`, `failed`, and `failed_items[]`
of `{input, reason}`. Each transcript is written to a private temporary directory first
and then moved into place, so an existing file or symlink at the transcript path is
replaced, never written through.

Errors:
- `DEPENDENCY_MISSING`: openai-whisper or ffmpeg is missing.
- `PARTIAL`: some files failed, or all of them did (`count: 0`).
- `USAGE`: the path is not found, or a directory has no `.mp4` files.
- `INTERRUPTED`: `data` has the same fields plus `interrupted_input`, the absolute path
  of the file in progress (`null` if the model was still loading). No transcript is
  written for that file; only files in `items` exist on disk.

## Progress events (`--events`)

One JSON object per line on stderr. stdout still carries only the final envelope.
`--verbose` logs share stderr, so parse only lines that start with `{"event"`. Treat
unknown event fields as optional.

```json
{"event":"step","step":"transcribe","detail":"a.mp4"}
{"event":"progress","total":3,"detail":"..."}
{"event":"progress","phase":"recording","recorded_s":5.78,"bytes":245716,"segments":0,"reconnects":0}
{"event":"segment","index":1,"segments":1,"recorded_s":5.78}
{"event":"item","status":"failed","id":"...","reason":"..."}
```

`item.status` is `success`, `failed` or `skipped`. `reason` appears only for failed or skipped items. While recording live, `live` emits one
progress event every 5 seconds per phase, and another as soon as the phase changes.
