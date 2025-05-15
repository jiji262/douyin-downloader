# 项目术语表 (Douyin Downloader)

## 1. DownloadConfig
**定义**：核心配置类，控制下载器的全局行为。  
**字段说明**：
```python
link: List[str]          # 待下载的抖音分享链接列表
path: Path               # 文件保存路径
music: bool              # 是否下载视频原声（默认True）
mode: List[str]          # 下载模式：post(发布)/like(喜欢)/mix(合集)
thread: int              # 下载线程数（默认5）
number: Dict[str, int]   # 各模式下载数量限制（0表示无限制）
```

## 2. CLI 参数
**定义**：命令行接口的配置参数，优先级高于配置文件。  
**关键参数**：
```bash
--link "https://v.douyin.com/xxx"  # 指定下载链接
--mode post like                   # 同时下载用户发布和喜欢的内容
--postnumber 20                   # 限制下载20个发布作品
--thread 10                       # 使用10个下载线程
```

## 3. YAML 配置
**定义**：通过`config.yml`文件持久化配置。  
**示例结构**：
```yaml
path: "/downloads"
music: false
mode: ["post"]
number:
  post: 50
  mix: 10
cookies:  # 登录凭证
  sid_tt: "xxxxxx"
```

## 4. 增量下载 (Increase)
**定义**：只下载新增内容，避免重复下载的机制。  
**实现逻辑**：
- 通过`increase.post`等布尔字段开关
- 依赖数据库记录已下载内容（需`database=true`）
- 根据作品发布时间过滤（需设置`start_time/end_time`）

## 5. 下载模式 (Mode)
**类型**：  
| 模式    | 目标内容                  | 对应API           |
|---------|--------------------------|-------------------|
| `post`  | 用户发布的视频/图集       | `getUserInfo()`   |
| `like`  | 用户点赞的作品            | `getUserInfo()`   |
| `mix`   | 用户创建的合集            | `getMixInfo()`    |
| `music` | 特定原声下的作品          | `getMusicInfo()`  |
```

---