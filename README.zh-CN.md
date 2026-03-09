# 抖音下载器 V2.0（Douyin Downloader）

<p align="center">
  <img src="https://socialify.git.ci/jiji262/douyin-downloader/image?custom_description=%E6%8A%96%E9%9F%B3%E6%89%B9%E9%87%8F%E4%B8%8B%E8%BD%BD%E5%B7%A5%E5%85%B7%EF%BC%8C%E5%8E%BB%E6%B0%B4%E5%8D%B0%EF%BC%8C%E6%94%AF%E6%8C%81%E8%A7%86%E9%A2%91%E3%80%81%E5%9B%BE%E9%9B%86%E3%80%81%E4%BD%9C%E8%80%85%E4%B8%BB%E9%A1%B5%E6%89%B9%E9%87%8F%E4%B8%8B%E8%BD%BD%E3%80%82&description=1&font=Jost&forks=1&logo=https%3A%2F%2Fraw.githubusercontent.com%2Fjiji262%2Fdouyin-downloader%2Frefs%2Fheads%2FV1.0%2Fimg%2Flogo.png&name=1&owner=1&pattern=Circuit+Board&pulls=1&stargazers=1&theme=Light" alt="douyin-downloader" width="820" />
</p>

一个面向实用场景的抖音下载工具，支持视频、图文、合集、音乐、收藏夹等多种类型下载，以及作者主页批量下载，默认带进度展示、重试、数据库去重、下载完整性校验和浏览器兜底能力。

> 当前文档对应 **V2.0（main 分支）**。  
> 如需使用旧版，请切回 **V1.0**：`git fetch --all && git switch V1.0`

## 功能概览

### 已支持

| 功能 | 说明 |
|------|------|
| 单个视频下载 | `/video/{aweme_id}` |
| 单个图文下载 | `/note/{note_id}`、`/gallery/{note_id}` |
| 单个合集下载 | `/collection/{mix_id}`、`/mix/{mix_id}` |
| 单个音乐下载 | `/music/{music_id}`（优先原声文件，缺失时回退到该音乐下首条作品） |
| 短链自动解析 | `https://v.douyin.com/...` |
| 用户主页批量下载 | `/user/{sec_uid}` + `mode: [post, like, mix, music]` |
| 当前登录账号收藏夹下载 | `/user/self?showTab=favorite_collection` + `mode: [collect, collectmix]` |
| 无水印优先 | 自动选择无水印视频源 |
| 附加资源下载 | 封面、音乐、头像、JSON 元数据 |
| 视频转写 | 可选功能，调用 OpenAI Transcriptions API |
| 并发下载 | 可配置并发数，默认 5 |
| 失败重试 | 指数退避重试（1s, 2s, 5s） |
| 速率限制 | 默认 2 请求/秒 |
| SQLite 去重 | 数据库 + 本地文件双重去重 |
| 增量下载 | `increase.post/like/mix/music` |
| 时间过滤 | `start_time` / `end_time` |
| 浏览器兜底 | 翻页受限时启动浏览器，支持人工过验证码 |
| 下载完整性校验 | Content-Length 比对，不完整文件自动清理并重试 |
| 进度条展示 | Rich 进度条，支持 `progress.quiet_logs` 静默模式 |
| Docker 部署 | 提供 Dockerfile |
| CI/CD | GitHub Actions 自动测试和 lint |

### 限制说明

- 浏览器兜底当前仅针对 `post` 完整验证，`like/mix/music` 主要依赖 API 正常分页
- `number.allmix` / `increase.allmix` 作为兼容别名保留，运行时会归一化到 `mix`
- `collect` / `collectmix` 当前仅支持当前已登录 Cookie 对应账号
- `collect` / `collectmix` 必须单独使用，不能和 `post` / `like` / `mix` / `music` 混用
- `increase` 当前仅支持 `post` / `like` / `mix` / `music`；收藏夹模式不支持增量截断

## 快速开始

### 1) 环境准备

- Python 3.8+
- macOS / Linux / Windows

### 2) 安装依赖

```bash
pip install -r requirements.txt
```

如需浏览器兜底或自动获取 Cookie：

```bash
pip install playwright
python -m playwright install chromium
```

### 3) 复制配置

```bash
cp config.example.yml config.yml
```

### 4) 获取 Cookie（推荐自动方式）

```bash
python -m tools.cookie_fetcher --config config.yml
```

登录抖音后回到终端按 Enter，程序会自动写入配置。

### 5) Docker 部署（可选）

```bash
docker build -t douyin-downloader .
docker run -v $(pwd)/config.yml:/app/config.yml -v $(pwd)/Downloaded:/app/Downloaded douyin-downloader
```

## 最小可用配置

```yaml
link:
  - https://www.douyin.com/user/MS4wLjABAAAAxxxx

path: ./Downloaded/
mode:
  - post

number:
  post: 0
  collect: 0
  collectmix: 0

thread: 5
retry_times: 3
proxy: ""
database: true
database_path: dy_downloader.db

progress:
  quiet_logs: true

cookies:
  msToken: ""
  ttwid: YOUR_TTWID
  odin_tt: YOUR_ODIN_TT
  passport_csrf_token: YOUR_CSRF_TOKEN
  sid_guard: ""

browser_fallback:
  enabled: true
  headless: false
  max_scrolls: 240
  idle_rounds: 8
  wait_timeout_seconds: 600

transcript:
  enabled: false
  model: gpt-4o-mini-transcribe
  output_dir: ""
  response_formats: ["txt", "json"]
  api_url: https://api.openai.com/v1/audio/transcriptions
  api_key_env: OPENAI_API_KEY
  api_key: ""
```

## 使用方式

### 使用配置文件运行

```bash
python run.py -c config.yml
```

### 命令行追加参数

```bash
python run.py -c config.yml \
  -u "https://www.douyin.com/video/7604129988555574538" \
  -t 8 \
  -p ./Downloaded
```

### 参数说明

| 参数 | 说明 |
|------|------|
| `-u, --url` | 追加下载链接（可重复传入） |
| `-c, --config` | 指定配置文件（默认 `config.yml`） |
| `-p, --path` | 指定下载目录 |
| `-t, --thread` | 指定并发数 |
| `--show-warnings` | 显示 warning/error 日志 |
| `-v, --verbose` | 显示 info/warning/error 日志 |
| `--version` | 显示版本号 |

## 典型场景

### 下载单个视频

```yaml
link:
  - https://www.douyin.com/video/7604129988555574538
```

### 下载单个图文

```yaml
link:
  - https://www.douyin.com/note/7341234567890123456
```

### 下载单个合集

```yaml
link:
  - https://www.douyin.com/collection/7341234567890123456
```

### 下载单个音乐

```yaml
link:
  - https://www.douyin.com/music/7341234567890123456
```

### 批量下载作者主页作品

```yaml
link:
  - https://www.douyin.com/user/MS4wLjABAAAAxxxx
mode:
  - post
number:
  post: 50
```

### 批量下载作者点赞作品

```yaml
link:
  - https://www.douyin.com/user/MS4wLjABAAAAxxxx
mode:
  - like
number:
  like: 0    # 0 表示全量下载
```

### 同时下载多种模式

```yaml
link:
  - https://www.douyin.com/user/MS4wLjABAAAAxxxx
mode:
  - post
  - like
  - mix
  - music
```

跨模式自动去重：同一个 aweme_id 在不同模式下不会重复下载。

### 批量下载当前登录账号收藏夹作品

```yaml
link:
  - https://www.douyin.com/user/self?showTab=favorite_collection
mode:
  - collect
number:
  collect: 0
```

### 批量下载当前登录账号收藏合集

```yaml
link:
  - https://www.douyin.com/user/self?showTab=favorite_collection
mode:
  - collectmix
number:
  collectmix: 0
```

### 增量下载（只下载新作品）

```yaml
increase:
  post: true
database: true    # 增量模式依赖数据库记录
```

### 全量抓取（不限制数量）

```yaml
number:
  post: 0
```

## 可选功能：视频转写（transcript）

当前实现仅对**视频作品**生效（图文不会生成转写）。

### 1) 开启方式

```yaml
transcript:
  enabled: true
  model: gpt-4o-mini-transcribe
  output_dir: ""        # 留空: 与视频同目录；非空: 镜像到指定目录
  response_formats:
    - txt
    - json
  api_key_env: OPENAI_API_KEY
  api_key: ""           # 可直接填，或使用环境变量
```

推荐通过环境变量提供密钥：

```bash
export OPENAI_API_KEY="sk-xxxx"
```

### 2) 输出文件

启用后会生成：

- `xxx.transcript.txt`
- `xxx.transcript.json`

若 `database: true`，会在数据库 `transcript_job` 表记录状态（`success/failed/skipped`）。

## 测试

推荐使用：

```bash
python3 -m pytest -q
```

当前也支持直接运行：

```bash
pytest -q
```

## 关键配置项

| 配置项 | 说明 |
|--------|------|
| `mode` | 支持 `post`/`like`/`mix`/`music`；当前登录收藏夹模式额外支持单独使用的 `collect`/`collectmix` |
| `number.post/like/mix/music/collect/collectmix` | 各模式下载数量限制，0 为不限 |
| `increase.post/like/mix/music` | 各模式增量开关 |
| `start_time` / `end_time` | 时间过滤（格式 `YYYY-MM-DD`） |
| `folderstyle` | 按作品维度创建子目录 |
| `browser_fallback.*` | `post` 翻页受限时启用浏览器兜底 |
| `progress.quiet_logs` | 进度阶段静默日志，减少刷屏 |
| `transcript.*` | 视频下载后的可选转写 |
| `proxy` | 为 API 请求和媒体下载设置 HTTP/HTTPS 代理，例如 `http://127.0.0.1:7890` |
| `database` | 启用 SQLite 去重和历史记录 |
| `database_path` | SQLite 文件路径，默认在当前工作目录生成 `dy_downloader.db` |
| `thread` | 并发下载数 |
| `retry_times` | 失败重试次数 |

## 输出目录

默认 `folderstyle: true` 且 `database_path: dy_downloader.db` 时：

```text
工作目录/
├── config.yml
├── dy_downloader.db          # database: true 时默认生成在这里
└── Downloaded/
    ├── download_manifest.jsonl
    └── 作者名/
        ├── post/
        │   └── 2024-02-07_作品标题_aweme_id/
        │       ├── ...mp4
        │       ├── ..._cover.jpg
        │       ├── ..._music.mp3
        │       ├── ..._data.json
        │       ├── ..._avatar.jpg
        │       ├── ...transcript.txt
        │       └── ...transcript.json
        ├── like/
        │   └── ...
        ├── mix/
        │   └── ...
        ├── music/
        │   └── ...
        ├── collect/
        │   └── ...
        └── collectmix/
            └── ...
```

## 重新下载

程序通过**数据库记录 + 本地文件**双重检查判断是否跳过已下载内容。要重新下载，需要按以下方式清理数据：

### 重新下载特定作品

```bash
# 删除本地文件（文件名中包含 aweme_id）
rm -rf Downloaded/作者名/post/*_<aweme_id>/

# 删除数据库记录
sqlite3 dy_downloader.db "DELETE FROM aweme WHERE aweme_id = '<aweme_id>';"
```

### 重新下载某个作者的全部作品

```bash
rm -rf Downloaded/作者名/
sqlite3 dy_downloader.db "DELETE FROM aweme WHERE author_name = '作者名';"
```

### 全部从零重新下载

```bash
rm -rf Downloaded/
rm dy_downloader.db
```

> **注意：** 只删数据库不删文件不会触发重新下载——程序会扫描本地文件名中的 aweme_id 进行去重。只删文件不删数据库会触发重新下载（数据库中有记录但文件不存在时视为需要重新下载）。

## 常见问题

### 1) 只能抓到 20 条作品怎么办？

这是翻页风控的常见现象。确保：

- `browser_fallback.enabled: true`
- `browser_fallback.headless: false`
- 浏览器弹窗出现后手动完成验证，不要立即关闭窗口

### 2) 进度条出现重复刷屏怎么办？

默认 `progress.quiet_logs: true` 会在进度阶段静默日志。  
调试时再临时加 `--show-warnings` 或 `-v`。

### 3) Cookie 失效怎么办？

重新执行：

```bash
python -m tools.cookie_fetcher --config config.yml
```

### 4) 为什么没有生成 transcript 文件？

请依次检查：

- `transcript.enabled` 是否为 `true`
- 是否下载的是视频（图文不转写）
- `OPENAI_API_KEY`（或 `transcript.api_key`）是否有效
- `response_formats` 是否包含 `txt` 或 `json`

### 5) 如何查看下载历史？

```bash
sqlite3 dy_downloader.db "SELECT aweme_id, title, author_name, datetime(download_time, 'unixepoch', 'localtime') FROM aweme ORDER BY download_time DESC LIMIT 20;"
```

## 旧版切换（V1.0）

如果你要继续使用老脚本风格（V1.0），可切换到旧分支：

```bash
git fetch --all
git switch V1.0
```

## 沟通群

<img src="./img/fuye.jpg" alt="qun" width="360" />

## 免责声明

本项目仅用于技术研究、学习交流与个人数据管理。请在合法合规前提下使用：

- 不得用于侵犯他人隐私、版权或其他合法权益
- 不得用于任何违法违规用途
- 使用者应自行承担因使用本项目产生的全部风险与责任
- 如平台规则、接口策略变更导致功能失效，属于正常技术风险

如果你继续使用本项目，即视为已阅读并同意上述声明。

## 许可证

本项目采用 MIT License，详见 [LICENSE](./LICENSE)。
