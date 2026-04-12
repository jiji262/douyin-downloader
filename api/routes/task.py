import asyncio
import sys
from pathlib import Path
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

router = APIRouter()
PROJECT_ROOT = Path(__file__).parent.parent.parent
# We enforce using the .venv python if we are running the server
PYTHON_EXE = sys.executable

@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    
    try:
        data = await websocket.receive_json()
        cmd_type = data.get("command")
        
        args = []
        if cmd_type == "download":
            args = [PYTHON_EXE, "run.py"]
        elif cmd_type == "whisper":
            args = [PYTHON_EXE, "cli/whisper_transcribe.py"]
        else:
            await websocket.send_text("Error: Unknown command")
            await websocket.close()
            return
            
        await websocket.send_text(f">>> 开始执行任务: {' '.join(args)}\n")
        
        import os
        import subprocess
        import threading
        import queue
        
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"
        env["TERM"] = "dumb"
        env["NO_COLOR"] = "1"
        
        # 使用同步 Popen（Windows 兼容）
        process = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=str(PROJECT_ROOT),
            env=env,
        )
        
        # 在后台线程中逐行读取子进程输出，推入队列
        line_queue: queue.Queue = queue.Queue()
        
        def reader():
            for line in iter(process.stdout.readline, b''):
                line_queue.put(line)
            process.stdout.close()
            process.wait()
            line_queue.put(None)  # 哨兵值，表示结束
        
        t = threading.Thread(target=reader, daemon=True)
        t.start()
        
        # 主协程从队列中取数据发送到 WebSocket
        while True:
            # 非阻塞等待队列数据
            line = await asyncio.get_event_loop().run_in_executor(None, line_queue.get)
            if line is None:
                break
            text = line.decode('utf-8', errors='replace')
            try:
                await websocket.send_text(text)
            except Exception:
                process.kill()
                return
        
        await websocket.send_text(f"\n>>> 任务结束，退出码 {process.returncode}")
        await websocket.close()
        
    except WebSocketDisconnect:
        print("Client disconnected")
    except Exception as e:
        import traceback
        error_detail = traceback.format_exc()
        print(f"WebSocket error: {error_detail}")
        try:
            await websocket.send_text(f"\nError: {str(e)}\n{error_detail}")
            await websocket.close()
        except:
            pass
