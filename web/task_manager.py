import asyncio
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from web.schemas import TaskInfo, TaskProgress, TaskStatus
from utils.logger import setup_logger

logger = setup_logger("TaskManager")


class TaskManager:
    _instance: Optional["TaskManager"] = None
    _lock = asyncio.Lock()

    def __new__(cls) -> "TaskManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._tasks: Dict[str, TaskInfo] = {}
        self._task_handles: Dict[str, asyncio.Task] = {}
        self._cancel_events: Dict[str, asyncio.Event] = {}
        self._progress_callbacks: Dict[str, List[Callable]] = {}

    @staticmethod
    def generate_task_id() -> str:
        return str(uuid.uuid4())[:8]

    async def create_task(
        self,
        url: str,
        download_func: Callable,
        **kwargs,
    ) -> str:
        task_id = self.generate_task_id()
        task_info = TaskInfo(
            task_id=task_id,
            url=url,
            status=TaskStatus.PENDING,
            progress=TaskProgress(),
            created_at=datetime.now(),
        )
        self._tasks[task_id] = task_info
        self._cancel_events[task_id] = asyncio.Event()
        self._progress_callbacks[task_id] = []

        async def run_download():
            task_info.status = TaskStatus.RUNNING
            task_info.started_at = datetime.now()

            cancel_event = self._cancel_events.get(task_id)

            try:
                def progress_callback(progress: TaskProgress):
                    task_info.progress = progress
                    for cb in self._progress_callbacks.get(task_id, []):
                        try:
                            cb(progress)
                        except Exception as e:
                            logger.warning("Progress callback error: %s", e)

                result = await download_func(
                    url,
                    progress_callback=progress_callback,
                    cancel_event=cancel_event,
                    **kwargs,
                )

                if cancel_event and cancel_event.is_set():
                    task_info.status = TaskStatus.CANCELLED
                else:
                    task_info.status = TaskStatus.COMPLETED
                    task_info.result = {
                        "total": result.total if result else 0,
                        "success": result.success if result else 0,
                        "failed": result.failed if result else 0,
                        "skipped": result.skipped if result else 0,
                    }

            except asyncio.CancelledError:
                task_info.status = TaskStatus.CANCELLED
                logger.info("Task %s cancelled", task_id)
            except Exception as e:
                task_info.status = TaskStatus.FAILED
                task_info.error = str(e)
                logger.error("Task %s failed: %s", task_id, e)
            finally:
                task_info.finished_at = datetime.now()
                self._task_handles.pop(task_id, None)

        handle = asyncio.create_task(run_download())
        self._task_handles[task_id] = handle

        logger.info("Created task %s for URL: %s", task_id, url)
        return task_id

    def get_task(self, task_id: str) -> Optional[TaskInfo]:
        return self._tasks.get(task_id)

    def get_all_tasks(self) -> List[TaskInfo]:
        return list(self._tasks.values())

    async def cancel_task(self, task_id: str) -> bool:
        task_info = self._tasks.get(task_id)
        if not task_info:
            return False

        if task_info.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
            return False

        cancel_event = self._cancel_events.get(task_id)
        if cancel_event:
            cancel_event.set()

        handle = self._task_handles.get(task_id)
        if handle and not handle.done():
            handle.cancel()
            try:
                await handle
            except asyncio.CancelledError:
                pass

        task_info.status = TaskStatus.CANCELLED
        task_info.finished_at = datetime.now()
        logger.info("Cancelled task %s", task_id)
        return True

    def add_progress_callback(self, task_id: str, callback: Callable):
        if task_id in self._progress_callbacks:
            self._progress_callbacks[task_id].append(callback)

    def remove_progress_callback(self, task_id: str, callback: Callable):
        if task_id in self._progress_callbacks:
            try:
                self._progress_callbacks[task_id].remove(callback)
            except ValueError:
                pass

    def clear_completed_tasks(self, max_age_hours: int = 24):
        now = datetime.now()
        to_remove = []
        for task_id, task_info in self._tasks.items():
            if task_info.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
                if task_info.finished_at:
                    age_hours = (now - task_info.finished_at).total_seconds() / 3600
                    if age_hours > max_age_hours:
                        to_remove.append(task_id)

        for task_id in to_remove:
            self._tasks.pop(task_id, None)
            self._cancel_events.pop(task_id, None)
            self._progress_callbacks.pop(task_id, None)

        if to_remove:
            logger.info("Cleared %d old tasks", len(to_remove))

    def get_running_count(self) -> int:
        return sum(
            1 for t in self._tasks.values()
            if t.status == TaskStatus.RUNNING
        )


task_manager = TaskManager()
