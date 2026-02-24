#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
抖音Cookie自动提取器
使用Playwright自动登录并提取Cookie
"""

import asyncio
import json
import os
import sys
import yaml
from pathlib import Path
from typing import Dict, Optional
import time

try:
    from playwright.async_api import async_playwright, Browser, Page
    from rich.console import Console
    from rich.prompt import Prompt, Confirm
    from rich.panel import Panel
    from rich import print as rprint
except ImportError:
    print("请安装必要的依赖: pip install playwright rich pyyaml")
    print("并运行: playwright install chromium")
    sys.exit(1)

console = Console()


class CookieExtractor:
    """Cookie提取器"""
    
    def __init__(self, config_path: str = "config_simple.yml"):
        self.config_path = config_path
        self.cookies = {}
        
    async def extract_cookies(self, headless: bool = False) -> Dict:
        """提取Cookie
        
        Args:
            headless: 是否无头模式运行
        """
        console.print(Panel.fit(
            "[bold cyan]抖音Cookie自动提取器[/bold cyan]\n"
            "[dim]将自动打开浏览器，请在浏览器中完成登录[/dim]",
            border_style="cyan"
        ))
        
        async with async_playwright() as p:
            # 启动浏览器
            browser = await p.chromium.launch(
                headless=headless,
                args=['--disable-blink-features=AutomationControlled']
            )
            
            # 创建上下文（模拟真实浏览器）
            context = await browser.new_context(
                viewport={'width': 1280, 'height': 720},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            )
            
            # 添加初始化脚本（隐藏自动化特征）
            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
            """)
            
            # 创建页面
            page = await context.new_page()
            
            try:
                # 访问抖音登录页
                console.print("\n[cyan]正在打开抖音登录页面...[/cyan]")
                await page.goto('https://www.douyin.com', wait_until='networkidle')
                
                # 等待用户登录
                console.print("\n[yellow]请在浏览器中完成登录操作[/yellow]")
                console.print("[dim]登录方式：[/dim]")
                console.print("  1. 扫码登录（推荐）")
                console.print("  2. 手机号登录")
                console.print("  3. 第三方账号登录")
                
                # 等待登录成功的标志
                logged_in = await self._wait_for_login(page)
                
                if logged_in:
                    console.print("\n[green]✅ 登录成功！正在提取Cookie...[/green]")
                    
                    # 提取Cookie
                    cookies = await context.cookies()
                    
                    # 转换为字典格式
                    cookie_dict = {}
                    cookie_string = ""
                    
                    for cookie in cookies:
                        cookie_dict[cookie['name']] = cookie['value']
                        cookie_string += f"{cookie['name']}={cookie['value']}; "
                    
                    self.cookies = cookie_dict
                    
                    # 显示重要Cookie
                    console.print("\n[cyan]提取到的关键Cookie:[/cyan]")
                    important_cookies = ['sessionid', 'sessionid_ss', 'ttwid', 'passport_csrf_token', 'msToken']
                    for name in important_cookies:
                        if name in cookie_dict:
                            value = cookie_dict[name]
                            console.print(f"  • {name}: {value[:20]}..." if len(value) > 20 else f"  • {name}: {value}")
                    
                    # 保存Cookie
                    if Confirm.ask("\n是否保存Cookie到配置文件？"):
                        self._save_cookies(cookie_dict)
                        console.print("[green]✅ Cookie已保存到配置文件[/green]")
                    
                    # 保存完整Cookie字符串到文件
                    with open('cookies.txt', 'w', encoding='utf-8') as f:
                        f.write(cookie_string.strip())
                    console.print("[green]✅ 完整Cookie已保存到 cookies.txt[/green]")
                    
                    return cookie_dict
                else:
                    console.print("\n[red]❌ 登录超时或失败[/red]")
                    return {}
                    
            except Exception as e:
                console.print(f"\n[red]❌ 提取Cookie失败: {e}[/red]")
                return {}
            finally:
                await browser.close()
    
    async def _wait_for_login(self, page: Page, timeout: int = 300) -> bool:
        """等待用户登录
        
        Args:
            page: 页面对象
            timeout: 超时时间（秒）
        """
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            # 检查是否已登录（多种判断方式）
            try:
                # 方式1：检查是否有用户头像
                avatar = await page.query_selector('div[class*="avatar"]')
                if avatar:
                    await asyncio.sleep(2)  # 等待Cookie完全加载
                    return True
                
                # 方式2：检查URL是否包含用户ID
                current_url = page.url
                if '/user/' in current_url:
                    await asyncio.sleep(2)
                    return True
                
                # 方式3：检查是否有特定的登录后元素
                user_menu = await page.query_selector('[class*="user-info"]')
                if user_menu:
                    await asyncio.sleep(2)
                    return True
                
            except:
                pass
            
            await asyncio.sleep(2)
            
            # 显示等待进度
            elapsed = int(time.time() - start_time)
            remaining = timeout - elapsed
            console.print(f"\r[dim]等待登录中... ({remaining}秒后超时)[/dim]", end="")
        
        return False
    
    def _save_cookies(self, cookies: Dict):
        """保存Cookie到配置文件"""
        # 读取现有配置
        if os.path.exists(self.config_path):
            with open(self.config_path, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f) or {}
        else:
            config = {}
        
        # 更新Cookie配置
        config['cookies'] = cookies
        
        # 保存配置
        with open(self.config_path, 'w', encoding='utf-8') as f:
            yaml.dump(config, f, allow_unicode=True, default_flow_style=False)
    
    async def quick_extract(self) -> Dict:
        """快速提取（使用已登录的浏览器会话）"""
        console.print("\n[cyan]尝试从已打开的浏览器提取Cookie...[/cyan]")
        console.print("[dim]请确保您已在浏览器中登录抖音[/dim]")
        
        # 这里可以使用CDP连接到已打开的浏览器
        # 需要浏览器以调试模式启动
        console.print("\n[yellow]请按以下步骤操作：[/yellow]")
        console.print("1. 关闭所有Chrome浏览器")
        console.print("2. 使用调试模式启动Chrome:")
        console.print("   Windows: chrome.exe --remote-debugging-port=9222")
        console.print("   Mac: /Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome --remote-debugging-port=9222")
        console.print("3. 在打开的浏览器中登录抖音")
        console.print("4. 按Enter继续...")
        
        input()
        
        try:
            async with async_playwright() as p:
                # 连接到已打开的浏览器
                browser = await p.chromium.connect_over_cdp("http://localhost:9222")
                contexts = browser.contexts
                
                if contexts:
                    context = contexts[0]
                    pages = context.pages
                    
                    # 查找抖音页面
                    douyin_page = None
                    for page in pages:
                        if 'douyin.com' in page.url:
                            douyin_page = page
                            break
                    
                    if douyin_page:
                        # 提取Cookie
                        cookies = await context.cookies()
                        cookie_dict = {}
                        
                        for cookie in cookies:
                            if 'douyin.com' in cookie.get('domain', ''):
                                cookie_dict[cookie['name']] = cookie['value']
                        
                        if cookie_dict:
                            console.print("[green]✅ 成功提取Cookie！[/green]")
                            self._save_cookies(cookie_dict)
                            return cookie_dict
                        else:
                            console.print("[red]未找到抖音Cookie[/red]")
                    else:
                        console.print("[red]未找到抖音页面，请先访问douyin.com[/red]")
                else:
                    console.print("[red]未找到浏览器上下文[/red]")
                    
        except Exception as e:
            console.print(f"[red]连接浏览器失败: {e}[/red]")
            console.print("[yellow]请确保浏览器以调试模式启动[/yellow]")
        
        return {}


async def main():
    """主函数"""
    extractor = CookieExtractor()
    
    console.print("\n[cyan]请选择提取方式：[/cyan]")
    console.print("1. 自动登录提取（推荐）")
    console.print("2. 从已登录浏览器提取")
    console.print("3. 手动输入Cookie")
    
    choice = Prompt.ask("请选择", choices=["1", "2", "3"], default="1")
    
    if choice == "1":
        # 自动登录提取
        headless = not Confirm.ask("是否显示浏览器界面？", default=True)
        cookies = await extractor.extract_cookies(headless=headless)
        
    elif choice == "2":
        # 从已登录浏览器提取
        cookies = await extractor.quick_extract()
        
    else:
        # 手动输入
        console.print("\n[cyan]请输入Cookie字符串：[/cyan]")
        console.print("[dim]格式: name1=value1; name2=value2; ...[/dim]")
        cookie_string = Prompt.ask("Cookie")
        
        cookies = {}
        for item in cookie_string.split(';'):
            if '=' in item:
                key, value = item.strip().split('=', 1)
                cookies[key] = value
        
        if cookies:
            extractor._save_cookies(cookies)
            console.print("[green]✅ Cookie已保存[/green]")
    
    if cookies:
        console.print("\n[green]✅ Cookie提取完成！[/green]")
        console.print("[dim]您现在可以运行下载器了：[/dim]")
        console.print("python3 downloader.py -c config_simple.yml")
    else:
        console.print("\n[red]❌ 未能提取Cookie[/red]")


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        console.print("\n[yellow]用户取消操作[/yellow]")
    except Exception as e:
        console.print(f"\n[red]程序异常: {e}[/red]")