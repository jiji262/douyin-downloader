// 抖音下载器 Web 界面交互脚本

class DouyinDownloader {
    constructor() {
        this.currentTab = 'video';
        this.currentTaskId = null;
        this.pollingInterval = null;
        
        this.init();
    }
    
    init() {
        this.checkAuth();
        this.bindEvents();
    }
    
    // API 请求封装
    async api(url, options = {}) {
        const defaultOptions = {
            headers: {
                'Content-Type': 'application/json',
            },
        };
        
        const response = await fetch(url, { ...defaultOptions, ...options });
        const data = await response.json();
        
        if (!response.ok) {
            throw new Error(data.error || 'Request failed');
        }
        
        return data;
    }
    
    // 检查认证状态
    async checkAuth() {
        try {
            const data = await this.api('/api/auth/status');
            
            if (data.logged_in) {
                this.showMainPage();
            } else {
                this.showLoginPage();
            }
        } catch (error) {
            this.showLoginPage();
        }
    }
    
    showLoginPage() {
        document.getElementById('login-page').classList.remove('hidden');
        document.getElementById('main-page').classList.add('hidden');
    }
    
    showMainPage() {
        document.getElementById('login-page').classList.add('hidden');
        document.getElementById('main-page').classList.remove('hidden');
        this.loadStats();
    }
    
    bindEvents() {
        // 登录表单
        document.getElementById('login-form').addEventListener('submit', (e) => {
            e.preventDefault();
            this.handleLogin();
        });
        
        // 登出
        document.getElementById('logout-btn').addEventListener('click', () => {
            this.handleLogout();
        });
        
        // 标签页切换
        document.querySelectorAll('.nav-link').forEach(link => {
            link.addEventListener('click', (e) => {
                e.preventDefault();
                const tab = e.target.dataset.tab;
                this.switchTab(tab);
            });
        });
        
        // 视频下载
        document.getElementById('video-download-btn').addEventListener('click', () => {
            this.handleVideoDownload();
        });
        
        document.getElementById('video-cancel-btn').addEventListener('click', () => {
            this.handleCancelTask();
        });
        
        // 主页管理
        document.getElementById('homepage-add-btn').addEventListener('click', () => {
            this.handleAddHomepage();
        });
        
        document.getElementById('homepage-start-btn').addEventListener('click', () => {
            this.handleStartScanner();
        });
        
        document.getElementById('homepage-stop-btn').addEventListener('click', () => {
            this.handleStopScanner();
        });
        
        document.getElementById('homepage-refresh-btn').addEventListener('click', () => {
            this.loadHomepageList();
        });
        
        // 下载记录
        document.getElementById('record-filter').addEventListener('change', () => {
            this.loadDownloadRecords();
        });
        
        document.getElementById('record-refresh-btn').addEventListener('click', () => {
            this.loadDownloadRecords();
            this.loadStats();
        });
        
        document.getElementById('error-clear-btn').addEventListener('click', () => {
            this.clearErrorLogs();
        });
        
        document.getElementById('error-refresh-btn').addEventListener('click', () => {
            this.loadErrorLogs();
        });
        
        // 系统设置
        document.getElementById('setting-save-btn').addEventListener('click', () => {
            this.saveSettings();
        });
        
        document.getElementById('vnc-start-btn').addEventListener('click', () => {
            this.startVNC();
        });
        
        document.getElementById('vnc-stop-btn').addEventListener('click', () => {
            this.stopVNC();
        });
        
        document.getElementById('vnc-open-cookie-btn').addEventListener('click', () => {
            this.openCookiePage();
        });
    }
    
    // 登录处理
    async handleLogin() {
        const username = document.getElementById('username').value;
        const password = document.getElementById('password').value;
        const errorDiv = document.getElementById('login-error');
        
        try {
            const data = await this.api('/api/auth/login', {
                method: 'POST',
                body: JSON.stringify({ username, password }),
            });
            
            if (data.success) {
                this.showToast('登录成功', 'success');
                this.showMainPage();
            }
        } catch (error) {
            errorDiv.textContent = error.message;
            
            if (error.message.includes('shutdown')) {
                this.showToast('登录失败次数过多，请重启服务器', 'error');
            } else {
                this.showToast('登录失败：' + error.message, 'error');
            }
        }
    }
    
    // 登出处理
    async handleLogout() {
        try {
            await this.api('/api/auth/logout', { method: 'POST' });
            this.showToast('已退出登录', 'info');
            this.showLoginPage();
        } catch (error) {
            this.showToast('退出失败', 'error');
        }
    }
    
    // 切换标签页
    switchTab(tab) {
        this.currentTab = tab;
        
        // 更新导航状态
        document.querySelectorAll('.nav-link').forEach(link => {
            link.classList.toggle('active', link.dataset.tab === tab);
        });
        
        // 更新内容显示
        document.querySelectorAll('.tab-content').forEach(content => {
            content.classList.toggle('active', content.id === `tab-${tab}`);
            content.classList.toggle('hidden', content.id !== `tab-${tab}`);
        });
        
        // 加载对应数据
        if (tab === 'homepage') {
            this.loadHomepageList();
            this.checkScannerStatus();
        } else if (tab === 'records') {
            this.loadDownloadRecords();
            this.loadStats();
            this.loadErrorLogs();
        } else if (tab === 'settings') {
            this.loadSettings();
            this.checkVNCStatus();
        }
    }
    
    // 视频下载
    async handleVideoDownload() {
        const text = document.getElementById('video-text').value;
        const mode = document.getElementById('video-mode').value;
        const downloadDir = document.getElementById('video-download-dir').value;
        
        if (!text.trim()) {
            this.showToast('请输入视频链接文本', 'error');
            return;
        }
        
        try {
            const btn = document.getElementById('video-download-btn');
            btn.disabled = true;
            
            const data = await this.api('/api/download/video', {
                method: 'POST',
                body: JSON.stringify({ text, mode, download_dir: downloadDir || undefined }),
            });
            
            if (data.success) {
                this.currentTaskId = data.task_id;
                this.showToast(`找到 ${data.urls_found} 个视频，开始下载`, 'success');
                this.showVideoProgress();
                this.startPolling();
            }
        } catch (error) {
            this.showToast('下载失败：' + error.message, 'error');
        } finally {
            document.getElementById('video-download-btn').disabled = false;
        }
    }
    
    showVideoProgress() {
        document.getElementById('video-progress-container').classList.remove('hidden');
    }
    
    async handleCancelTask() {
        if (!this.currentTaskId) return;
        
        try {
            await this.api(`/api/download/cancel/${this.currentTaskId}`, {
                method: 'POST',
            });
            
            this.showToast('任务已取消', 'info');
            this.stopPolling();
        } catch (error) {
            this.showToast('取消失败', 'error');
        }
    }
    
    // 主页管理
    async handleAddHomepage() {
        const url = document.getElementById('homepage-url').value.trim();
        
        if (!url) {
            this.showToast('请输入主页链接', 'error');
            return;
        }
        
        try {
            const data = await this.api('/api/download/homepage/add', {
                method: 'POST',
                body: JSON.stringify({ url }),
            });
            
            if (data.success) {
                this.showToast('主页添加成功', 'success');
                document.getElementById('homepage-url').value = '';
                this.loadHomepageList();
            }
        } catch (error) {
            this.showToast('添加失败：' + error.message, 'error');
        }
    }
    
    async loadHomepageList() {
        try {
            const data = await this.api('/api/download/homepage/list');
            
            const tbody = document.getElementById('homepage-list');
            tbody.innerHTML = '';
            
            data.homepages.forEach((url, index) => {
                const tr = document.createElement('tr');
                tr.innerHTML = `
                    <td>${index + 1}</td>
                    <td>${this.truncateUrl(url)}</td>
                    <td><span class="status-indicator online">等待扫描</span></td>
                    <td>
                        <button class="btn btn-sm btn-danger" onclick="app.removeHomepage('${url}')">移除</button>
                    </td>
                `;
                tbody.appendChild(tr);
            });
        } catch (error) {
            console.error('Failed to load homepage list:', error);
        }
    }
    
    async removeHomepage(url) {
        try {
            await this.api('/api/download/homepage/remove', {
                method: 'POST',
                body: JSON.stringify({ url }),
            });
            
            this.showToast('主页已移除', 'success');
            this.loadHomepageList();
        } catch (error) {
            this.showToast('移除失败', 'error');
        }
    }
    
    truncateUrl(url, maxLength = 50) {
        if (url.length <= maxLength) return url;
        return url.substring(0, maxLength) + '...';
    }
    
    async handleStartScanner() {
        try {
            const data = await this.api('/api/download/homepage/start', {
                method: 'POST',
            });
            
            if (data.success) {
                this.showToast('扫描已启动', 'success');
                this.checkScannerStatus();
            }
        } catch (error) {
            this.showToast('启动失败：' + error.message, 'error');
        }
    }
    
    async handleStopScanner() {
        try {
            const data = await this.api('/api/download/homepage/stop', {
                method: 'POST',
            });
            
            if (data.success) {
                this.showToast('扫描停止中', 'info');
                this.checkScannerStatus();
            }
        } catch (error) {
            this.showToast('停止失败', 'error');
        }
    }
    
    async checkScannerStatus() {
        try {
            const data = await this.api('/api/download/homepage/status');
            
            const indicator = document.querySelector('#scanner-status .status-indicator');
            if (data.running) {
                indicator.className = 'status-indicator online';
                indicator.textContent = '运行中';
                document.getElementById('homepage-progress-container').classList.remove('hidden');
                
                if (data.progress) {
                    this.updateHomepageProgress(data.progress);
                }
            } else {
                indicator.className = 'status-indicator offline';
                indicator.textContent = '未运行';
                document.getElementById('homepage-progress-container').classList.add('hidden');
            }
        } catch (error) {
            console.error('Failed to check scanner status:', error);
        }
    }
    
    updateHomepageProgress(progress) {
        const percent = progress.percent || 0;
        document.getElementById('homepage-progress-fill').style.width = percent + '%';
        document.getElementById('homepage-progress-percent').textContent = Math.round(percent) + '%';
        document.getElementById('homepage-progress-status').textContent = progress.current_item || '扫描中...';
        document.getElementById('homepage-progress-detail').textContent = progress.speed || '';
    }
    
    // 下载记录
    async loadDownloadRecords() {
        const filter = document.getElementById('record-filter').value;
        const url = filter ? `/api/logs/downloads?mode=${filter}` : '/api/logs/downloads';
        
        try {
            const data = await this.api(url);
            
            const tbody = document.getElementById('download-records');
            tbody.innerHTML = '';
            
            data.records.slice(0, 50).forEach(record => {
                const tr = document.createElement('tr');
                tr.innerHTML = `
                    <td>${this.formatTime(record.timestamp)}</td>
                    <td>${record.mode === 'video' ? '📹 视频' : '🏠 主页'}</td>
                    <td>${this.truncateUrl(record.url, 40)}</td>
                    <td>
                        <span class="status-indicator ${record.status === 'success' ? 'online' : 'offline'}">
                            ${record.status === 'success' ? '成功' : '失败'}
                        </span>
                    </td>
                    <td>${record.duration ? record.duration.toFixed(1) + 's' : '-'}</td>
                `;
                tbody.appendChild(tr);
            });
        } catch (error) {
            console.error('Failed to load download records:', error);
        }
    }
    
    async loadStats() {
        try {
            const data = await this.api('/api/settings/stats');
            const stats = data.stats;
            
            document.getElementById('stat-total-downloads').textContent = stats.total_downloads || 0;
            document.getElementById('stat-active-homepages').textContent = stats.active_homepages || 0;
            document.getElementById('stat-inactive-homepages').textContent = stats.inactive_homepages || 0;
            document.getElementById('stat-errors').textContent = stats.error_count || 0;
        } catch (error) {
            console.error('Failed to load stats:', error);
        }
    }
    
    async loadErrorLogs() {
        try {
            const data = await this.api('/api/logs/errors?limit=50');
            
            const container = document.getElementById('error-logs');
            container.innerHTML = '';
            
            data.logs.forEach(log => {
                const div = document.createElement('div');
                div.className = 'log-entry';
                div.innerHTML = `
                    <div class="log-time">${this.formatTime(log.timestamp)}</div>
                    <span class="log-level ${log.level}">${log.level}</span>
                    <div class="log-message">${log.source ? `[${log.source}] ` : ''}${log.message}</div>
                `;
                container.appendChild(div);
            });
        } catch (error) {
            console.error('Failed to load error logs:', error);
        }
    }
    
    async clearErrorLogs() {
        if (!confirm('确定要清空所有错误日志吗？')) return;
        
        try {
            await this.api('/api/logs/errors/clear', { method: 'POST' });
            this.showToast('日志已清空', 'success');
            this.loadErrorLogs();
            this.loadStats();
        } catch (error) {
            this.showToast('清空失败', 'error');
        }
    }
    
    // 系统设置
    async loadSettings() {
        try {
            const data = await this.api('/api/settings/config');
            const config = data.config;
            
            document.getElementById('setting-download-dir').value = config.download_base_dir || '';
            document.getElementById('setting-scan-interval').value = config.scan_interval_minutes || 30;
            document.getElementById('setting-download-mode').value = config.download_mode || 'balance';
        } catch (error) {
            console.error('Failed to load settings:', error);
        }
    }
    
    async saveSettings() {
        const downloadDir = document.getElementById('setting-download-dir').value;
        const scanInterval = parseInt(document.getElementById('setting-scan-interval').value);
        const downloadMode = document.getElementById('setting-download-mode').value;
        
        try {
            await this.api('/api/settings/config', {
                method: 'PUT',
                body: JSON.stringify({
                    download_base_dir: downloadDir,
                    scan_interval_minutes: scanInterval,
                    download_mode: downloadMode,
                }),
            });
            
            this.showToast('设置已保存', 'success');
        } catch (error) {
            this.showToast('保存失败：' + error.message, 'error');
        }
    }
    
    // VNC 相关
    async startVNC() {
        try {
            const data = await this.api('/api/settings/vnc/start', { method: 'POST' });
            
            if (data.success) {
                this.showToast('VNC 已启动', 'success');
                this.checkVNCStatus();
            }
        } catch (error) {
            this.showToast('启动失败：' + error.message, 'error');
        }
    }
    
    async stopVNC() {
        try {
            await this.api('/api/settings/vnc/stop', { method: 'POST' });
            this.showToast('VNC 已停止', 'info');
            this.checkVNCStatus();
        } catch (error) {
            this.showToast('停止失败', 'error');
        }
    }
    
    async openCookiePage() {
        try {
            const data = await this.api('/api/settings/vnc/open-cookie', { method: 'POST' });
            
            if (data.success) {
                this.showToast('正在打开抖音登录页面', 'success');
            }
        } catch (error) {
            this.showToast('打开失败：' + error.message, 'error');
        }
    }
    
    async checkVNCStatus() {
        try {
            const data = await this.api('/api/settings/vnc/status');
            
            const indicator = document.querySelector('#vnc-status .status-indicator');
            if (data.running) {
                indicator.className = 'status-indicator online';
                indicator.textContent = '运行中';
                document.getElementById('vnc-frame').src = data.url;
                document.getElementById('vnc-frame-container').classList.remove('hidden');
            } else {
                indicator.className = 'status-indicator offline';
                indicator.textContent = '未启动';
                document.getElementById('vnc-frame-container').classList.add('hidden');
            }
        } catch (error) {
            console.error('Failed to check VNC status:', error);
        }
    }
    
    // 轮询任务进度
    startPolling() {
        this.pollingInterval = setInterval(async () => {
            if (!this.currentTaskId) return;
            
            try {
                const data = await this.api(`/api/download/task/${this.currentTaskId}`);
                
                if (data.success) {
                    const progress = data.progress;
                    this.updateVideoProgress(progress);
                    
                    if (progress.status === 'completed' || progress.status === 'failed' || progress.status === 'cancelled') {
                        this.stopPolling();
                        this.showToast(`任务${this.getStatusText(progress.status)}`, 
                            progress.status === 'completed' ? 'success' : 'info');
                        document.getElementById('video-download-btn').disabled = false;
                    }
                }
            } catch (error) {
                console.error('Polling error:', error);
            }
        }, 1000);
    }
    
    stopPolling() {
        if (this.pollingInterval) {
            clearInterval(this.pollingInterval);
            this.pollingInterval = null;
        }
    }
    
    updateVideoProgress(progress) {
        const percent = progress.percent || 0;
        document.getElementById('video-progress-fill').style.width = percent + '%';
        document.getElementById('video-progress-percent').textContent = Math.round(percent) + '%';
        document.getElementById('video-progress-status').textContent = progress.current_item || '下载中...';
        document.getElementById('video-progress-detail').textContent = 
            `${progress.current}/${progress.total} - ${progress.speed || ''}`;
    }
    
    getStatusText(status) {
        const map = {
            'completed': '已完成',
            'failed': '失败',
            'cancelled': '已取消',
        };
        return map[status] || status;
    }
    
    formatTime(isoString) {
        if (!isoString) return '-';
        const date = new Date(isoString);
        return date.toLocaleString('zh-CN', {
            month: '2-digit',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit',
        });
    }
    
    // Toast 通知
    showToast(message, type = 'info') {
        const toast = document.getElementById('toast');
        toast.textContent = message;
        toast.className = `toast ${type}`;
        toast.classList.remove('hidden');
        
        setTimeout(() => {
            toast.classList.add('hidden');
        }, 3000);
    }
}

// 初始化应用
const app = new DouyinDownloader();
