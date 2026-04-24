"""纯 Python 的后台下载任务模型，不依赖 FastAPI。

将 job 生命周期从 HTTP 层解耦，便于被 CLI 以外的入口复用（如未来的 MCP server）。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional


def _now_iso() -> str:
    # 统一使用 timezone-aware UTC ISO-8601 字符串
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class JobStatus:
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"

    TERMINAL = frozenset({SUCCESS, FAILED})


class DownloadJob:
    def __init__(self, job_id: str, url: str):
        self.job_id = job_id
        self.url = url
        self.status = JobStatus.PENDING
        self.created_at = _now_iso()
        self.started_at: Optional[str] = None
        self.finished_at: Optional[str] = None
        # 单调时钟时间戳，用于 TTL / LRU 剪裁（不受系统时钟跳变影响）
        self.finished_monotonic: Optional[float] = None
        self.total = 0
        self.success = 0
        self.failed = 0
        self.skipped = 0
        self.error: Optional[str] = None
        self._task: Optional[asyncio.Task] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "url": self.url,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "total": self.total,
            "success": self.success,
            "failed": self.failed,
            "skipped": self.skipped,
            "error": self.error,
        }


class JobManager:
    """内存 job 存储 + 并发执行器，带 TTL + 容量上限。

    不做持久化——进程重启就丢失——因为当前目标只是暴露 HTTP 接口。
    如需持久化可以后续在此加一层 SQLite。

    剪裁策略：
    - 每次 submit 前先剪裁一次：
        a. 丢弃 finished_monotonic 超过 job_ttl_seconds 的终态 job；
        b. 若剩余总数仍超过 max_jobs，按 finished_monotonic 升序淘汰最老的终态 job；
        c. in-flight（pending/running）job 永不淘汰。
    """

    DEFAULT_MAX_JOBS = 500
    DEFAULT_JOB_TTL_SECONDS = 24 * 3600  # 24 小时

    def __init__(
        self,
        executor: Callable[[str], Awaitable[Dict[str, int]]],
        *,
        max_concurrency: int = 2,
        max_jobs: int = DEFAULT_MAX_JOBS,
        job_ttl_seconds: float = DEFAULT_JOB_TTL_SECONDS,
    ):
        self.executor = executor
        self._jobs: Dict[str, DownloadJob] = {}
        self._semaphore = asyncio.Semaphore(max(1, max_concurrency))
        self._lock = asyncio.Lock()
        self.max_jobs = max(1, int(max_jobs))
        self.job_ttl_seconds = max(0.0, float(job_ttl_seconds))

    async def submit(self, url: str) -> DownloadJob:
        job_id = uuid.uuid4().hex[:12]
        job = DownloadJob(job_id=job_id, url=url)
        async with self._lock:
            self._prune_locked()
            self._jobs[job_id] = job
        # 异步调度，立即返回 job 给调用方
        job._task = asyncio.create_task(self._run(job))
        return job

    def _prune_locked(self) -> None:
        """持锁内调用：按 TTL + 容量上限剪裁终态 job。"""
        now = time.monotonic()

        # 1) TTL
        if self.job_ttl_seconds > 0:
            expired_ids = [
                jid
                for jid, j in self._jobs.items()
                if j.status in JobStatus.TERMINAL
                and j.finished_monotonic is not None
                and (now - j.finished_monotonic) > self.job_ttl_seconds
            ]
            for jid in expired_ids:
                self._jobs.pop(jid, None)

        # 2) 容量上限：只淘汰终态 job，保留 in-flight
        if len(self._jobs) < self.max_jobs:
            return
        terminal_jobs = [
            (j.finished_monotonic or 0.0, jid)
            for jid, j in self._jobs.items()
            if j.status in JobStatus.TERMINAL
        ]
        terminal_jobs.sort(key=lambda pair: pair[0])
        overflow = len(self._jobs) - self.max_jobs + 1  # +1 是为新 job 腾位
        for _, jid in terminal_jobs[:overflow]:
            self._jobs.pop(jid, None)

    async def _run(self, job: DownloadJob) -> None:
        async with self._semaphore:
            job.status = JobStatus.RUNNING
            job.started_at = _now_iso()
            try:
                counts = await self.executor(job.url)
                job.total = int(counts.get("total", 0))
                job.success = int(counts.get("success", 0))
                job.failed = int(counts.get("failed", 0))
                job.skipped = int(counts.get("skipped", 0))
                # 只要跑完就是 success；具体成功/失败个数通过字段区分
                job.status = (
                    JobStatus.SUCCESS if job.failed == 0 else JobStatus.FAILED
                )
            except Exception as exc:
                job.status = JobStatus.FAILED
                job.error = f"{type(exc).__name__}: {exc}"
            finally:
                job.finished_at = _now_iso()
                job.finished_monotonic = time.monotonic()

    async def get(self, job_id: str) -> Optional[DownloadJob]:
        async with self._lock:
            return self._jobs.get(job_id)

    async def list_jobs(self) -> List[DownloadJob]:
        async with self._lock:
            return list(self._jobs.values())

    async def shutdown(self) -> None:
        """等待所有 pending/running 任务结束。"""
        tasks = [j._task for j in self._jobs.values() if j._task is not None]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
