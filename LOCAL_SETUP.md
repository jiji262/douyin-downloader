# 本地部署说明（本机）

路径：`D:\dev\github\douyin-downloader`（业务工具仓，不进 `D:\dev\infra`）

## 已完成

- 克隆仓库
- `.venv` + 依赖（含 Playwright Chromium、FastAPI/Uvicorn）
- `config.yml`（已 gitignore，勿提交）
- 热搜榜冒烟：`--hot-board` 已通

## 生产面（收藏日更 → MCP 转写 → `_factory`）

权威路径与权限见 ghaishu：`.agents/contracts/media-sources.md`。

| 用途 | 落点 |
|------|------|
| 日更入口 | `scripts/daily-favorites.ps1` |
| 计划任务说明 | `scripts/SCHEDULED-TASK.md` |
| 薄配置 overlay | `config.automation.yml`（与 `%APPDATA%\Douzy\config.yml` 合并） |
| 转写 hook | `local_pipeline/mcp_transcript.py`（直调 MCP `extract_text`，非 `:8080`） |
| 存量单条补跑 | `tools/run_mcp_transcript.py` |
| 配置合并 | `tools/merge_daily_config.py` |

硬约束：

- Douzy `transcript.enabled` 保持 `false`；生产转写走 `mcp_transcript`
- Cookie / 媒体根复用 `%APPDATA%\Douzy\`（`.cookies.json` + `path: G:\media\douyin`）
- 收藏落盘：`collect_use_aweme_author_dir: true` + `group_by_mode: false` → `G:\media\douyin\<原作者>\{date}_{title}_{id}\`（无 `self`/`collect` 层）
- 文件名模板保持 Douzy 默认 `{date}_{title}_{id}`（引擎已截断，不必再缩短）
- API Key：优先环境变量 `DOUYIN_API_KEY`（否则 `API_KEY` / `OPENAI_API_KEY`）；禁止写入 git

### 存量补洞（本地已有 mp4、日更会 skip 下载时）

日更对已落盘 aweme **不会**再进 hook。补转写须显式指定路径（禁止整库递归脚本当 Agent 默认动作）：

**单条：**

```powershell
$env:DOUYIN_API_KEY = "..."   # 或依赖 Douzy transcript.api_key 由日更脚本进程内加载
.\.venv\Scripts\python.exe tools\run_mcp_transcript.py `
  --aweme-id <id> `
  --video "G:\media\douyin\作者\...\xxx_<id>.mp4" `
  --author "作者名"
```

**按失败 delta 批量（只读 JSONL，不扫库）：**

```powershell
.\.venv\Scripts\python.exe tools\backfill_failed_from_delta.py `
  --delta G:\media\douyin\_factory\delta-2026-08-09.jsonl
```

然后投影到 ghaishu（缺 delta 默认非 0；历史 UTC 错位可用 `-CompatMiskeyedUtc`）：

```powershell
powershell -File D:\ghaishu\.scripts\ingest-douyin-delta.ps1
# 或恢复错位批次：
powershell -File D:\ghaishu\.scripts\ingest-douyin-delta.ps1 -Date 2026-08-10 -CompatMiskeyedUtc
```

转写默认优先本地 mp4 ASR（规避分享页 HTML / `videoInfoRes` 间歇失败）。

长片偶发硅基 `500`（出现 `.transcript.err.txt`）：下载仍算成功；稍后重跑上面的补洞命令即可。

## 还差一步：Cookie（调试 / 非 Douzy 路径）

若不用 Douzy 目录、改用仓库 `config.yml`：

```bat
scripts\fetch-cookies.bat
```

生产日更只维护 `%APPDATA%\Douzy\.cookies.json`。

## 常用命令

```powershell
# 生产日更（收藏增量 + MCP 转写 + _factory）
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\daily-favorites.ps1 -CollectLimit 5

# 单链下载（调试）
.\.venv\Scripts\python.exe run.py -c "$env:APPDATA\Douzy\config.yml" -u "https://..." -v

# REST API（本机，默认 8010；8000 常被占用）
.\scripts\serve.ps1

# 热搜（可不登录）
.\.venv\Scripts\python.exe run.py --hot-board 10 -p .\Downloaded
```

## 输出

- 生产媒体根：`G:\media\douyin`（与 Douzy 一致）
- 工厂索引：`G:\media\douyin\_factory\`（`manifest.jsonl`、`delta-YYYY-MM-DD.jsonl`）
- 去重库：尽量共用 `%APPDATA%\Douzy\dy_downloader.db`（日更写入绝对 `database_path`）
