from fastapi import APIRouter, HTTPException

from web.schemas import ErrorResponse, TaskInfo, TaskListResponse
from web.task_manager import task_manager

router = APIRouter(prefix="/api", tags=["task"])


@router.get(
    "/task/{task_id}",
    response_model=TaskInfo,
    responses={404: {"model": ErrorResponse}},
    summary="查询任务状态",
    description="根据任务 ID 查询下载任务的状态和进度",
)
async def get_task_status(task_id: str):
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
    return task


@router.get(
    "/task/{task_id}/cancel",
    response_model=TaskInfo,
    responses={404: {"model": ErrorResponse}},
    summary="取消任务",
    description="取消正在运行或等待中的下载任务",
)
async def cancel_task(task_id: str):
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")

    success = await task_manager.cancel_task(task_id)
    if not success:
        raise HTTPException(
            status_code=400,
            detail="无法取消任务：任务可能已完成或已取消",
        )

    return task_manager.get_task(task_id)


@router.get(
    "/tasks",
    response_model=TaskListResponse,
    summary="获取所有任务",
    description="获取所有下载任务的列表",
)
async def get_all_tasks():
    tasks = task_manager.get_all_tasks()
    return TaskListResponse(tasks=tasks, total=len(tasks))


@router.delete(
    "/tasks/clear",
    summary="清理已完成任务",
    description="清理已完成、失败或取消的任务（默认清理 24 小时前的任务）",
)
async def clear_completed_tasks(max_age_hours: int = 24):
    task_manager.clear_completed_tasks(max_age_hours=max_age_hours)
    return {"message": f"已清理 {max_age_hours} 小时前的已完成任务"}


@router.get(
    "/tasks/stats",
    summary="获取任务统计",
    description="获取当前任务的统计信息",
)
async def get_task_stats():
    all_tasks = task_manager.get_all_tasks()
    stats = {
        "total": len(all_tasks),
        "pending": 0,
        "running": 0,
        "completed": 0,
        "failed": 0,
        "cancelled": 0,
    }
    for task in all_tasks:
        status = task.status.value
        if status in stats:
            stats[status] += 1
    return stats
