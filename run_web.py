#!/usr/bin/env python3
"""
抖音下载器 Web 模块启动脚本

使用方法:
    python run_web.py [--port PORT] [--host HOST]

示例:
    python run_web.py              # 默认端口 8886
    python run_web.py --port 9000  # 指定端口
    python run_web.py --host 0.0.0.0  # 监听所有接口
"""

import os
import sys
import argparse

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main():
    parser = argparse.ArgumentParser(description='抖音下载器 Web 管理界面')
    parser.add_argument('--port', type=int, default=8886, help='服务端口 (默认：8886)')
    parser.add_argument('--host', type=str, default='0.0.0.0', help='监听地址 (默认：0.0.0.0)')
    parser.add_argument('--debug', action='store_true', help='启用调试模式')
    
    args = parser.parse_args()
    
    print("=" * 60)
    print("🎬 抖音下载器 - Web 管理界面")
    print("=" * 60)
    print(f"📍 访问地址：http://localhost:{args.port}")
    print(f"👤 用户名：admin")
    print(f"🔑 密码：qq2669035538")
    print("=" * 60)
    print()
    print("功能说明:")
    print("  1. 视频模式：粘贴包含抖音链接的文本，批量下载视频")
    print("  2. 主页模式：添加主页链接，后台定时扫描下载")
    print("  3. 下载记录：查看所有下载历史和统计信息")
    print("  4. 系统设置：配置下载目录、扫描间隔、Cookie 验证等")
    print()
    print("⚠️  注意：登录失败 3 次后需要重启服务器才能继续登录")
    print("=" * 60)
    print()
    
    # 设置环境变量
    os.environ['WEB_PORT'] = str(args.port)
    
    # 导入并运行应用
    from web.app import app
    
    try:
        app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)
    except KeyboardInterrupt:
        print("\n\n正在关闭服务...")
        sys.exit(0)
    except Exception as e:
        print(f"\n错误：{e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
