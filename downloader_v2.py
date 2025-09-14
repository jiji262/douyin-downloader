#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
抖音下载器 v2.0 - 增强版
基于研究成果优化，提供更友好的使用方式
支持多种cookie获取方式和URL类型
"""

import asyncio
import json
import logging
import os
import re
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any, Union
from urllib.parse import urlparse, parse_qs
import argparse
import yaml

# 第三方库
try:
    import aiohttp
    import requests
    from rich.console import Console
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeRemainingColumn, TaskProgressColumn
    from rich.table import Table
    from rich.panel import Panel
    from rich.prompt import Prompt, Confirm
    from rich import print as rprint
except ImportError as e:
    print(f"请安装必要的依赖: pip install aiohttp requests rich pyyaml")
    sys.exit(1)

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 配置日志（需要在导入模块之前）
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('downloader_v2.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

# 导入项目模块
from apiproxy.douyin import douyin_headers
from apiproxy.douyin.urls import Urls
from apiproxy.douyin.result import Result
from apiproxy.common.utils import Utils
from apiproxy.douyin.auth.cookie_manager import AutoCookieManager
from apiproxy.douyin.auth.signature_generator import get_x_bogus, get_a_bogus
from apiproxy.douyin.database import DataBase
from apiproxy.douyin.core.download_logger import DownloadLogger

# 尝试导入browser_cookies，如果失败则设置为None
try:
    from apiproxy.douyin.auth.browser_cookies import get_browser_cookies
    HAS_BROWSER_COOKIES = True
except (ImportError, OSError) as e:
    logger.warning(f"无法导入browser_cookies模块: {e}")
    logger.warning("浏览器Cookie提取功能不可用")
    HAS_BROWSER_COOKIES = False
    get_browser_cookies = None

# 控制台日志级别设置为WARNING，减少干扰
for handler in logging.root.handlers:
    if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
        handler.setLevel(logging.WARNING)

# Rich console
console = Console()


class URLExtractor:
    """URL提取器 - 支持多种URL格式"""

    # URL模式定义
    PATTERNS = {
        'video': [
            r'https?://(?:www\.)?douyin\.com/video/(\d+)',
            r'https?://(?:www\.)?douyin\.com/note/(\d+)',
            r'https?://(?:www\.)?iesdouyin\.com/share/video/(\d+)',
        ],
        'user': [
            r'https?://(?:www\.)?douyin\.com/user/([\w-]+)',
            r'https?://(?:www\.)?douyin\.com/user/\?.*sec_uid=([\w-]+)',
            r'https?://(?:www\.)?iesdouyin\.com/share/user/([\w-]+)',
        ],
        'live': [
            r'https?://live\.douyin\.com/(\d+)',
            r'https?://(?:www\.)?douyin\.com/live/(\d+)',
        ],
        'mix': [
            r'https?://(?:www\.)?douyin\.com/collection/(\d+)',
            r'https?://(?:www\.)?douyin\.com/mix/detail/(\d+)',
        ],
        'music': [
            r'https?://(?:www\.)?douyin\.com/music/(\d+)',
        ],
        'challenge': [
            r'https?://(?:www\.)?douyin\.com/challenge/(\d+)',
        ],
        'search': [
            r'https?://(?:www\.)?douyin\.com/search/([^?]+)',
        ]
    }

    @classmethod
    def extract_from_text(cls, text: str) -> List[Dict[str, str]]:
        """从文本中提取所有抖音链接"""
        urls = []

        # 提取所有URL
        url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+(?:[/?#][^\s]*)?'
        found_urls = re.findall(url_pattern, text)

        for url in found_urls:
            url_info = cls.parse_url(url)
            if url_info:
                urls.append(url_info)

        return urls

    @classmethod
    def parse_url(cls, url: str) -> Optional[Dict[str, str]]:
        """解析单个URL"""
        url = url.strip()

        # 检测URL类型
        for url_type, patterns in cls.PATTERNS.items():
            for pattern in patterns:
                match = re.match(pattern, url)
                if match:
                    return {
                        'url': url,
                        'type': url_type,
                        'id': match.group(1),
                        'original': url
                    }

        # 处理短链接
        if 'v.douyin.com' in url or 'dyv.im' in url:
            return {
                'url': url,
                'type': 'short',
                'id': None,
                'original': url
            }

        return None

    @classmethod
    def extract_id_from_share_text(cls, text: str) -> Optional[str]:
        """从分享文本中提取ID（处理复制的口令）"""
        # 匹配形如 "8.43 abc:/ 复制打开抖音" 的格式
        # 需要匹配完整的短链接ID，不只是前几个字符
        pattern = r'([a-zA-Z0-9]+):/'
        match = re.search(pattern, text)
        if match:
            # 返回完整的ID
            full_id = match.group(1)
            # 验证ID长度（通常是10-15个字符）
            if len(full_id) >= 10:
                return full_id

        # 匹配抖音号
        pattern = r'@([a-zA-Z0-9_]+)'
        match = re.search(pattern, text)
        if match:
            return match.group(1)

        return None


class CookieHelper:
    """Cookie助手 - 提供多种Cookie获取方式"""

    @staticmethod
    def get_cookie_menu() -> str:
        """显示Cookie获取菜单"""
        console.print("\n[bold cyan]🍪 Cookie获取方式[/bold cyan]")
        console.print("1. 自动从浏览器提取（推荐）")
        console.print("2. 使用Playwright自动获取")
        console.print("3. 手动输入Cookie字符串")
        console.print("4. 从文件加载Cookie")
        console.print("5. 不使用Cookie（游客模式）")

        choice = Prompt.ask(
            "\n请选择获取方式",
            choices=["1", "2", "3", "4", "5"],
            default="1"
        )

        return choice

    @staticmethod
    async def get_cookies_interactive() -> Optional[Union[str, Dict]]:
        """交互式获取Cookie"""
        choice = CookieHelper.get_cookie_menu()

        if choice == "1":
            # 从浏览器提取
            browser = Prompt.ask(
                "选择浏览器",
                choices=["chrome", "edge", "firefox", "brave"],
                default="chrome"
            )

            if not HAS_BROWSER_COOKIES:
                console.print("[red]浏览器Cookie提取功能不可用（Cryptodome模块问题）[/red]")
                console.print("[yellow]请使用其他方式或手动输入Cookie[/yellow]")
                return None

            try:
                console.print(f"[cyan]正在从{browser}提取Cookie...[/cyan]")
                cookies = get_browser_cookies(browser, '.douyin.com')

                if cookies:
                    # 显示提取结果
                    console.print(f"[green]✅ 成功提取{len(cookies)}个Cookie[/green]")

                    # 检查关键Cookie
                    important_cookies = ['msToken', 'ttwid', 'sessionid', 'sid_guard']
                    for key in important_cookies:
                        if key in cookies:
                            console.print(f"  ✓ {key}: {cookies[key][:20]}...")

                    return cookies
                else:
                    console.print("[yellow]未能提取到Cookie[/yellow]")

            except Exception as e:
                console.print(f"[red]提取失败: {e}[/red]")

        elif choice == "2":
            # Playwright自动获取
            try:
                console.print("[cyan]正在启动浏览器自动获取Cookie...[/cyan]")
                async with AutoCookieManager(cookie_file='cookies.pkl', headless=False) as cm:
                    cookies_list = await cm.get_cookies()
                    if cookies_list:
                        # 转换为字典
                        cookie_dict = {c['name']: c['value'] for c in cookies_list if 'name' in c and 'value' in c}
                        console.print(f"[green]✅ 成功获取{len(cookie_dict)}个Cookie[/green]")
                        return cookie_dict

            except Exception as e:
                console.print(f"[red]获取失败: {e}[/red]")

        elif choice == "3":
            # 手动输入
            console.print("\n[dim]示例: msToken=xxx; ttwid=yyy; sessionid=zzz[/dim]")
            cookie_str = Prompt.ask("请输入Cookie字符串")
            return cookie_str

        elif choice == "4":
            # 从文件加载
            file_path = Prompt.ask("请输入Cookie文件路径", default="cookies.txt")
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read().strip()
                    # 尝试解析为JSON
                    try:
                        return json.loads(content)
                    except:
                        return content
            except Exception as e:
                console.print(f"[red]读取文件失败: {e}[/red]")

        elif choice == "5":
            # 不使用Cookie
            console.print("[yellow]将以游客模式运行（部分功能可能受限）[/yellow]")
            return None

        return None


class EnhancedDownloader:
    """增强版下载器"""

    def __init__(self, config_path: str = "config_downloader.yml"):
        """初始化下载器"""
        self.config = self._load_config(config_path)
        self.urls_helper = Urls()
        self.result_helper = Result()
        self.utils = Utils()

        # 初始化组件
        self.rate_limiter = RateLimiter(max_per_second=2)
        self.retry_manager = RetryManager(max_retries=3)

        # 生成必要的token
        self.mstoken = self._generate_mstoken()
        self.device_id = self._generate_device_id()

        # Cookie管理 - 从配置文件读取
        self.cookies = self.config.get('cookies') if 'cookies' in self.config else self.config.get('cookie')
        self.headers = {**douyin_headers}
        self.headers['accept-encoding'] = 'gzip, deflate'

        # 如果配置了Cookie，立即设置
        if self.cookies:
            self._apply_cookies_to_headers()

        # 数据库和日志
        self.enable_database = bool(self.config.get('database', True))
        self.db = DataBase() if self.enable_database else None

        # 保存路径
        self.save_path = Path(self.config.get('path', './Downloaded'))
        self.save_path.mkdir(parents=True, exist_ok=True)

        # 下载日志记录器
        self.download_logger = DownloadLogger(str(self.save_path))

        # URL提取器
        self.url_extractor = URLExtractor()

        # 统计信息
        self.stats = {
            'total': 0,
            'success': 0,
            'failed': 0,
            'skipped': 0,
            'start_time': time.time()
        }

    def _load_config(self, config_path: str) -> Dict:
        """加载配置文件"""
        if not os.path.exists(config_path):
            logger.warning(f"配置文件 {config_path} 不存在，使用默认配置")
            return {}

        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f) or {}
            logger.info(f"加载配置文件: {config_path}")
            return config

    def _generate_mstoken(self) -> str:
        """生成msToken"""
        import random
        import string
        charset = string.ascii_letters + string.digits + '-_='
        base_length = random.randint(100, 110)
        mstoken = ''.join(random.choice(charset) for _ in range(base_length))
        logger.info(f"生成msToken: {mstoken[:20]}...")
        return mstoken

    def _generate_device_id(self) -> str:
        """生成设备ID"""
        import random
        device_id = ''.join([str(random.randint(0, 9)) for _ in range(19)])
        logger.info(f"生成设备ID: {device_id}")
        return device_id

    def _apply_cookies_to_headers(self):
        """应用Cookie到请求头"""
        cookie_str = self._build_cookie_string()
        if cookie_str:
            # 确保cookie字符串可以被latin-1编码
            try:
                cookie_str.encode('latin-1')
            except UnicodeEncodeError:
                logger.warning("Cookie包含非ASCII字符，正在进行安全编码...")
                # 重新构建安全的cookie字符串
                cookie_str = self._build_cookie_string()

            self.headers['Cookie'] = cookie_str
            # 更新全局headers
            from apiproxy.douyin import douyin_headers
            douyin_headers['Cookie'] = cookie_str
            logger.info("Cookie已设置")

    async def initialize_cookies(self, cookies=None):
        """初始化Cookie"""
        if cookies:
            self.cookies = cookies
            self._apply_cookies_to_headers()
            console.print("[green]✅ Cookie设置成功[/green]")
        elif not self.cookies:
            # 如果没有配置Cookie，尝试从配置获取
            self.cookies = self.config.get('cookies') or self.config.get('cookie')
            if self.cookies:
                self._apply_cookies_to_headers()
                console.print("[green]✅ Cookie从配置文件加载成功[/green]")

    def _build_cookie_string(self) -> str:
        """构建Cookie字符串"""
        def safe_encode(value):
            """安全编码cookie值，处理非ASCII字符"""
            if not value:
                return ''
            # 将字符串转换为UTF-8编码，然后转换为ASCII安全的字符串
            if isinstance(value, str):
                try:
                    # 尝试使用latin-1编码
                    value.encode('latin-1')
                    return value
                except UnicodeEncodeError:
                    # 如果包含非ASCII字符，使用URL编码
                    import urllib.parse
                    return urllib.parse.quote(value, safe='')
            return str(value)

        if isinstance(self.cookies, str):
            # 处理字符串形式的cookies
            cookie_pairs = []
            for pair in self.cookies.split(';'):
                pair = pair.strip()
                if '=' in pair:
                    key, value = pair.split('=', 1)
                    cookie_pairs.append(f'{key.strip()}={safe_encode(value.strip())}')
                elif pair:
                    cookie_pairs.append(pair)
            return '; '.join(cookie_pairs)
        elif isinstance(self.cookies, dict):
            return '; '.join([f'{k}={safe_encode(v)}' for k, v in self.cookies.items()])
        elif isinstance(self.cookies, list):
            kv = {c.get('name'): safe_encode(c.get('value')) for c in self.cookies if c.get('name') and c.get('value')}
            return '; '.join([f'{k}={v}' for k, v in kv.items()])
        return ''

    async def download_from_url(self, url: str, progress=None, task_id=None) -> bool:
        """根据URL类型自动选择下载方式"""
        try:
            # 解析URL
            url_info = self.url_extractor.parse_url(url)

            if not url_info:
                # 尝试解析为分享文本
                share_id = self.url_extractor.extract_id_from_share_text(url)
                if share_id:
                    # 构造为短链接
                    url = f"https://v.douyin.com/{share_id}/"
                    url_info = {'url': url, 'type': 'short', 'id': share_id}
                else:
                    logger.error(f"无法识别的URL格式: {url}")
                    return False

            # 处理短链接
            if url_info['type'] == 'short':
                resolved_url = await self.resolve_short_url(url_info['url'])
                url_info = self.url_extractor.parse_url(resolved_url)
                if not url_info:
                    logger.error(f"短链接解析失败: {url}")
                    return False

            # 根据类型调用对应的下载方法
            if url_info['type'] in ['video', 'note']:
                return await self.download_single_video(url_info['url'], url_info['id'], progress, task_id)
            elif url_info['type'] == 'user':
                return await self.download_user_page(url_info['url'], url_info['id'])
            elif url_info['type'] == 'mix':
                return await self.download_mix(url_info['url'], url_info['id'])
            elif url_info['type'] == 'music':
                return await self.download_music(url_info['url'], url_info['id'])
            elif url_info['type'] == 'live':
                console.print("[yellow]直播下载功能开发中...[/yellow]")
                return False
            elif url_info['type'] == 'challenge':
                console.print("[yellow]话题下载功能开发中...[/yellow]")
                return False
            else:
                logger.error(f"不支持的URL类型: {url_info['type']}")
                return False

        except Exception as e:
            logger.error(f"下载失败: {e}")
            traceback.print_exc()
            return False

    async def resolve_short_url(self, url: str) -> str:
        """解析短链接"""
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            }

            # 使用requests处理重定向
            response = requests.get(url, headers=headers, allow_redirects=False, timeout=10)

            # 逐步跟踪重定向
            redirect_count = 0
            max_redirects = 5
            current_url = url

            while redirect_count < max_redirects:
                if response.status_code in [301, 302, 303, 307, 308]:
                    location = response.headers.get('Location', '')
                    if location:
                        # 处理相对路径
                        if location.startswith('/'):
                            parsed = urlparse(current_url)
                            location = f"{parsed.scheme}://{parsed.netloc}{location}"

                        current_url = location

                        # 检查是否包含视频ID - 改进的模式匹配
                        if '/share/video/' in location:
                            # 从分享链接提取视频ID
                            video_id_match = re.search(r'/share/video/(\d+)', location)
                            if video_id_match:
                                video_id = video_id_match.group(1)
                                video_url = f"https://www.douyin.com/video/{video_id}"
                                logger.info(f"解析短链接成功（分享链接）: {url} -> {video_url}")
                                return video_url
                        elif '/video/' in location or '/note/' in location:
                            logger.info(f"解析短链接成功: {url} -> {location}")
                            return location
                        elif '/user/' in location or 'sec_uid=' in location:
                            # 处理用户主页链接
                            logger.info(f"解析短链接成功（用户主页）: {url} -> {location}")
                            return location
                        elif 'modal_id=' in location:
                            # 从modal_id提取
                            modal_match = re.search(r'modal_id=(\d+)', location)
                            if modal_match:
                                video_id = modal_match.group(1)
                                video_url = f"https://www.douyin.com/video/{video_id}"
                                logger.info(f"解析短链接成功（modal_id）: {url} -> {video_url}")
                                return video_url

                        # 继续跟随重定向
                        response = requests.get(location, headers=headers, allow_redirects=False, timeout=10)
                        redirect_count += 1
                    else:
                        break
                else:
                    # 最终URL
                    final_url = response.url if hasattr(response, 'url') else current_url

                    # 检查最终URL是否是用户主页
                    if '/user/' in final_url or 'sec_uid=' in final_url:
                        logger.info(f"解析短链接成功（最终用户主页）: {url} -> {final_url}")
                        return final_url

                    # 尝试从页面内容提取
                    if response.text:
                        # 提取视频ID
                        video_id_match = re.search(r'/video/(\d+)', response.text)
                        if video_id_match:
                            video_id = video_id_match.group(1)
                            video_url = f"https://www.douyin.com/video/{video_id}"
                            logger.info(f"从页面提取视频ID: {url} -> {video_url}")
                            return video_url

                    return final_url

            return url

        except Exception as e:
            logger.warning(f"解析短链接失败: {e}")
            return url

    async def download_single_video(self, url: str, video_id: str = None, progress=None, task_id=None) -> bool:
        """下载单个视频/图文 - 优化版"""
        try:
            # 如果没有video_id，尝试提取
            if not video_id:
                video_id = self._extract_video_id(url)

            if not video_id:
                logger.error(f"无法提取视频ID: {url}")
                return False

            # 更新进度
            if progress and task_id is not None:
                progress.update(task_id, description="[yellow]获取视频信息...[/yellow]")

            # 获取视频信息 - 使用多种方法尝试
            video_info = await self._get_video_info_with_fallback(video_id)

            if not video_info:
                logger.error(f"无法获取视频信息: {video_id}")
                self.stats['failed'] += 1
                return False

            # 下载媒体文件
            if progress and task_id is not None:
                desc = video_info.get('desc', '无标题')[:30]
                media_type = '图文' if video_info.get('images') else '视频'
                progress.update(task_id, description=f"[cyan]下载{media_type}: {desc}[/cyan]")

            success = await self._download_media_files(video_info, progress, task_id)

            if success:
                self.stats['success'] += 1
                logger.info(f"下载成功: {url}")
                self.download_logger.add_success({
                    "url": url,
                    "title": video_info.get('desc', '无标题'),
                    "video_id": video_id,
                    "file_path": str(self.save_path)
                })
            else:
                self.stats['failed'] += 1
                logger.error(f"下载失败: {url}")
                self.download_logger.add_failure({
                    "url": url,
                    "video_id": video_id,
                    "error_message": "下载媒体文件失败"
                })

            return success

        except Exception as e:
            logger.error(f"下载视频异常: {e}")
            self.stats['failed'] += 1
            return False
        finally:
            self.stats['total'] += 1

    def _extract_video_id(self, url: str) -> Optional[str]:
        """提取视频ID"""
        patterns = [
            r'/video/(\d+)',
            r'/note/(\d+)',
            r'modal_id=(\d+)',
            r'aweme_id=(\d+)',
            r'item_id=(\d+)',
            r'/(\d{15,20})',  # 直接的数字ID
        ]

        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)

        return None

    async def _get_video_info_with_fallback(self, video_id: str) -> Optional[Dict]:
        """获取视频信息 - 带多种降级策略"""

        # 方法1: 使用现有的Douyin类
        try:
            from apiproxy.douyin.douyin import Douyin
            dy = Douyin(database=False)

            # 设置Cookie
            if self.cookies:
                cookie_str = self._build_cookie_string()
                if cookie_str:
                    from apiproxy.douyin import douyin_headers
                    # 确保cookie字符串可以被latin-1编码
                    try:
                        cookie_str.encode('latin-1')
                    except UnicodeEncodeError:
                        logger.warning("Cookie包含非ASCII字符，已进行安全编码")
                    douyin_headers['Cookie'] = cookie_str

            result = dy.getAwemeInfo(video_id)
            if result:
                logger.info(f"方法1成功: 获取到视频信息")
                return result

        except Exception as e:
            logger.debug(f"方法1失败: {e}")

        # 方法2: 使用官方API带签名
        try:
            params = self._build_api_params(video_id)
            x_bogus = get_x_bogus(params, self.headers.get('User-Agent'))
            api_url = f"https://www.douyin.com/aweme/v1/web/aweme/detail/?{params}&X-Bogus={x_bogus}"

            headers = {**self.headers}
            if self.cookies:
                cookie_str = self._build_cookie_string()
                if cookie_str and 'msToken=' not in cookie_str:
                    cookie_str += f'; msToken={self.mstoken}'
                # 确保cookie字符串可以被latin-1编码
                try:
                    cookie_str.encode('latin-1')
                except UnicodeEncodeError:
                    logger.warning("Cookie包含非ASCII字符，已进行安全编码")
                headers['Cookie'] = cookie_str

            async with aiohttp.ClientSession() as session:
                async with session.get(api_url, headers=headers, timeout=15) as response:
                    if response.status == 200:
                        data = await response.json()
                        if 'aweme_detail' in data:
                            logger.info(f"方法2成功: 官方API获取成功")
                            return data['aweme_detail']

        except Exception as e:
            logger.debug(f"方法2失败: {e}")

        # 方法3: 使用备用API
        try:
            api_url = f"https://www.iesdouyin.com/web/api/v2/aweme/iteminfo/?item_ids={video_id}"

            async with aiohttp.ClientSession() as session:
                async with session.get(api_url, headers=self.headers, timeout=15) as response:
                    if response.status == 200:
                        data = await response.json()
                        if 'item_list' in data and data['item_list']:
                            logger.info(f"方法3成功: 备用API获取成功")
                            return data['item_list'][0]

        except Exception as e:
            logger.debug(f"方法3失败: {e}")

        logger.error(f"所有方法都失败了，无法获取视频信息: {video_id}")
        return None

    def _build_api_params(self, aweme_id: str) -> str:
        """构建API参数"""
        params = [
            f'aweme_id={aweme_id}',
            'device_platform=webapp',
            'aid=6383',
            'channel=channel_pc_web',
            'pc_client_type=1',
            'version_code=170400',
            'version_name=17.4.0',
            'cookie_enabled=true',
            'screen_width=1920',
            'screen_height=1080',
            'browser_language=zh-CN',
            'browser_platform=MacIntel',
            'browser_name=Chrome',
            'browser_version=122.0.0.0',
            'browser_online=true',
            f'msToken={self.mstoken}',
            f'device_id={self.device_id}',
        ]
        return '&'.join(params)

    async def _download_media_files(self, video_info: Dict, progress=None, task_id=None) -> bool:
        """下载媒体文件"""
        try:
            # 判断类型
            is_image = bool(video_info.get('images'))

            # 构建保存路径
            author_name = video_info.get('author', {}).get('nickname', 'unknown')
            desc = video_info.get('desc', '')[:50].replace('/', '_').replace('\n', ' ')

            # 处理时间戳
            create_time = video_info.get('create_time', time.time())
            if isinstance(create_time, (int, float)):
                dt = datetime.fromtimestamp(create_time)
            else:
                dt = datetime.now()

            create_time_str = dt.strftime('%Y%m%d_%H%M%S')

            # 创建文件夹
            folder_name = f"{create_time_str}_{desc}" if desc else create_time_str
            save_dir = self.save_path / author_name / folder_name
            save_dir.mkdir(parents=True, exist_ok=True)

            success = True

            if is_image:
                # 下载图文
                images = video_info.get('images', [])
                total = len(images)

                for i, img in enumerate(images, 1):
                    if progress and task_id is not None:
                        progress.update(task_id,
                                      description=f"[cyan]下载图片 {i}/{total}[/cyan]",
                                      completed=(i-1)*100/total)

                    img_url = self._get_best_quality_url(img.get('url_list', []))
                    if img_url:
                        file_path = save_dir / f"image_{i}.jpg"
                        if not await self._download_file(img_url, file_path):
                            success = False

            else:
                # 下载视频
                video_url = self._get_no_watermark_url(video_info)
                if video_url:
                    file_path = save_dir / f"{folder_name}.mp4"
                    if progress and task_id is not None:
                        progress.update(task_id, description=f"[cyan]下载视频[/cyan]")

                    if not await self._download_file_with_progress(video_url, file_path, progress, task_id):
                        success = False

                # 下载音频
                if self.config.get('music', True):
                    music_url = self._get_music_url(video_info)
                    if music_url:
                        file_path = save_dir / f"{folder_name}_music.mp3"
                        await self._download_file(music_url, file_path)

            # 下载封面
            if self.config.get('cover', True):
                cover_url = self._get_cover_url(video_info)
                if cover_url:
                    file_path = save_dir / f"{folder_name}_cover.jpg"
                    await self._download_file(cover_url, file_path)

            # 保存JSON
            if self.config.get('json', True):
                json_path = save_dir / f"{folder_name}_data.json"
                with open(json_path, 'w', encoding='utf-8') as f:
                    json.dump(video_info, f, ensure_ascii=False, indent=2)

            if progress and task_id is not None:
                progress.update(task_id, completed=100, description="[green]✓ 完成[/green]")

            return success

        except Exception as e:
            logger.error(f"下载媒体文件失败: {e}")
            return False

    def _get_no_watermark_url(self, video_info: Dict) -> Optional[str]:
        """获取无水印视频URL"""
        try:
            # 优先使用play_addr
            play_addr = video_info.get('video', {}).get('play_addr') or \
                       video_info.get('video', {}).get('play_addr_h264')

            if play_addr:
                url_list = play_addr.get('url_list', [])
                if url_list:
                    # 替换为无水印版本
                    url = url_list[0].replace('playwm', 'play')
                    return url

            # 备用download_addr
            download_addr = video_info.get('video', {}).get('download_addr')
            if download_addr:
                url_list = download_addr.get('url_list', [])
                if url_list:
                    return url_list[0].replace('playwm', 'play')

        except Exception as e:
            logger.error(f"获取无水印URL失败: {e}")

        return None

    def _get_best_quality_url(self, url_list: List[str]) -> Optional[str]:
        """获取最高质量的URL"""
        if not url_list:
            return None

        # 优先选择包含特定关键词的URL
        for keyword in ['1080', 'origin', 'high']:
            for url in url_list:
                if keyword in url:
                    return url

        return url_list[0]

    def _get_music_url(self, video_info: Dict) -> Optional[str]:
        """获取音乐URL"""
        try:
            music = video_info.get('music', {})
            play_url = music.get('play_url', {})
            url_list = play_url.get('url_list', [])
            return url_list[0] if url_list else None
        except:
            return None

    def _get_cover_url(self, video_info: Dict) -> Optional[str]:
        """获取封面URL"""
        try:
            cover = video_info.get('video', {}).get('cover', {})
            url_list = cover.get('url_list', [])
            return self._get_best_quality_url(url_list)
        except:
            return None

    async def _download_file(self, url: str, save_path: Path) -> bool:
        """下载文件"""
        try:
            if save_path.exists():
                logger.debug(f"文件已存在: {save_path.name}")
                return True

            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=self.headers, timeout=30) as response:
                    if response.status == 200:
                        content = await response.read()
                        with open(save_path, 'wb') as f:
                            f.write(content)
                        return True
                    else:
                        logger.error(f"下载失败，状态码: {response.status}")
                        return False

        except Exception as e:
            logger.error(f"下载文件失败: {e}")
            return False

    async def _download_file_with_progress(self, url: str, save_path: Path, progress=None, task_id=None) -> bool:
        """带进度的文件下载"""
        try:
            if save_path.exists():
                logger.debug(f"文件已存在: {save_path.name}")
                return True

            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=self.headers, timeout=60) as response:
                    if response.status == 200:
                        total_size = int(response.headers.get('content-length', 0))
                        chunk_size = 8192
                        downloaded = 0

                        with open(save_path, 'wb') as f:
                            async for chunk in response.content.iter_chunked(chunk_size):
                                f.write(chunk)
                                downloaded += len(chunk)

                                if progress and task_id is not None and total_size > 0:
                                    progress.update(task_id, completed=downloaded * 100 / total_size)

                        return True
                    else:
                        logger.error(f"下载失败，状态码: {response.status}")
                        return False

        except Exception as e:
            logger.error(f"下载文件失败: {e}")
            return False

    async def download_user_page(self, url: str, user_id: str = None) -> bool:
        """下载用户主页作品"""
        try:
            console.print("[cyan]正在获取用户信息...[/cyan]")

            # 优先使用传入的user_id参数
            sec_uid = user_id

            # 如果没有传入user_id，则从URL解析
            if not sec_uid:
                # 如果是短链接，先获取重定向后的URL
                if 'v.douyin.com' in url:
                    resolved_url = await self.resolve_short_url(url)
                    url = resolved_url

                # 从URL提取sec_uid
                import re
                patterns = [
                    r'sec_uid=([\w-]+)',
                    r'/user/([\w-]+)',
                    r'/share/user/([\w-]+)'
                ]

                for pattern in patterns:
                    match = re.search(pattern, url)
                    if match:
                        sec_uid = match.group(1)
                        break

                if not sec_uid:
                    logger.error(f"无法从URL提取用户ID: {url}")
                    return False

            console.print(f"[green]用户ID: {sec_uid}[/green]")

            # 获取用户作品列表
            videos = await self._get_user_posts(sec_uid)

            if not videos:
                console.print("[yellow]未找到用户作品[/yellow]")
                return False

            console.print(f"[green]找到 {len(videos)} 个作品[/green]")

            # 询问下载数量
            max_count = min(len(videos), 10)  # 默认最多下载10个
            console.print(f"\n[cyan]准备下载前 {max_count} 个作品[/cyan]")

            # 创建进度条
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(bar_width=40),
                TaskProgressColumn(),
                TimeRemainingColumn(),
                console=console
            ) as progress:

                task = progress.add_task(
                    f"下载用户作品 [0/{max_count}]",
                    total=max_count
                )

                success_count = 0
                for i, video_info in enumerate(videos[:max_count], 1):
                    progress.update(task, description=f"下载用户作品 [{i}/{max_count}]")

                    # 直接使用已获取的视频信息下载，不再调用单个视频API
                    if video_info and isinstance(video_info, dict):
                        # 更新进度
                        desc = video_info.get('desc', '无标题')[:30]
                        media_type = '图文' if video_info.get('images') else '视频'
                        progress.update(task, description=f"[cyan]下载{media_type}: {desc}[/cyan]")

                        # 直接下载媒体文件
                        success = await self._download_media_files(video_info)
                        if success:
                            success_count += 1
                            video_id = video_info.get('aweme_id', 'unknown')
                            logger.info(f"成功下载视频: {video_id}")
                            self.stats['success'] += 1
                        else:
                            self.stats['failed'] += 1
                    else:
                        # 如果视频信息无效，尝试使用旧方法
                        video_id = video_info.get('aweme_id') or video_info.get('video_id')
                        if video_id:
                            video_url = f"https://www.douyin.com/video/{video_id}"
                            success = await self.download_single_video(video_url, video_id)
                            if success:
                                success_count += 1

                    progress.update(task, completed=i)
                    self.stats['total'] += 1

                    # 速率限制
                    await self.rate_limiter.acquire()

            console.print(f"\n[green]✅ 成功下载 {success_count}/{max_count} 个作品[/green]")
            return success_count > 0

        except Exception as e:
            logger.error(f"下载用户页面失败: {e}")
            import traceback
            traceback.print_exc()
            return False

    async def _get_user_posts(self, sec_uid: str, max_count: int = 50) -> List[Dict]:
        """获取用户作品列表"""
        try:
            # 构建API参数
            params = [
                f'sec_uid={sec_uid}',
                'count=20',
                'max_cursor=0',
                'aid=6383',
                'device_platform=webapp',
                'channel=channel_pc_web',
                'pc_client_type=1',
                'version_code=170400',
                'version_name=17.4.0',
                'cookie_enabled=true',
                'screen_width=1920',
                'screen_height=1080',
                'browser_language=zh-CN',
                'browser_platform=MacIntel',
                'browser_name=Chrome',
                'browser_version=122.0.0.0',
                'browser_online=true',
                f'msToken={self.mstoken}',
            ]

            params_str = '&'.join(params)

            # 生成X-Bogus签名
            from apiproxy.douyin.auth.signature_generator import get_x_bogus
            x_bogus = get_x_bogus(params_str, self.headers.get('User-Agent'))

            # 构建完整URL
            api_url = f"https://www.douyin.com/aweme/v1/web/aweme/post/?{params_str}&X-Bogus={x_bogus}"

            # 设置headers - 重要：不包含sessionid，避免返回登录用户的作品
            headers = {**self.headers}
            if self.cookies:
                cookie_str = self._build_cookie_string()
                if cookie_str:
                    # 移除所有session相关的cookie，只保留其他必要的cookie
                    # session相关的cookie会导致API返回登录用户的作品而不是请求的用户
                    cookie_parts = []
                    session_keywords = ['sessionid', 'sid_guard', 'sid_tt', 'uid_tt']
                    for part in cookie_str.split(';'):
                        part = part.strip()
                        if part:
                            # 检查是否包含session相关的关键词
                            is_session_cookie = False
                            for keyword in session_keywords:
                                if part.startswith(keyword + '=') or part.startswith(keyword + '_'):
                                    is_session_cookie = True
                                    break

                            if not is_session_cookie:
                                cookie_parts.append(part)

                    filtered_cookie_str = '; '.join(cookie_parts)

                    # 确保cookie字符串可以被latin-1编码
                    try:
                        filtered_cookie_str.encode('latin-1')
                    except UnicodeEncodeError:
                        logger.warning("Cookie包含非ASCII字符，已进行安全编码")

                    if filtered_cookie_str:
                        headers['Cookie'] = filtered_cookie_str
                        logger.info(f"请求用户作品时使用过滤后的Cookie（移除了sessionid）")

            # 请求API
            async with aiohttp.ClientSession() as session:
                async with session.get(api_url, headers=headers, timeout=15) as response:
                    if response.status == 200:
                        data = await response.json()

                        if data and 'aweme_list' in data:
                            logger.info(f"获取到 {len(data['aweme_list'])} 个作品")
                            return data['aweme_list']
                        elif data:
                            logger.warning(f"API返回格式异常: {data}")
                        else:
                            logger.warning("API返回空数据")
                    else:
                        logger.error(f"API请求失败，状态码: {response.status}")

            # 备用方案：使用Douyin类
            logger.info("尝试备用方案获取用户作品...")
            from apiproxy.douyin.douyin import Douyin
            dy = Douyin(database=False)

            # 设置Cookie - 同样需要移除sessionid
            if self.cookies:
                cookie_str = self._build_cookie_string()
                if cookie_str:
                    # 移除所有session相关的cookie，避免返回登录用户的作品
                    cookie_parts = []
                    session_keywords = ['sessionid', 'sid_guard', 'sid_tt', 'uid_tt']
                    for part in cookie_str.split(';'):
                        part = part.strip()
                        if part:
                            # 检查是否包含session相关的关键词
                            is_session_cookie = False
                            for keyword in session_keywords:
                                if part.startswith(keyword + '=') or part.startswith(keyword + '_'):
                                    is_session_cookie = True
                                    break

                            if not is_session_cookie:
                                cookie_parts.append(part)

                    filtered_cookie_str = '; '.join(cookie_parts)

                    from apiproxy.douyin import douyin_headers
                    # 确保cookie字符串可以被latin-1编码
                    try:
                        filtered_cookie_str.encode('latin-1')
                    except UnicodeEncodeError:
                        logger.warning("Cookie包含非ASCII字符，已进行安全编码")

                    if filtered_cookie_str:
                        douyin_headers['Cookie'] = filtered_cookie_str
                        logger.info(f"备用方案：使用过滤后的Cookie（移除了sessionid）")

            # 获取用户作品
            result = dy.getUserInfo(sec_uid, mode="post", count=max_count)
            if result and isinstance(result, list):
                return result

        except Exception as e:
            logger.error(f"获取用户作品失败: {e}")

        return []

    async def download_mix(self, url: str, mix_id: str = None) -> bool:
        """下载合集"""
        console.print("[yellow]合集下载功能开发中...[/yellow]")
        return True

    async def download_music(self, url: str, music_id: str = None) -> bool:
        """下载音乐页作品"""
        console.print("[yellow]音乐页下载功能开发中...[/yellow]")
        return True

    def show_stats(self):
        """显示统计信息"""
        elapsed = time.time() - self.stats['start_time']

        table = Table(title="📊 下载统计", show_header=True, header_style="bold magenta")
        table.add_column("项目", style="cyan", width=12)
        table.add_column("数值", style="green")

        table.add_row("总任务数", str(self.stats['total']))
        table.add_row("成功", str(self.stats['success']))
        table.add_row("失败", str(self.stats['failed']))
        table.add_row("跳过", str(self.stats['skipped']))

        if self.stats['total'] > 0:
            success_rate = self.stats['success'] / self.stats['total'] * 100
            table.add_row("成功率", f"{success_rate:.1f}%")

        table.add_row("用时", f"{elapsed:.1f}秒")

        console.print(table)


class RateLimiter:
    """速率限制器"""
    def __init__(self, max_per_second: float = 2):
        self.max_per_second = max_per_second
        self.min_interval = 1.0 / max_per_second
        self.last_request = 0

    async def acquire(self):
        """获取许可"""
        current = time.time()
        time_since_last = current - self.last_request
        if time_since_last < self.min_interval:
            await asyncio.sleep(self.min_interval - time_since_last)
        self.last_request = time.time()


class RetryManager:
    """重试管理器"""
    def __init__(self, max_retries: int = 3):
        self.max_retries = max_retries
        self.retry_delays = [1, 2, 5]

    async def execute_with_retry(self, func, *args, **kwargs):
        """执行函数并自动重试"""
        last_error = None
        for attempt in range(self.max_retries):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                last_error = e
                if attempt < self.max_retries - 1:
                    delay = self.retry_delays[min(attempt, len(self.retry_delays) - 1)]
                    logger.warning(f"第 {attempt + 1} 次尝试失败: {e}, {delay}秒后重试...")
                    await asyncio.sleep(delay)
        raise last_error


async def interactive_mode():
    """交互模式"""
    console.print(Panel.fit(
        "[bold cyan]🎬 抖音下载器 v2.0 - 增强版[/bold cyan]\n"
        "[dim]支持多种URL格式和Cookie获取方式[/dim]",
        border_style="cyan"
    ))

    # 创建下载器
    downloader = EnhancedDownloader()

    # 获取Cookie
    console.print("\n[bold]步骤1: 配置Cookie[/bold]")
    cookies = await CookieHelper.get_cookies_interactive()
    await downloader.initialize_cookies(cookies)

    # 获取URL
    console.print("\n[bold]步骤2: 输入下载链接[/bold]")
    console.print("[dim]支持的格式:[/dim]")
    console.print("  • 视频/图文链接")
    console.print("  • 用户主页链接")
    console.print("  • 合集/音乐页链接")
    console.print("  • 短链接/分享口令")
    console.print("  • 批量输入（每行一个）")

    urls = []
    console.print("\n[dim]输入链接（输入空行结束）:[/dim]")

    while True:
        url = input("> ").strip()
        if not url:
            break

        # 解析URL或文本
        extracted_urls = URLExtractor.extract_from_text(url)
        if extracted_urls:
            urls.extend([u['url'] for u in extracted_urls])
            console.print(f"[green]✓ 识别到 {len(extracted_urls)} 个链接[/green]")
        else:
            # 可能是分享文本
            urls.append(url)
            console.print(f"[yellow]添加: {url[:50]}...[/yellow]")

    if not urls:
        console.print("[red]没有输入任何链接[/red]")
        return

    # 确认下载
    console.print(f"\n[cyan]准备下载 {len(urls)} 个链接[/cyan]")
    if not Confirm.ask("开始下载?", default=True):
        return

    # 开始下载
    console.print("\n[bold green]⏳ 开始下载...[/bold green]\n")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=40),
        TaskProgressColumn(),
        TimeRemainingColumn(),
        console=console
    ) as progress:

        for i, url in enumerate(urls, 1):
            task = progress.add_task(
                f"[{i}/{len(urls)}] 处理中...",
                total=100
            )

            success = await downloader.download_from_url(url, progress, task)

            if success:
                progress.update(task, description=f"[green]✓[/green] [{i}/{len(urls)}] 完成")
            else:
                progress.update(task, description=f"[red]✗[/red] [{i}/{len(urls)}] 失败")

    # 显示统计
    downloader.show_stats()
    console.print("\n[bold green]✅ 下载任务完成！[/bold green]")


async def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description='抖音下载器 v2.0 - 增强版',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument(
        'urls',
        nargs='*',
        help='要下载的URL（可以是视频、用户、合集等）'
    )

    parser.add_argument(
        '-c', '--config',
        default='config_downloader.yml',
        help='配置文件路径 (默认: config_downloader.yml)'
    )

    parser.add_argument(
        '-o', '--output',
        default='./Downloaded',
        help='输出目录'
    )

    parser.add_argument(
        '--cookie',
        help='Cookie字符串或browser:chrome格式'
    )

    parser.add_argument(
        '-i', '--interactive',
        action='store_true',
        help='交互模式'
    )

    parser.add_argument(
        '--no-music',
        action='store_true',
        help='不下载音频'
    )

    parser.add_argument(
        '--no-cover',
        action='store_true',
        help='不下载封面'
    )

    args = parser.parse_args()

    try:
        if args.interactive or not args.urls:
            # 交互模式
            await interactive_mode()
        else:
            # 命令行模式
            downloader = EnhancedDownloader(args.config)

            # 设置配置
            downloader.save_path = Path(args.output)
            downloader.save_path.mkdir(parents=True, exist_ok=True)

            if args.no_music:
                downloader.config['music'] = False
            if args.no_cover:
                downloader.config['cover'] = False

            # 处理Cookie
            if args.cookie:
                if args.cookie.startswith('browser:'):
                    if not HAS_BROWSER_COOKIES:
                        console.print("[red]浏览器Cookie提取功能不可用[/red]")
                        console.print("[yellow]请使用配置文件中的Cookie或手动指定[/yellow]")
                    else:
                        browser = args.cookie.split(':', 1)[1]
                        cookies = get_browser_cookies(browser, '.douyin.com')
                        await downloader.initialize_cookies(cookies)
                else:
                    await downloader.initialize_cookies(args.cookie)

            # 下载
            console.print(f"[cyan]开始下载 {len(args.urls)} 个链接...[/cyan]")

            for url in args.urls:
                console.print(f"\n处理: {url}")
                success = await downloader.download_from_url(url)
                if success:
                    console.print("[green]✓ 成功[/green]")
                else:
                    console.print("[red]✗ 失败[/red]")

            # 显示统计
            downloader.show_stats()

    except KeyboardInterrupt:
        console.print("\n[yellow]用户中断[/yellow]")
    except Exception as e:
        console.print(f"\n[red]错误: {e}[/red]")
        traceback.print_exc()


if __name__ == '__main__':
    asyncio.run(main())