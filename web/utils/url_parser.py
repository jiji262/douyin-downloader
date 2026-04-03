"""
URL 解析工具 - 从文本中提取抖音链接
"""
import re
from typing import List


class URLParser:
    """URL 解析器"""
    
    # 抖音链接匹配模式
    DOUYIN_PATTERNS = [
        r'https?://v\.douyin\.com/[A-Za-z0-9]+/?',
        r'https?://www\.douyin\.com/video/[A-Za-z0-9]+',
        r'https?://www\.douyin\.com/note/[A-Za-z0-9]+',
        r'https?://m\.douyin\.com/share/video/\d+',
        r'https?://share\.douyin\.com/[A-Za-z0-9]+/?',
    ]
    
    @classmethod
    def extract_douyin_urls(cls, text: str) -> List[str]:
        """从文本中提取所有抖音链接"""
        urls = []
        for pattern in cls.DOUYIN_PATTERNS:
            matches = re.findall(pattern, text)
            urls.extend(matches)
        
        # 去重
        return list(dict.fromkeys(urls))
    
    @classmethod
    def is_douyin_url(cls, url: str) -> bool:
        """判断是否为抖音链接"""
        for pattern in cls.DOUYIN_PATTERNS:
            if re.match(pattern, url):
                return True
        return False
    
    @classmethod
    def normalize_url(cls, url: str) -> str:
        """标准化 URL（去除尾部斜杠等）"""
        url = url.strip()
        if url.endswith('/'):
            url = url[:-1]
        return url
