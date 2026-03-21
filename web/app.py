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
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <meta name="theme-color" content="#1a1a2e">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
    <title>抖音下载器</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; -webkit-tap-highlight-color: transparent; }
        
        :root {
            --primary: #fe2c55;
            --primary-dark: #e91e45;
            --success: #25f4ee;
            --warning: #ffd93d;
            --danger: #ff6b6b;
            --bg-dark: #121212;
            --bg-card: #1e1e1e;
            --bg-card-hover: #252525;
            --text-primary: #ffffff;
            --text-secondary: rgba(255,255,255,0.7);
            --text-muted: rgba(255,255,255,0.5);
            --border-color: rgba(255,255,255,0.1);
            --shadow: 0 4px 20px rgba(0,0,0,0.3);
            --radius: 16px;
            --radius-sm: 10px;
        }
        
        html { font-size: 16px; scroll-behavior: smooth; }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif;
            background: var(--bg-dark);
            min-height: 100vh;
            color: var(--text-primary);
            line-height: 1.6;
            -webkit-font-smoothing: antialiased;
        }
        
        .container { 
            max-width: 800px; 
            margin: 0 auto; 
            padding: 16px;
            padding-bottom: 100px;
        }
        
        header {
            text-align: center;
            padding: 24px 0;
            margin-bottom: 16px;
        }
        
        h1 { 
            font-size: 1.75rem;
            font-weight: 700;
            background: linear-gradient(135deg, var(--primary), var(--success));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
            display: inline-flex;
            align-items: center;
            gap: 8px;
        }
        
        h1::before { content: '🎬'; -webkit-text-fill-color: initial; }
        
        .subtitle {
            color: var(--text-muted);
            font-size: 0.875rem;
            margin-top: 8px;
        }
        
        .card {
            background: var(--bg-card);
            border-radius: var(--radius);
            padding: 20px;
            margin-bottom: 16px;
            box-shadow: var(--shadow);
            border: 1px solid var(--border-color);
            transition: transform 0.2s, box-shadow 0.2s;
        }
        
        .card:active { transform: scale(0.98); }
        
        .card h2 { 
            font-size: 1rem;
            font-weight: 600;
            margin-bottom: 16px;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        
        .card h2::before {
            content: '';
            width: 4px;
            height: 16px;
            background: var(--primary);
            border-radius: 2px;
        }
        
        .form-group { margin-bottom: 16px; }
        
        label { 
            display: block;
            margin-bottom: 8px;
            font-size: 0.875rem;
            color: var(--text-secondary);
            font-weight: 500;
        }
        
        textarea {
            width: 100%;
            padding: 14px;
            border: 2px solid var(--border-color);
            border-radius: var(--radius-sm);
            background: var(--bg-dark);
            color: var(--text-primary);
            font-size: 1rem;
            resize: none;
            min-height: 100px;
            transition: border-color 0.2s, box-shadow 0.2s;
            font-family: inherit;
        }
        
        textarea:focus {
            outline: none;
            border-color: var(--primary);
            box-shadow: 0 0 0 3px rgba(254, 44, 85, 0.2);
        }
        
        textarea::placeholder { color: var(--text-muted); }
        
        .btn {
            padding: 14px 24px;
            border: none;
            border-radius: var(--radius-sm);
            cursor: pointer;
            font-size: 1rem;
            font-weight: 600;
            transition: all 0.2s;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            min-height: 48px;
            touch-action: manipulation;
        }
        
        .btn-primary { 
            background: var(--primary);
            color: #fff;
            flex: 1;
        }
        
        .btn-primary:active { 
            background: var(--primary-dark);
            transform: scale(0.96);
        }
        
        .btn-secondary { 
            background: var(--bg-dark);
            color: var(--text-primary);
            border: 1px solid var(--border-color);
        }
        
        .btn-secondary:active { 
            background: var(--bg-card-hover);
            transform: scale(0.96);
        }
        
        .btn-danger {
            background: rgba(255, 107, 107, 0.2);
            color: var(--danger);
            border: 1px solid rgba(255, 107, 107, 0.3);
        }
        
        .btn-group { 
            display: flex; 
            gap: 12px;
            flex-wrap: wrap;
        }
        
        .btn-group .btn { flex: 1; min-width: 120px; }
        
        .stats { 
            display: grid; 
            grid-template-columns: repeat(4, 1fr);
            gap: 12px;
            margin-bottom: 16px;
        }
        
        .stat-item { 
            text-align: center;
            padding: 12px 8px;
            background: var(--bg-dark);
            border-radius: var(--radius-sm);
        }
        
        .stat-value { 
            font-size: 1.5rem;
            font-weight: 700;
            color: var(--text-primary);
        }
        
        .stat-label { 
            font-size: 0.75rem;
            color: var(--text-muted);
            margin-top: 4px;
        }
        
        .stat-item.running .stat-value { color: var(--success); }
        .stat-item.completed .stat-value { color: #4d96ff; }
        .stat-item.failed .stat-value { color: var(--danger); }
        
        .task-list { list-style: none; }
        
        .task-item {
            background: var(--bg-dark);
            border-radius: var(--radius-sm);
            padding: 16px;
            margin-bottom: 12px;
            border: 1px solid var(--border-color);
            transition: background 0.2s;
        }
        
        .task-item:active { background: var(--bg-card-hover); }
        
        .task-header {
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            gap: 12px;
            margin-bottom: 8px;
        }
        
        .task-id {
            font-size: 0.875rem;
            font-weight: 600;
            word-break: break-all;
        }
        
        .task-status {
            padding: 4px 10px;
            border-radius: 20px;
            font-size: 0.75rem;
            font-weight: 600;
            white-space: nowrap;
            flex-shrink: 0;
        }
        
        .status-pending { background: rgba(255, 217, 61, 0.2); color: var(--warning); }
        .status-running { background: rgba(37, 244, 238, 0.2); color: var(--success); }
        .status-completed { background: rgba(77, 150, 255, 0.2); color: #4d96ff; }
        .status-failed { background: rgba(255, 107, 107, 0.2); color: var(--danger); }
        .status-cancelled { background: rgba(255,255,255,0.1); color: var(--text-muted); }
        
        .task-url {
            font-size: 0.8rem;
            color: var(--text-muted);
            word-break: break-all;
            margin-bottom: 8px;
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            overflow: hidden;
        }
        
        .task-progress {
            margin-top: 12px;
        }
        
        .progress-step {
            font-size: 0.8rem;
            color: var(--text-secondary);
            margin-bottom: 8px;
        }
        
        .progress-bar {
            width: 100%;
            height: 8px;
            background: var(--bg-card);
            border-radius: 4px;
            overflow: hidden;
        }
        
        .progress-fill {
            height: 100%;
            background: linear-gradient(90deg, var(--primary), var(--success));
            border-radius: 4px;
            transition: width 0.3s ease;
        }
        
        .task-result {
            display: flex;
            gap: 16px;
            margin-top: 12px;
            padding-top: 12px;
            border-top: 1px solid var(--border-color);
            font-size: 0.8rem;
        }
        
        .task-result span { color: var(--text-secondary); }
        .task-result .success { color: var(--success); }
        .task-result .fail { color: var(--danger); }
        .task-result .skip { color: var(--warning); }
        
        .task-error {
            margin-top: 8px;
            padding: 8px 12px;
            background: rgba(255, 107, 107, 0.1);
            border-radius: 8px;
            font-size: 0.8rem;
            color: var(--danger);
        }
        
        .task-actions {
            margin-top: 12px;
            display: flex;
            justify-content: flex-end;
        }
        
        .task-actions .btn {
            padding: 8px 16px;
            font-size: 0.875rem;
            min-height: 36px;
        }
        
        .empty-state {
            text-align: center;
            padding: 40px 20px;
            color: var(--text-muted);
        }
        
        .empty-state::before {
            content: '📋';
            font-size: 3rem;
            display: block;
            margin-bottom: 12px;
            opacity: 0.5;
        }
        
        .config-grid {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 12px;
        }
        
        .config-item {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 12px;
            background: var(--bg-dark);
            border-radius: var(--radius-sm);
        }
        
        .config-label { font-size: 0.875rem; color: var(--text-secondary); }
        .config-value { font-weight: 600; }
        .config-value.on { color: var(--success); }
        .config-value.off { color: var(--text-muted); }
        
        .config-path {
            grid-column: 1 / -1;
            flex-direction: column;
            align-items: flex-start;
            gap: 4px;
        }
        
        .config-path .config-value {
            font-size: 0.8rem;
            color: var(--text-muted);
            word-break: break-all;
        }
        
        .toast {
            position: fixed;
            bottom: 80px;
            left: 50%;
            transform: translateX(-50%);
            padding: 12px 24px;
            border-radius: var(--radius-sm);
            background: var(--bg-card);
            color: var(--text-primary);
            z-index: 1000;
            animation: toastIn 0.3s ease;
            box-shadow: var(--shadow);
            border: 1px solid var(--border-color);
            font-size: 0.9rem;
            max-width: calc(100% - 32px);
            text-align: center;
        }
        
        .toast.error { border-color: var(--danger); color: var(--danger); }
        .toast.success { border-color: var(--success); color: var(--success); }
        
        @keyframes toastIn { 
            from { transform: translateX(-50%) translateY(20px); opacity: 0; } 
            to { transform: translateX(-50%) translateY(0); opacity: 1; } 
        }
        
        .loading {
            display: inline-block;
            width: 20px;
            height: 20px;
            border: 2px solid var(--border-color);
            border-radius: 50%;
            border-top-color: var(--primary);
            animation: spin 0.8s linear infinite;
        }
        
        @keyframes spin { to { transform: rotate(360deg); } }
        
        .api-link {
            text-align: center;
            padding: 20px;
            color: var(--text-muted);
            font-size: 0.875rem;
        }
        
        .api-link a {
            color: var(--primary);
            text-decoration: none;
            font-weight: 500;
        }
        
        .refresh-indicator {
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            height: 3px;
            background: linear-gradient(90deg, transparent, var(--primary), transparent);
            animation: refreshPulse 1s ease-in-out;
            z-index: 1000;
        }
        
        @keyframes refreshPulse {
            0% { opacity: 0; transform: scaleX(0); }
            50% { opacity: 1; transform: scaleX(1); }
            100% { opacity: 0; transform: scaleX(0); }
        }
        
        @media (max-width: 480px) {
            .container { padding: 12px; padding-bottom: 80px; }
            header { padding: 16px 0; margin-bottom: 12px; }
            h1 { font-size: 1.5rem; }
            .card { padding: 16px; margin-bottom: 12px; border-radius: 12px; }
            .stats { grid-template-columns: repeat(2, 1fr); }
            .config-grid { grid-template-columns: 1fr; }
            .btn { padding: 12px 16px; }
            .task-item { padding: 12px; }
            .task-header { flex-direction: column; gap: 8px; }
            .task-result { flex-wrap: wrap; gap: 12px; }
        }
        
        @media (min-width: 768px) {
            .container { padding: 24px; }
            h1 { font-size: 2rem; }
            .card { padding: 24px; }
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>抖音下载器</h1>
            <div class="subtitle">支持视频、图集、用户主页、合集、音乐</div>
        </header>
        
        <div class="card">
            <h2>提交下载任务</h2>
            <div class="form-group">
                <label>抖音链接</label>
                <textarea id="urlInput" placeholder="粘贴抖音链接，支持多个链接（每行一个）&#10;&#10;示例：&#10;https://v.douyin.com/xxx&#10;https://www.douyin.com/video/xxx"></textarea>
            </div>
            <div class="btn-group">
                <button class="btn btn-primary" onclick="submitDownload()">开始下载</button>
                <button class="btn btn-secondary" onclick="parseUrl()">解析链接</button>
            </div>
        </div>

        <div class="card">
            <h2>任务状态</h2>
            <div class="btn-group" style="margin-bottom: 16px;">
                <button class="btn btn-secondary" onclick="refreshTasks()">刷新</button>
                <button class="btn btn-danger" onclick="clearCompleted()">清理已完成</button>
            </div>
            <div id="statsContainer" class="stats"></div>
            <ul id="taskList" class="task-list"></ul>
        </div>

        <div class="card">
            <h2>当前配置</h2>
            <div id="configContainer" class="config-grid"></div>
        </div>

        <div class="api-link">
            <a href="/docs">API 文档</a>
        </div>
    </div>

    <script>
        let refreshInterval = null;
        
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
            const existing = document.querySelector('.toast');
            if (existing) existing.remove();
            
            const toast = document.createElement('div');
            toast.className = 'toast ' + type;
            toast.textContent = message;
            document.body.appendChild(toast);
            setTimeout(() => toast.remove(), 3000);
        }

        function showRefreshIndicator() {
            const existing = document.querySelector('.refresh-indicator');
            if (existing) existing.remove();
            
            const indicator = document.createElement('div');
            indicator.className = 'refresh-indicator';
            document.body.appendChild(indicator);
            setTimeout(() => indicator.remove(), 1000);
        }

        async function submitDownload() {
            const urls = document.getElementById('urlInput').value.trim().split('\\n').filter(u => u.trim());
            if (!urls.length) {
                showToast('请输入链接', 'error');
                return;
            }
            
            let successCount = 0;
            for (const url of urls) {
                try {
                    await fetchAPI('/api/download', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ url: url.trim() })
                    });
                    successCount++;
                } catch (e) {}
            }
            
            if (successCount > 0) {
                showToast('已创建 ' + successCount + ' 个任务', 'success');
                document.getElementById('urlInput').value = '';
                refreshTasks();
            }
        }

        async function parseUrl() {
            const url = document.getElementById('urlInput').value.trim();
            if (!url) {
                showToast('请输入链接', 'error');
                return;
            }
            try {
                const data = await fetchAPI('/api/url/parse?url=' + encodeURIComponent(url));
                showToast('类型: ' + data.url_type, 'success');
            } catch (e) {}
        }

        async function refreshTasks() {
            showRefreshIndicator();
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
            document.getElementById('statsContainer').innerHTML = 
                '<div class="stat-item"><div class="stat-value">' + stats.total + '</div><div class="stat-label">总任务</div></div>' +
                '<div class="stat-item running"><div class="stat-value">' + stats.running + '</div><div class="stat-label">运行中</div></div>' +
                '<div class="stat-item completed"><div class="stat-value">' + stats.completed + '</div><div class="stat-label">已完成</div></div>' +
                '<div class="stat-item failed"><div class="stat-value">' + stats.failed + '</div><div class="stat-label">失败</div></div>';
        }

        function renderTasks(tasks) {
            const list = document.getElementById('taskList');
            if (!tasks.length) {
                list.innerHTML = '<li class="empty-state">暂无任务，请提交下载链接</li>';
                return;
            }
            
            list.innerHTML = tasks.map(function(t) {
                const progress = t.progress || {};
                const percent = progress.item_total ? Math.round((progress.item_current / progress.item_total) * 100) : 0;
                const canCancel = t.status === 'running' || t.status === 'pending';
                
                let html = '<li class="task-item">' +
                    '<div class="task-header">' +
                        '<div class="task-id">' + t.task_id + '</div>' +
                        '<span class="task-status status-' + t.status + '">' + getStatusText(t.status) + '</span>' +
                    '</div>' +
                    '<div class="task-url">' + t.url + '</div>';
                
                if (progress.step) {
                    html += '<div class="task-progress">' +
                        '<div class="progress-step">' + progress.step + (progress.detail ? ': ' + progress.detail : '') + '</div>' +
                        '<div class="progress-bar"><div class="progress-fill" style="width:' + percent + '%"></div></div>' +
                    '</div>';
                }
                
                if (t.result) {
                    html += '<div class="task-result">' +
                        '<span class="success">成功: ' + t.result.success + '</span>' +
                        '<span class="fail">失败: ' + t.result.failed + '</span>' +
                        '<span class="skip">跳过: ' + t.result.skipped + '</span>' +
                    '</div>';
                }
                
                if (t.error) {
                    html += '<div class="task-error">' + t.error + '</div>';
                }
                
                if (canCancel) {
                    html += '<div class="task-actions">' +
                        '<button class="btn btn-danger" onclick="cancelTask(\\'' + t.task_id + '\\')">取消任务</button>' +
                    '</div>';
                }
                
                html += '</li>';
                return html;
            }).join('');
        }
        
        function getStatusText(status) {
            const map = {
                'pending': '等待中',
                'running': '下载中',
                'completed': '已完成',
                'failed': '失败',
                'cancelled': '已取消'
            };
            return map[status] || status;
        }

        async function cancelTask(taskId) {
            try {
                await fetchAPI('/api/task/' + taskId + '/cancel');
                showToast('任务已取消', 'success');
                refreshTasks();
            } catch (e) {}
        }

        async function clearCompleted() {
            try {
                await fetchAPI('/api/tasks/clear', { method: 'DELETE' });
                showToast('已清理完成的任务', 'success');
                refreshTasks();
            } catch (e) {}
        }

        async function loadConfig() {
            try {
                const data = await fetchAPI('/api/config');
                document.getElementById('configContainer').innerHTML = 
                    '<div class="config-item"><span class="config-label">并发数</span><span class="config-value">' + data.thread + '</span></div>' +
                    '<div class="config-item"><span class="config-label">下载封面</span><span class="config-value ' + (data.cover ? 'on' : 'off') + '">' + (data.cover ? '开启' : '关闭') + '</span></div>' +
                    '<div class="config-item"><span class="config-label">下载音乐</span><span class="config-value ' + (data.music ? 'on' : 'off') + '">' + (data.music ? '开启' : '关闭') + '</span></div>' +
                    '<div class="config-item"><span class="config-label">下载头像</span><span class="config-value ' + (data.avatar ? 'on' : 'off') + '">' + (data.avatar ? '开启' : '关闭') + '</span></div>' +
                    '<div class="config-item config-path"><span class="config-label">保存路径</span><span class="config-value">' + data.path + '</span></div>';
            } catch (e) {}
        }

        refreshTasks();
        loadConfig();
        refreshInterval = setInterval(refreshTasks, 5000);
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
