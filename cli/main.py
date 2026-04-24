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
from utils.notifier import build_notifier
from utils.validators import is_short_url, normalize_short_url

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
    rate_limiter = RateLimiter(max_per_second=float(config.get('rate_limit', 2) or 2))
    retry_handler = RetryHandler(max_retries=config.get('retry_times', 3))
    queue_manager = QueueManager(max_workers=int(config.get('thread', 5) or 5))

    original_url = url

    async with DouyinAPIClient(cookie_manager.get_cookies()) as api_client:
        if progress_reporter:
            progress_reporter.advance_step("解析链接", "检查短链并解析 URL")
        # 支持多种短链变体：v.douyin.com / v.iesdouyin.com / 无 scheme 的裸链接
        if is_short_url(url):
            resolved_url = await api_client.resolve_short_url(normalize_short_url(url))
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
            safe_config = {
                k: v for k, v in config.config.items()
                if k not in ("cookies", "cookie", "transcript")
            }
            await database.add_history({
                'url': original_url,
                'url_type': parsed['type'],
                'total_count': result.total,
                'success_count': result.success,
                'config': json.dumps(safe_config, ensure_ascii=False),
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

    # 若 config 不存在且使用了 --hot-board / --search / --serve 等独立子命令，
    # 允许以默认配置运行（只要命令行提供了 --path）。
    if not Path(config_path).exists():
        if not (args.hot_board is not None or args.search or args.serve):
            display.print_error(f"Config file not found: {config_path}")
            return
        config = ConfigLoader(None)
    else:
        config = ConfigLoader(config_path)

    if args.path:
        config.update(path=args.path)

    # 独立子命令：热榜 / 搜索 / 服务
    if args.hot_board is not None or args.search:
        await _run_discovery_subcommand(args, config)
        return
    if args.serve:
        await _run_serve_subcommand(args, config)
        return

    if args.url:
        urls = args.url if isinstance(args.url, list) else [args.url]
        for url in urls:
            if url not in config.get('link', []):
                config.update(link=config.get('link', []) + [url])

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
        db_path = config.get('database_path', 'dy_downloader.db') or 'dy_downloader.db'
        database = Database(db_path=str(db_path))
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
        if database is not None:
            await database.close()
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

        await _dispatch_notifications(config, total_result, len(urls))
    else:
        # 所有链接都失败时，也发通知（若启用）
        await _dispatch_notifications(config, None, len(urls))


async def _run_discovery_subcommand(args, config: ConfigLoader) -> None:
    """处理 --hot-board 与 --search 子命令。"""
    from core.discovery import dump_hot_board, search_and_dump

    cookies = config.get_cookies()
    cookie_manager = CookieManager()
    cookie_manager.set_cookies(cookies)

    base_path = Path(config.get('path') or './Downloaded/')

    async with DouyinAPIClient(cookie_manager.get_cookies()) as api_client:
        if args.hot_board is not None:
            display.print_info("拉取抖音热搜榜...")
            result = await dump_hot_board(
                api_client, base_path, limit=int(args.hot_board or 0)
            )
            display.print_success(
                f"热榜已保存：{result['count']} 条 -> {result['path']}"
            )
        if args.search:
            display.print_info(f"搜索关键词：{args.search}")
            result = await search_and_dump(
                api_client,
                args.search,
                base_path,
                max_items=int(args.search_max or 50),
            )
            display.print_success(
                f"搜索结果已保存：{result['count']} 条 -> {result['path']}"
            )


async def _run_serve_subcommand(args, config: ConfigLoader) -> None:
    """启动 REST API 服务模式（fastapi + uvicorn 为可选依赖）。"""
    try:
        from server.app import run_server
    except ImportError as exc:
        display.print_error(
            f"REST 服务模式需要安装可选依赖 fastapi + uvicorn："
            f"\n  pip install fastapi uvicorn\n原始错误：{exc}"
        )
        return

    display.print_info(
        f"启动 REST 服务：http://{args.serve_host}:{args.serve_port}"
    )
    await run_server(config, host=args.serve_host, port=args.serve_port)


async def _dispatch_notifications(
    config: ConfigLoader, total_result: Any, url_count: int
) -> None:
    notifier = build_notifier(config)
    if not notifier.enabled:
        return

    if total_result is None:
        title = "抖音下载器：全部失败"
        body = f"共处理 {url_count} 个链接，无成功结果"
        level = "failure"
    else:
        fail_or_partial = total_result.failed > 0 or total_result.success == 0
        level = "failure" if fail_or_partial else "success"
        title = "抖音下载完成" if level == "success" else "抖音下载部分失败"
        body = (
            f"链接 {url_count} / 总作品 {total_result.total} / "
            f"成功 {total_result.success} / 失败 {total_result.failed} / "
            f"跳过 {total_result.skipped}"
        )

    try:
        summary = await notifier.send(title=title, body=body, level=level)
        if summary:
            succ = sum(1 for ok in summary.values() if ok)
            logger.info(
                "Notification dispatched to %d provider(s), %d ok",
                len(summary),
                succ,
            )
    except Exception as exc:  # 通知失败不应影响主流程
        logger.warning("Notification dispatch error: %s", exc)


def main():
    parser = argparse.ArgumentParser(description='Douyin Downloader - 抖音批量下载工具')
    parser.add_argument('-u', '--url', action='append', help='Download URL(s)')
    parser.add_argument('-c', '--config', help='Config file path (default: config.yml)')
    parser.add_argument('-p', '--path', help='Save path')
    parser.add_argument('-t', '--thread', type=int, help='Thread count')
    parser.add_argument('--show-warnings', action='store_true', help='Show warning logs in console')
    parser.add_argument('-v', '--verbose', action='store_true', help='Enable verbose console logs')
    parser.add_argument(
        '--hot-board',
        type=int,
        nargs='?',
        const=0,
        default=None,
        metavar='N',
        help='拉取抖音热搜榜并导出 JSONL，可选上限 N（默认全部）',
    )
    parser.add_argument(
        '--search',
        type=str,
        default=None,
        metavar='KEYWORD',
        help='按关键词搜索作品并导出 JSONL',
    )
    parser.add_argument(
        '--search-max',
        type=int,
        default=50,
        help='--search 场景下最多拉取条数（默认 50）',
    )
    parser.add_argument(
        '--serve',
        action='store_true',
        help='以 REST API 服务模式运行（需要安装 fastapi + uvicorn）',
    )
    parser.add_argument(
        '--serve-host', type=str, default='127.0.0.1', help='REST 服务监听地址'
    )
    parser.add_argument(
        '--serve-port', type=int, default=8000, help='REST 服务监听端口'
    )
    try:
        from __init__ import __version__
    except ImportError:
        __version__ = "2.0.0"
    parser.add_argument('--version', action='version', version=__version__)

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
