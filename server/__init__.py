"""REST API 服务模式（可选）。

依赖：fastapi, uvicorn（非强制，按需 pip install）

用法：
    douyin-dl --serve --serve-host 127.0.0.1 --serve-port 8000

接口：
    POST /api/v1/download  {"url": "..."} -> {job_id}
    GET  /api/v1/jobs/{job_id}
    GET  /api/v1/jobs
    GET  /api/v1/health
"""
