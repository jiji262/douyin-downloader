"""
Flask 路由 - 下载相关
"""
import asyncio
import uuid
from flask import Blueprint, request, jsonify
from flask_socketio import emit
from flask_login import login_required

from web.services import get_task_manager, get_downloader_adapter, DownloadMode
from web.utils import URLParser, DatabaseManager
from web.routes.auth_routes import login_required as web_login_required

download_bp = Blueprint('download', __name__, url_prefix='/api/download')

task_manager = get_task_manager()
db_manager = DatabaseManager()


@download_bp.route('/video', methods=['POST'])
@web_login_required
def download_video():
    """视频模式下载"""
    data = request.get_json()
    text = data.get('text', '')
    download_dir = data.get('download_dir', None)
    mode = data.get('mode', 'balance')  # fast, balance, stable
    
    if not text:
        return jsonify({'success': False, 'error': 'No text provided'}), 400
    
    # 提取所有抖音链接
    urls = URLParser.extract_douyin_urls(text)
    
    if not urls:
        return jsonify({'success': False, 'error': 'No valid Douyin URLs found'}), 400
    
    # 创建任务
    task_id = str(uuid.uuid4())[:8]
    task_manager.create_video_task(task_id, urls)
    
    # 异步执行下载
    async def run_download():
        adapter = get_downloader_adapter()
        
        for i, url in enumerate(urls, 1):
            if task_manager.should_stop():
                task_manager.update_task_progress(
                    task_id, 
                    current=i,
                    status='cancelled'
                )
                break
            
            try:
                # 进度回调
                def progress_callback(progress_data):
                    if progress_data.get('type') == 'progress':
                        task_manager.update_task_progress(
                            task_id,
                            current=i,
                            percent=progress_data.get('percent', 0),
                            speed=progress_data.get('detail', ''),
                            current_item=url,
                        )
                
                result = await adapter.download_video(
                    url=url,
                    download_dir=download_dir,
                    mode=mode,
                    progress_callback=progress_callback
                )
                
                # 记录下载结果
                db_manager.add_download_record(
                    mode='video',
                    url=url,
                    status='success' if result.get('success') else 'failed',
                    error_message=result.get('error'),
                    duration=result.get('duration')
                )
                
            except Exception as e:
                db_manager.add_error_log(
                    level='ERROR',
                    message=f'Video download failed: {url}',
                    source='download_video',
                    details=str(e)
                )
                
                task_manager.update_task_progress(
                    task_id,
                    current=i,
                    error_message=str(e)
                )
        
        task_manager.update_task_progress(
            task_id,
            status='completed' if not task_manager.should_stop() else 'cancelled'
        )
    
    # 启动异步任务
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_in_executor(None, lambda: loop.run_until_complete(run_download()))
    
    return jsonify({
        'success': True,
        'task_id': task_id,
        'urls_found': len(urls),
        'message': f'Found {len(urls)} video(s), downloading...'
    })


@download_bp.route('/homepage/add', methods=['POST'])
@web_login_required
def add_homepage():
    """添加主页链接"""
    from web.utils import HomepageManager
    
    data = request.get_json()
    url = data.get('url', '')
    
    if not url:
        return jsonify({'success': False, 'error': 'No URL provided'}), 400
    
    hp_manager = HomepageManager()
    
    if hp_manager.add_homepage(url):
        return jsonify({
            'success': True,
            'message': 'Homepage added successfully'
        })
    else:
        return jsonify({
            'success': False,
            'error': 'Homepage already exists'
        }), 400


@download_bp.route('/homepage/remove', methods=['POST'])
@web_login_required
def remove_homepage():
    """移除主页链接"""
    from web.utils import HomepageManager
    
    data = request.get_json()
    url = data.get('url', '')
    
    if not url:
        return jsonify({'success': False, 'error': 'No URL provided'}), 400
    
    hp_manager = HomepageManager()
    
    if hp_manager.remove_homepage(url):
        return jsonify({'success': True, 'message': 'Homepage removed successfully'})
    else:
        return jsonify({'success': False, 'error': 'Homepage not found'}), 404


@download_bp.route('/homepage/list', methods=['GET'])
@web_login_required
def list_homepages():
    """获取主页链接列表"""
    from web.utils import HomepageManager
    
    hp_manager = HomepageManager()
    homepages = hp_manager.get_all_homepages()
    
    return jsonify({
        'success': True,
        'homepages': homepages,
        'count': len(homepages)
    })


@download_bp.route('/homepage/start', methods=['POST'])
@web_login_required
def start_homepage_scan():
    """启动主页扫描任务"""
    from web.services.homepage_scanner import start_homepage_scanner
    
    if task_manager.is_homepage_running():
        return jsonify({
            'success': False,
            'error': 'Homepage scanner is already running'
        }), 400
    
    start_homepage_scanner()
    
    return jsonify({
        'success': True,
        'message': 'Homepage scanner started'
    })


@download_bp.route('/homepage/stop', methods=['POST'])
@web_login_required
def stop_homepage_scan():
    """停止主页扫描任务"""
    task_manager.stop_homepage_scan()
    
    return jsonify({
        'success': True,
        'message': 'Homepage scanner stopping...'
    })


@download_bp.route('/homepage/status', methods=['GET'])
@web_login_required
def homepage_status():
    """获取主页扫描状态"""
    return jsonify({
        'success': True,
        'running': task_manager.is_homepage_running(),
        'progress': task_manager.get_task('homepage_scanner').to_dict() 
                    if task_manager.get_task('homepage_scanner') else None
    })


@download_bp.route('/task/<task_id>', methods=['GET'])
@web_login_required
def get_task_progress(task_id):
    """获取任务进度"""
    task = task_manager.get_task(task_id)
    
    if not task:
        return jsonify({'success': False, 'error': 'Task not found'}), 404
    
    return jsonify({
        'success': True,
        'progress': task.to_dict()
    })


@download_bp.route('/tasks', methods=['GET'])
@web_login_required
def get_all_tasks():
    """获取所有任务"""
    tasks = [t.to_dict() for t in task_manager.get_all_tasks()]
    
    return jsonify({
        'success': True,
        'tasks': tasks
    })


@download_bp.route('/cancel/<task_id>', methods=['POST'])
@web_login_required
def cancel_task(task_id):
    """取消任务"""
    if task_manager.cancel_task(task_id):
        return jsonify({'success': True, 'message': 'Task cancelled'})
    else:
        return jsonify({'success': False, 'error': 'Cannot cancel task'}), 400
