import sqlite3
import subprocess
import os
from pathlib import Path
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import List, Dict, Any

router = APIRouter()
DB_PATH = Path(__file__).parent.parent.parent / "dy_downloader.db"


def _get_conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


@router.get("/stats")
def get_stats() -> Dict[str, Any]:
    """获取总体统计数据"""
    if not DB_PATH.exists():
        return {"total_aweme": 0, "total_authors": 0, "total_history": 0}

    try:
        conn = _get_conn()
        c = conn.cursor()

        c.execute("SELECT COUNT(*) FROM aweme")
        total_aweme = c.fetchone()[0]

        c.execute("SELECT COUNT(DISTINCT author_id) FROM aweme WHERE author_id IS NOT NULL")
        total_authors = c.fetchone()[0]

        c.execute("SELECT COUNT(*) FROM download_history")
        total_history = c.fetchone()[0]

        conn.close()
        return {
            "total_aweme": total_aweme,
            "total_authors": total_authors,
            "total_history": total_history,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/aweme")
def get_aweme_list(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    author: str = Query("", description="按作者名筛选"),
    keyword: str = Query("", description="按标题关键词搜索"),
) -> Dict[str, Any]:
    """获取已下载作品列表（带分页和筛选）"""
    if not DB_PATH.exists():
        return {"items": [], "total": 0}

    try:
        conn = _get_conn()
        c = conn.cursor()

        where_parts = []
        params: list = []

        if author:
            where_parts.append("author_name LIKE ?")
            params.append(f"%{author}%")
        if keyword:
            where_parts.append("title LIKE ?")
            params.append(f"%{keyword}%")

        where_sql = (" WHERE " + " AND ".join(where_parts)) if where_parts else ""

        # 总数
        c.execute(f"SELECT COUNT(*) FROM aweme{where_sql}", params)
        total = c.fetchone()[0]

        # 数据
        c.execute(
            f"""
            SELECT aweme_id, aweme_type, title, author_id, author_name,
                   create_time, download_time, file_path
            FROM aweme{where_sql}
            ORDER BY download_time DESC
            LIMIT ? OFFSET ?
            """,
            params + [limit, offset],
        )
        rows = c.fetchall()
        items = [dict(row) for row in rows]

        conn.close()
        return {"items": items, "total": total}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/download_history")
def get_download_history(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> Dict[str, Any]:
    """获取下载任务历史"""
    if not DB_PATH.exists():
        return {"items": [], "total": 0}

    try:
        conn = _get_conn()
        c = conn.cursor()

        c.execute("SELECT COUNT(*) FROM download_history")
        total = c.fetchone()[0]

        c.execute(
            """
            SELECT id, url, url_type, download_time, total_count, success_count
            FROM download_history
            ORDER BY download_time DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )
        rows = c.fetchall()
        items = [dict(row) for row in rows]

        conn.close()
        return {"items": items, "total": total}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/open_folder")
def open_folder(data: dict):
    """在Windows资源管理器中打开文件所在目录"""
    file_path = data.get("file_path", "")
    if not file_path:
        raise HTTPException(status_code=400, detail="缺少 file_path")

    target = Path(file_path)

    # 如果是文件，打开所在目录并选中文件
    if target.is_file():
        subprocess.Popen(["explorer", "/select,", str(target)])
    # 如果是目录，直接打开
    elif target.is_dir():
        subprocess.Popen(["explorer", str(target)])
    # 如果文件不存在，尝试打开父目录
    elif target.parent.is_dir():
        subprocess.Popen(["explorer", str(target.parent)])
    else:
        raise HTTPException(status_code=404, detail="路径不存在")

    return {"ok": True}


class RenameRequest(BaseModel):
    aweme_id: str
    new_title: str


@router.post("/rename")
def rename_item(req: RenameRequest):
    """重命名已下载的作品（更新数据库标题 + 磁盘文件/文件夹名）"""
    if not DB_PATH.exists():
        raise HTTPException(status_code=404, detail="数据库不存在")

    conn = _get_conn()
    c = conn.cursor()

    c.execute("SELECT file_path, title FROM aweme WHERE aweme_id = ?", (req.aweme_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="作品不存在")

    old_path = row["file_path"]
    old_title = row["title"] or ""
    new_title = req.new_title.strip()

    if not new_title:
        conn.close()
        raise HTTPException(status_code=400, detail="标题不能为空")

    # 更新数据库标题
    c.execute("UPDATE aweme SET title = ? WHERE aweme_id = ?", (new_title, req.aweme_id))
    conn.commit()

    rename_result = "仅更新了数据库标题"

    # 尝试重命名磁盘上的文件/文件夹
    if old_path:
        old_p = Path(old_path)
        if old_p.exists():
            try:
                # 如果是文件，替换文件名中的旧标题
                if old_p.is_file():
                    old_name = old_p.stem
                    new_name = old_name.replace(old_title, new_title) if old_title in old_name else new_title
                    new_p = old_p.with_stem(new_name)
                    old_p.rename(new_p)
                    c.execute("UPDATE aweme SET file_path = ? WHERE aweme_id = ?", (str(new_p), req.aweme_id))
                    conn.commit()
                    rename_result = "已重命名文件"
                # 如果是目录
                elif old_p.is_dir():
                    parent = old_p.parent
                    old_dir_name = old_p.name
                    new_dir_name = old_dir_name.replace(old_title, new_title) if old_title in old_dir_name else new_title
                    new_p = parent / new_dir_name
                    old_p.rename(new_p)
                    c.execute("UPDATE aweme SET file_path = ? WHERE aweme_id = ?", (str(new_p), req.aweme_id))
                    conn.commit()
                    rename_result = "已重命名文件夹"
            except Exception as e:
                rename_result = f"数据库已更新，但文件重命名失败: {str(e)}"

    conn.close()
    return {"ok": True, "detail": rename_result}
