"""
Flask 路由 - 日志和记录相关
"""
from flask import Blueprint, request, jsonify

from web.utils import DatabaseManager
from web.routes.auth_routes import login_required as web_login_required

logs_bp = Blueprint('logs', __name__, url_prefix='/api/logs')

db_manager = DatabaseManager()


@logs_bp.route('/errors', methods=['GET'])
@web_login_required
def get_error_logs():
    """获取错误日志"""
    limit = request.args.get('limit', 100, type=int)
    offset = request.args.get('offset', 0, type=int)
    
    logs = db_manager.get_error_logs(limit=limit, offset=offset)
    
    return jsonify({
        'success': True,
        'logs': logs,
        'count': len(logs)
    })


@logs_bp.route('/errors/clear', methods=['POST'])
@web_login_required
def clear_error_logs():
    """清空错误日志"""
    # 简单实现：删除所有日志
    import sqlite3
    conn = sqlite3.connect(db_manager.db_path)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM error_logs')
    conn.commit()
    conn.close()
    
    return jsonify({
        'success': True,
        'message': 'Error logs cleared'
    })


@logs_bp.route('/downloads', methods=['GET'])
@web_login_required
def get_download_records():
    """获取下载记录"""
    limit = request.args.get('limit', 100, type=int)
    offset = request.args.get('offset', 0, type=int)
    mode = request.args.get('mode', None)  # video 或 homepage
    
    records = db_manager.get_download_records(limit=limit, offset=offset, mode=mode)
    
    return jsonify({
        'success': True,
        'records': records,
        'count': len(records)
    })


@logs_bp.route('/homepages', methods=['GET'])
@web_login_required
def get_homepage_stats():
    """获取主页统计信息"""
    status = request.args.get('status', None)  # active 或 inactive
    
    stats = db_manager.get_homepage_stats(status=status)
    
    return jsonify({
        'success': True,
        'stats': stats,
        'count': len(stats)
    })


@logs_bp.route('/homepages/remove-inactive', methods=['POST'])
@web_login_required
def remove_inactive_homepages():
    """移除失效的主页"""
    from web.utils import HomepageManager
    
    inactive_stats = db_manager.get_homepage_stats(status='inactive')
    hp_manager = HomepageManager()
    
    removed_count = 0
    for stat in inactive_stats:
        hp_manager.remove_homepage(stat['url'])
        db_manager.remove_homepage(stat['sec_uid'])
        removed_count += 1
    
    return jsonify({
        'success': True,
        'message': f'Removed {removed_count} inactive homepage(s)',
        'removed_count': removed_count
    })
