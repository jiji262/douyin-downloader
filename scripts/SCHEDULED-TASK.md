# Task Scheduler recipe (M2) — Douyin favorites daily

Do **not** run as SYSTEM if G: is a user-mapped drive; register under your login.

## Trigger (current)

**每天本地时间 03:00**（`StartWhenAvailable` + `WakeToRun`）。  
要求：到点时用户会话仍在（锁屏可以；完全注销可能不跑 Interactive 任务）。G: 须已映射可写。

一键（重）注册（Access Denied 时用管理员 PowerShell）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File D:\dev\github\douyin-downloader\scripts\register-DouyinFavoritesDaily.ps1
```

## Manual register snippet

```powershell
$script = 'D:\dev\github\douyin-downloader\scripts\daily-favorites.ps1'
$action = New-ScheduledTaskAction `
  -Execute 'powershell.exe' `
  -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$script`""
$trigger = New-ScheduledTaskTrigger -Daily -At '3:00AM'
$settings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -WakeToRun
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask `
  -TaskName 'DouyinFavoritesDaily' `
  -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
  -Description 'Douyin favorites daily 03:00 → MCP transcript → _factory (+ ghaishu ingest)' `
  -Force
```

## Manual / WhatIf

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File D:\dev\github\douyin-downloader\scripts\daily-favorites.ps1 -CollectLimit 5
# Skip ghaishu projection:
powershell -NoProfile -ExecutionPolicy Bypass -File D:\dev\github\douyin-downloader\scripts\daily-favorites.ps1 -SkipIngest
```

Layout (production daily): `G:\media\douyin\<原作者>\{date}_{title}_{id}\`  
(`collect_use_aweme_author_dir=true`, `group_by_mode=false` — no `self` / no `collect` layer)

## 存量补洞

日更对**已落盘** aweme 会 skip 下载，因而**不会**触发 MCP hook。

### 单条

见 `LOCAL_SETUP.md`「存量补洞」，入口：`tools/run_mcp_transcript.py`（须显式 `--video` / `--aweme-id`）。

### 按 delta 批量（推荐：修复 HTML/`videoInfoRes` 失败后的存量）

只读 `_factory/delta-*.jsonl` 中的 `failed` 行（**不**递归扫媒体库）：

```powershell
cd D:\dev\github\douyin-downloader
.\.venv\Scripts\python.exe tools\backfill_failed_from_delta.py `
  --delta G:\media\douyin\_factory\delta-2026-08-09.jsonl
# 投影到当日 handoff（本地日键）：
powershell -NoProfile -ExecutionPolicy Bypass -File D:\ghaishu\.scripts\ingest-douyin-delta.ps1
```

生产转写默认 **prefer local mp4 ASR**（跳过脆弱的分享页 HTML / `videoInfoRes`）；若本地文件无音轨或抽音失败，自动回退 share URL 重拉再 ASR（`via=share_url_after_local_fail`）。

长片若出现硅基 `500`（`.transcript.err.txt`）：下载仍算成功；稍后重跑 `run_mcp_transcript.py` / backfill 即可。

## 观测约定（P2）

**权威回放面 = 应用日志**，不是 Task Scheduler Operational 频道。

| 信号 | 路径 / 含义 |
|------|-------------|
| 应用日志 | `%LOCALAPPDATA%\douyin-downloader-daily\logs\YYYYMMDD.log` |
| 成功戳 | `G:\media\douyin\_factory\LAST_OK`（ISO 本地时间） |
| 降级戳 | `G:\media\douyin\_factory\LAST_DEGRADED`（缺 delta / 转写全失败 / ingest 失败时写入；成功跑会删除） |
| 日键 delta | `G:\media\douyin\_factory\delta-YYYY-MM-DD.jsonl`（**本地 UTC+8 日历日**，与 ingest 一致） |
| ghaishu handoff | `D:\ghaishu\01-topics\refs\media-ingest\YYYY-MM-DD.md` |
| 计划任务结果 | `Get-ScheduledTaskInfo '\DouyinFavoritesDaily'` → `LastTaskResult`（0=业务成功；6/7/8=业务失败，见 `daily-favorites.ps1` 头注释） |

`Microsoft-Windows-TaskScheduler/Operational` 在本机可能为 **disabled**。不要求启用；排障优先读应用日志 + `LAST_*` + delta。若需启用：

```powershell
# 可选；非本流水线前置条件
wevtutil sl Microsoft-Windows-TaskScheduler/Operational /e:true
```

## Cookie renewal

Only maintain `%APPDATA%\Douzy\.cookies.json` (Douzy login or cookie_fetcher).  
Logs: `%LOCALAPPDATA%\douyin-downloader-daily\logs\YYYYMMDD.log`

## API key

Prefer `DOUYIN_API_KEY` env. Else `API_KEY` / `OPENAI_API_KEY`.  
If unset, the script may load Douzy `transcript.api_key` into the **process env only** (never git).
