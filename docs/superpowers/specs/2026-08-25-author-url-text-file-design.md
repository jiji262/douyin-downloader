# 博主主页地址独立文件设计

## 目标

执行可解析到单一博主的抖音主页下载任务时，在该博主下载根目录写入
`author_url.txt`。文件与可选的 `主页截图.png` 位于同一层，用于在脱离
`download_manifest.jsonl` 时仍能直接追溯下载来源。

## 方案选择

采用“用户主页任务入口一次性写入”方案：`UserDownloader` 获取用户资料后，
复用截图所使用的作者目录解析逻辑，写入一个固定文件。

未采用以下方案：

- 绑定截图保存流程：会让 URL 是否留存错误地依赖 `homepage_screenshot` 开关。
- 在逐作品下载完成后写入：同一博主会重复覆盖文件，且没有作品时无法留存主页来源。

## 行为定义

1. 文件名固定为 `author_url.txt`，编码为 UTF-8。
2. 内容仅包含规范化主页地址和一个末尾换行，例如：
   `https://www.douyin.com/user/MS4w...\n`。
3. 每次主页任务都覆盖写入，以当前解析出的 `sec_uid` 为准。
4. 写入不依赖 `homepage_screenshot`；截图关闭或截图失败时仍写入 TXT。
5. 作者目录继续由 `FileManager.get_author_dir()` 按 `author_dir` 配置计算，
   因此 TXT 与 `主页截图.png` 必定使用同一父目录。
6. 仅处理能对应单一目标博主的主页任务。`/user/self` 无法解析为真实
   `sec_uid` 或仅执行 `collect` / `collectmix` 时不创建文件。
7. 无法构造主页 URL 或文件写入失败时记录 warning，但不使下载任务失败。

## 数据流

`UserDownloader.download()` 在用户资料解析成功后确定有效 `sec_uid`，调用独立的
主页地址保存方法。该方法构造规范化 URL、解析作者目录并异步覆盖
`author_url.txt`，随后现有截图和各下载模式流程照常执行。

## 实现边界

- 共享实现同步到 `douyin-downloader` 与 `douyin-downloader-desktop`。
- 不新增配置项或桌面 UI。
- 不改变 `download_manifest.jsonl`、SQLite schema、截图开关或历史数据。
- 不为既有下载目录自动回填；再次运行对应博主主页任务时自然生成。

## 验证

- 截图关闭时仍在配置后的作者根目录写入正确 URL。
- 截图开启时 TXT 与 `主页截图.png` 的父目录一致。
- 不同 `author_dir` 风格下路径正确。
- collect-only 上下文不创建错误的单博主文件。
- URL 写入失败只记录 warning，下载结果保持不变。
- 两个仓库运行相关 pytest、ruff 与共享文件同步检查。
