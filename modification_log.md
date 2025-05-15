<!-- by 梁志灿 -->
# README文档修改记录 

## 
1. 初始版本创建
- 添加项目标题和基本介绍 <!-- Project title and basic introduction -->
- 设置文档框架结构 <!-- Setup document framework -->
- 添加作者注释标记 <!-- Add author annotation tags -->

## 
1. 核心功能描述更新
- 完善视频下载功能说明 <!-- Enhanced video download description -->
- 添加批量下载参数说明 <!-- Added batch download parameters -->
- 更新技术实现细节 <!-- Updated technical implementation details -->

2. 性能指标添加
- 添加吞吐量数据(~50MB/s) <!-- Throughput data -->
- 记录内存占用(<50MB) <!-- Memory usage recording -->
- 添加CPU使用率指标 <!-- CPU usage metrics -->

## 
1. 平台兼容性说明
- 添加Windows支持说明 <!-- Windows support -->
- 添加macOS兼容性描述 <!-- macOS compatibility -->
- 补充Linux测试结果 <!-- Linux test results -->

2. 截图示例更新
- 添加DouYinCommand1.png <!-- CLI screenshot 1 -->
- 添加DouYinCommand2.png <!-- CLI screenshot 2 -->
- 添加下载过程截图 <!-- Download process screenshot -->

## 待更新内容
1. 直播录制功能文档 <!-- Live streaming recording -->
2. 无水印下载配置说明 <!-- Watermark-free download config -->
3. 高级用法示例补充 <!-- Advanced usage examples -->
<!-- by 邓凯 -->
# 2205308020308 邓凯 

### 系统要求
- Python 3.7+
- Windows/macOS/Linux

### 安装步骤
1. 克隆仓库：
```bash
git clone https://github.com/yourname/douyin-downloader.git
cd douyin-downloader
```

2. 安装依赖：
```bash
pip install -r requirements.txt
```

### 常见安装问题
1. **依赖冲突**  
   解决方案：创建虚拟环境
   ```bash
   python -m venv venv
   source venv/bin/activate  # Linux/macOS
   venv\Scripts\activate     # Windows
   ```

2. **网络问题**  
   解决方案：使用国内镜像源
   ```bash
   pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
   ```

## 使用指南

#修改记录
1. **克隆项目仓库**  
   使用 Git 克隆代码到本地并进入项目目录：
   ```bash
   git clone https://github.com/yourname/douyin-downloader.git
   cd douyin-downloader

# 安装所需的 Python 第三方库：

pip install -r requirements.txt

# 执行 pip install 时出现权限不足或版本冲突提示
# 创建虚拟环境
python -m venv venv

# 激活虚拟环境
# macOS/Linux
source venv/bin/activate
# Windows
venv\Scripts\activate

# 再次安装依赖
pip install -r requirements.txt

# 安装卡住、报错 ReadTimeout、ConnectionResetError 等
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# fatal: unable to access... 或下载速度极慢
git clone https://github.com.cnpmjs.org/yourname/douyin-downloader.git

# 配置文件准备

copy config.example.yml config.yml 

# Windows

# 方式一：使用配置文件（推荐）

python DouYinCommand.py

# 方式二：使用命令行参数（适合脚本调用）

python DouYinCommand.py -C True -l "https://v.douyin.com/xxxxxx/" -p "downloads/"
<!-- by 甘润 -->

## 2205308020348  甘润

## 任务分工
负责README.md中的"Usage Guide"部分（Feature 1）
负责README.zh.md中的"使用指南"部分（功能一）


### 使用 AI 工具：
借助kimiai 将我负责的任务翻译并与使用方法比对，并进行增删改查

### 提问：
1.根据抖音下载器完成以下
负责README.md中的"Usage Guide"部分（Feature 1）
负责README.zh.md中的"使用指南"部分（功能一）
2.对项目进行正删改查

### 修改记录：
在readme.zh中：
新增内容：
高级使用方法：增加了关于如何自定义输出目录和启用详细日志模式的内容。
示例：在示例部分，增加了对不同使用场景的命令示例，包括使用默认目录下载和指定目录及日志模式下载的命令示例。
修改记录内容：
在 README.zh.md 的 “使用指南” 部分，我新增了“高级使用方法”部分，详细说明了如何使用-o或--output选项来自定义输出目录，以及如何通过添加-v或--verbose标志来启用详细日志模式进行下载。同时，在“示例”部分，我添加了两个具体的命令示例：一个用于演示如何使用默认目录下载视频，另一个则展示了如何同时指定下载位置和启用日志记录模式。
以上就是对 README.md 和 README.zh.md 中对应部分修改记录的总结。

在rademe中：
新增内容：
Advanced Usage：增加了关于如何自定义输出目录和启用详细日志模式的内容。
Example：在示例部分，增加了对不同使用场景的命令示例，包括使用默认目录下载和指定目录及日志模式下载的命令示例。
修改记录内容：
在 README.md 的 “Usage Guide” 部分，我新增了“Advanced Usage”部分，详细说明了如何使用-o或--output选项来自定义输出目录，以及如何通过添加-v或--verbose标志来启用详细日志模式进行下载。同时，在“Example”部分，我添加了两个具体的命令示例：一个用于演示如何使用默认目录下载视频，另一个则展示了如何同时指定下载位置和启用日志记录模式。
README.zh.md 中的 “使用指南” 部分（功能一）

<!-- by 郭海生 -->
👤 2205308020317 郭海生

## 任务分工
郭海生
负责README.md中的"Usage Guide"部分（Feature 2）
负责README.zh.md中的"使用指南"部分（功能二）
负责相关功能截图


### 使用 AI 工具：
借助deepseek 将How to Use翻译并与使用方法比对，并进行增删改查

### 提问：
1.README.md使用指南编写建议
2.抖音批量下载用户作品方法
3.抖音批量下载工具使用与优化建议

### 修改记录：
批量下载用户作品
```bash
python DouYinCommand.py --user "https://www.douyin.com/user/xxxxx" --number 50
```

 增量更新
```bash
python DouYinCommand.py --update "https://www.douyin.com/user/xxxxx"
```

常见问题与解决方案
1. 下载失败
**错误现象**：`Failed to fetch video info`或网络超时
**解决方案**：
1. 检查网络连接
2. 更新Cookie：
   ```yml
   # config.yml中修改
   cookies: "your_new_cookie_here"
   ```
3. 降低线程数：
   ```yml
   thread: 2  # 默认是5
   ```

2. 视频不完整
**解决方案**：
1. 使用HD参数：
   ```bash
   python DouYinCommand.py --hd "https://v.douyin.com/xxxxx/"
   ```
2. 确保使用有效的Cookie

3. 403禁止访问
**解决方案**：
1. 更换User-Agent：
   ```yml
   headers:
     User-Agent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
   ```
2. 等待1小时后重试

<!-- by 秦登基 -->

## 👤 2205308020324 秦登基

## 任务分工
负责README.md中的"How to Use"部分
负责README.zh.md中的"使用方法"部分


### 使用 AI 工具：
借助kimiai 将How to Use翻译并与使用方法比对，并进行增删改查

### 提问：
1.对抖音下载器生成一个How to use
2.自动翻译How to use
3.帮我新增注意事项部分，获取 Cookie 的方法，设置文件夹权限的说明，示例链接解析失败说明

### 修改记录：
在readme.zh中：
新增注意事项部分：增加了关于检查链接有效性、网络问题、Cookie 获取和文件夹权限的说明。
新增获取 Cookie 的方法：提供了详细的步骤，帮助用户获取所需的 Cookie 信息。
新增设置文件夹权限的说明：提供了在 Windows 系统中设置文件夹权限的步骤。
新增示例链接解析失败说明：解释了链接解析失败的可能原因，并提供了相应的解决建议。

在rademe中：
1.新增 Notes on Link Parsing 部分：增加了关于链接解析失败的说明，引导用户检查链接的合法性和网络连接。
2. 新增 Additional Tips 部分：提供了关于检查链接有效性、网络问题、Cookie 获取和文件夹权限的额外提示。
3. 新增 Cookie Acquisition Method 部分：详细说明了如何获取所需的 Cookie 信息。
4. 新增 Setting Folder Permissions 部分：提供了在 Windows 系统中设置文件夹权限的步骤。

<!-- by 冯浩 -->
### 任务分工：
负责README.md中的"Contributing"部分
负责README.zh.md中的"贡献指南"部分

### 使用 AI 工具：
kimi 智能助手

### 提问：
> “1.关于贡献指南的基本框架。”
> “2.关于贡献指南的内容资料。”

### 修改记录：
- 对贡献指南的中英文相关内容进行修稿以及补充相关资料。


<!-- by 王瀚龙 -->

## 任务分工
负责terms.md的编写和维护
负责专业术语的审核和补充
负责AI使用文档的编写


### 使用 AI 工具：
借助kimiAI助手

### 提问：
1.如何正确编写terms.md文件？
2.如何对terms.md文件进行维护？
3.如何对该术语表进行补充修改？

### 修改记录：
在terms.md文件中进行术语表的编写和维护，完成专业术语的审核和补充

在terms.md中：
新增内容：
### 术语定义