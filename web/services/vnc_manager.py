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
            
            # 先清理可能的残留进程和锁文件
            self._cleanup_stale_resources()
            
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
                time.sleep(2)  # 等待 Xvfb 完全启动
                
                # 设置 DISPLAY 环境变量
                env = os.environ.copy()
                env['DISPLAY'] = f':{self.display}'
                
                # 2. 启动 x11vnc (如果可用，否则跳过)
                x11vnc_available = self._check_command('x11vnc')
                if x11vnc_available:
                    x11vnc_cmd = [
                        'x11vnc',
                        f'-display :{self.display}',
                        '-forever',
                        '-shared',
                        '-rfbport', str(5900 + self.display),
                        '-nopw',
                        '-loglevel', '0'
                    ]
                    self.novnc_process = subprocess.Popen(x11vnc_cmd, env=env, preexec_fn=os.setsid)
                    time.sleep(1)
                
                # 3. 启动 Chromium 浏览器 (必须指定 user-data-dir)
                user_data_dir = os.path.expanduser('~/.chrome-vnc-data')
                os.makedirs(user_data_dir, exist_ok=True)
                
                # 检测可用的 Chrome/Chromium
                chrome_path = self._find_chrome_path()
                
                if chrome_path:
                    chrome_cmd = [
                        chrome_path,
                        '--no-sandbox',
                        '--disable-dev-shm-usage',
                        '--disable-gpu',
                        '--remote-debugging-port=9222',
                        '--remote-debugging-address=0.0.0.0',
                        f'--window-size={self.width},{self.height}',
                        f'--user-data-dir={user_data_dir}',
                        '--disable-features=TranslateUI',
                        '--disable-ipc-flooding-protection',
                        '--disable-first-run-ui',
                        '--no-default-browser-check',
                        'about:blank'
                    ]
                    self.chrome_process = subprocess.Popen(chrome_cmd, env=env, preexec_fn=os.setsid)
                    time.sleep(3)  # 等待 Chrome 完全启动
                    
                    self._running = True
                    vnc_info = f"Display :{self.display}, Debug Port 9222"
                    if x11vnc_available:
                        vnc_info += f", VNC Port {5900 + self.display}"
                    print(f"✓ VNC 服务已启动：{vnc_info}")
                    print(f"✓ 浏览器已启动：{chrome_path}")
                    return True
                else:
                    # 浏览器不可用，但 Xvfb+VNC 仍然可以运行
                    self._running = True
                    vnc_info = f"Display :{self.display}"
                    if x11vnc_available:
                        vnc_info += f", VNC Port {5900 + self.display}"
                    print(f"⚠️  VNC 服务已部分启动：{vnc_info}")
                    print("⚠️  未找到 Chrome/Chromium 浏览器，无法显示网页")
                    print("💡 请安装浏览器：apt install chromium 或 apt install google-chrome-stable")
                    return True
                
            except Exception as e:
                print(f"✗ 启动 VNC 服务失败：{e}")
                self.stop()
                return False
    
    def _find_chrome_path(self) -> Optional[str]:
        """查找 Chrome/Chromium 浏览器路径"""
        possible_paths = [
            '/usr/bin/chromium',
            '/usr/bin/chromium-browser',
            '/usr/bin/google-chrome',
            '/usr/bin/google-chrome-stable',
            '/snap/bin/chromium',
        ]
        
        for path in possible_paths:
            if os.path.isfile(path) and os.access(path, os.X_OK):
                return path
        
        # 尝试通过 which 命令查找
        try:
            result = subprocess.run(['which', 'chromium'], capture_output=True, text=True)
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass
        
        try:
            result = subprocess.run(['which', 'chromium-browser'], capture_output=True, text=True)
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass
        
        return None
    
    def _check_command(self, cmd: str) -> bool:
        """检查命令是否可用"""
        try:
            result = subprocess.run(['which', cmd], capture_output=True, text=True)
            return result.returncode == 0
        except Exception:
            return False
    
    def _cleanup_stale_resources(self):
        """清理残留的进程和锁文件"""
        # 清理锁文件
        for display_num in range(1, 10):
            lock_file = f"/tmp/.X{display_num}-lock"
            x11_socket = f"/tmp/.X11-unix/X{display_num}"
            
            for file_path in [lock_file, x11_socket]:
                if os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                        print(f"已清理锁文件：{file_path}")
                    except Exception as e:
                        print(f"清理锁文件失败 {file_path}: {e}")
        
        # 杀死可能残留的 Xvfb 进程
        try:
            subprocess.run(['pkill', '-9', 'Xvfb'], capture_output=True, timeout=5)
        except Exception:
            pass
        
        # 杀死可能残留的 x11vnc 进程
        try:
            subprocess.run(['pkill', '-9', 'x11vnc'], capture_output=True, timeout=5)
        except Exception:
            pass
        
        # 杀死可能残留的 chromium 进程
        try:
            subprocess.run(['pkill', '-9', 'chromium'], capture_output=True, timeout=5)
        except Exception:
            pass
    
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
