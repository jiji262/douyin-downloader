# 项目实现总结（dy-downloader）

## 1. 项目概览

- **项目名称**: Douyin Downloader (`dy-downloader`)
- **版本**: `2.0.0`
- **更新时间**: `2026-02-18`
- **当前状态**: ✅ 核心功能可用，自动化测试通过


## 2. 当前实现能力（按代码现状）

### 2.1 已支持

- 单个视频下载（`/video/{aweme_id}`）
- 单个图文下载（`/note/{note_id}`）
- 抖音短链下载（`https://v.douyin.com/...`，会先解析后下载）
- 用户主页发布作品批量下载（`/user/{sec_uid}` + `mode: [post]`）
- 无水印优先下载，支持封面/音乐/头像/原始 JSON
- 并发下载、重试、速率限制
- 基于作品发布时间（`create_time`）生成文件名/目录日期前缀（`YYYY-MM-DD_...`）
- 生成独立下载清单文件 `download_manifest.jsonl`
- 时间过滤（`start_time` / `end_time`）
- 数量限制（当前对 `number.post` 生效）
- SQLite 去重与增量下载（当前对 `increase.post` 生效）
- 翻页受限时的浏览器兜底（采集 `aweme_id` 并补全详情）

### 2.2 暂未支持（配置项预留）

- 用户点赞下载（`mode: [like]`）
- 合集下载（`mode: [mix]` / `collection`）
- `number.like` / `number.mix` / `increase.like` / `increase.mix` 等预留字段


## 3. 架构与模块

```text
dy-downloader/
├── cli/               # CLI 入口与展示
├── core/              # 下载主流程、URL解析、API客户端
├── storage/           # 文件、元数据、数据库
├── auth/              # Cookie / token 管理
├── control/           # 限速、重试、并发队列
├── config/            # 配置加载与默认配置
└── utils/             # 日志与通用工具
```


## 4. 下载数据落盘策略

### 4.1 文件系统（主数据）

默认目录结构（`folderstyle: true`）：

```text
Downloaded/
├── download_manifest.jsonl
└── 作者名/
    └── post/
        └── 2024-02-07_作品标题_aweme_id/
            ├── 2024-02-07_作品标题_aweme_id.mp4
            ├── 2024-02-07_作品标题_aweme_id_cover.jpg
            ├── 2024-02-07_作品标题_aweme_id_music.mp3
            ├── 2024-02-07_作品标题_aweme_id_avatar.jpg
            └── 2024-02-07_作品标题_aweme_id_data.json
```

命名日期优先使用作品发布时间 `create_time`；若缺失或非法，会回退到当前日期并记录告警。

### 4.2 独立下载清单（新增）

- 文件：`{path}/download_manifest.jsonl`
- 形式：每行一条 JSON（append-only）
- 典型字段：
  - `date`（作品发布日期）
  - `aweme_id`
  - `author_name`
  - `desc`
  - `media_type`
  - `tags`（来自 `text_extra`、`cha_list`、`desc` 中 `#`）
  - `file_names`
  - `file_paths`
  - `publish_timestamp`（若可解析）
  - `recorded_at`（写入时间）

### 4.3 SQLite 数据库（可开关）

- 默认开关：`database: true`
- 默认库文件：`dy_downloader.db`
- 表结构：
  - `aweme`：作品明细、作者、发布时间、下载时间、保存路径、原始 metadata
  - `download_history`：每次任务 URL、类型、总数、成功数、配置快照

> 当 `database: false` 时，不写 SQLite，但**仍会写**媒体文件和 `download_manifest.jsonl`。


## 5. 关键流程（简版）

1. 读取配置（命令行 > 环境变量 > 配置文件 > 默认配置）
2. 初始化 Cookie 与 API 客户端
3. 解析链接类型（视频 / 图文 / 用户）
4. 拉取作品数据并应用时间/数量/增量规则
5. 并发下载媒体文件
6. 写入可选 JSON 元数据
7. 追加写入 `download_manifest.jsonl`
8. 若开启数据库，写入 `aweme` 与 `download_history`


## 6. 近期更新（2026-02-18）

- ✅ 文件名和目录日期从“下载时间”改为“作品发布时间（`create_time`）”
- ✅ 新增独立下载清单 `download_manifest.jsonl`
- ✅ 清单中补充 `date/file_names/tags` 等可追溯字段
- ✅ 增加对应测试，确保发布时间命名与清单写入行为


## 7. 测试与验证

执行命令：

```bash
PYTHONPATH=. pytest -q
```

结果：

```text
32 passed
```

说明：当前有 `pytest-asyncio` 的 deprecation warning（事件循环 scope 配置），不影响功能正确性。


## 8. 后续建议

1. 增加 `mode: like/mix` 的实际下载实现，打通预留配置。
2. 为 `download_manifest.jsonl` 增加轮转或归档策略（长期运行场景）。
3. 补充数据库查询 CLI（例如按作者/日期/标签检索）。
