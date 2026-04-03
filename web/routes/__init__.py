"""
路由模块初始化文件
"""
from .auth_routes import auth_bp, login_required, is_shutdown_required
from .download_routes import download_bp
from .settings_routes import settings_bp
from .logs_routes import logs_bp

__all__ = [
    'auth_bp',
    'download_bp', 
    'settings_bp',
    'logs_bp',
    'login_required',
    'is_shutdown_required'
]
