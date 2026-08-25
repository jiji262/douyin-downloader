# Author URL Text File Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every single-creator Douyin homepage task writes the canonical creator URL to `author_url.txt` in the same author root as `主页截图.png`.

**Architecture:** Keep the write at the `UserDownloader` orchestration boundary, immediately after user information resolves and before optional screenshot capture or mode downloads. Reuse `FileManager.get_author_dir()` and `build_author_home_url()` so the TXT path and screenshot path follow the same author-directory rules, while keeping the TXT write independent and non-fatal.

**Tech Stack:** Python 3.8+, asyncio, aiofiles, pytest/pytest-asyncio, Ruff, React/TypeScript, Vitest.

## Global Constraints

- Write `author_url.txt` for every resolvable single-creator homepage task regardless of `homepage_screenshot`; use UTF-8, one canonical URL plus a trailing newline, and overwrite on each task.
- Skip unresolved `/user/self` and collect-only (`collect` / `collectmix`) contexts; log construction or write failures without failing the download.
- Keep CLI and desktop Python behavior equivalent; preserve unrelated pre-existing branch differences.
- Do not add configuration, schema, dependencies, routes, or manifest changes.
- Codex execution uses `superpowers:executing-plans` inline; do not use subagent-driven implementation.

---

### Task 1: Add the CLI backend behavior with TDD

**Files:**
- Modify: `tests/test_user_downloader.py:340-431`
- Modify: `core/user_downloader.py:1-150`

**Interfaces:**
- Consumes `build_author_home_url(...)` and `FileManager.get_author_dir(...)`; produces `UserDownloader._save_author_home_url(sec_uid: str, user_info: Dict[str, Any], modes: List[str]) -> None`.

- [ ] **Step 1: Write failing URL-file tests**

Extend the screenshot-disabled test with:

```python
author_url_path = tmp_path / "Downloaded" / "tester" / "author_url.txt"
assert author_url_path.read_text(encoding="utf-8") == (
    "https://www.douyin.com/user/sec_uid_x\n"
)
```

Extend the configured-author-root test with:

```python
author_root = tmp_path / "Downloaded" / "tester_sec_uid_x"
assert (author_root / "author_url.txt").read_text(encoding="utf-8") == (
    "https://www.douyin.com/user/sec_uid_x\n"
)
assert screenshot_path.parent == author_root.resolve()
```

Add direct tests for overwrite, collect-only skipping, and non-fatal write errors:

```python
def test_author_url_overwrites_existing_file(tmp_path):
    downloader = _build_downloader(tmp_path, _FakeAPIClient(), browser_enabled=False)
    target = tmp_path / "Downloaded" / "tester" / "author_url.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("stale\n", encoding="utf-8")
    asyncio.run(
        downloader._save_author_home_url(
            "sec_uid_x",
            {"sec_uid": "sec_uid_x", "nickname": "tester"},
            ["post"],
        )
    )
    assert target.read_text(encoding="utf-8") == (
        "https://www.douyin.com/user/sec_uid_x\n"
    )
```

```python
def test_author_url_skips_collect_only_context(tmp_path):
    downloader = _build_downloader(tmp_path, _FakeAPIClient(), browser_enabled=False)
    asyncio.run(
        downloader._save_author_home_url(
            "sec_uid_x",
            {"sec_uid": "sec_uid_x", "nickname": "tester"},
            ["collect"],
        )
    )
    assert not (tmp_path / "Downloaded" / "tester" / "author_url.txt").exists()
```

```python
def test_author_url_write_failure_does_not_raise(tmp_path, monkeypatch, caplog):
    downloader = _build_downloader(tmp_path, _FakeAPIClient(), browser_enabled=False)
    def _fail_open(*_args, **_kwargs):
        raise OSError("disk full")
    monkeypatch.setattr("core.user_downloader.aiofiles.open", _fail_open)
    asyncio.run(
        downloader._save_author_home_url(
            "sec_uid_x",
            {"sec_uid": "sec_uid_x", "nickname": "tester"},
            ["post"],
        )
    )
    assert "Author homepage URL failed" in caplog.text
```

- [ ] **Step 2: Run the focused tests and confirm RED**

```bash
python -m pytest \
  tests/test_user_downloader.py::test_homepage_screenshot_disabled_does_not_call_api \
  tests/test_user_downloader.py::test_homepage_screenshot_uses_configured_author_root \
  tests/test_user_downloader.py::test_author_url_overwrites_existing_file \
  tests/test_user_downloader.py::test_author_url_skips_collect_only_context \
  tests/test_user_downloader.py::test_author_url_write_failure_does_not_raise -q
```

Expected: failures because `author_url.txt` is absent and `_save_author_home_url` is undefined.

- [ ] **Step 3: Write the minimal async implementation**

Add imports:

```python
import aiofiles

from core.metadata import build_author_home_url
```

Call the writer immediately before the existing screenshot call:

```python
await self._save_author_home_url(sec_uid, user_info, modes)
await self._save_homepage_screenshot(sec_uid, user_info, modes)
```

Add this method next to `_save_homepage_screenshot`:

```python
async def _save_author_home_url(
    self,
    sec_uid: str,
    user_info: Dict[str, Any],
    modes: List[str],
) -> None:
    normalized_modes = {str(mode or "").strip() for mode in modes}
    if sec_uid == "self" or normalized_modes.issubset(self.SELF_COLLECT_MODES):
        return
    effective_sec_uid = str(user_info.get("sec_uid") or sec_uid).strip()
    author_url = build_author_home_url(effective_sec_uid)
    if not author_url or effective_sec_uid == "self":
        logger.warning("Author homepage URL skipped because sec_uid is unavailable")
        return
    author_name = str(user_info.get("nickname") or "unknown")
    try:
        author_dir = self.file_manager.get_author_dir(
            author_name,
            author_sec_uid=effective_sec_uid,
            author_dir_style=self.config.get("author_dir") or "nickname",
        )
        async with aiofiles.open(
            (author_dir / "author_url.txt").resolve(), "w", encoding="utf-8"
        ) as output:
            await output.write(f"{author_url}\n")
    except Exception as exc:
        logger.warning("Author homepage URL failed for %s: %s", effective_sec_uid, exc)
```

- [ ] **Step 4: Run focused and module-level checks**

```bash
python -m pytest tests/test_user_downloader.py -q
ruff check core/user_downloader.py tests/test_user_downloader.py
```

Expected: all tests pass and Ruff exits 0.

- [ ] **Step 5: Commit the CLI behavior**

```bash
git add core/user_downloader.py tests/test_user_downloader.py
git commit -m "feat(download): 保存博主主页地址文件"
```

### Task 2: Mirror the backend and explain the output in Desktop settings

**Files:**
- Modify: `core/user_downloader.py:1-150`
- Modify: `tests/test_user_downloader.py:340-490`
- Modify: `desktop/src/renderer/pages/Settings.tsx:3044-3058`
- Modify: `desktop/src/renderer/pages/Settings.test.tsx:2064-2087`

**Interfaces:**
- Consumes Task 1's `_save_author_home_url(...)`; produces equivalent desktop behavior plus settings copy describing `author_url.txt`.

- [ ] **Step 1: Create a clean isolated desktop worktree**

Create branch `codex/author-url-file` from the desktop repository's current local `main`. Do not modify the dirty main checkout. Confirm the new worktree starts clean.

- [ ] **Step 2: Apply backend tests and confirm RED**

Apply Task 1's test assertions and direct tests to desktop `tests/test_user_downloader.py`. Run the same five pytest node IDs. Expected: URL assertions and undefined-method tests fail.

- [ ] **Step 3: Apply backend implementation and confirm GREEN**

Apply Task 1's import, call site, and method to desktop `core/user_downloader.py` without altering its existing disk-based incremental block. Run:

```bash
python -m pytest tests/test_user_downloader.py -q
ruff check core/user_downloader.py tests/test_user_downloader.py
```

Expected: all tests pass and Ruff exits 0.

- [ ] **Step 4: Write the failing Settings copy assertion**

```typescript
expect(
  screen.getByText(
    '主页地址始终保存为 author_url.txt；开启后额外保存主页首屏截图，两者每次下载都会覆盖。',
  ),
).toBeTruthy()
```

From `desktop/`, run:

```bash
npm test -- src/renderer/pages/Settings.test.tsx
```

Expected: FAIL because the old screenshot-only copy is still rendered.

- [ ] **Step 5: Update the Settings explanation**

```tsx
{t(
  '主页地址始终保存为 author_url.txt；开启后额外保存主页首屏截图，两者每次下载都会覆盖。',
  'The homepage URL is always saved as author_url.txt. Enable this to also save the first viewport; both are replaced on each download.',
)}
```

- [ ] **Step 6: Verify Desktop backend and UI**

From the desktop repository root:

```bash
python -m pytest tests/test_user_downloader.py -q
ruff check core/user_downloader.py tests/test_user_downloader.py
```

From `desktop/`:

```bash
npm test -- src/renderer/pages/Settings.test.tsx
npm run typecheck
```

Expected: pytest, Ruff, Vitest, and TypeScript all pass.

- [ ] **Step 7: Commit the Desktop behavior and explanation**

```bash
git add \
  core/user_downloader.py \
  tests/test_user_downloader.py \
  desktop/src/renderer/pages/Settings.tsx \
  desktop/src/renderer/pages/Settings.test.tsx
git commit -m "feat(download): 保存博主主页地址文件"
```

### Task 3: Cross-repository verification and review

**Files:**
- Verify: CLI and desktop feature commits
- Verify: approved design and implementation plan

**Interfaces:**
- Consumes both implementations; produces test evidence, feature-hunk parity evidence, and an independent review result.

- [ ] **Step 1: Compare feature hunks**

Compare the imports, `download()` call site, `_save_author_home_url(...)`, and new tests between both worktrees. The pre-existing desktop-only `force_download` block may differ; every new URL-file line must match.

- [ ] **Step 2: Run full CLI Python verification**

```bash
python -m pytest tests/
ruff check .
```
Expected: full pytest and Ruff pass.

- [ ] **Step 3: Run full Desktop Python verification**

```bash
python -m pytest tests/
ruff check .
```

Expected: full pytest and Ruff pass; report only independently reproduced baseline failures as pre-existing.

- [ ] **Step 4: Request independent code review**

Use `superpowers:requesting-code-review` with a fresh read-only reviewer. Supply both worktree paths, approved spec, plan, commit SHAs, and verification output. Fix validated findings serially and rerun affected checks.

- [ ] **Step 5: Run completion verification**

Use `superpowers:verification-before-completion`. Verify worktree status, final commits, tests, lint, frontend typecheck, and settings test. Report pre-existing branch divergence without merging, pushing, or changing remote history.
