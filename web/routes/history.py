from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from storage import Database
from web.schemas import ErrorResponse, HistoryItem, HistoryListResponse
from utils.logger import setup_logger

logger = setup_logger("HistoryRoute")

router = APIRouter(prefix="/api", tags=["history"])

_database: Optional[Database] = None
_config_path: str = "config.yml"


def set_config_path(config_path: str):
    global _config_path, _database
    _config_path = config_path
    _database = None


async def get_database() -> Optional[Database]:
    global _database
    if _database is None:
        from config import ConfigLoader
        config = ConfigLoader(_config_path)
        if config.get("database"):
            db_path = config.get("database_path", "dy_downloader.db") or "dy_downloader.db"
            _database = Database(db_path=str(db_path))
            await _database.initialize()
    return _database


@router.get(
    "/history",
    response_model=HistoryListResponse,
    summary="获取下载历史",
    description="获取历史下载记录列表",
)
async def get_history(
    limit: int = Query(default=20, ge=1, le=100, description="返回记录数量"),
    offset: int = Query(default=0, ge=0, description="偏移量"),
    url_type: Optional[str] = Query(default=None, description="按 URL 类型筛选"),
    database: Optional[Database] = Depends(get_database),
):
    if not database:
        raise HTTPException(status_code=400, detail="数据库未启用")

    try:
        records = await database.get_history(limit=limit, offset=offset, url_type=url_type)
        total = await database.count_history(url_type=url_type)

        items: List[HistoryItem] = []
        for record in records:
            items.append(HistoryItem(
                id=record.get("id", 0),
                url=record.get("url", ""),
                url_type=record.get("url_type", "unknown"),
                total_count=record.get("total_count", 0),
                success_count=record.get("success_count", 0),
                created_at=record.get("created_at", ""),
            ))

        return HistoryListResponse(items=items, total=total)
    except Exception as e:
        logger.error("Failed to get history: %s", e)
        raise HTTPException(status_code=500, detail=f"获取历史记录失败: {str(e)}")


@router.delete(
    "/history/{record_id}",
    summary="删除历史记录",
    description="删除指定的历史下载记录",
)
async def delete_history_record(
    record_id: int,
    database: Optional[Database] = Depends(get_database),
):
    if not database:
        raise HTTPException(status_code=400, detail="数据库未启用")

    try:
        success = await database.delete_history_record(record_id)
        if not success:
            raise HTTPException(status_code=404, detail=f"记录不存在: {record_id}")
        return {"message": f"已删除记录 {record_id}"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to delete history record: %s", e)
        raise HTTPException(status_code=500, detail=f"删除记录失败: {str(e)}")


@router.delete(
    "/history",
    summary="清空历史记录",
    description="清空所有历史下载记录",
)
async def clear_history(
    database: Optional[Database] = Depends(get_database),
):
    if not database:
        raise HTTPException(status_code=400, detail="数据库未启用")

    try:
        await database.clear_history()
        return {"message": "已清空所有历史记录"}
    except Exception as e:
        logger.error("Failed to clear history: %s", e)
        raise HTTPException(status_code=500, detail=f"清空历史记录失败: {str(e)}")
