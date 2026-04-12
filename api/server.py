import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes import config as config_router
from api.routes import task as task_router
from api.routes import history as history_router

app = FastAPI(title="Douyin Downloader API")

# Allow CORS for React frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(config_router.router, prefix="/api/config", tags=["Config"])
app.include_router(task_router.router, prefix="/api/task", tags=["Task"])
app.include_router(history_router.router, prefix="/api/history", tags=["History"])

if __name__ == "__main__":
    uvicorn.run("api.server:app", host="127.0.0.1", port=8000, reload=True)
