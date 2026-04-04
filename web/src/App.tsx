import { useState, useEffect, useRef } from 'react'
import { Download, MonitorPlay, Settings, HardDrive, Play } from 'lucide-react'
import ConfigEditor from './ConfigEditor'
import HistoryViewer from './HistoryViewer'
import './index.css'

function App() {
  const [activeTab, setActiveTab] = useState('dashboard')
  const [termOutput, setTermOutput] = useState<string[]>([])
  const [isTaskRunning, setIsTaskRunning] = useState(false)
  const termEndRef = useRef<HTMLDivElement>(null)
  
  // 自动滚动到终端底部
  useEffect(() => {
    if (termEndRef.current) {
        termEndRef.current.scrollIntoView({ behavior: 'smooth' })
    }
  }, [termOutput])

  const runTask = (cmd: string) => {
    if (isTaskRunning) return;
    setIsTaskRunning(true)
    setTermOutput([]) // Clear
    
    // 打开 WebSocket
    const ws = new WebSocket('ws://127.0.0.1:8000/api/task/ws')
    ws.onopen = () => {
      ws.send(JSON.stringify({ command: cmd }))
    }
    ws.onmessage = (event) => {
      setTermOutput(prev => [...prev, event.data])
    }
    ws.onclose = () => setIsTaskRunning(false)
    ws.onerror = () => setIsTaskRunning(false)
  }

  return (
    <>
      <div className="sidebar">
        <div className="brand">
          <MonitorPlay size={24} color="#00f0ff" />
          <span>Douyin DL<span style={{color: '#94a3b8', fontSize: '0.8rem'}}> Console</span></span>
        </div>
        
        <div className={`nav-item ${activeTab === 'dashboard' ? 'active' : ''}`} onClick={() => setActiveTab('dashboard')}>
          <MonitorPlay size={20} /> 控制面板
        </div>
        <div className={`nav-item ${activeTab === 'config' ? 'active' : ''}`} onClick={() => setActiveTab('config')}>
          <Settings size={20} /> 核心配置
        </div>
        <div className={`nav-item ${activeTab === 'db' ? 'active' : ''}`} onClick={() => setActiveTab('db')}>
          <HardDrive size={20} /> 本地资产
        </div>
      </div>

      <div className="main-content">
        {activeTab === 'dashboard' && (
          <div className="animation-fade-in" style={{animation: 'fadeIn 0.5s'}}>
            <h1>欢迎回来, Master</h1>
            
            <div className="grid-cards" style={{marginBottom: '32px'}}>
               <div className="glass-panel stat-card">
                  <span className="stat-label">API 状态节点</span>
                  <span className="stat-value" style={{color: '#00f0ff'}}>就绪 (Ready)</span>
               </div>
               <div className="glass-panel stat-card">
                  <span className="stat-label">运行管线</span>
                  <span className="stat-value" style={{color: isTaskRunning ? '#ff2a5f' : '#94a3b8'}}>{isTaskRunning ? '繁忙执行中...' : '空闲挂机'}</span>
               </div>
               <div className="glass-panel stat-card" style={{gridColumn: 'span 2'}}>
                  <span className="stat-label">快捷指令发射器</span>
                  <div style={{display:'flex', gap:'12px', marginTop:'8px'}}>
                    <button className="button-primary" onClick={() => runTask('download')} disabled={isTaskRunning}>
                      <Download size={18} /> {isTaskRunning ? '拉取锁定' : '开始多维拉取任务'}
                    </button>
                    <button className="button-primary" style={{background: 'linear-gradient(135deg, #7000ff 0%, #0080ff 100%)'}} onClick={() => runTask('whisper')} disabled={isTaskRunning}>
                      <Play size={18} /> Whisper 音轨解构
                    </button>
                  </div>
               </div>
            </div>

            <div className="glass-panel" style={{padding: '24px'}}>
              <div className="panel-header">子进程终端镜像 (Live Stdout Pipe)</div>
              <div className="terminal">
                {termOutput.length === 0 && <span style={{opacity: 0.5}}>&gt; 通信链路空闲, 等待分配调度任务...</span>}
                {termOutput.map((line, i) => <div key={i} className="terminal-line">{line}</div>)}
                <div ref={termEndRef} />
              </div>
            </div>
          </div>
        )}
        
        {activeTab === 'config' && (
          <div className="animation-fade-in">
            <h1 style={{marginBottom: '16px'}}>核心系统参数修改</h1>
            <ConfigEditor />
          </div>
        )}

        {activeTab === 'db' && (
          <div className="animation-fade-in">
            <h1>本地资产管理</h1>
            <HistoryViewer />
          </div>
        )}
      </div>
    </>
  )
}

export default App
