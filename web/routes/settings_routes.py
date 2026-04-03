"""
Flask 路由 - 系统设置相关
"""
import os
from flask import Blueprint, request, jsonify

from web.utils import WebConfig, DatabaseManager
from web.services import get_vnc_manager
from web.routes.auth_routes import login_required as web_login_required

settings_bp = Blueprint('settings', __name__, url_prefix='/api/settings')

config = WebConfig()
db_manager = DatabaseManager()


@settings_bp.route('/config', methods=['GET'])
@web_login_required
def get_config():
    """获取配置"""
    return jsonify({
        'success': True,
        'config': {
            'download_base_dir': config.download_base_dir,
            'scan_interval_minutes': config.scan_interval_minutes,
            'download_mode': config.download_mode,
            'max_error_logs': config.max_error_logs,
            'vnc_port': config.vnc_port,
        }
    })


@settings_bp.route('/config', methods=['PUT'])
@web_login_required
def update_config():
    """更新配置"""
    data = request.get_json()
    
    if 'download_base_dir' in data:
        config.set('download_base_dir', data['download_base_dir'])
    
    if 'scan_interval_minutes' in data:
        config.set('scan_interval_minutes', data['scan_interval_minutes'])
        
        # 更新扫描器间隔
        from web.services.homepage_scanner import get_scanner
        scanner = get_scanner()
        scanner.set_scan_interval(data['scan_interval_minutes'])
    
    if 'download_mode' in data:
        config.set('download_mode', data['download_mode'])
    
    if 'max_error_logs' in data:
        config.set('max_error_logs', data['max_error_logs'])
    
    return jsonify({
        'success': True,
        'message': 'Configuration updated'
    })


@settings_bp.route('/download-dir', methods=['POST'])
@web_login_required
def set_download_dir():
    """设置下载目录"""
    data = request.get_json()
    download_dir = data.get('path', '')
    
    if not download_dir:
        return jsonify({'success': False, 'error': 'No path provided'}), 400
    
    # 创建目录（如果不存在）
    try:
        os.makedirs(download_dir, exist_ok=True)
        config.set('download_base_dir', download_dir)
        
        return jsonify({
            'success': True,
            'message': f'Download directory set to: {download_dir}'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@settings_bp.route('/stats', methods=['GET'])
@web_login_required
def get_statistics():
    """获取统计信息"""
    stats = db_manager.get_statistics()
    
    return jsonify({
        'success': True,
        'stats': stats
    })


@settings_bp.route('/vnc/start', methods=['POST'])
@web_login_required
def start_vnc():
    """启动 VNC 服务"""
    vnc_manager = get_vnc_manager()
    
    if vnc_manager.is_running():
        return jsonify({
            'success': True,
            'message': 'VNC already running',
            'url': vnc_manager.get_vnc_url()
        })
    
    if vnc_manager.start():
        return jsonify({
            'success': True,
            'message': 'VNC started',
            'url': vnc_manager.get_vnc_url()
        })
    else:
        return jsonify({
            'success': False,
            'error': 'Failed to start VNC'
        }), 500


@settings_bp.route('/vnc/stop', methods=['POST'])
@web_login_required
def stop_vnc():
    """停止 VNC 服务"""
    vnc_manager = get_vnc_manager()
    vnc_manager.stop()
    
    return jsonify({
        'success': True,
        'message': 'VNC stopped'
    })


@settings_bp.route('/vnc/status', methods=['GET'])
@web_login_required
def vnc_status():
    """获取 VNC 状态"""
    vnc_manager = get_vnc_manager()
    
    return jsonify({
        'success': True,
        'running': vnc_manager.is_running(),
        'url': vnc_manager.get_vnc_url() if vnc_manager.is_running() else None
    })


@settings_bp.route('/vnc/open-cookie', methods=['POST'])
@web_login_required
def open_cookie_page():
    """在 VNC 浏览器中打开 Cookie 验证页面"""
    vnc_manager = get_vnc_manager()
    
    if not vnc_manager.is_running():
        return jsonify({
            'success': False,
            'error': 'VNC not running. Please start it first.'
        }), 400
    
    # 打开抖音登录页面
    vnc_manager.open_url('https://www.douyin.com/')
    
    return jsonify({
        'success': True,
        'message': 'Opening Douyin login page in VNC browser'
    })
