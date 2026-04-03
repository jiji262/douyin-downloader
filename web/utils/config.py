"""
配置文件管理模块
"""
import os
import json
from pathlib import Path
from datetime import datetime


class WebConfig:
    """Web 模块配置管理类"""
    
    def __init__(self, config_file: str = None):
        self.config_file = config_file or "web_config.json"
        self.config = self._load_config()
    
    def _load_config(self) -> dict:
        """加载配置文件"""
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
        
        # 默认配置
        return {
            "download_base_dir": "./downloads",
            "scan_interval_minutes": 30,
            "download_mode": "balance",  # fast, balance, stable
            "max_error_logs": 500,
            "vnc_port": 6080,
            "vnc_display": 1,
        }
    
    def save_config(self):
        """保存配置文件"""
        with open(self.config_file, 'w', encoding='utf-8') as f:
            json.dump(self.config, f, ensure_ascii=False, indent=2)
    
    def get(self, key: str, default=None):
        """获取配置项"""
        return self.config.get(key, default)
    
    def set(self, key: str, value):
        """设置配置项并保存"""
        self.config[key] = value
        self.save_config()
    
    @property
    def download_base_dir(self) -> str:
        return self.config.get("download_base_dir", "./downloads")
    
    @property
    def scan_interval_minutes(self) -> int:
        return self.config.get("scan_interval_minutes", 30)
    
    @property
    def download_mode(self) -> str:
        return self.config.get("download_mode", "balance")
    
    @property
    def max_error_logs(self) -> int:
        return self.config.get("max_error_logs", 500)
    
    @property
    def vnc_port(self) -> int:
        return self.config.get("vnc_port", 6080)
    
    @property
    def vnc_display(self) -> int:
        return self.config.get("vnc_display", 1)
