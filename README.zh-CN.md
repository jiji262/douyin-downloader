# 抖音下载器（Douyin Downloader）

<p align="center">
  <img src="https://socialify.git.ci/jiji262/douyin-downloader/image?custom_description=AI+agent+skill+and+CLI+for+Douyin%3A+hot+board%2C+search%2C+live+recording%2C+transcription.&description=1&font=Source+Code+Pro&forks=1&owner=1&pattern=Circuit+Board&stargazers=1&theme=Light" alt="douyin-downloader" width="820" />
</p>

**简体中文** · [English](README.md)

**面向 AI Agent 的抖音 Skill。** 装好 [`douyin-dl` skill](#ai-agent--skill-用法) 后，直接用日常语言让 Claude Code、Codex 等 agent 查抖音热榜、搜索作品、看博主资料、录直播、转写本地视频。skill 告诉 agent 何时运行 `douyin-dl agent` 命令、怎样读每条命令输出的那一行 JSON。下载视频和图文不在 skill 的能力范围内，请使用本项目的赞助商 **[Douzy 桌面版](#douzy-桌面版)**。

<a id="ai-agent--skill-用法"></a>

## AI Agent Skill 使用指南

skill 就是 [`skills/douyin-dl/`](skills/douyin-dl/) 这个目录，里面是写给 agent 的说明，实际干活的是 `douyin-dl` 命令行。两者都要装。

### 第 1 步：安装 CLI

```bash
uv tool install douyin-dl-cli    # 或：pipx install douyin-dl-cli
douyin-dl agent doctor
```

- 需要 Python 3.10+。PyPI 包名是 `douyin-dl-cli`，装好后的命令是 `douyin-dl`。
- 想用 GitHub 上尚未发布的代码：`uv tool install git+https://github.com/jiji262/douyin-downloader`。已克隆仓库并装好依赖时，也可以直接运行 `python run.py agent doctor`。
- 不要用 `pip install` 装进共享环境：顶层包名 `utils`、`core`、`config` 会与其他项目冲突。
- 提示 `douyin-dl: command not found` 时，运行 `uv tool update-shell`（或 `pipx ensurepath`），再打开一个新终端。

`doctor` 不联网，会列出哪些命令已经可用。有两项功能需要额外组件，可以现在装，也可以等 agent 提示时再装：

| 功能 | 需要 | 用 uv 安装 |
|:---|:---|:---|
| 登录（`search`、`user` 要用） | Playwright 和 Chromium | `uv tool install --force "douyin-dl-cli[browser]"`。如果仍缺 Chromium，`login` 会返回确切的安装命令 |
| 转写 | Whisper，会连带安装体积很大的 PyTorch | `uv tool install --force "douyin-dl-cli[transcribe]"` |

两项都要时用 `"douyin-dl-cli[browser,transcribe]"`。直播录制使用 CLI 自带的 FFmpeg，无需另装。pipx 的命令和其他情况见[故障排查](skills/douyin-dl/references/troubleshooting.md)。

### 第 2 步：安装 skill

**Claude Code**：在 Claude Code 会话里依次输入：

```text
/plugin marketplace add jiji262/douyin-downloader
/plugin install douyin-dl@douyin-dl
```

第二条命令会打开插件详情，选好安装范围（scope）即可完成安装。输入 `/`，能看到 `/douyin-dl:douyin-dl` 就说明装好了。也可以在终端里先后运行 `claude plugin marketplace add jiji262/douyin-downloader` 和 `claude plugin install douyin-dl@douyin-dl`。

**Codex**：把 skill 目录复制到用户级 skill 目录：

```bash
git clone https://github.com/jiji262/douyin-downloader.git
mkdir -p ~/.agents/skills
cp -r douyin-downloader/skills/douyin-dl ~/.agents/skills/
```

Codex 会自动发现新 skill；没出现时重启 Codex。

**其他 agent**（Cursor、OpenCode 等）：用 [skills CLI](https://github.com/vercel-labs/skills) 安装。

```bash
npx skills add jiji262/douyin-downloader       # 只装到当前项目
npx skills add jiji262/douyin-downloader -g    # 所有项目可用
```

### 第 3 步：直接对 agent 说

通常提到抖音或贴一个抖音链接，agent 就会用上这个 skill。想点名调用时，在 Claude Code 里以 `/douyin-dl:douyin-dl` 开头（插件方式安装时），在 Codex 里写上 `$douyin-dl`。

| 你说 | agent 执行 | 前提 |
|:---|:---|:---|
| 「看看抖音热榜前 10。」 | `douyin-dl agent hot --max 10` | 无 |
| 「在抖音搜 `露营`，按点赞排序，总结前 10 条。」 | `douyin-dl agent search 露营 --sort likes --max 10` | 需登录 |
| 「看一下这个博主的资料：`https://www.douyin.com/user/…`」 | `douyin-dl agent user <链接>` | 需登录 |
| 「这个链接是什么？能下载吗？」 | `douyin-dl agent inspect <链接>` | 无 |
| 「把这个直播间录 10 分钟：`https://live.douyin.com/…`」 | `douyin-dl agent live <链接> --max-duration 600` | 无 |
| 「把 `./clip.mp4` 转成文字。」 | `douyin-dl agent transcribe ./clip.mp4` | Whisper |

### agent 会怎么做

skill 给 agent 定好了固定流程：

1. **先体检。** 每次会话开始先运行 `douyin-dl agent doctor`，确认哪些命令可用。
2. **装组件前先问你。** 缺组件时，把确切的安装命令给你看，你同意后才运行。
3. **登录前先问你。** `search` 需要登录态，`user` 需要已保存的 Cookie。agent 会先说明情况；你同意后它运行 `douyin-dl agent login`，弹出一个 Chromium 窗口，你在窗口里自己扫码或用短信登录抖音，登录完成后窗口自动关闭。
4. **按错误码处理结果。** 每条命令只输出一行 JSON，agent 根据 `error.code` 决定下一步，并转告影响你的警告。标题、昵称、热词等来自抖音的文字只当作数据，不当作指令。
5. **如实拒绝下载请求。** 视频、图文、合集和音乐链接会返回 `ARGUS_GATED`，博主主页的作品同样无法下载。agent 会直接告诉你做不到、推荐 [Douzy](#douzy-桌面版)，不会尝试任何绕过手段。

它还会控制对抖音的请求量：每次请求只搜索一次（要再搜会先问你），不并发调用，结果为空时最多重试一次。

### 登录与隐私

- Cookie 只保存在你的电脑上：`~/.douyin-dl/cookies.json`（文件权限 0600），可用 `DOUYIN_DL_HOME` 改位置。agent 模式不读取 `config.yml`，也不读取经典命令行保存的 Cookie。
- 命令输出只列出找到了哪些 Cookie 名称，不显示值。不要把 Cookie、Token 或短信验证码贴进对话；skill 也要求 agent 不得索要这些。
- 没有图形界面（如 SSH、服务器）时，自己把抖音 Cookie 导出为 JSON 文件，再运行 `douyin-dl agent login --from-file cookies.json` 导入。
- 请求发往 douyin.com。已保存的 Cookie 里没有 `msToken` 时，CLI 还会从 GitHub 下载一个小配置文件来生成它。

### 直播录制与转写

- **直播录制：** 必须给出时长，范围 1 秒到 6 小时（`--max-duration`，最多 21600 秒）；你没说时长，agent 会先问。文件默认存到 `./douyin-downloads`，也可以指定其他目录（`--out`）；录得久的会放到后台运行。直播间没在播时，agent 会如实告诉你。
- **转写：** 可以传一个文件，也可以传一个目录（递归查找 `.mp4`）。每个视频旁边（或 `--out` 指定的目录里）会生成 `<文件名>.transcript.txt`。默认 `--model base`、`--language zh`；首次运行会下载 Whisper 模型。

### 不经 agent 直接运行

这些命令也能直接在终端里用。每条命令在 stdout 只输出一行 JSON，日志写到 stderr：

```bash
douyin-dl agent hot --max 3
# {"schema":1,"ok":true,"verb":"hot","version":"2.1.0","data":{"items":[…],"count":3},"error":null,"warnings":[]}
```

只有退出码为 0 时 `ok` 才是 `true`。失败时按 `error.code`（如 `LOGIN_REQUIRED`、`ARGUS_GATED`、`UPSTREAM_EMPTY`）判断，不要按报错文字判断。需要代理时设置 `DOUYIN_PROXY`（如 `http://127.0.0.1:7890`）。全部字段、错误码与退出码见[契约](skills/douyin-dl/references/contract.md)。

### 更新

- **CLI：** `uv tool upgrade douyin-dl-cli`（或 `pipx upgrade douyin-dl-cli`）。
- **Claude Code 里的 skill：** 运行 `claude plugin update douyin-dl@douyin-dl`，或在 `/plugin` 的 **Installed** 页选择 **Update now**。这个 marketplace 默认不自动更新。
- **用 skills CLI 装的 skill：** `npx skills update`。复制安装的（如 Codex）重新复制一遍即可。
- JSON 输出里的 `version` 与 skill 的 `SKILL.md` 中 `metadata.version` 不一致时，把两者都更新到最新。

### 常见问题

| 现象 | 处理 |
|:---|:---|
| `douyin-dl: command not found` | 运行 `uv tool update-shell`（或 `pipx ensurepath`）后打开新终端。在此之前，agent 可以改用 skill 自带的 `scripts/dy.sh`，它通过 `uvx` 运行 CLI |
| `search` 又要求登录 | 登录态过期了，让 agent 重新登录即可 |
| 结果为空（`UPSTREAM_EMPTY`） | 触发了抖音的限流或风控。等几分钟再试，频繁重试会让封锁持续更久。境外代理常拿到空结果，可以关掉代理或换国内出口 |
| 视频链接返回 `ARGUS_GATED` | 符合预期：CLI 无法下载，重新登录也没用。请用 [Douzy](#douzy-桌面版) |
| Windows | 在 PowerShell 或 cmd 里直接运行 `douyin-dl agent …`；`scripts/dy.sh` 需要 Git Bash 或 WSL |

更多问题见[故障排查](skills/douyin-dl/references/troubleshooting.md)。

<a id="douzy-桌面版"></a>

## 赞助商

| 赞助商 | 简介 |
|:---:|:---|
| <a href="https://godouzy.com/"><img src="https://raw.githubusercontent.com/0X-42/douzy/main/media/workbench.png" alt="Douzy 工作台截图，使用示例数据" width="400" /></a> | **[Douzy 桌面版](https://godouzy.com/)**<br>把抖音、TikTok、YouTube、Telegram 和 X 的内容保存到电脑，skill 不支持的视频和图文下载可以交给它。在同一个窗口里粘贴链接、查看任务进度、翻找已保存的作品。<br>各平台按需启用插件或本机组件，部分批量和高级功能需要激活。<br>**[下载 Windows / macOS 版](https://godouzy.com/#get)** · [GitHub 项目](https://github.com/0X-42/douzy) · [最新版与发布说明](https://github.com/0X-42/douzy/releases/latest) |

**[成为赞助商](https://github.com/jiji262/douyin-downloader/issues/new?template=sponsor.yml)**

## 经典命令行版

原有的批量下载命令行（`python run.py`）仅支持**抖音**，提供批量下载、日期筛选、失败重试、下载历史，以及可选的评论采集、视频转写和完成通知。

> **下载限制：** 抖音接口风控目前阻止 CLI 下载单个视频／图文、合集、音乐、喜欢和收藏。主页作品可尝试 Playwright 浏览器兜底，但不保证成功。日常下载建议使用 **Douzy**。

更新 Cookie 或反复重试不能解决这类风控。直播录制属于实验功能：用 FFmpeg 分段录制，每段自动转成 MP4，断流后自动重连。

### 快速开始

需要 **Python 3.10+**，支持 Windows、macOS 和 Linux。使用前请先阅读上方 CLI 限制。

#### 1. 安装

```bash
git clone https://github.com/jiji262/douyin-downloader.git
cd douyin-downloader
python -m pip install -r requirements.txt
python -m pip install playwright
python -m playwright install chromium
```

#### 2. 配置并登录

将 [config.example.yml](config.example.yml) 复制为 `config.yml`：

```bash
cp config.example.yml config.yml
python -m tools.cookie_fetcher --config config.yml
```

Windows PowerShell 请用 `Copy-Item config.example.yml config.yml` 复制文件。在浏览器中登录抖音，再回到终端按 Enter 保存 Cookie。

把 `config.yml` 中的示例 `link` 换成目标博主主页，保留已保存的 Cookie，按需修改以下字段：

```yaml
link:
  - https://www.douyin.com/user/YOUR_SEC_UID
path: ./Downloaded/
mode: [post]
number:
  post: 10                 # 0 表示不限数量
increase:
  post: true              # 跳过已下载作品
redownload_missing_files: true
```

#### 3. 运行

```bash
python run.py -c config.yml
```

全部参数见 `python run.py --help`。

| 参数 | 用途 |
|:---|:---|
| `-c, --config` | 配置文件 |
| `-u, --url` | 追加链接，不会替换配置中的链接；可重复使用 |
| `-p, --path` | 下载目录 |
| `-t, --thread` | 并发下载数 |
| `-v, --verbose` | 详细日志 |

| 退出码 | 含义 |
|:---|:---|
| `0` | 运行结束；部分作品仍可能失败，以结束时的汇总为准 |
| `1` | 一个都没有下载或跳过且至少有一个失败，或发生致命错误 |
| `3` | 登录态失效且没能重新登录；同批其余链接仍会处理完 |

### 常用配置

完整配置与示例见 **[config.example.yml](config.example.yml)**。

| 配置 | 用途 |
|:---|:---|
| `number.post` | 下载数量；`0` 表示不限 |
| `start_time` / `end_time` | 日期范围（`YYYY-MM-DD`），包含结束日期当天 |
| `video_quality` | 默认 `highest`；`original` 尝试原片，失败时回退 |
| `increase.post` | `true` 跳过已下载作品；`false` 重下并覆盖当前筛选范围内的文件 |
| `redownload_missing_files` | 默认 `true`：文件缺失时补下；设为 `false` 后，有有效数据库记录的作品仍会跳过 |

增量下载会检查磁盘上的非空主媒体文件。需要重新下载时，将 `increase.post` 设为 `false`，**无需清空数据库**。`like`、`mix`、`music` 也有对应开关。

<details>
<summary>更多命令</summary>

```bash
# 热搜榜、关键词搜索 → JSONL
python run.py --hot-board 30
python run.py --search "猫咪" --search-max 50

# 可选 REST API 服务
python -m pip install fastapi uvicorn
python run.py --serve --serve-port 8000
```

评论采集、视频转写、完成通知和直播录制均在 [config.example.yml](config.example.yml) 中配置。

</details>

### 常见问题

**Cookie 失效？** 重新运行 `python -m tools.cookie_fetcher --config config.yml`。

**主页只获取到少量作品？** 保持 `browser_fallback.enabled: true`、`headless: false`，在浏览器中自行完成验证；当前风控仍可能阻止下载。

**需要查看日志？** 运行 `python run.py -c config.yml -v`。

## 开发

```bash
python -m pip install -e ".[dev]"
python -m pytest tests/
ruff check .
```

## 社区与许可

[QQ 交流群](https://qm.qq.com/q/8wrCzYyLHa) · [LINUX DO](https://linux.do/) · [MIT 许可证](LICENSE)

<img src="img/qq-group.png" alt="交流群二维码" width="200" />

仅用于学习和个人数据管理。请遵守版权、隐私与平台规则，并自行承担使用责任；平台变化可能影响功能可用性。

## Star History

[查看 GitHub Star History](https://www.star-history.com/?repos=jiji262%2Fdouyin-downloader&type=date&legend=top-left)
