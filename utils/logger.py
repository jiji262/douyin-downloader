import logging
from pathlib import Path
from logging.handlers import RotatingFileHandler
import os

def setup_logger(name, log_file, level=logging.INFO):
    """配置日志系统"""
    log_path = Path(log_file).parent
    log_path.mkdir(exist_ok=True)
    
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s'
    )
    
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=10*1024*1024,  # 10MB
        backupCount=5
    )
    file_handler.setFormatter(formatter)
    
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger

# 创建全局logger实例
logger = setup_logger("douyin_downloader", "logs/douyin_downloader.log") 