# 抖音下载器 Web 模块

基于 Flask 的抖音下载器 Web 管理界面，提供视频下载、主页监控、Cookie 验证等功能。

## 🚀 快速开始

### 1. 安装依赖

```bash
pip install -r web/requirements.txt
```

### 2. 启动服务

```bash
python run_web.py
```

或者指定端口：

```bash
python run_web.py --port 9000
```

### 3. 访问界面

打开浏览器访问：http://localhost:8886

**默认账号:**
- 用户名：`admin`
- 密码：`qq2669035538`

⚠️ **注意：** 登录失败 3 次后需要重启服务器才能继续登录

---

## 📋 功能特性

### 1. 视频模式下载
- 粘贴包含抖音链接的文本（支持批量）
- 自动解析所有视频链接
- 实时显示下载进度
- 支持三种下载模式：急速、平衡、稳定

### 2. 主页模式下载
- 添加抖音主页链接到监控库
- 后台定时扫描（默认 30 分钟）
- 逐个扫描避免触发风控
- 实时显示扫描进度
- 支持启动/停止扫描任务

### 3. 下载记录与统计
- 查看所有下载历史记录
- 统计每个主页的视频数量
- 检测主页有效性（私密/封禁）
- 自动移除失效主页

### 4. Cookie 验证
- 集成 noVNC + Xvfb + Chromium
- 在 Web 界面内嵌浏览器进行登录
- 实时显示 Cookie 有效性
- 一键重新获取认证

### 5. 系统设置
- 自定义下载目录
- 配置扫描间隔时间
- 选择默认下载模式
- 查看错误日志

---

## 🛠️ 技术架构

### 后端
- **框架**: Flask 2.3
- **认证**: Flask-Login
- **实时通信**: Flask-SocketIO
- **数据库**: SQLite

### 前端
- **样式**: 原生 CSS (现代渐变设计)
- **交互**: 原生 JavaScript (ES6+)
- **布局**: 响应式设计

### 核心服务
- **VNC 服务**: noVNC + Xvfb + Chromium
- **任务管理**: 多线程异步任务
- **下载适配**: 桥接原有 CLI 下载功能

---

## 📁 目录结构

```
web/
├── __init__.py           # 模块初始化
├── app.py                # Flask 应用入口
├── requirements.txt      # Python 依赖
├── services/             # 核心服务
│   ├── __init__.py
│   ├── task_manager.py   # 任务管理器
│   ├── vnc_manager.py    # VNC 服务管理
│   ├── homepage_scanner.py # 主页扫描器
│   └── downloader_adapter.py # 下载适配器
├── routes/               # API 路由
│   ├── __init__.py
│   ├── auth_routes.py    # 认证相关
│   ├── download_routes.py # 下载相关
│   ├── settings_routes.py # 设置相关
│   └── logs_routes.py    # 日志相关
├── utils/                # 工具类
│   ├── __init__.py
│   ├── config.py         # 配置管理
│   ├── database.py       # 数据库操作
│   ├── homepage_manager.py # 主页链接管理
│   └── url_parser.py     # URL 解析
├── static/               # 静态资源
│   ├── css/
│   │   └── style.css     # 样式文件
│   └── js/
│       └── app.js        # 前端交互
└── templates/            # HTML 模板
    └── index.html        # 主页面
```

---

## 🔌 API 接口

### 认证接口
- `POST /api/auth/login` - 用户登录
- `POST /api/auth/logout` - 用户登出
- `GET /api/auth/status` - 认证状态

### 下载接口
- `POST /api/download/video` - 视频模式下载
- `POST /api/download/homepage/add` - 添加主页
- `POST /api/download/homepage/remove` - 移除主页
- `GET /api/download/homepage/list` - 主页列表
- `POST /api/download/homepage/start` - 启动扫描
- `POST /api/download/homepage/stop` - 停止扫描
- `GET /api/download/task/<task_id>` - 任务进度

### 设置接口
- `GET /api/settings/config` - 获取配置
- `PUT /api/settings/config` - 更新配置
- `GET /api/settings/stats` - 统计信息
- `POST /api/settings/vnc/start` - 启动 VNC
- `POST /api/settings/vnc/stop` - 停止 VNC
- `GET /api/settings/vnc/status` - VNC 状态

### 日志接口
- `GET /api/logs/errors` - 错误日志
- `GET /api/logs/downloads` - 下载记录
- `GET /api/logs/homepages` - 主页统计

---

## ⚙️ 配置说明

配置文件 `web_config.json` 会自动生成，包含以下选项：

```json
{
  "download_base_dir": "./downloads",
  "scan_interval_minutes": 30,
  "download_mode": "balance",
  "max_error_logs": 500,
  "vnc_port": 6080,
  "vnc_display": 1
}
```

---

## 🔐 安全说明

1. **登录保护**: 仅支持单账号 (admin)，失败 3 次锁定
2. **会话管理**: 使用 Flask Session 进行认证
3. **CORS**: 已配置跨域访问限制

---

## 🐛 故障排除

### VNC 无法启动
确保已安装以下依赖：
```bash
apt-get install xvfb chromium-browser websockify
```

### Cookie 无效
1. 在"系统设置"中启动 VNC
2. 点击"打开登录页"
3. 在 VNC 浏览器中完成抖音登录
4. Cookie 会自动保存

### 下载失败
1. 检查 Cookie 是否有效
2. 查看错误日志
3. 尝试降低下载模式（从急速改为平衡/稳定）

---

## 📝 注意事项

1. **不要修改原有项目代码** - Web 模块完全独立新增
2. **首次使用需要获取 Cookie** - 通过 VNC 或命令行工具
3. **主页扫描间隔不宜过短** - 建议 30 分钟以上，避免触发风控
4. **及时清理失效主页** - 系统会自动标记，但需手动确认移除

---

## 📄 许可证

与原项目保持一致
