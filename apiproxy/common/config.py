from typing import TypedDict, Optional
from pathlib import Path
import yaml

class DownloadConfig(TypedDict):
    max_concurrent: int
    chunk_size: int
    retry_times: int
    timeout: int

class LoggingConfig(TypedDict):
    level: str
    file_path: str
    max_size: int
    backup_count: int

class Config:
    def __init__(self, config_path: Path):
        with open(config_path) as f:
            self.config = yaml.safe_load(f)
            
    @property
    def download_config(self) -> DownloadConfig:
        return self.config.get('download', {})
        
    @property
    def logging_config(self) -> LoggingConfig:
        return self.config.get('logging', {}) 