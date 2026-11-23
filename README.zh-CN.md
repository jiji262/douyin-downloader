# 抖音下载器（Douyin Downloader）

<p align="center">
  <img src="https://socialify.git.ci/jiji262/douyin-downloader/image?custom_description=Free+Douyin+video+downloader+with+CLI+and+AI+Agent+Skill%3A+batch+download%2C+hot+board%2C+search%2C+live+recording%2C+transcription.&description=1&font=Source+Code+Pro&forks=1&owner=1&pattern=Circuit+Board&stargazers=1&theme=Light" alt="douyin-downloader" width="820" />
</p>

**简体中文** · [English](README.md)

**抖音下载器（Douyin Downloader）** 是一款免费、开源的抖音（Douyin）音视频内容下载与数据提取工具。本项目同时提供 **CLI 命令行版本** 与 **AI Agent Skill 版本** 两种形态，满足不同场景下的需求：

- 🚀 **CLI 命令行版（免费批量下载）**：专注**免费批量下载抖音无水印视频与图文**。支持博主主页发布作品批量下载、单个/批量作品下载、合集/喜欢/音乐下载；内置增量下载（自动跳过已下载文件）、发布时间范围筛选、下载历史记录管理、失败自动重试、Whisper 本地音视频字幕转写及直播流录制等丰富功能。
- 🤖 **AI Agent Skill 版（智能对话交互）**：专为 Claude Code、Codex、Cursor、OpenCode 等 AI Agent 打造。直接在对话中使用日常自然语言，即可让 Agent 查询抖音实时热搜榜、全网搜索作品、查询博主公开资料、录制直播间与转写音视频，无缝融入大模型自动化工作流。

<a id="douzy-桌面版"></a>

## 赞助商

<table>
  <tr>
    <th>赞助商</th>
    <th align="left">简介</th>
  </tr>
  <tr>
    <td align="center" width="96"><a href="https://godouzy.com/"><img src="https://godouzy.com/douzy-mark.svg" alt="Douzy" width="64" /></a></td>
    <td><b><a href="https://godouzy.com/">Douzy 桌面版</a></b><br>把抖音、TikTok、YouTube、Telegram 和 X 的内容保存到电脑。在同一个窗口里粘贴链接、查看任务进度、翻找已保存的作品档案。<br>各平台按需启用插件或本机组件，部分批量和高级功能需要激活。<br><b><a href="https://godouzy.com/#get">下载 Windows / macOS 版</a></b> · <a href="https://github.com/0X-42/douzy">GitHub 项目</a> · <a href="https://github.com/0X-42/douzy/releases/latest">最新版与发布说明</a></td>
  </tr>
</table>

<table>
  <tr>
    <td width="33.3%" align="center">
      <img src="https://godouzy.com/shots/douyin.webp" alt="Douzy 抖音页，亮色主题" width="100%" /><br />
      <sub>抖音页（亮色）</sub>
    </td>
    <td width="33.3%" align="center">
      <img src="https://godouzy.com/shots/douyin-dark.webp" alt="Douzy 抖音页，暗色主题" width="100%" /><br />
      <sub>抖音页（暗色）</sub>
    </td>
    <td width="33.3%" align="center">
      <img src="https://godouzy.com/shots/tasks.webp" alt="Douzy 任务中心，亮色主题" width="100%" /><br />
      <sub>任务中心（亮色）</sub>
    </td>
  </tr>
</table>

**[成为赞助商](https://github.com/jiji262/douyin-downloader/issues/new?template=sponsor.yml)**

---

## 版本概览

| 功能特性 | 🚀 CLI 命令行版 | 🤖 AI Agent Skill 版 |
|:---|:---:|:---:|
| **定位场景** | 开发者 / 批量归档 / 定时脚本 | AI Agent 自然语言对话 / 工作流自动化 |
| **抖音视频/图文批量下载** | ✅ **免费支持**（主页/合集/单作品） | ❌ 仅元数据查询（下载请用 CLI） |
| **增量下载（跳过已下载）** | ✅ 支持（按日期/已下载跳过） | - |
| **抖音实时热搜榜** | ✅ `douyin-dl agent hot` | ✅ 自然语言「看看抖音热榜」 |
| **关键词搜索作品** | ✅ `douyin-dl agent search` | ✅ 自然语言「搜露营并总结」 |
| **博主主页数据查询** | ✅ `douyin-dl agent user` | ✅ 自然语言「查博主资料」 |
| **直播间录制** | ✅ 支持（FFmpeg 分段转 MP4） | ✅ 自然语言「录直播 10 分钟」 |
| **本地音视频转写字幕** | ✅ 支持（Whisper 本地模型） | ✅ 自然语言「转写本地视频」 |
| **授权与费用** | **完全免费 / 开源 (MIT)** | **完全免费 / 开源 (MIT)** |

---

<a id="cli-版本"></a>

## CLI 命令行版使用指南（免费批量下载）

CLI 命令行版本是本项目的核心下载引擎，提供开箱即用的批量下载能力、灵活的 YAML 配置文件与丰富选项。

### 1. 快速安装

需要 **Python 3.10+**，支持 Windows、macOS 和 Linux。

**推荐方式：使用 uv 或 pipx 安装**
```bash
uv tool install douyin-dl-cli    # 或: pipx install douyin-dl-cli
```

**或者通过源码克隆使用：**
```bash
git clone https://github.com/jiji262/douyin-downloader.git
cd douyin-downloader
python -m pip install -r requirements.txt
python -m pip install playwright
python -m playwright install chromium
```

### 2. 配置与登录

将 [config.example.yml](config.example.yml) 复制为 `config.yml`，并获取登录 Cookie：

```bash
cp config.example.yml config.yml
python -m tools.cookie_fetcher --config config.yml
```
*(Windows PowerShell 用户请用 `Copy-Item config.example.yml config.yml`)*

弹出的浏览器中完成抖音登录，回到终端按 Enter 即可保存 Cookie。

### 3. 运行批量下载

编辑 `config.yml`，填入目标博主主页链接或作品链接：

```yaml
link:
  - https://www.douyin.com/user/YOUR_SEC_UID
path: ./Downloaded/
mode: [post]             # post(作品), like(喜欢), mix(合集), music(音乐)
number:
  post: 10               # 0 表示不限数量
increase:
  post: true             # 增量下载：自动跳过磁盘上已有的作品
redownload_missing_files: true
```

启动批量下载：
```bash
# 源码运行
python run.py -c config.yml

# 或使用全局命令运行（指定链接）
douyin-dl -c config.yml -u "https://www.douyin.com/user/..."
```

| 参数 | 用途 |
|:---|:---|
| `-c, --config` | 指定配置文件路径 |
| `-u, --url` | 追加下载链接（可多次使用，不替换配置文件中的链接） |
| `-p, --path` | 指定下载保存目录 |
| `-t, --thread` | 并发下载线程数 |
| `-v, --verbose` | 输出详细运行日志 |

### 4. 常用配置与高级特性

完整配置与示例说明见 **[config.example.yml](config.example.yml)**。

| 配置项 | 说明 |
|:---|:---|
| `number.post` | 下载数量限制；`0` 为不限数量 |
| `start_time` / `end_time` | 按发布日期过滤（`YYYY-MM-DD`），包含结束日期当天 |
| `video_quality` | 视频画质，默认 `highest`；设为 `original` 优先尝试原片 |
| `increase.post` | 增量下载开关；`true` 跳过已下载作品，`false` 重新下载并覆盖 |
| `redownload_missing_files` | 默认 `true`：文件缺失时自动补下；设为 `false` 则数据库有记录即跳过 |

> **提示：** 增量下载会智能比对磁盘上的主媒体文件，无需清空数据库即可实现断点续传与补充下载。`like`、`mix`、`music` 同样支持对应的增量与数量设置。

<details>
<summary>更多命令行功能（热搜、搜索、REST 服务等）</summary>

```bash
# 获取热搜榜（前 30 条）并保存为 JSONL
python run.py --hot-board 30

# 关键词搜索作品并输出 JSONL
python run.py --search "猫咪" --search-max 50

# 启动可选的 REST API 服务
python -m pip install fastapi uvicorn
python run.py --serve --serve-port 8000
```

评论采集、音视频转写、完成通知和直播录制均可在 [config.example.yml](config.example.yml) 中配置开启。
</details>

---

<a id="ai-agent--skill-用法"></a>

## AI Agent Skill 使用指南

Skill 目录为 [`skills/douyin-dl/`](skills/douyin-dl/)，专门为大模型智能体提供机器可读的接口与操作规范，实际执行由底层的 `douyin-dl` 命令行驱动。

### 第 1 步：安装 CLI

```bash
uv tool install douyin-dl-cli    # 或：pipx install douyin-dl-cli
douyin-dl agent doctor
```

- 需要 Python 3.10+。PyPI 包名是 `douyin-dl-cli`，命令为 `douyin-dl`。
- `doctor` 离线体检，会列出当前环境各命令的准备情况。
- 如果需要登录态（搜索/查博主）或音视频转写，可按需安装可选依赖：
  - 浏览器依赖（登录）：`uv tool install --force "douyin-dl-cli[browser]"`
  - 转写依赖（Whisper）：`uv tool install --force "douyin-dl-cli[transcribe]"`

### 第 2 步：安装 Skill

**Claude Code**：在会话中直接执行：
```text
/plugin marketplace add jiji262/douyin-downloader
/plugin install douyin-dl@douyin-dl
```

**Codex**：将 skill 目录复制到用户 skills 目录：
```bash
git clone https://github.com/jiji262/douyin-downloader.git
mkdir -p ~/.agents/skills
cp -r douyin-downloader/skills/douyin-dl ~/.agents/skills/
```

**其他 Agent（Cursor、OpenCode 等）**：使用 [skills CLI](https://github.com/vercel-labs/skills)：
```bash
npx skills add jiji262/douyin-downloader       # 当前项目可用
npx skills add jiji262/douyin-downloader -g    # 全局可用
```

### 第 3 步：直接用自然语言对 Agent 提问

安装完成后，在对话中提及抖音或输入抖音链接，Agent 即可自动调用 Skill 执行：

| 你的自然语言输入 | Agent 调用的底层命令 | 说明 |
|:---|:---|:---|
| 「看看抖音热榜前 10。」 | `douyin-dl agent hot --max 10` | 获取热搜榜单 |
| 「在抖音搜 `露营`，按点赞排序，总结前 10 条。」 | `douyin-dl agent search 露营 --sort likes --max 10` | 关键词搜索（需登录） |
| 「看一下这个博主的资料：`https://www.douyin.com/user/…`」 | `douyin-dl agent user <链接>` | 获取博主主页数据（需 Cookie） |
| 「这个链接是什么？能解析吗？」 | `douyin-dl agent inspect <链接>` | 链接类型识别 |
| 「把这个直播间录 10 分钟：`https://live.douyin.com/…`」 | `douyin-dl agent live <链接> --max-duration 600` | 直播流录制 |
| 「把 `./clip.mp4` 转成文字。」 | `douyin-dl agent transcribe ./clip.mp4` | 本地 Whisper 字幕转写 |

> **关于下载能力的说明：**
> Skill 模式设计为轻量、安全的元数据查询通道（防止在对话中拉取大体积媒体）。需要批量下载音视频或图文文件时，请直接使用上方的 **[CLI 命令行版本](#cli-命令行版使用指南免费批量下载)**。

### 不经 Agent 直接运行（命令行 JSON 输出）

各子命令也可以在普通终端中直接运行，每条命令在 stdout 输出一行标准 JSON：

```bash
douyin-dl agent hot --max 3
# {"schema":1,"ok":true,"verb":"hot","version":"2.1.1","data":{"items":[...],"count":3},"error":null,"warnings":[]}
```

---

## 常见问题

| 现象 | 处理方式 |
|:---|:---|
| `douyin-dl: command not found` | 运行 `uv tool update-shell`（或 `pipx ensurepath`）后打开新终端窗口。 |
| Cookie 失效 / 要求登录 | 运行 `python -m tools.cookie_fetcher --config config.yml` 或 `douyin-dl agent login` 重新扫码登录。 |
| 结果为空（`UPSTREAM_EMPTY`） | 触发了抖音限流或风控。请稍候几分钟再试；境外代理易被拦截，建议使用国内直连。 |
| 主页只获取到少量作品 | 检查配置中 `browser_fallback.enabled: true`，在弹出的浏览器中手动完成滑动验证。 |
| Windows 运行提示 | PowerShell 或 cmd 中均可直接使用 `douyin-dl`；`scripts/dy.sh` 需要 Git Bash 或 WSL。 |

更多问题请参阅[故障排查指南](skills/douyin-dl/references/troubleshooting.md)。

---

## 开发

```bash
python -m pip install -e ".[dev]"
python -m pytest tests/
ruff check .
```

## 社区与许可

[QQ 交流群](https://qm.qq.com/q/8wrCzYyLHa) · [LINUX DO](https://linux.do/) · [MIT 许可证](LICENSE)

<img src="img/qq-group.png" alt="交流群二维码" width="200" />

仅用于学习和个人数据管理。请遵守版权、隐私与平台规则，并自行承担使用责任；平台接口变更可能影响部分功能可用性。

## Star History

[查看 GitHub Star History](https://www.star-history.com/?repos=jiji262%2Fdouyin-downloader&type=date&legend=top-left)
