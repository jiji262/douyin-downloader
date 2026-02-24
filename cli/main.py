import asyncio
import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

from config import ConfigLoader
from auth import CookieManager
from storage import Database, FileManager
from control import QueueManager, RateLimiter, RetryHandler
from core import DouyinAPIClient, URLParser, DownloaderFactory
from cli.progress_display import ProgressDisplay
from utils.logger import setup_logger, set_console_log_level

logger = setup_logger('CLI')
display = ProgressDisplay()


def _as_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


async def download_url(
    url: str,
    config: ConfigLoader,
    cookie_manager: CookieManager,
    database: Database = None,
    progress_reporter: ProgressDisplay = None,
):
    if progress_reporter:
        progress_reporter.advance_step("初始化", "创建下载组件")
    file_manager = FileManager(config.get('path'))
    rate_limiter = RateLimiter(max_per_second=2)
    retry_handler = RetryHandler(max_retries=config.get('retry_times', 3))
    queue_manager = QueueManager(max_workers=int(config.get('thread', 5) or 5))

    original_url = url

    async with DouyinAPIClient(cookie_manager.get_cookies()) as api_client:
        if progress_reporter:
            progress_reporter.advance_step("解析链接", "检查短链并解析 URL")
        if url.startswith('https://v.douyin.com'):
            resolved_url = await api_client.resolve_short_url(url)
            if resolved_url:
                url = resolved_url
            else:
                if progress_reporter:
                    progress_reporter.update_step("解析链接", "短链解析失败")
                display.print_error(f"Failed to resolve short URL: {url}")
                return None

        parsed = URLParser.parse(url)
        if not parsed:
            if progress_reporter:
                progress_reporter.update_step("解析链接", "URL 解析失败")
            display.print_error(f"Failed to parse URL: {url}")
            return None

        if not progress_reporter:
            display.print_info(f"URL type: {parsed['type']}")
        if progress_reporter:
            progress_reporter.advance_step("创建下载器", f"URL 类型: {parsed['type']}")

        downloader = DownloaderFactory.create(
            parsed['type'],
            config,
            api_client,
            file_manager,
            cookie_manager,
            database,
            rate_limiter,
            retry_handler,
            queue_manager,
            progress_reporter=progress_reporter,
        )

        if not downloader:
            if progress_reporter:
                progress_reporter.update_step("创建下载器", "未找到匹配下载器")
            display.print_error(f"No downloader found for type: {parsed['type']}")
            return None

        if progress_reporter:
            progress_reporter.advance_step("执行下载", "开始拉取与下载资源")
        result = await downloader.download(parsed)

        if progress_reporter:
            progress_reporter.advance_step(
                "记录历史",
                "写入数据库历史" if (result and database) else "数据库未启用，跳过",
            )
        if result and database:
            await database.add_history({
                'url': original_url,
                'url_type': parsed['type'],
                'total_count': result.total,
                'success_count': result.success,
                'config': json.dumps(config.config, ensure_ascii=False),
            })

        if progress_reporter:
            if result:
                progress_reporter.advance_step(
                    "收尾",
                    f"成功 {result.success} / 失败 {result.failed} / 跳过 {result.skipped}",
                )
            else:
                progress_reporter.advance_step("收尾", "无可统计结果")

        return result


async def main_async(args):
    display.show_banner()

    if args.config:
        config_path = args.config
    else:
        config_path = 'config.yml'

    if not Path(config_path).exists():
        display.print_error(f"Config file not found: {config_path}")
        return

    config = ConfigLoader(config_path)

    if args.url:
        urls = args.url if isinstance(args.url, list) else [args.url]
        for url in urls:
            if url not in config.get('link', []):
                config.update(link=config.get('link', []) + [url])

    if args.path:
        config.update(path=args.path)

    if args.thread:
        config.update(thread=args.thread)

    if not config.validate():
        display.print_error("Invalid configuration: missing required fields")
        return

    cookies = config.get_cookies()
    cookie_manager = CookieManager()
    cookie_manager.set_cookies(cookies)

    if not cookie_manager.validate_cookies():
        display.print_warning("Cookies may be invalid or incomplete")

    database = None
    if config.get('database'):
        database = Database()
        await database.initialize()
        display.print_success("Database initialized")

    urls = config.get_links()
    display.print_info(f"Found {len(urls)} URL(s) to process")

    all_results = []
    progress_config = config.get("progress", {}) or {}
    quiet_by_config = _as_bool(progress_config.get("quiet_logs", True), default=True)
    quiet_progress_logs = quiet_by_config and not (args.verbose or args.show_warnings)
    if quiet_progress_logs:
        # Progress 运行期间若有大量错误日志会触发 rich 反复重绘，导致屏幕出现重复块。
        # 默认静默控制台日志，下载完成后再恢复。
        set_console_log_level(logging.CRITICAL)

    display.start_download_session(len(urls))
    try:
        for i, url in enumerate(urls, 1):
            display.start_url(i, len(urls), url)

            result = await download_url(
                url,
                config,
                cookie_manager,
                database,
                progress_reporter=display,
            )
            if result:
                all_results.append(result)
                display.complete_url(result)
            else:
                display.fail_url("下载失败或链接无效")
    finally:
        display.stop_download_session()
        if quiet_progress_logs:
            set_console_log_level(logging.ERROR)

    if all_results:
        from core.downloader_base import DownloadResult
        total_result = DownloadResult()
        for r in all_results:
            total_result.total += r.total
            total_result.success += r.success
            total_result.failed += r.failed
            total_result.skipped += r.skipped

        display.print_success("\n=== Overall Summary ===")
        display.show_result(total_result)


def main():
    parser = argparse.ArgumentParser(description='Douyin Downloader - 抖音批量下载工具')
    parser.add_argument('-u', '--url', action='append', help='Download URL(s)')
    parser.add_argument('-c', '--config', help='Config file path (default: config.yml)')
    parser.add_argument('-p', '--path', help='Save path')
    parser.add_argument('-t', '--thread', type=int, help='Thread count')
    parser.add_argument('--show-warnings', action='store_true', help='Show warning logs in console')
    parser.add_argument('-v', '--verbose', action='store_true', help='Enable verbose console logs')
    parser.add_argument('--version', action='version', version='2.0.0')

    args = parser.parse_args()

    if args.verbose:
        set_console_log_level(logging.INFO)
    elif args.show_warnings:
        set_console_log_level(logging.WARNING)
    else:
        set_console_log_level(logging.ERROR)

    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        display.print_warning("\nDownload interrupted by user")
        sys.exit(0)
    except Exception as e:
        display.print_error(f"Fatal error: {e}")
        logger.exception("Fatal error occurred")
        sys.exit(1)


if __name__ == '__main__':
    main()
