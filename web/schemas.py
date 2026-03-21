from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DownloadRequest(BaseModel):
    url: str = Field(..., description="抖音视频/用户/合集/音乐链接")
    path: Optional[str] = Field(None, description="自定义保存路径")
    thread: Optional[int] = Field(None, description="并发线程数", ge=1, le=20)
    cover: Optional[bool] = Field(None, description="是否下载封面")
    music: Optional[bool] = Field(None, description="是否下载音乐")
    avatar: Optional[bool] = Field(None, description="是否下载头像")
    save_json: Optional[bool] = Field(None, description="是否保存元数据 JSON")


class DownloadResponse(BaseModel):
    task_id: str = Field(..., description="任务 ID")
    message: str = Field(..., description="响应消息")
    status: TaskStatus = Field(..., description="任务状态")


class TaskProgress(BaseModel):
    step: str = Field(default="", description="当前步骤")
    detail: str = Field(default="", description="步骤详情")
    item_total: int = Field(default=0, description="总项目数")
    item_current: int = Field(default=0, description="当前项目数")
    success: int = Field(default=0, description="成功数")
    failed: int = Field(default=0, description="失败数")
    skipped: int = Field(default=0, description="跳过数")


class TaskInfo(BaseModel):
    task_id: str = Field(..., description="任务 ID")
    url: str = Field(..., description="下载链接")
    status: TaskStatus = Field(..., description="任务状态")
    progress: TaskProgress = Field(default_factory=TaskProgress, description="进度信息")
    result: Optional[Dict[str, Any]] = Field(None, description="下载结果")
    error: Optional[str] = Field(None, description="错误信息")
    created_at: datetime = Field(..., description="创建时间")
    started_at: Optional[datetime] = Field(None, description="开始时间")
    finished_at: Optional[datetime] = Field(None, description="完成时间")


class TaskListResponse(BaseModel):
    tasks: List[TaskInfo] = Field(..., description="任务列表")
    total: int = Field(..., description="任务总数")


class HistoryItem(BaseModel):
    id: int = Field(..., description="记录 ID")
    url: str = Field(..., description="下载链接")
    url_type: str = Field(..., description="链接类型")
    total_count: int = Field(..., description="总数")
    success_count: int = Field(..., description="成功数")
    created_at: str = Field(..., description="创建时间")


class HistoryListResponse(BaseModel):
    items: List[HistoryItem] = Field(..., description="历史记录列表")
    total: int = Field(..., description="记录总数")


class ConfigResponse(BaseModel):
    path: str = Field(..., description="保存路径")
    thread: int = Field(..., description="并发线程数")
    cover: bool = Field(..., description="是否下载封面")
    music: bool = Field(..., description="是否下载音乐")
    avatar: bool = Field(..., description="是否下载头像")
    save_json: bool = Field(..., description="是否保存元数据")
    proxy: Optional[str] = Field(None, description="代理设置")
    rate_limit: float = Field(..., description="速率限制")


class ConfigUpdateRequest(BaseModel):
    path: Optional[str] = Field(None, description="保存路径")
    thread: Optional[int] = Field(None, description="并发线程数", ge=1, le=20)
    cover: Optional[bool] = Field(None, description="是否下载封面")
    music: Optional[bool] = Field(None, description="是否下载音乐")
    avatar: Optional[bool] = Field(None, description="是否下载头像")
    save_json: Optional[bool] = Field(None, description="是否保存元数据")
    proxy: Optional[str] = Field(None, description="代理设置")
    rate_limit: Optional[float] = Field(None, description="速率限制", ge=0.1, le=100)


class CookieUpdateRequest(BaseModel):
    cookies: Dict[str, str] = Field(..., description="Cookie 键值对")


class URLParseResponse(BaseModel):
    original_url: str = Field(..., description="原始 URL")
    url_type: str = Field(..., description="URL 类型")
    aweme_id: Optional[str] = Field(None, description="视频 ID")
    sec_uid: Optional[str] = Field(None, description="用户 ID")
    mix_id: Optional[str] = Field(None, description="合集 ID")
    music_id: Optional[str] = Field(None, description="音乐 ID")


class ErrorResponse(BaseModel):
    error: str = Field(..., description="错误信息")
    detail: Optional[str] = Field(None, description="详细错误")
