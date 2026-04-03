"""
Flask 应用主入口
"""
import os
import sys
from flask import Flask, render_template, send_from_directory
from flask_cors import CORS
from flask_socketio import SocketIO

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from web.routes import auth_bp, download_bp, settings_bp, logs_bp, is_shutdown_required
from web.utils import WebConfig


def create_app():
    """创建 Flask 应用"""
    app = Flask(
        __name__,
        template_folder='templates',
        static_folder='static'
    )
    
    # 配置
    app.secret_key = os.urandom(24).hex()
    app.config['SESSION_TYPE'] = 'filesystem'
    app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max request size
    
    # 启用 CORS
    CORS(app, resources={r"/api/*": {"origins": "*"}})
    
    # 注册蓝图
    app.register_blueprint(auth_bp)
    app.register_blueprint(download_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(logs_bp)
    
    # 静态文件路由
    @app.route('/')
    def index():
        return render_template('index.html')
    
    @app.route('/static/<path:filename>')
    def serve_static(filename):
        return send_from_directory('static', filename)
    
    # 健康检查
    @app.route('/health')
    def health():
        return {'status': 'ok'}
    
    # 错误处理
    @app.errorhandler(404)
    def not_found(error):
        return {'success': False, 'error': 'Not found'}, 404
    
    @app.errorhandler(500)
    def internal_error(error):
        return {'success': False, 'error': 'Internal server error'}, 500
    
    # 请求后钩子 - 检查是否需要关闭
    @app.after_request
    def check_shutdown(response):
        if is_shutdown_required():
            response.headers['X-Shutdown-Required'] = 'true'
        return response
    
    return app


# 创建应用实例
app = create_app()

if __name__ == '__main__':
    config = WebConfig()
    port = int(os.environ.get('WEB_PORT', 8886))
    
    print(f"Starting Douyin Downloader Web Interface")
    print(f"Access URL: http://localhost:{port}")
    print(f"Username: admin")
    print(f"Password: qq2669035538")
    print("-" * 50)
    
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
