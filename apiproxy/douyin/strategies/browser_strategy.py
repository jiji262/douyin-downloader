#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
浏览器自动化下载策略
使用Playwright实现浏览器自动化下载
"""

import asyncio
import json
import logging
import time
import os
from typing import Dict, Optional, List, Any
from pathlib import Path

from .base import IDownloadStrategy, DownloadTask, DownloadResult, TaskType

logger = logging.getLogger(__name__)

# 动态导入Playwright，避免未安装时报错
try:
    from playwright.async_api import async_playwright, Browser, BrowserContext, Page
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    logger.warning("Playwright未安装，浏览器策略不可用。请运行: pip install playwright && playwright install chromium")


class BrowserDownloadStrategy(IDownloadStrategy):
    """浏览器自动化下载策略"""
    
    def __init__(self, headless: bool = True, timeout: int = 30000):
        """
        初始化浏览器策略
        
        Args:
            headless: 是否无头模式
            timeout: 页面加载超时时间（毫秒）
        """
        if not PLAYWRIGHT_AVAILABLE:
            raise ImportError("Playwright未安装，请运行: pip install playwright && playwright install chromium")
        
        self.headless = headless
        self.timeout = timeout
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.playwright = None
        self.initialized = False
        
        # 浏览器配置
        self.browser_args = [
            '--disable-blink-features=AutomationControlled',
            '--disable-dev-shm-usage',
            '--disable-web-security',
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--disable-accelerated-2d-canvas',
            '--disable-gpu'
        ]
        
        # User-Agent
        self.user_agent = (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
            '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )
    
    @property
    def name(self) -> str:
        return "Browser Automation Strategy"
    
    def get_priority(self) -> int:
        """浏览器策略优先级中等"""
        return 50
    
    async def can_handle(self, task: DownloadTask) -> bool:
        """判断是否可以处理任务"""
        # 浏览器策略可以处理视频和图集
        return task.task_type in [TaskType.VIDEO, TaskType.IMAGE]
    
    async def initialize(self):
        """初始化浏览器"""
        if self.initialized:
            return
        
        try:
            logger.info("初始化浏览器...")
            self.playwright = await async_playwright().start()
            
            # 启动浏览器
            self.browser = await self.playwright.chromium.launch(
                headless=self.headless,
                args=self.browser_args
            )
            
            # 创建上下文
            self.context = await self.browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent=self.user_agent,
                locale='zh-CN',
                timezone_id='Asia/Shanghai'
            )
            
            # 添加反检测脚本
            await self.context.add_init_script("""
                // 隐藏webdriver特征
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
                
                // 修改navigator.plugins
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [1, 2, 3, 4, 5]
                });
                
                // 修改navigator.languages
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['zh-CN', 'zh', 'en']
                });
                
                // 修改权限查询
                const originalQuery = window.navigator.permissions.query;
                window.navigator.permissions.query = (parameters) => (
                    parameters.name === 'notifications' ?
                        Promise.resolve({ state: Notification.permission }) :
                        originalQuery(parameters)
                );
            """)
            
            self.initialized = True
            logger.info("浏览器初始化完成")
            
        except Exception as e:
            logger.error(f"浏览器初始化失败: {e}")
            await self.cleanup()
            raise
    
    async def download(self, task: DownloadTask) -> DownloadResult:
        """执行下载任务"""
        start_time = time.time()
        
        try:
            # 初始化浏览器
            await self.initialize()
            
            # 创建新页面
            page = await self.context.new_page()
            
            try:
                # 设置cookies（如果有）
                if task.metadata.get('cookies'):
                    await self._set_cookies(page, task.metadata['cookies'])
                
                # 访问页面
                logger.info(f"浏览器访问: {task.url}")
                await page.goto(task.url, wait_until='networkidle', timeout=self.timeout)
                
                # 等待页面加载
                await asyncio.sleep(2)
                
                # 根据任务类型处理
                if task.task_type == TaskType.VIDEO:
                    result = await self._download_video(page, task)
                else:
                    result = await self._download_images(page, task)
                
                result.duration = time.time() - start_time
                return result
                
            finally:
                await page.close()
                
        except Exception as e:
            logger.error(f"浏览器下载失败: {e}")
            return DownloadResult(
                success=False,
                task_id=task.task_id,
                error_message=str(e),
                duration=time.time() - start_time
            )
    
    async def _download_video(self, page: 'Page', task: DownloadTask) -> DownloadResult:
        """下载视频"""
        try:
            # 等待视频元素加载
            video_selector = 'video'
            await page.wait_for_selector(video_selector, timeout=10000)
            
            # 获取视频信息
            video_info = await page.evaluate("""
                () => {
                    const video = document.querySelector('video');
                    if (!video) return null;
                    
                    // 尝试多种方式获取视频URL
                    let videoUrl = video.src || video.currentSrc;
                    
                    // 如果没有直接的src，尝试从source标签获取
                    if (!videoUrl) {
                        const source = video.querySelector('source');
                        if (source) {
                            videoUrl = source.src;
                        }
                    }
                    
                    // 获取视频标题
                    let title = document.title;
                    const titleElement = document.querySelector('h1, .video-title, [class*="title"]');
                    if (titleElement) {
                        title = titleElement.innerText || title;
                    }
                    
                    // 获取作者信息
                    let author = '';
                    const authorElement = document.querySelector('[class*="author"], [class*="nickname"]');
                    if (authorElement) {
                        author = authorElement.innerText;
                    }
                    
                    return {
                        url: videoUrl,
                        title: title,
                        author: author,
                        duration: video.duration,
                        width: video.videoWidth,
                        height: video.videoHeight
                    };
                }
            """)
            
            if not video_info or not video_info.get('url'):
                # 尝试拦截网络请求获取视频URL
                video_url = await self._intercept_video_url(page)
                if not video_url:
                    return DownloadResult(
                        success=False,
                        task_id=task.task_id,
                        error_message="无法获取视频URL"
                    )
                video_info = {'url': video_url}
            
            # 获取其他媒体资源
            media_urls = await self._extract_media_urls(page)
            
            logger.info(f"获取到视频信息: {video_info}")
            
            # 返回结果（实际下载由其他组件处理）
            return DownloadResult(
                success=True,
                task_id=task.task_id,
                file_paths=[],  # 这里只返回URL，实际下载由Download类处理
                metadata={
                    'video_url': video_info['url'],
                    'title': video_info.get('title', ''),
                    'author': video_info.get('author', ''),
                    'media_urls': media_urls,
                    'video_info': video_info
                }
            )
            
        except Exception as e:
            logger.error(f"视频下载失败: {e}")
            return DownloadResult(
                success=False,
                task_id=task.task_id,
                error_message=str(e)
            )
    
    async def _download_images(self, page: 'Page', task: DownloadTask) -> DownloadResult:
        """下载图集"""
        try:
            # 等待图片加载
            await page.wait_for_selector('img', timeout=10000)
            
            # 获取所有图片URL
            image_urls = await page.evaluate("""
                () => {
                    const images = [];
                    
                    // 查找主要的图片容器
                    const selectors = [
                        '.swiper-slide img',
                        '[class*="image-list"] img',
                        '[class*="gallery"] img',
                        'img[class*="image"]'
                    ];
                    
                    for (const selector of selectors) {
                        const imgs = document.querySelectorAll(selector);
                        if (imgs.length > 0) {
                            imgs.forEach(img => {
                                const url = img.src || img.dataset.src;
                                if (url && !url.includes('avatar') && !url.includes('icon')) {
                                    images.push(url);
                                }
                            });
                            break;
                        }
                    }
                    
                    // 如果没找到，获取所有大图
                    if (images.length === 0) {
                        document.querySelectorAll('img').forEach(img => {
                            if (img.naturalWidth > 200 && img.naturalHeight > 200) {
                                const url = img.src || img.dataset.src;
                                if (url) images.push(url);
                            }
                        });
                    }
                    
                    return [...new Set(images)];  // 去重
                }
            """)
            
            if not image_urls:
                return DownloadResult(
                    success=False,
                    task_id=task.task_id,
                    error_message="未找到图片"
                )
            
            logger.info(f"找到 {len(image_urls)} 张图片")
            
            return DownloadResult(
                success=True,
                task_id=task.task_id,
                file_paths=[],
                metadata={
                    'image_urls': image_urls,
                    'count': len(image_urls)
                }
            )
            
        except Exception as e:
            logger.error(f"图集下载失败: {e}")
            return DownloadResult(
                success=False,
                task_id=task.task_id,
                error_message=str(e)
            )
    
    async def _intercept_video_url(self, page: 'Page') -> Optional[str]:
        """拦截网络请求获取视频URL"""
        video_url = None
        
        def handle_response(response):
            nonlocal video_url
            url = response.url
            # 检查是否是视频请求
            if any(ext in url for ext in ['.mp4', '.m3u8', '.flv', 'video', 'stream']):
                if response.status == 200:
                    video_url = url
                    logger.info(f"拦截到视频URL: {url}")
        
        # 监听响应
        page.on('response', handle_response)
        
        # 等待一段时间收集请求
        await asyncio.sleep(3)
        
        # 尝试触发视频加载
        await page.evaluate("""
            () => {
                const video = document.querySelector('video');
                if (video) {
                    video.play();
                }
            }
        """)
        
        await asyncio.sleep(2)
        
        return video_url
    
    async def _extract_media_urls(self, page: 'Page') -> Dict[str, str]:
        """提取页面中的媒体资源URL"""
        media_urls = await page.evaluate("""
            () => {
                const urls = {};
                
                // 获取音频URL
                const audio = document.querySelector('audio');
                if (audio) {
                    urls.audio = audio.src;
                }
                
                // 获取封面图
                const meta_image = document.querySelector('meta[property="og:image"]');
                if (meta_image) {
                    urls.cover = meta_image.content;
                }
                
                // 获取头像
                const avatar = document.querySelector('[class*="avatar"] img');
                if (avatar) {
                    urls.avatar = avatar.src;
                }
                
                return urls;
            }
        """)
        
        return media_urls
    
    async def _set_cookies(self, page: 'Page', cookies: Any):
        """设置cookies"""
        try:
            if isinstance(cookies, str):
                # 解析cookie字符串
                cookie_list = []
                for item in cookies.split(';'):
                    if '=' in item:
                        key, value = item.strip().split('=', 1)
                        cookie_list.append({
                            'name': key,
                            'value': value,
                            'domain': '.douyin.com',
                            'path': '/'
                        })
                await page.context.add_cookies(cookie_list)
            elif isinstance(cookies, list):
                await page.context.add_cookies(cookies)
            elif isinstance(cookies, dict):
                cookie_list = [
                    {'name': k, 'value': v, 'domain': '.douyin.com', 'path': '/'}
                    for k, v in cookies.items()
                ]
                await page.context.add_cookies(cookie_list)
                
            logger.info("Cookies设置成功")
            
        except Exception as e:
            logger.warning(f"设置Cookies失败: {e}")
    
    async def cleanup(self):
        """清理资源"""
        try:
            if self.context:
                await self.context.close()
                self.context = None
            
            if self.browser:
                await self.browser.close()
                self.browser = None
            
            if self.playwright:
                await self.playwright.stop()
                self.playwright = None
            
            self.initialized = False
            logger.info("浏览器资源已清理")
            
        except Exception as e:
            logger.error(f"清理浏览器资源失败: {e}")
    
    async def __aenter__(self):
        """异步上下文管理器入口"""
        await self.initialize()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器出口"""
        await self.cleanup()