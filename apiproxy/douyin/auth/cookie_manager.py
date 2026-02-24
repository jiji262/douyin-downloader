#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Cookie自动管理器
自动获取、刷新和管理抖音Cookies
"""

import asyncio
import json
import time
import logging
import pickle
from typing import Dict, List, Optional, Any
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

try:
    from playwright.async_api import async_playwright, Browser, BrowserContext, Page
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    logger.warning("Playwright未安装，自动Cookie管理不可用")


@dataclass
class CookieInfo:
    """Cookie信息"""
    cookies: List[Dict[str, Any]]
    created_at: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)
    login_method: str = "manual"  # manual, qrcode, phone
    is_valid: bool = True
    
    def is_expired(self, max_age_hours: int = 24) -> bool:
        """检查Cookie是否过期"""
        age = time.time() - self.created_at
        return age > max_age_hours * 3600
    
    def to_dict(self) -> Dict:
        """转换为字典格式"""
        return {
            'cookies': self.cookies,
            'created_at': self.created_at,
            'last_used': self.last_used,
            'login_method': self.login_method,
            'is_valid': self.is_valid
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'CookieInfo':
        """从字典创建"""
        return cls(**data)


class AutoCookieManager:
    """自动Cookie管理器"""
    
    def __init__(
        self,
        cookie_file: str = "cookies.pkl",
        auto_refresh: bool = True,
        refresh_interval: int = 3600,
        headless: bool = False
    ):
        """
        初始化Cookie管理器
        
        Args:
            cookie_file: Cookie保存文件
            auto_refresh: 是否自动刷新
            refresh_interval: 刷新间隔（秒）
            headless: 浏览器是否无头模式
        """
        self.cookie_file = Path(cookie_file)
        self.auto_refresh = auto_refresh
        self.refresh_interval = refresh_interval
        self.headless = headless
        
        self.current_cookies: Optional[CookieInfo] = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.playwright = None
        
        self._refresh_task = None
        self._lock = asyncio.Lock()
        
        # 加载已保存的Cookies
        self._load_cookies()
    
    def _load_cookies(self):
        """从文件加载Cookies"""
        if self.cookie_file.exists():
            try:
                with open(self.cookie_file, 'rb') as f:
                    data = pickle.load(f)
                    self.current_cookies = CookieInfo.from_dict(data)
                    logger.info(f"已加载保存的Cookies (创建时间: {datetime.fromtimestamp(self.current_cookies.created_at)})")
            except Exception as e:
                logger.error(f"加载Cookies失败: {e}")
                self.current_cookies = None
    
    def _save_cookies(self):
        """保存Cookies到文件"""
        if self.current_cookies:
            try:
                with open(self.cookie_file, 'wb') as f:
                    pickle.dump(self.current_cookies.to_dict(), f)
                logger.info("Cookies已保存")
            except Exception as e:
                logger.error(f"保存Cookies失败: {e}")
    
    async def get_cookies(self) -> Optional[List[Dict[str, Any]]]:
        """
        获取有效的Cookies
        
        Returns:
            Cookie列表
        """
        async with self._lock:
            # 检查是否需要刷新
            if self._need_refresh():
                await self._refresh_cookies()
            
            if self.current_cookies and self.current_cookies.is_valid:
                self.current_cookies.last_used = time.time()
                return self.current_cookies.cookies
            
            return None
    
    def _need_refresh(self) -> bool:
        """判断是否需要刷新Cookies"""
        if not self.current_cookies:
            return True
        
        # 检查是否过期
        if self.current_cookies.is_expired(max_age_hours=24):
            logger.info("Cookies已过期，需要刷新")
            return True
        
        # 检查是否长时间未使用
        idle_time = time.time() - self.current_cookies.last_used
        if idle_time > self.refresh_interval:
            logger.info(f"Cookies已闲置 {idle_time/3600:.1f} 小时，需要刷新")
            return True
        
        return False
    
    async def _refresh_cookies(self):
        """登录并获取新的Cookies"""
        logger.info("需要重新登录获取Cookies")
        
        try:
            browser = await self._get_browser()
            page = await browser.new_page()
            
            # 访问抖音，放宽等待条件
            try:
                await page.goto("https://www.douyin.com", wait_until='domcontentloaded', timeout=120000)
                # 额外等待页面稳定，给验证码页面加载留出时间
                await asyncio.sleep(10)
            except Exception as e:
                logger.warning(f"页面加载超时，继续尝试: {e}")
                # 即使超时也继续尝试
            
            # 检查是否需要登录
            is_logged_in = await self._check_login_status(page)
            
            if not is_logged_in:
                # 执行登录流程
                login_method = await self._perform_login(page)
                
                if not login_method:
                    logger.error("登录失败")
                    await page.close()
                    return
            else:
                login_method = "already_logged_in"
            
            # 获取Cookies
            cookies = await page.context.cookies()
            
            # 过滤必要的Cookies
            filtered_cookies = self._filter_cookies(cookies)
            
            self.current_cookies = CookieInfo(
                cookies=filtered_cookies,
                login_method=login_method
            )
            
            self._save_cookies()
            logger.info(f"成功获取Cookies (登录方式: {login_method})")
            
            await page.close()
            
        except Exception as e:
            logger.error(f"登录获取Cookies失败: {e}")
    
    async def _try_refresh_existing(self) -> bool:
        """尝试刷新现有Cookies"""
        try:
            browser = await self._get_browser()
            page = await browser.new_page()
            
            # 设置现有Cookies
            await page.context.add_cookies(self.current_cookies.cookies)
            
            # 访问抖音主页
            await page.goto("https://www.douyin.com", wait_until='networkidle')
            
            # 检查是否仍然登录
            is_logged_in = await self._check_login_status(page)
            
            if is_logged_in:
                # 获取更新后的Cookies
                cookies = await page.context.cookies()
                self.current_cookies = CookieInfo(
                    cookies=cookies,
                    login_method="refresh"
                )
                self._save_cookies()
                logger.info("Cookies刷新成功")
                await page.close()
                return True
            
            await page.close()
            return False
            
        except Exception as e:
            logger.error(f"刷新Cookies失败: {e}")
            return False
    
    async def _login_and_get_cookies(self):
        """登录并获取新的Cookies"""
        logger.info("需要重新登录获取Cookies")
        
        try:
            browser = await self._get_browser()
            page = await browser.new_page()
            
            # 访问抖音
            await page.goto("https://www.douyin.com", wait_until='networkidle')
            
            # 检查是否需要登录
            is_logged_in = await self._check_login_status(page)
            
            if not is_logged_in:
                # 执行登录流程
                login_method = await self._perform_login(page)
                
                if not login_method:
                    logger.error("登录失败")
                    await page.close()
                    return
            else:
                login_method = "already_logged_in"
            
            # 获取Cookies
            cookies = await page.context.cookies()
            
            # 过滤必要的Cookies
            filtered_cookies = self._filter_cookies(cookies)
            
            self.current_cookies = CookieInfo(
                cookies=filtered_cookies,
                login_method=login_method
            )
            
            self._save_cookies()
            logger.info(f"成功获取Cookies (登录方式: {login_method})")
            
            await page.close()
            
        except Exception as e:
            logger.error(f"登录获取Cookies失败: {e}")
    
    async def _check_login_status(self, page: 'Page') -> bool:
        """检查登录状态"""
        try:
            # 查找用户头像或其他登录标识
            selectors = [
                '[data-e2e="user-avatar"]',
                '.user-avatar',
                '[class*="avatar"]',
                '.login-success',
                '[class*="user"]',
                '[class*="profile"]',
                'img[alt*="头像"]',
                'img[alt*="avatar"]',
                '[data-e2e="profile"]',
                '.profile-info'
            ]
            
            for selector in selectors:
                try:
                    element = await page.wait_for_selector(selector, timeout=5000)
                    if element:
                        logger.info("检测到已登录")
                        return True
                except:
                    continue
            
            # 额外检查：查找登录按钮，如果找不到说明可能已登录
            try:
                login_indicators = [
                    '[data-e2e="login-button"]',
                    '.login-button',
                    'button:has-text("登录")',
                    'a:has-text("登录")'
                ]
                
                for indicator in login_indicators:
                    try:
                        element = await page.wait_for_selector(indicator, timeout=2000)
                        if element:
                            logger.info("检测到登录按钮，未登录")
                            return False
                    except:
                        continue
                
                # 如果找不到登录按钮，可能已登录
                logger.info("未找到登录按钮，可能已登录")
                return True
                
            except Exception:
                pass
            
            return False
            
        except Exception as e:
            logger.warning(f"检查登录状态失败: {e}")
            return False
    
    async def _perform_login(self, page: 'Page') -> Optional[str]:
        """执行登录流程"""
        logger.info("开始登录流程...")
        
        # 首先尝试二维码登录
        login_method = await self._qrcode_login(page)
        
        if not login_method:
            # 如果二维码登录失败，尝试其他方式
            login_method = await self._manual_login(page)
        
        return login_method
    
    async def _qrcode_login(self, page: Page) -> Optional[str]:
        """二维码登录"""
        try:
            logger.info("尝试二维码登录...")
            
            # 查找并点击登录按钮
            login_button_selectors = [
                '[data-e2e="login-button"]',
                '.login-button',
                'button:has-text("登录")',
                'a:has-text("登录")',
                '[class*="login"]',
                'button:has-text("登入")',
                'a:has-text("登入")'
            ]
            
            for selector in login_button_selectors:
                try:
                    button = await page.wait_for_selector(selector, timeout=15000)
                    if button:
                        await button.click()
                        break
                except:
                    continue
            
            # 等待登录弹窗
            await asyncio.sleep(8)
            
            # 选择二维码登录
            qr_selectors = [
                '[data-e2e="qrcode-tab"]',
                '.qrcode-login',
                'text=扫码登录',
                'text=二维码登录',
                '[class*="qrcode"]',
                'text=二维码',
                'text=扫码'
            ]
            
            for selector in qr_selectors:
                try:
                    qr_tab = await page.wait_for_selector(selector, timeout=15000)
                    if qr_tab:
                        await qr_tab.click()
                        break
                except:
                    continue
            
            # 等待二维码出现
            qr_img_selectors = [
                '.qrcode-img', 
                '[class*="qrcode"] img', 
                'canvas',
                '[class*="qr"] img',
                'img[alt*="二维码"]',
                'img[alt*="QR"]'
            ]
            
            qr_found = False
            for selector in qr_img_selectors:
                try:
                    await page.wait_for_selector(selector, timeout=20000)
                    qr_found = True
                    break
                except:
                    continue
            
            if not qr_found:
                logger.warning("未找到二维码，尝试继续等待...")
                # 即使没找到二维码也继续等待，可能页面还在加载
            
            if not self.headless:
                print("\n" + "="*60)
                print("请使用抖音APP扫描二维码登录")
                print("如果出现验证码，请完成验证码验证")
                print("等待登录中...")
                print("="*60 + "\n")
            
            # 等待用户扫码（最多等待300秒，给验证码验证留出时间）
            start_time = time.time()
            while time.time() - start_time < 300:
                is_logged_in = await self._check_login_status(page)
                if is_logged_in:
                    logger.info("二维码登录成功")
                    return "qrcode"
                await asyncio.sleep(8)
            
            logger.warning("二维码登录超时")
            return None
            
        except Exception as e:
            logger.error(f"二维码登录失败: {e}")
            return None
    
    async def _manual_login(self, page: Page) -> Optional[str]:
        """手动登录（等待用户操作）"""
        if self.headless:
            logger.error("无头模式下无法进行手动登录")
            return None
        
        print("\n" + "="*60)
        print("请在浏览器中手动完成登录")
        print("如果出现验证码，请完成验证码验证")
        print("登录成功后将自动继续...")
        print("="*60 + "\n")
        
        # 等待用户手动登录（最多等待600秒，给验证码验证留出充足时间）
        start_time = time.time()
        while time.time() - start_time < 600:
            is_logged_in = await self._check_login_status(page)
            if is_logged_in:
                logger.info("手动登录成功")
                return "manual"
            await asyncio.sleep(8)
        
        logger.warning("手动登录超时")
        return None
    
    def _filter_cookies(self, cookies: List[Dict]) -> List[Dict]:
        """过滤必要的Cookies"""
        # 必要的Cookie名称
        required_names = [
            'msToken',
            'ttwid', 
            'odin_tt',
            'passport_csrf_token',
            'sid_guard',
            'uid_tt',
            'sessionid',
            'sid_tt'
        ]
        
        filtered = []
        for cookie in cookies:
            # 保留必要的Cookie或抖音域名下的所有Cookie
            if cookie['name'] in required_names or '.douyin.com' in cookie.get('domain', ''):
                filtered.append(cookie)
        
        logger.info(f"过滤后保留 {len(filtered)} 个Cookies")
        return filtered
    
    async def _get_browser(self) -> Browser:
        """获取浏览器实例"""
        if not self.browser:
            if not PLAYWRIGHT_AVAILABLE:
                raise ImportError("Playwright未安装")
            
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(
                headless=self.headless,
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--disable-dev-shm-usage',
                    '--no-sandbox'
                ]
            )
            
            self.context = await self.browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                locale='zh-CN'
            )
        
        return self.context
    
    async def start_auto_refresh(self):
        """启动自动刷新任务"""
        if self.auto_refresh and not self._refresh_task:
            self._refresh_task = asyncio.create_task(self._auto_refresh_loop())
            logger.info("自动Cookie刷新已启动")
    
    async def stop_auto_refresh(self):
        """停止自动刷新任务"""
        if self._refresh_task:
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass
            self._refresh_task = None
            logger.info("自动Cookie刷新已停止")
    
    async def _auto_refresh_loop(self):
        """自动刷新循环"""
        while True:
            try:
                await asyncio.sleep(self.refresh_interval)
                
                if self._need_refresh():
                    logger.info("触发自动Cookie刷新")
                    await self._refresh_cookies()
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"自动刷新异常: {e}")
                await asyncio.sleep(60)  # 出错后等待1分钟再试
    
    async def cleanup(self):
        """清理资源"""
        await self.stop_auto_refresh()
        
        if self.context:
            await self.context.close()
            self.context = None
        
        if self.browser:
            await self.browser.close()
            self.browser = None
        
        if self.playwright:
            await self.playwright.stop()
            self.playwright = None
        
        logger.info("Cookie管理器资源已清理")
    
    def get_cookie_dict(self) -> Optional[Dict[str, str]]:
        """获取Cookie字典格式"""
        if not self.current_cookies:
            return None
        
        cookie_dict = {}
        for cookie in self.current_cookies.cookies:
            cookie_dict[cookie['name']] = cookie['value']
        
        return cookie_dict
    
    def get_cookie_string(self) -> Optional[str]:
        """获取Cookie字符串格式"""
        cookie_dict = self.get_cookie_dict()
        if not cookie_dict:
            return None
        
        return '; '.join([f'{k}={v}' for k, v in cookie_dict.items()])
    
    async def __aenter__(self):
        """异步上下文管理器入口"""
        await self.start_auto_refresh()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器出口"""
        await self.cleanup()