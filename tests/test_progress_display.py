from types import SimpleNamespace

from cli.progress_display import ProgressDisplay


class _FakeProgress:
    def __init__(self):
        self.tasks = {}
        self.removed = []
        self._next_id = 1
        self.console = SimpleNamespace(print=lambda *_args, **_kwargs: None)

    def add_task(self, description, total, completed=0, detail="", **kwargs):
        task_id = self._next_id
        self._next_id += 1
        self.tasks[task_id] = {
            "description": description,
            "total": total,
            "completed": completed,
            "detail": detail,
        }
        self.tasks[task_id].update(kwargs)
        return task_id

    def update(self, task_id, **kwargs):
        self.tasks[task_id].update(kwargs)

    def advance(self, task_id, advance=1):
        self.tasks[task_id]["completed"] = self.tasks[task_id].get("completed", 0) + advance

    def remove_task(self, task_id):
        self.removed.append(task_id)
        self.tasks.pop(task_id, None)


class _FakeProgressContext:
    def __init__(self, progress):
        self.progress = progress
        self.exited = False

    def __enter__(self):
        return self.progress

    def __exit__(self, *_args):
        self.exited = True


def test_single_url_overall_progress_follows_item_count(monkeypatch):
    display = ProgressDisplay()
    fake_progress = _FakeProgress()
    fake_ctx = _FakeProgressContext(fake_progress)
    monkeypatch.setattr(display, "create_progress", lambda: fake_ctx)

    display.start_download_session(1)
    overall_task_id = display._overall_task_id
    assert overall_task_id is not None
    assert fake_progress.tasks[overall_task_id]["total"] == 1

    display.start_url(1, 1, "https://example.com/u")
    display.set_item_total(5, "作品待下载")
    assert fake_progress.tasks[overall_task_id]["total"] == 5
    assert fake_progress.tasks[overall_task_id]["completed"] == 0

    display.advance_item("success", "a1")
    display.advance_item("failed", "a2")
    assert fake_progress.tasks[overall_task_id]["completed"] == 2

    display.complete_url(SimpleNamespace(success=3, failed=1, skipped=1))
    assert fake_progress.tasks[overall_task_id]["completed"] == 5


def test_multi_url_overall_progress_stays_url_based(monkeypatch):
    display = ProgressDisplay()
    fake_progress = _FakeProgress()
    fake_ctx = _FakeProgressContext(fake_progress)
    monkeypatch.setattr(display, "create_progress", lambda: fake_ctx)

    display.start_download_session(2)
    overall_task_id = display._overall_task_id
    assert overall_task_id is not None
    assert fake_progress.tasks[overall_task_id]["total"] == 2

    display.start_url(1, 2, "https://example.com/u1")
    display.set_item_total(8, "作品待下载")
    display.advance_item("success", "a1")
    assert fake_progress.tasks[overall_task_id]["completed"] == 0

    display.complete_url(SimpleNamespace(success=8, failed=0, skipped=0))
    assert fake_progress.tasks[overall_task_id]["completed"] == 1

    display.start_url(2, 2, "https://example.com/u2")
    display.fail_url("url failed")
    assert fake_progress.tasks[overall_task_id]["completed"] == 2


def test_advance_item_shows_skip_or_failure_reason(monkeypatch):
    display = ProgressDisplay()
    fake_progress = _FakeProgress()
    monkeypatch.setattr(display, "create_progress", lambda: _FakeProgressContext(fake_progress))

    display.start_download_session(1)
    display.start_url(1, 1, "https://example.com/u")
    display.set_item_total(2, "作品待下载")
    item_task_id = display._item_task_id

    display.advance_item("skipped", "a1", reason="下载目录里已有该作品")
    assert fake_progress.tasks[item_task_id]["detail"] == "最近: 跳过 a1 · 下载目录里已有该作品"

    display.advance_item("success", "a2")
    assert fake_progress.tasks[item_task_id]["detail"] == "最近: 成功 a2"


def test_show_item_reasons_groups_reasons_across_urls(monkeypatch):
    display = ProgressDisplay()
    fake_progress = _FakeProgress()
    monkeypatch.setattr(display, "create_progress", lambda: _FakeProgressContext(fake_progress))
    printed = []
    monkeypatch.setattr(display, "_active_console", lambda: SimpleNamespace(print=printed.append))

    display.start_download_session(2)
    display.start_url(1, 2, "https://example.com/u1")
    display.set_item_total(3, "作品待下载")
    display.advance_item("skipped", "a1", reason="下载目录里已有该作品")
    display.advance_item("failed", "a2", reason="视频所有下载线路均失败，请稍后重试")
    display.advance_item("success", "a3")
    display.start_url(2, 2, "https://example.com/u2")
    display.set_item_total(1, "作品待下载")
    display.advance_item("skipped", "b1", reason="下载目录里已有该作品")
    display.stop_download_session()

    display.show_item_reasons()

    assert len(printed) == 1
    table = printed[0]
    rows = list(zip(*(column._cells for column in table.columns)))
    assert rows == [
        ("跳过", "下载目录里已有该作品", "2"),
        ("失败", "视频所有下载线路均失败，请稍后重试", "1"),
    ]


def test_show_item_reasons_prints_nothing_without_reasons(monkeypatch):
    display = ProgressDisplay()
    printed = []
    monkeypatch.setattr(display, "_active_console", lambda: SimpleNamespace(print=printed.append))

    display.show_item_reasons()

    assert printed == []


def test_rollback_url_item_reasons_drops_counts_from_aborted_attempt(monkeypatch):
    """CLI 重新登录后会整条 URL 重跑；上一轮已结算的原因不能再算一遍。"""
    display = ProgressDisplay()
    fake_progress = _FakeProgress()
    monkeypatch.setattr(display, "create_progress", lambda: _FakeProgressContext(fake_progress))
    printed = []
    monkeypatch.setattr(display, "_active_console", lambda: SimpleNamespace(print=printed.append))

    display.start_download_session(2)
    display.start_url(1, 2, "https://example.com/u1")
    display.set_item_total(1, "作品待下载")
    display.advance_item("skipped", "a1", reason="下载目录里已有该作品")
    display.start_url(2, 2, "https://example.com/u2")
    display.set_item_total(1, "作品待下载")
    display.advance_item("skipped", "b1", reason="下载目录里已有该作品")
    display.rollback_url_item_reasons()
    display.advance_item("skipped", "b1", reason="下载目录里已有该作品")
    display.stop_download_session()

    display.show_item_reasons()

    rows = list(zip(*(column._cells for column in printed[0].columns)))
    assert rows == [("跳过", "下载目录里已有该作品", "2")]
