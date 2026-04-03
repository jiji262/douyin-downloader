"""
VNC 服务管理 - noVNC + Xvfb + Chromium
"""
import os
import subprocess
import threading
import time
import signal
from typing import Optional
from pathlib import Path


class VNCManager:
    """VNC 服务管理器"""
    
    def __init__(self, display: int = 1, port: int = 6080, width: int = 1280, height: int = 720):
        self.display = display
        self.port = port
        self.width = width
        self.height = height
        
        self.xvfb_process: Optional[subprocess.Popen] = None
        self.novnc_process: Optional[subprocess.Popen] = None
        self.chrome_process: Optional[subprocess.Popen] = None
        
        self._running = False
        self._lock = threading.Lock()
    
    def start(self) -> bool:
        """启动 VNC 服务"""
        with self._lock:
            if self._running:
                return True
            
            try:
                # 1. 启动 Xvfb (虚拟 framebuffer)
                xvfb_cmd = [
                    'Xvfb',
                    f':{self.display}',
                    '-screen', '0', f'{self.width}x{self.height}x24',
                    '-ac',
                    '+extension', 'RANDR'
                ]
                self.xvfb_process = subprocess.Popen(xvfb_cmd, preexec_fn=os.setsid)
                time.sleep(1)
                
                # 设置 DISPLAY 环境变量
                env = os.environ.copy()
                env['DISPLAY'] = f':{self.display}'
                
                # 2. 启动 noVNC (通过 websockify)
                novnc_path = self._find_novnc_path()
                if novnc_path:
                    novnc_cmd = [
                        'websockify',
                        '--web', novnc_path,
                        str(self.port),
                        f'localhost:{5900 + self.display}'
                    ]
                    self.novnc_process = subprocess.Popen(novnc_cmd, env=env, preexec_fn=os.setsid)
                    time.sleep(1)
                
                # 3. 启动 Chromium 浏览器
                chrome_cmd = [
                    'google-chrome',
                    '--no-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-gpu',
                    '--remote-debugging-port=9222',
                    f'--window-size={self.width},{self.height}',
                    'about:blank'
                ]
                self.chrome_process = subprocess.Popen(chrome_cmd, env=env, preexec_fn=os.setsid)
                
                self._running = True
                return True
                
            except Exception as e:
                print(f"启动 VNC 服务失败：{e}")
                self.stop()
                return False
    
    def stop(self):
        """停止 VNC 服务"""
        with self._lock:
            if not self._running:
                return
            
            processes = [self.chrome_process, self.novnc_process, self.xvfb_process]
            
            for proc in processes:
                if proc:
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                        proc.wait(timeout=5)
                    except Exception:
                        try:
                            proc.kill()
                        except Exception:
                            pass
            
            self.chrome_process = None
            self.novnc_process = None
            self.xvfb_process = None
            self._running = False
    
    def restart(self) -> bool:
        """重启 VNC 服务"""
        self.stop()
        time.sleep(2)
        return self.start()
    
    def is_running(self) -> bool:
        """检查 VNC 服务是否运行"""
        return self._running
    
    def get_vnc_url(self) -> str:
        """获取 VNC Web 访问 URL"""
        return f"http://localhost:{self.port}/vnc.html"
    
    def _find_novnc_path(self) -> Optional[str]:
        """查找 noVNC 安装路径"""
        possible_paths = [
            '/usr/share/novnc',
            '/usr/share/noVNC',
            '/opt/noVNC',
            './node_modules/@novnc/novnc',
        ]
        
        for path in possible_paths:
            if os.path.exists(path):
                return path
        
        return None
    
    def open_url(self, url: str):
        """在 Chromium 中打开 URL"""
        if not self._running or not self.chrome_process:
            return False
        
        try:
            # 使用 Chrome DevTools Protocol 打开 URL
            import requests
            response = requests.post(
                'http://localhost:9222/json/new',
                json={'url': url},
                timeout=5
            )
            return response.status_code == 200
        except Exception:
            # 备选方案：直接发送信号（不太可靠）
            pass
        
        return False


# 全局 VNC 管理器实例
_vnc_manager: Optional[VNCManager] = None


def get_vnc_manager() -> VNCManager:
    """获取全局 VNC 管理器实例"""
    global _vnc_manager
    if _vnc_manager is None:
        _vnc_manager = VNCManager()
    return _vnc_manager
