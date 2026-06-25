# Auto-Relogin on Expired Douyin Session — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When Douyin rejects a request with "请先登录" (status_code 2483), automatically open a browser to re-login, capture fresh cookies, and retry the original command once — covering all CLI commands.

**Architecture:** Single detection point in the shared `core/api_client.py` (`_request_json`) raises `LoginRequiredError`; the CLI top level catches it, runs an interactive browser login (reusing `tools/cookie_fetcher.py`), refreshes cookies, and retries once. Detection + exception are mirrored byte-for-byte into the desktop repo; the interactive orchestration stays CLI-only.

**Tech Stack:** Python 3, asyncio, aiohttp, pytest + pytest-asyncio (`asyncio_mode = "auto"`), Playwright (already a dependency of the cookie fetcher).

## Global Constraints

- Git identity for commits (both repos): `jiji262 <jiguofei@msn.com>` via repo-local `git config` (do NOT touch global identity).
- No `Co-Authored-By` / "Generated with Claude" trailers in commit messages.
- Detection predicate stays narrow: `status_code == 2483` OR `status_msg` contains `请先登录`.
- Retry at most once; never loop. Non-interactive environments (`--serve`, no TTY) must NOT auto-launch a browser.
- Shared files (`core/api_client.py`, `core/__init__.py`, `tools/cookie_fetcher.py`) must stay byte-identical between `douyin-downloader` (CLI) and `douyin-downloader-desktop`. CLI-only files: `cli/login_flow.py`, `cli/main.py`.
- CLI repo root: `/Users/crimson/codes/douyin/douyin-downloader`. Desktop repo root: `/Users/crimson/codes/douyin/douyin-downloader-desktop`. Run tests with `.venv/bin/python -m pytest`.

---

## File Structure

CLI repo (`douyin-downloader`):
- `core/api_client.py` — add `LoginRequiredError`, `_LOGIN_REQUIRED_STATUS_CODES`, `_is_login_required()`, wire raise into `_request_json` (modify).
- `core/__init__.py` — export `LoginRequiredError` (modify).
- `tools/cookie_fetcher.py` — add `fetch_cookies(...)` param-based wrapper (modify, additive).
- `cli/login_flow.py` — NEW: `interactive_relogin()`, `can_interactive_login()`.
- `cli/main.py` — add `_run_with_relogin()`, apply to discovery subcommand + per-URL download loop (modify).
- `tests/test_api_client_login_required.py` — NEW.
- `tests/test_cookie_fetcher_fetch.py` — NEW.
- `tests/test_login_flow.py` — NEW.
- `tests/test_relogin_retry.py` — NEW.

Desktop repo (`douyin-downloader-desktop`): mirror of `core/api_client.py`, `core/__init__.py`, `tools/cookie_fetcher.py`, and the core detection test only.

---

## Task 1: Core detection + `LoginRequiredError` (CLI repo)

**Files:**
- Modify: `core/api_client.py` (add exception + helper near top of module; wire into `_request_json` before its success return at `core/api_client.py:223`)
- Modify: `core/__init__.py`
- Test: `tests/test_api_client_login_required.py`

**Interfaces:**
- Produces: `core.api_client.LoginRequiredError(status_code: int, status_msg: str, path: str)` with attributes `.status_code`, `.status_msg`, `.path`; `core.api_client._is_login_required(data: dict) -> bool`. Re-exported as `core.LoginRequiredError`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_api_client_login_required.py`:

```python
"""Login-required detection in the shared API client."""

import pytest

from core.api_client import LoginRequiredError, _is_login_required


@pytest.mark.parametrize(
    "data,expected",
    [
        ({"status_code": 2483, "status_msg": "请先登录，再继续搜索吧"}, True),
        ({"status_code": 0, "status_msg": "请先登录后再试"}, True),  # msg match
        ({"status_code": 2483}, True),
        ({"status_code": 0, "status_msg": "ok"}, False),
        ({"status_code": 10000, "status_msg": "rate limited"}, False),
        ({}, False),
        ({"data": [{"aweme_info": {}}], "status_code": 0}, False),
        ("not-a-dict", False),
    ],
)
def test_is_login_required(data, expected):
    assert _is_login_required(data) is expected


def test_login_required_error_fields():
    err = LoginRequiredError(2483, "请先登录", "/aweme/v1/web/general/search/single/")
    assert err.status_code == 2483
    assert err.status_msg == "请先登录"
    assert err.path == "/aweme/v1/web/general/search/single/"
    assert "2483" in str(err)


def test_login_required_error_exported_from_core():
    from core import LoginRequiredError as Exported

    assert Exported is LoginRequiredError
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_api_client_login_required.py -v`
Expected: FAIL — `ImportError: cannot import name 'LoginRequiredError'`.

- [ ] **Step 3: Write minimal implementation**

In `core/api_client.py`, near the top of the module (after imports, before `class DouyinAPIClient`), add:

```python
_LOGIN_REQUIRED_STATUS_CODES = {2483}


class LoginRequiredError(Exception):
    """Raised when Douyin rejects a request because the session is not logged in.

    Signalled by ``status_code == 2483`` (or a ``status_msg`` asking to log in).
    Higher layers (CLI) catch this to trigger an interactive re-login + retry.
    """

    def __init__(self, status_code: int, status_msg: str, path: str):
        self.status_code = status_code
        self.status_msg = status_msg
        self.path = path
        super().__init__(
            f"login required (status_code={status_code}) at {path}: {status_msg}"
        )


def _is_login_required(data: object) -> bool:
    if not isinstance(data, dict):
        return False
    code = data.get("status_code")
    msg = str(data.get("status_msg") or "")
    return code in _LOGIN_REQUIRED_STATUS_CODES or "请先登录" in msg
```

In `_request_json`, replace the success return at `core/api_client.py:223`:

```python
                        return data if isinstance(data, dict) else {}
```

with:

```python
                        result = data if isinstance(data, dict) else {}
                        if _is_login_required(result):
                            raise LoginRequiredError(
                                int(result.get("status_code") or 0),
                                str(result.get("status_msg") or ""),
                                path,
                            )
                        return result
```

In `core/__init__.py`, change the api_client import line and `__all__`:

```python
from .api_client import DouyinAPIClient, LoginRequiredError
```

and add `"LoginRequiredError",` to the `__all__` list.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_api_client_login_required.py -v`
Expected: PASS (all parametrized cases + 2 field/export tests).

- [ ] **Step 5: Run the existing suite to confirm no regressions**

Run: `.venv/bin/python -m pytest tests/test_discovery.py -q`
Expected: PASS (unchanged — fakes never produce 2483).

- [ ] **Step 6: Commit**

```bash
git add core/api_client.py core/__init__.py tests/test_api_client_login_required.py
git commit -m "feat(core): raise LoginRequiredError on Douyin 2483 not-logged-in responses"
```

---

## Task 2: `fetch_cookies()` param wrapper in cookie fetcher (CLI repo)

**Files:**
- Modify: `tools/cookie_fetcher.py` (add `fetch_cookies` after `capture_cookies`)
- Test: `tests/test_cookie_fetcher_fetch.py`

**Interfaces:**
- Consumes: existing `tools.cookie_fetcher.capture_cookies(args: argparse.Namespace) -> int`.
- Produces: `async def fetch_cookies(*, output: Path, config: Optional[Path] = None, url: str = DEFAULT_URL, browser: str = "chromium", headless: bool = False, include_all: bool = False) -> int`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_cookie_fetcher_fetch.py`:

```python
"""fetch_cookies builds the right Namespace and delegates to capture_cookies."""

from pathlib import Path

import pytest

import tools.cookie_fetcher as cf


@pytest.mark.asyncio
async def test_fetch_cookies_delegates(monkeypatch):
    seen = {}

    async def fake_capture(args):
        seen["args"] = args
        return 0

    monkeypatch.setattr(cf, "capture_cookies", fake_capture)

    rc = await cf.fetch_cookies(output=Path("/tmp/x/cookies.json"))

    assert rc == 0
    args = seen["args"]
    assert args.output == Path("/tmp/x/cookies.json")
    assert args.browser == "chromium"
    assert args.headless is False
    assert args.include_all is False
    assert args.url == cf.DEFAULT_URL
    assert args.config is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_cookie_fetcher_fetch.py -v`
Expected: FAIL — `AttributeError: module 'tools.cookie_fetcher' has no attribute 'fetch_cookies'`.

- [ ] **Step 3: Write minimal implementation**

In `tools/cookie_fetcher.py`, add after `capture_cookies` (before `is_timeout_error`):

```python
async def fetch_cookies(
    *,
    output: Path,
    config: Optional[Path] = None,
    url: str = DEFAULT_URL,
    browser: str = "chromium",
    headless: bool = False,
    include_all: bool = False,
) -> int:
    """Parameterised entry to the manual-login cookie capture flow.

    Thin wrapper around :func:`capture_cookies` so callers (e.g. the CLI
    auto-relogin flow) don't have to fake an argparse.Namespace.
    """
    args = argparse.Namespace(
        output=output,
        config=config,
        url=url,
        browser=browser,
        headless=headless,
        include_all=include_all,
    )
    return await capture_cookies(args)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_cookie_fetcher_fetch.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tools/cookie_fetcher.py tests/test_cookie_fetcher_fetch.py
git commit -m "feat(tools): add param-based fetch_cookies wrapper for programmatic login"
```

---

## Task 3: `cli/login_flow.py` — interactive relogin + interactivity guard (CLI repo)

**Files:**
- Create: `cli/login_flow.py`
- Test: `tests/test_login_flow.py`

**Interfaces:**
- Consumes: `tools.cookie_fetcher.fetch_cookies(...)` (Task 2); `utils.cookie_utils.sanitize_cookies`.
- Produces:
  - `def can_interactive_login(*, serve: bool = False) -> bool` — True only when stdin is a TTY and not serve mode.
  - `async def interactive_relogin(cookies_path: Path = Path("config/cookies.json")) -> Optional[dict]` — runs the browser login, returns the fresh cookie dict (must contain `sessionid`) or `None` on failure.

- [ ] **Step 1: Write the failing test**

Create `tests/test_login_flow.py`:

```python
"""Interactive relogin orchestration (CLI-only)."""

import json
from pathlib import Path

import pytest

import cli.login_flow as lf


def test_can_interactive_login_requires_tty(monkeypatch):
    monkeypatch.setattr(lf.sys.stdin, "isatty", lambda: True)
    assert lf.can_interactive_login(serve=False) is True
    assert lf.can_interactive_login(serve=True) is False
    monkeypatch.setattr(lf.sys.stdin, "isatty", lambda: False)
    assert lf.can_interactive_login(serve=False) is False


@pytest.mark.asyncio
async def test_interactive_relogin_success(monkeypatch, tmp_path):
    cookies_path = tmp_path / "cookies.json"

    async def fake_fetch(*, output, **kwargs):
        Path(output).write_text(
            json.dumps({"sessionid": "fresh", "ttwid": "t"}), encoding="utf-8"
        )
        return 0

    monkeypatch.setattr(lf, "fetch_cookies", fake_fetch)

    result = await lf.interactive_relogin(cookies_path=cookies_path)
    assert result == {"sessionid": "fresh", "ttwid": "t"}


@pytest.mark.asyncio
async def test_interactive_relogin_failure_returns_none(monkeypatch, tmp_path):
    cookies_path = tmp_path / "cookies.json"

    async def fake_fetch(*, output, **kwargs):
        return 1  # playwright missing / user aborted

    monkeypatch.setattr(lf, "fetch_cookies", fake_fetch)
    assert await lf.interactive_relogin(cookies_path=cookies_path) is None


@pytest.mark.asyncio
async def test_interactive_relogin_no_session_cookie_returns_none(monkeypatch, tmp_path):
    cookies_path = tmp_path / "cookies.json"

    async def fake_fetch(*, output, **kwargs):
        Path(output).write_text(json.dumps({"ttwid": "t"}), encoding="utf-8")
        return 0

    monkeypatch.setattr(lf, "fetch_cookies", fake_fetch)
    assert await lf.interactive_relogin(cookies_path=cookies_path) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_login_flow.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cli.login_flow'`.

- [ ] **Step 3: Write minimal implementation**

Create `cli/login_flow.py`:

```python
"""CLI-only interactive re-login flow.

Detects nothing itself — invoked by cli.main when a LoginRequiredError
bubbles up. Opens a browser via the cookie fetcher, guides manual Douyin
login, then loads the freshly captured cookies from disk.

NOT shared with the desktop project: the desktop app drives its own GUI
login surface instead of this terminal-driven Playwright flow.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

from tools.cookie_fetcher import fetch_cookies
from utils.cookie_utils import sanitize_cookies
from utils.logger import setup_logger

logger = setup_logger("LoginFlow")

_DEFAULT_COOKIES_PATH = Path("config/cookies.json")


def can_interactive_login(*, serve: bool = False) -> bool:
    """True only when we can safely open a browser and read a terminal Enter."""
    if serve:
        return False
    try:
        return bool(sys.stdin.isatty())
    except (AttributeError, ValueError):
        return False


async def interactive_relogin(
    cookies_path: Path = _DEFAULT_COOKIES_PATH,
) -> Optional[dict]:
    """Open a browser, guide login, capture cookies. Returns fresh cookies or None."""
    print(
        "\n[登录态已失效] 抖音要求重新登录。即将打开浏览器，请完成抖音登录，"
        "登录成功后回到本终端按 Enter。\n"
    )
    try:
        rc = await fetch_cookies(output=cookies_path)
    except Exception as exc:  # noqa: BLE001 — surface, don't crash the run
        logger.error("Interactive relogin failed to launch: %s", exc)
        print(
            "[ERROR] 无法启动登录流程。请确认已安装 Playwright："
            "\n  pip install playwright && playwright install chromium"
            "\n或手动更新 config/cookies.json 后重试。"
        )
        return None

    if rc != 0:
        print("[ERROR] 登录流程未成功完成，已中止。")
        return None

    try:
        raw = json.loads(Path(cookies_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("Could not read captured cookies from %s: %s", cookies_path, exc)
        return None

    cookies = sanitize_cookies(raw if isinstance(raw, dict) else {})
    if not cookies.get("sessionid"):
        print("[ERROR] 登录后未获取到有效会话（缺少 sessionid），请重试。")
        return None
    return cookies
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_login_flow.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add cli/login_flow.py tests/test_login_flow.py
git commit -m "feat(cli): interactive relogin flow reusing the cookie fetcher"
```

---

## Task 4: Wire detection→relogin→retry into `cli/main.py` (CLI repo)

**Files:**
- Modify: `cli/main.py` (import; add `_run_with_relogin`; wrap discovery subcommand at `cli/main.py:176`; wrap per-URL download at `cli/main.py:226`)
- Test: `tests/test_relogin_retry.py`

**Interfaces:**
- Consumes: `core.LoginRequiredError`; `cli.login_flow.interactive_relogin`, `cli.login_flow.can_interactive_login`; `auth.CookieManager`; `config.ConfigLoader`.
- Produces: `async def _run_with_relogin(make_coro, config, cookie_manager, *, serve=False) -> Any` — runs `make_coro()` (a zero-arg coroutine factory); on `LoginRequiredError`, if interactive, re-logins, updates `config`+`cookie_manager`, retries once; otherwise re-raises.

- [ ] **Step 1: Write the failing test**

Create `tests/test_relogin_retry.py`:

```python
"""_run_with_relogin: detect LoginRequiredError, relogin, retry once."""

import pytest

import cli.main as m
from auth import CookieManager
from config import ConfigLoader
from core import LoginRequiredError


def _mk():
    config = ConfigLoader(None)
    cm = CookieManager()
    cm.set_cookies({"sessionid": "old"})
    return config, cm


@pytest.mark.asyncio
async def test_retries_once_after_relogin(monkeypatch):
    config, cm = _mk()
    calls = {"n": 0}

    async def make_coro():
        calls["n"] += 1
        if calls["n"] == 1:
            raise LoginRequiredError(2483, "请先登录", "/search")
        return "done"

    async def fake_relogin(cookies_path=None):
        return {"sessionid": "fresh"}

    monkeypatch.setattr(m, "can_interactive_login", lambda *, serve=False: True)
    monkeypatch.setattr(m, "interactive_relogin", fake_relogin)

    result = await m._run_with_relogin(make_coro, config, cm)

    assert result == "done"
    assert calls["n"] == 2
    assert cm.get_cookies().get("sessionid") == "fresh"
    assert config.get_cookies().get("sessionid") == "fresh"


@pytest.mark.asyncio
async def test_non_interactive_does_not_relogin(monkeypatch):
    config, cm = _mk()
    called = {"relogin": False}

    async def make_coro():
        raise LoginRequiredError(2483, "请先登录", "/search")

    async def fake_relogin(cookies_path=None):
        called["relogin"] = True
        return {"sessionid": "fresh"}

    monkeypatch.setattr(m, "can_interactive_login", lambda *, serve=False: False)
    monkeypatch.setattr(m, "interactive_relogin", fake_relogin)

    with pytest.raises(LoginRequiredError):
        await m._run_with_relogin(make_coro, config, cm)
    assert called["relogin"] is False


@pytest.mark.asyncio
async def test_gives_up_when_relogin_fails(monkeypatch):
    config, cm = _mk()

    async def make_coro():
        raise LoginRequiredError(2483, "请先登录", "/search")

    async def fake_relogin(cookies_path=None):
        return None

    monkeypatch.setattr(m, "can_interactive_login", lambda *, serve=False: True)
    monkeypatch.setattr(m, "interactive_relogin", fake_relogin)

    with pytest.raises(LoginRequiredError):
        await m._run_with_relogin(make_coro, config, cm)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_relogin_retry.py -v`
Expected: FAIL — `AttributeError: module 'cli.main' has no attribute '_run_with_relogin'` (and missing imports).

- [ ] **Step 3: Write minimal implementation**

In `cli/main.py`, add to the imports near the top (after the existing `from core import ...` at `cli/main.py:13`):

```python
from core import LoginRequiredError
from cli.login_flow import can_interactive_login, interactive_relogin
```

Add the wrapper (place it above `main_async`, e.g. after the `_as_bool` helper at `cli/main.py:30`):

```python
async def _run_with_relogin(make_coro, config, cookie_manager, *, serve=False):
    """Run make_coro(); on LoginRequiredError, relogin once and retry.

    make_coro is a zero-arg callable returning a fresh coroutine each call,
    so the retry re-creates its own DouyinAPIClient with refreshed cookies.
    """
    for attempt in range(2):
        try:
            return await make_coro()
        except LoginRequiredError as exc:
            if attempt == 1 or not can_interactive_login(serve=serve):
                display.print_error(
                    f"登录态失效，需要重新登录（status {exc.status_code}）："
                    f"{exc.status_msg or '请先登录'}。"
                )
                if not can_interactive_login(serve=serve):
                    display.print_warning(
                        "当前为非交互环境，未自动打开浏览器。请手动更新 "
                        "config/cookies.json（或运行 python tools/cookie_fetcher.py 登录）。"
                    )
                raise
            display.print_warning(
                f"检测到未登录（status {exc.status_code}），开始重新登录…"
            )
            new_cookies = await interactive_relogin()
            if not new_cookies:
                display.print_error("重新登录未完成，已中止。")
                raise
            config.update(cookies=new_cookies)
            cookie_manager.set_cookies(new_cookies)
            display.print_success("已更新登录态，正在重试…")
```

Apply to the discovery subcommand. Replace the block at `cli/main.py:175-177`:

```python
    # 独立子命令：热榜 / 搜索 / 服务
    if args.hot_board is not None or args.search:
        await _run_discovery_subcommand(args, config)
        return
```

with:

```python
    # 独立子命令：热榜 / 搜索 / 服务
    if args.hot_board is not None or args.search:
        discovery_cookies = config.get_cookies()
        discovery_cm = CookieManager()
        discovery_cm.set_cookies(discovery_cookies)
        await _run_with_relogin(
            lambda: _run_discovery_subcommand(args, config),
            config,
            discovery_cm,
            serve=False,
        )
        return
```

Apply to the per-URL download loop. Replace the call at `cli/main.py:226-232`:

```python
            result = await download_url(
                url,
                config,
                cookie_manager,
                database,
                progress_reporter=display,
            )
```

with (capture `url` via default arg to avoid late-binding):

```python
            result = await _run_with_relogin(
                lambda u=url: download_url(
                    u,
                    config,
                    cookie_manager,
                    database,
                    progress_reporter=display,
                ),
                config,
                cookie_manager,
                serve=False,
            )
```

Note: `_run_discovery_subcommand` reads `config.get_cookies()` internally, so after `config.update(cookies=...)` the retried call rebuilds its `DouyinAPIClient` with fresh cookies. The download loop's `download_url` builds its client from `cookie_manager.get_cookies()`, which we update in place.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_relogin_retry.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Run the full CLI suite for regressions**

Run: `.venv/bin/python -m pytest tests/test_discovery.py tests/test_api_client_login_required.py tests/test_login_flow.py tests/test_cookie_fetcher_fetch.py tests/test_relogin_retry.py -q`
Expected: PASS.

- [ ] **Step 6: Manual smoke check (no network needed for import sanity)**

Run: `.venv/bin/python -c "import cli.main; import cli.login_flow; from core import LoginRequiredError; print('imports ok')"`
Expected: prints `imports ok` with no traceback.

- [ ] **Step 7: Commit**

```bash
git add cli/main.py tests/test_relogin_retry.py
git commit -m "feat(cli): auto-relogin and retry once on expired Douyin session"
```

---

## Task 5: Mirror shared changes into the desktop repo

**Files (desktop repo `/Users/crimson/codes/douyin/douyin-downloader-desktop`):**
- Modify: `core/api_client.py` (identical to CLI Task 1)
- Modify: `core/__init__.py` (identical to CLI Task 1)
- Modify: `tools/cookie_fetcher.py` (identical to CLI Task 2)
- Test: `tests/test_api_client_login_required.py` (copy of CLI Task 1 test)

**Why desktop needs this (it has no Douyin search):** The desktop GUI has no Douyin
content search — its "搜索" box is a local task-list filter (`/api/v1/history?q=`). The
benefit comes from desktop's login-required personal-content endpoints
(`/api/v1/my-content/likes`, `/collects`, `/collectmixes`, `/self`), which route through
the shared `_request_json` and return the same `2483 / 请先登录` on an expired session.
This task mirrors detection only; no search code is added to desktop.

**Interfaces:**
- Produces: same `LoginRequiredError` / `_is_login_required` / `fetch_cookies` symbols in desktop. NOT consumed by any desktop interactive flow in this task — `server/jobs.py` already catches `Exception` and surfaces it.

- [ ] **Step 1: Confirm the shared files are still byte-identical before editing**

Run:
```bash
diff /Users/crimson/codes/douyin/douyin-downloader/core/api_client.py /Users/crimson/codes/douyin/douyin-downloader-desktop/core/api_client.py
diff /Users/crimson/codes/douyin/douyin-downloader/tools/cookie_fetcher.py /Users/crimson/codes/douyin/douyin-downloader-desktop/tools/cookie_fetcher.py
```
Expected: only the Task 1/Task 2 additions show as differences (i.e. the diff is exactly the new blocks). If anything else differs, STOP and reconcile before copying.

- [ ] **Step 2: Apply the identical edits to desktop**

Copy the same three edits from Task 1 (exception + `_is_login_required` + `_request_json` raise + `core/__init__.py` export) and Task 2 (`fetch_cookies`) into the desktop files at the same locations.

- [ ] **Step 3: Add the desktop core test**

Copy `tests/test_api_client_login_required.py` from the CLI repo into the desktop repo unchanged.

- [ ] **Step 4: Run the desktop tests**

Run (from desktop root):
```bash
/Users/crimson/codes/douyin/douyin-downloader-desktop/.venv/bin/python -m pytest tests/test_api_client_login_required.py -q
```
Expected: PASS.

- [ ] **Step 5: Verify desktop job runner degrades gracefully (no crash)**

Confirm by reading `server/jobs.py` `JobManager._run` that its `except Exception` branch sets `job.status = FAILED` and calls `reporter.on_error(...)`. (No code change — this is the safety net that catches `LoginRequiredError`.) Document this in the commit body.

- [ ] **Step 6: Verify byte-identical shared files after edits**

Run:
```bash
diff /Users/crimson/codes/douyin/douyin-downloader/core/api_client.py /Users/crimson/codes/douyin/douyin-downloader-desktop/core/api_client.py && echo IDENTICAL
diff /Users/crimson/codes/douyin/douyin-downloader/core/__init__.py /Users/crimson/codes/douyin/douyin-downloader-desktop/core/__init__.py && echo IDENTICAL
diff /Users/crimson/codes/douyin/douyin-downloader/tools/cookie_fetcher.py /Users/crimson/codes/douyin/douyin-downloader-desktop/tools/cookie_fetcher.py && echo IDENTICAL
```
Expected: three `IDENTICAL` lines.

- [ ] **Step 7: Commit (in desktop repo, with repo-local jiji262 identity)**

```bash
cd /Users/crimson/codes/douyin/douyin-downloader-desktop
git config user.name jiji262
git config user.email jiguofei@msn.com
git add core/api_client.py core/__init__.py tools/cookie_fetcher.py tests/test_api_client_login_required.py
git commit -m "feat(core): raise LoginRequiredError on Douyin 2483; caught by job runner

Mirrors douyin-downloader CLI shared-logic change. server/jobs.py _run
already catches Exception, so an expired session surfaces as a failed job
with a clear message instead of empty results."
```

---

## Self-Review

**1. Spec coverage:**
- Detection (status 2483 / "请先登录") → Task 1. ✓
- Covers all commands via single `_request_json` chokepoint → Task 1 (download/search/hot-board all route through it). ✓
- Reuse cookie fetcher → Task 2 + Task 3. ✓
- Interactive relogin + Playwright-missing graceful path → Task 3. ✓
- Top-level catch + retry once → Task 4. ✓
- Non-interactive guard (`--serve`/no TTY) → Task 3 (`can_interactive_login`) + Task 4 (wired). ✓
- Error-handling matrix (give up after 1 retry, user-abort returns None) → Task 4 tests + Task 3 tests. ✓
- Desktop sync (core + fetcher identical; jobs.py safety net) → Task 5. ✓
- Tests in both repos → Tasks 1–4 (CLI), Task 5 (desktop). ✓

**2. Placeholder scan:** No TBD/TODO; every code step shows full code; commands have expected output. ✓

**3. Type consistency:** `LoginRequiredError(status_code, status_msg, path)` and `_is_login_required(data)` consistent across Tasks 1/4/5. `fetch_cookies(*, output=...)` keyword-only, used identically in Task 3. `interactive_relogin(cookies_path=...)` / `can_interactive_login(*, serve=...)` consistent between Task 3 (def) and Task 4 (call + monkeypatch targets `m.interactive_relogin` / `m.can_interactive_login`). ✓

**Note on monkeypatch targets:** Task 4 imports the two names into `cli.main` (`from cli.login_flow import can_interactive_login, interactive_relogin`), so tests patch `cli.main.can_interactive_login` / `cli.main.interactive_relogin` — matching the test code in Task 4 Step 1.
