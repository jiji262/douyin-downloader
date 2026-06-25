# 登录态失效自动重新登录（auto-relogin）设计

日期：2026-06-25
状态：待评审

## 背景与问题

`--search "手机" --search-max 10` 输出 `data` 为空。排查证据：抖音返回

```json
{"status_code": 2483, "status_msg": "请先登录，再继续搜索吧"}
```

`config/cookies.json`（2026-02-18）里的登录会话已过期。cookie 确实被发往
`douyin.com`（`sessionid`/`sid_tt` 等都在请求里），所以不是传输 bug，是**服务端登录态失效**。

当前体验缺陷：CLI 把这种情况当成"成功 0 条"（`print_success("搜索结果已保存：0 条")`），
真实原因（需重新登录）被埋没，看起来像 bug。

## 目标

正常运行任意命令时，若检测到抖音返回"未登录"（2483），**自动打开浏览器引导登录、抓取新
cookie、然后自动重试一次原命令**。覆盖所有命令（下载/搜索/热榜）。

## 非目标

- 不做主动 pre-flight 登录探测（公开作品下载本就不需要登录，避免误触发）。
- 不在桌面端实现终端浏览器登录流程（桌面有自己的 GUI 登录）。
- 不处理无限重试；只重试一次。

## 方案（Option A）：单点检测抛异常 + CLI 顶层捕获重试

三层分工：

| 层 | 文件 | 两仓库 |
|---|---|---|
| 检测 + `LoginRequiredError` | `core/api_client.py`（+ `core/__init__.py` 导出） | **逐字节同步到 desktop** |
| 终端浏览器登录的可复用入口 | `tools/cookie_fetcher.py` | **同步到 desktop（加法式）** |
| 交互式重新登录 + 捕获重试 | `cli/login_flow.py`（新）、`cli/main.py` | **仅 CLI** |

### 1. 检测（`core/api_client.py`，共享）

新增异常类（定义在 `core/api_client.py`，并在 `core/__init__.py` 中 re-export）：

```python
class LoginRequiredError(Exception):
    def __init__(self, status_code: int, status_msg: str, path: str):
        self.status_code = status_code
        self.status_msg = status_msg
        self.path = path
        super().__init__(f"login required (status_code={status_code}) at {path}: {status_msg}")
```

检测谓词（窄、可扩展）：

```python
_LOGIN_REQUIRED_STATUS_CODES = {2483}

def _is_login_required(data: dict) -> bool:
    if not isinstance(data, dict):
        return False
    code = data.get("status_code")
    msg = str(data.get("status_msg") or "")
    return code in _LOGIN_REQUIRED_STATUS_CODES or "请先登录" in msg
```

落点：`_request_json` 解析出 `data`（dict）后、`return` 之前调用检测，命中即**立即抛
`LoginRequiredError`**，不消耗那 3 次重试（重试登录态响应无意义）。
因为所有 endpoint 都走 `_request_json`，**一个点天然覆盖全部命令**。

> 注意：2483 响应是 status 200 + 非空 JSON body，会正常通过 empty-body 重试判断、被
> 解析成 dict，然后命中检测。不影响既有的反爬空响应重试逻辑。

### 2. 可复用的登录抓取入口（`tools/cookie_fetcher.py`，共享、加法式）

现有 `capture_cookies(args: argparse.Namespace)` 依赖 argparse。新增一个**参数化薄封装**，
避免上层伪造 Namespace：

```python
async def fetch_cookies(
    *, output: Path, config: Optional[Path] = None,
    url: str = DEFAULT_URL, browser: str = "chromium",
    headless: bool = False, include_all: bool = False,
) -> int:
    args = argparse.Namespace(output=output, config=config, url=url,
                              browser=browser, headless=headless, include_all=include_all)
    return await capture_cookies(args)
```

`main()` 保持不变（仍走 argparse → capture_cookies）。纯加法。

### 3. 交互式重新登录（`cli/login_flow.py`，新文件，仅 CLI）

```python
async def interactive_relogin(config) -> Optional[dict]:
    """打开浏览器引导登录、抓 cookie、写回 config/cookies.json，返回新 cookie dict 或 None。"""
```

行为：
- 打印中文提示："登录态已失效（抖音要求重新登录）。即将打开浏览器，请完成抖音登录后回到终端按 Enter。"
- 调用 `tools.cookie_fetcher.fetch_cookies(output=Path("config/cookies.json"))`。
- 成功后从磁盘读回新 cookie，返回 dict；失败（含用户未登录成功、cookie 仍缺 `sessionid`）返回 None。
- **Playwright 未安装**：`fetch_cookies` 已优雅返回非 0 并打印安装提示；这里捕获并给出
  `pip install playwright && playwright install chromium，或手动更新 config/cookies.json`。

### 4. 顶层捕获 + 重试一次（`cli/main.py`，仅 CLI）

新增包装器：

```python
async def _run_with_relogin(make_coro, config, cookie_manager) -> Any:
    for attempt in range(2):  # 最多一次重登
        try:
            return await make_coro()
        except LoginRequiredError as exc:
            if attempt == 1 or not _can_interactive_login():
                display.print_error(f"登录态失效且无法自动重登：{exc.status_msg}")
                raise  # 放弃：向上抛，由 main() 顶层 handler 统一报错并退出（exit 1）
            display.print_warning(f"检测到未登录（status {exc.status_code}），开始重新登录…")
            new_cookies = await interactive_relogin(config)
            if not new_cookies:
                display.print_error("重新登录未完成，已中止。")
                raise
            config.update(cookies=new_cookies)
            cookie_manager.set_cookies(new_cookies)
```

应用点：
- **discovery 子命令**：包住 `_run_discovery_subcommand` 的实际工作（搜索/热榜单次调用，重试干净）。
- **下载循环**：在 `main_async` 的 per-URL 循环里包 `download_url`。重登后更新 `cookie_manager`，
  后续 URL 各自新建的 `DouyinAPIClient` 自动用上新 cookie；已下文件被既有 skip 机制跳过，整体安全。

护栏 `_can_interactive_login()`：`sys.stdin.isatty()` 且非 `--serve`。非交互（CI/服务模式）
**不自动开浏览器**，只清晰报错并停止。只重试一次，绝不死循环。

## 错误处理矩阵

| 情况 | 行为 |
|---|---|
| 2483 / "请先登录" | 抛 `LoginRequiredError` → CLI 触发重登 → 重试一次 |
| 重登成功、重试仍 2483 | 第二次抛出后不再重登，明确报错中止 |
| 非交互环境 | 不开浏览器，打印"请手动更新 cookie"并中止 |
| Playwright 未装 | 打印安装指引并中止，不崩 |
| 用户关掉浏览器/没登录成功 | `interactive_relogin` 返回 None → 明确报错中止 |

## 测试计划

- **core（两仓库各一份）**：`_is_login_required` 真值表；构造 2483 dict 断言
  `_request_json` 抛 `LoginRequiredError`（monkeypatch session 返回该 body），正常 body 不抛。
- **CLI（仅 CLI）**：
  - `_run_with_relogin`：假 `make_coro` 第一次抛 `LoginRequiredError`、第二次成功，
    mock `interactive_relogin` 返回新 cookie，断言重试一次且 `cookie_manager` 被更新。
  - 非 tty 时不调用 `interactive_relogin`、直接报错。
- 现有 `tests/test_discovery.py`、api_client 相关测试保持绿。

## 桌面端同步清单（desktop）

> 注意：桌面端**没有抖音搜索功能**（GUI 的"搜索"框只是任务列表本地过滤 `/api/v1/history?q=`，
> 不是抖音内容搜索）。但检测是 endpoint 无关的：桌面的个人内容端点
> （`/api/v1/my-content/likes`、`/collects`、`/collectmixes`、`/self`）都走共享 `_request_json`、
> 都需要登录，会话失效时同样返回 2483。**桌面端的收益来自这些 my-content 流程，而非搜索。**
> 因此本同步不搬任何搜索代码。

- **必做**：把 `LoginRequiredError` + `_is_login_required` + `_request_json` 检测落点
  同步进 desktop `core/api_client.py`（该文件与 CLI 逐字节相同，零冲突），并在 desktop
  `core/__init__.py` 同步导出。
  - 不会破坏桌面：`server/jobs.py:JobManager._run` 已有 catch-all `except Exception`，
    会把 `LoginRequiredError` 转成 FAILED + SSE error 事件，前端正常显示。
- **必做**：同步 `tools/cookie_fetcher.py` 的 `fetch_cookies` 薄封装（加法式）。
- **不搬到桌面**：`cli/login_flow.py`、`cli/main.py` 的重登包装（桌面用自己的 GUI 登录）。
  这是有意分叉（桌面 `cli/main.py` 与 CLI 本就不同）。
- **桌面端可选增强（非本次范围）**：在 `_run` 专门 catch `LoginRequiredError`，给明确
  "登录态失效，请重新登录" + 特定事件，前端弹"重新登录"按钮，复用其 `POST /api/v1/cookies` GUI 登录。

## 受影响文件一览

CLI 仓库：
- `core/api_client.py`（异常类 + 检测 + 落点）
- `core/__init__.py`（导出）
- `tools/cookie_fetcher.py`（`fetch_cookies` 封装）
- `cli/login_flow.py`（新）
- `cli/main.py`（包装器 + 应用到 discovery / 下载循环）
- `tests/`（core 检测测试 + cli 重登测试）

desktop 仓库：
- `core/api_client.py`、`core/__init__.py`、`tools/cookie_fetcher.py`、对应 core 测试
