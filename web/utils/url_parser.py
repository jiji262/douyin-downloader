"""
URL 解析工具 - 从文本中提取抖音链接
"""
import re
from typing import List, Dict, Optional


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
    
    # 用户主页链接模式
    USER_PATTERNS = [
        r'https?://www\.douyin\.com/user/([A-Za-z0-9_-]+)',
        r'https?://v\.douyin\.com/user/([A-Za-z0-9_-]+)',
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
    
    @classmethod
    def parse_douyin_user_url(cls, url: str) -> Optional[Dict[str, str]]:
        """解析抖音用户主页 URL，提取 sec_uid"""
        url = cls.normalize_url(url)
        
        for pattern in cls.USER_PATTERNS:
            match = re.search(pattern, url)
            if match:
                return {
                    'sec_uid': match.group(1),
                    'url': url,
                    'type': 'user'
                }
        
        # 尝试从短链接解析（需要重定向后才能获取真实 sec_uid）
        if 'v.douyin.com' in url and '/user/' not in url:
            # 短链接，返回 URL 让后续处理
            return {
                'sec_uid': None,  # 需要后续通过 API 获取
                'url': url,
                'type': 'user_short'
            }
        
        return None
    
    @classmethod
    def extract_video_id(cls, url: str) -> Optional[str]:
        """从视频 URL 中提取视频 ID"""
        patterns = [
            r'/video/(\d+)',
            r'/note/(\d+)',
            r'modal_id=(\d+)',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        
        return None
