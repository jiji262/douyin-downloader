"""
主页链接管理器 - 使用 TXT 文件存储
"""
import os
from typing import List, Dict, Optional
from pathlib import Path
from datetime import datetime


class HomepageManager:
    """主页链接管理器"""
    
    def __init__(self, file_path: str = "homepages.txt"):
        self.file_path = file_path
        self._ensure_file_exists()
    
    def _ensure_file_exists(self):
        """确保文件存在"""
        if not os.path.exists(self.file_path):
            Path(self.file_path).touch()
    
    def _read_lines(self) -> List[str]:
        """读取所有行"""
        try:
            with open(self.file_path, 'r', encoding='utf-8') as f:
                return [line.strip() for line in f.readlines() if line.strip()]
        except Exception:
            return []
    
    def _write_lines(self, lines: List[str]):
        """写入所有行"""
        with open(self.file_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
            if lines:
                f.write('\n')
    
    def add_homepage(self, url: str) -> bool:
        """添加主页链接"""
        lines = self._read_lines()
        if url not in lines:
            lines.append(url)
            self._write_lines(lines)
            return True
        return False
    
    def remove_homepage(self, url: str) -> bool:
        """移除主页链接"""
        lines = self._read_lines()
        if url in lines:
            lines.remove(url)
            self._write_lines(lines)
            return True
        return False
    
    def get_all_homepages(self) -> List[str]:
        """获取所有主页链接"""
        return self._read_lines()
    
    def count(self) -> int:
        """获取主页数量"""
        return len(self._read_lines())
    
    def clear(self):
        """清空所有主页"""
        self._write_lines([])
