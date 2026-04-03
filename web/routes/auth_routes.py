"""
Flask 路由 - 认证相关
"""
from flask import Blueprint, request, jsonify, session, redirect, url_for
from functools import wraps

auth_bp = Blueprint('auth', __name__, url_prefix='/api/auth')

# 配置
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "qq2669035538"
MAX_LOGIN_ATTEMPTS = 3

# 全局登录尝试计数
_login_attempts = 0
_server_shutdown_required = False


def login_required(f):
    """登录验证装饰器"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return jsonify({'success': False, 'error': 'Please login first'}), 401
        return f(*args, **kwargs)
    return decorated_function


@auth_bp.route('/login', methods=['POST'])
def login():
    """用户登录"""
    global _login_attempts, _server_shutdown_required
    
    if _server_shutdown_required:
        return jsonify({
            'success': False, 
            'error': 'Server shutdown required due to multiple failed attempts'
        }), 403
    
    data = request.get_json()
    username = data.get('username', '')
    password = data.get('password', '')
    
    if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
        # 登录成功
        session['logged_in'] = True
        session['username'] = username
        _login_attempts = 0
        return jsonify({
            'success': True,
            'message': 'Login successful',
            'username': username
        })
    else:
        # 登录失败
        _login_attempts += 1
        
        if _login_attempts >= MAX_LOGIN_ATTEMPTS:
            _server_shutdown_required = True
            return jsonify({
                'success': False,
                'error': f'Failed {_login_attempts} times. Server must be restarted.',
                'shutdown_required': True
            }), 403
        
        return jsonify({
            'success': False,
            'error': f'Invalid credentials. {_login_attempts}/{MAX_LOGIN_ATTEMPTS} attempts'
        }), 401


@auth_bp.route('/logout', methods=['POST'])
@login_required
def logout():
    """用户登出"""
    session.clear()
    return jsonify({'success': True, 'message': 'Logged out successfully'})


@auth_bp.route('/status', methods=['GET'])
def auth_status():
    """获取认证状态"""
    return jsonify({
        'logged_in': session.get('logged_in', False),
        'username': session.get('username', None)
    })


@auth_bp.route('/check-shutdown', methods=['GET'])
def check_shutdown():
    """检查是否需要关闭服务器"""
    return jsonify({
        'shutdown_required': _server_shutdown_required,
        'attempts': _login_attempts
    })


def is_shutdown_required():
    """检查是否需要关闭服务器（供外部调用）"""
    return _server_shutdown_required
