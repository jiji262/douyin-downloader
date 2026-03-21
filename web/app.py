import argparse
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from web.routes import download, task, history, config
from web.task_manager import task_manager
from utils.logger import setup_logger, set_console_log_level

logger = setup_logger("WebApp")

_config_path: Optional[str] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Douyin Downloader Web Server")
    task_manager.clear_completed_tasks(max_age_hours=1)
    yield
    logger.info("Shutting down Douyin Downloader Web Server")


def create_app(config_path: str = "config.yml") -> FastAPI:
    global _config_path
    _config_path = config_path

    download.set_config_path(config_path)
    history.set_config_path(config_path)
    config.set_config_path(config_path)

    app = FastAPI(
        title="Douyin Downloader API",
        description="抖音视频/用户/合集/音乐下载服务 API",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(download.router)
    app.include_router(task.router)
    app.include_router(history.router)
    app.include_router(config.router)

    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def root():
        index_path = Path(__file__).parent / "static" / "index.html"
        if index_path.exists():
            return HTMLResponse(content=index_path.read_text(encoding="utf-8"))
        return HTMLResponse(content=get_default_html())

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        logger.error("Unhandled exception: %s", exc, exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"error": "Internal server error", "detail": str(exc)},
        )

    return app


def get_default_html() -> str:
    return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>抖音下载器</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            min-height: 100vh;
            color: #fff;
            padding: 20px;
        }
        .container { max-width: 1200px; margin: 0 auto; }
        h1 { text-align: center; margin-bottom: 30px; font-size: 2em; }
        .card {
            background: rgba(255,255,255,0.1);
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 20px;
            backdrop-filter: blur(10px);
        }
        .card h2 { margin-bottom: 15px; font-size: 1.2em; border-bottom: 1px solid rgba(255,255,255,0.2); padding-bottom: 10px; }
        .form-group { margin-bottom: 15px; }
        label { display: block; margin-bottom: 5px; font-size: 0.9em; opacity: 0.8; }
        input[type="text"], textarea {
            width: 100%;
            padding: 12px;
            border: none;
            border-radius: 8px;
            background: rgba(255,255,255,0.15);
            color: #fff;
            font-size: 1em;
        }
        input[type="text"]:focus, textarea:focus { outline: none; background: rgba(255,255,255,0.2); }
        textarea { resize: vertical; min-height: 80px; }
        .btn {
            padding: 12px 24px;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            font-size: 1em;
            font-weight: 600;
            transition: all 0.3s;
        }
        .btn-primary { background: #e94560; color: #fff; }
        .btn-primary:hover { background: #ff6b6b; }
        .btn-secondary { background: rgba(255,255,255,0.2); color: #fff; }
        .btn-secondary:hover { background: rgba(255,255,255,0.3); }
        .btn-group { display: flex; gap: 10px; flex-wrap: wrap; }
        .task-list { list-style: none; }
        .task-item {
            background: rgba(255,255,255,0.05);
            border-radius: 8px;
            padding: 15px;
            margin-bottom: 10px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 10px;
        }
        .task-info { flex: 1; min-width: 200px; }
        .task-url { font-size: 0.9em; word-break: break-all; opacity: 0.8; }
        .task-status {
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 0.8em;
            font-weight: 600;
        }
        .status-pending { background: #ffd93d; color: #333; }
        .status-running { background: #6bcb77; color: #fff; }
        .status-completed { background: #4d96ff; color: #fff; }
        .status-failed { background: #ff6b6b; color: #fff; }
        .status-cancelled { background: #888; color: #fff; }
        .progress-bar {
            width: 100%;
            height: 6px;
            background: rgba(255,255,255,0.2);
            border-radius: 3px;
            margin-top: 10px;
            overflow: hidden;
        }
        .progress-fill {
            height: 100%;
            background: #e94560;
            transition: width 0.3s;
        }
        .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(100px, 1fr)); gap: 15px; margin-top: 15px; }
        .stat-item { text-align: center; }
        .stat-value { font-size: 1.5em; font-weight: 700; }
        .stat-label { font-size: 0.8em; opacity: 0.7; }
        .toast {
            position: fixed;
            bottom: 20px;
            right: 20px;
            padding: 15px 25px;
            border-radius: 8px;
            background: #333;
            color: #fff;
            z-index: 1000;
            animation: slideIn 0.3s ease;
        }
        @keyframes slideIn { from { transform: translateX(100%); opacity: 0; } to { transform: translateX(0); opacity: 1; } }
        .loading { display: inline-block; width: 20px; height: 20px; border: 2px solid #fff; border-radius: 50%; border-top-color: transparent; animation: spin 1s linear infinite; }
        @keyframes spin { to { transform: rotate(360deg); } }
        .api-link { text-align: center; margin-top: 20px; opacity: 0.7; }
        .api-link a { color: #e94560; text-decoration: none; }
    </style>
</head>
<body>
    <div class="container">
        <h1>🎬 抖音下载器</h1>
        
        <div class="card">
            <h2>📥 提交下载任务</h2>
            <div class="form-group">
                <label>抖音链接（视频/用户主页/合集/音乐）</label>
                <textarea id="urlInput" placeholder="粘贴抖音链接，支持多个链接（每行一个）"></textarea>
            </div>
            <div class="btn-group">
                <button class="btn btn-primary" onclick="submitDownload()">开始下载</button>
                <button class="btn btn-secondary" onclick="parseUrl()">解析链接</button>
            </div>
        </div>

        <div class="card">
            <h2>📊 任务状态</h2>
            <div class="btn-group" style="margin-bottom: 15px;">
                <button class="btn btn-secondary" onclick="refreshTasks()">刷新</button>
                <button class="btn btn-secondary" onclick="clearCompleted()">清理已完成</button>
            </div>
            <div id="statsContainer" class="stats"></div>
            <ul id="taskList" class="task-list"></ul>
        </div>

        <div class="card">
            <h2>⚙️ 配置</h2>
            <div id="configContainer"></div>
        </div>

        <div class="api-link">
            <a href="/docs">📚 API 文档</a>
        </div>
    </div>

    <script>
        async function fetchAPI(url, options = {}) {
            try {
                const response = await fetch(url, options);
                const data = await response.json();
                if (!response.ok) throw new Error(data.detail || data.error || '请求失败');
                return data;
            } catch (e) {
                showToast(e.message, 'error');
                throw e;
            }
        }

        function showToast(message, type = 'info') {
            const toast = document.createElement('div');
            toast.className = 'toast';
            toast.style.background = type === 'error' ? '#ff6b6b' : '#4d96ff';
            toast.textContent = message;
            document.body.appendChild(toast);
            setTimeout(() => toast.remove(), 3000);
        }

        async function submitDownload() {
            const urls = document.getElementById('urlInput').value.trim().split('\\n').filter(u => u.trim());
            if (!urls.length) return showToast('请输入链接', 'error');
            
            for (const url of urls) {
                try {
                    const data = await fetchAPI('/api/download', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ url: url.trim() })
                    });
                    showToast(`任务已创建: ${data.task_id}`);
                } catch (e) {}
            }
            document.getElementById('urlInput').value = '';
            refreshTasks();
        }

        async function parseUrl() {
            const url = document.getElementById('urlInput').value.trim();
            if (!url) return showToast('请输入链接', 'error');
            try {
                const data = await fetchAPI(`/api/url/parse?url=${encodeURIComponent(url)}`);
                showToast(`类型: ${data.url_type}`);
            } catch (e) {}
        }

        async function refreshTasks() {
            try {
                const [tasks, stats] = await Promise.all([
                    fetchAPI('/api/tasks'),
                    fetchAPI('/api/tasks/stats')
                ]);
                renderStats(stats);
                renderTasks(tasks.tasks);
            } catch (e) {}
        }

        function renderStats(stats) {
            document.getElementById('statsContainer').innerHTML = `
                <div class="stat-item"><div class="stat-value">${stats.total}</div><div class="stat-label">总任务</div></div>
                <div class="stat-item"><div class="stat-value">${stats.running}</div><div class="stat-label">运行中</div></div>
                <div class="stat-item"><div class="stat-value">${stats.completed}</div><div class="stat-label">已完成</div></div>
                <div class="stat-item"><div class="stat-value">${stats.failed}</div><div class="stat-label">失败</div></div>
            `;
        }

        function renderTasks(tasks) {
            const list = document.getElementById('taskList');
            if (!tasks.length) {
                list.innerHTML = '<li class="task-item">暂无任务</li>';
                return;
            }
            list.innerHTML = tasks.map(t => {
                const progress = t.progress || {};
                const percent = progress.item_total ? Math.round((progress.item_current / progress.item_total) * 100) : 0;
                return `
                    <li class="task-item">
                        <div class="task-info">
                            <div><strong>${t.task_id}</strong> <span class="task-status status-${t.status}">${t.status}</span></div>
                            <div class="task-url">${t.url}</div>
                            ${progress.step ? `<div style="font-size:0.8em;opacity:0.7;margin-top:5px;">${progress.step}: ${progress.detail}</div>` : ''}
                            ${t.status === 'running' ? `<div class="progress-bar"><div class="progress-fill" style="width:${percent}%"></div></div>` : ''}
                            ${t.result ? `<div style="font-size:0.8em;margin-top:5px;">✅ 成功:${t.result.success} ❌ 失败:${t.result.failed} ⏭️ 跳过:${t.result.skipped}</div>` : ''}
                            ${t.error ? `<div style="color:#ff6b6b;font-size:0.8em;">${t.error}</div>` : ''}
                        </div>
                        ${t.status === 'running' || t.status === 'pending' ? `<button class="btn btn-secondary" onclick="cancelTask('${t.task_id}')">取消</button>` : ''}
                    </li>
                `;
            }).join('');
        }

        async function cancelTask(taskId) {
            try {
                await fetchAPI(`/api/task/${taskId}/cancel`);
                showToast('任务已取消');
                refreshTasks();
            } catch (e) {}
        }

        async function clearCompleted() {
            try {
                await fetchAPI('/api/tasks/clear', { method: 'DELETE' });
                showToast('已清理完成的任务');
                refreshTasks();
            } catch (e) {}
        }

        async function loadConfig() {
            try {
                const data = await fetchAPI('/api/config');
                document.getElementById('configContainer').innerHTML = `
                    <div class="stats">
                        <div class="stat-item"><div class="stat-value">${data.thread}</div><div class="stat-label">并发数</div></div>
                        <div class="stat-item"><div class="stat-value">${data.cover ? '✅' : '❌'}</div><div class="stat-label">下载封面</div></div>
                        <div class="stat-item"><div class="stat-value">${data.music ? '✅' : '❌'}</div><div class="stat-label">下载音乐</div></div>
                        <div class="stat-item"><div class="stat-value">${data.avatar ? '✅' : '❌'}</div><div class="stat-label">下载头像</div></div>
                    </div>
                    <div style="margin-top:10px;font-size:0.9em;opacity:0.7;">保存路径: ${data.path}</div>
                `;
            } catch (e) {}
        }

        refreshTasks();
        loadConfig();
        setInterval(refreshTasks, 5000);
    </script>
</body>
</html>"""


def main():
    parser = argparse.ArgumentParser(description="Douyin Downloader Web Server")
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host to bind (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to bind (default: 8000)",
    )
    parser.add_argument(
        "--config",
        default="config.yml",
        help="Config file path (default: config.yml)",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable auto-reload for development",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging (INFO level)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging (DEBUG level)",
    )
    args = parser.parse_args()

    # 设置控制台日志级别
    if args.debug:
        set_console_log_level(logging.DEBUG)
    elif args.verbose:
        set_console_log_level(logging.INFO)
    else:
        set_console_log_level(logging.WARNING)

    if not os.path.exists(args.config):
        logger.warning("Config file not found: %s, using default settings", args.config)

    import uvicorn
    app = create_app(config_path=args.config)

    log_config = uvicorn.config.LOGGING_CONFIG
    log_config["formatters"]["access"]["fmt"] = '%(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s'
    log_config["loggers"]["uvicorn.access"]["level"] = "WARNING"
    log_config["loggers"]["uvicorn.error"]["level"] = "WARNING"

    uvicorn.run(
        "web.app:create_app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        factory=True,
        log_config=log_config,
    )


if __name__ == "__main__":
    main()
