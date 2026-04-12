import { useState, useEffect } from 'react';
import axios from 'axios';
import { Save, RefreshCw, CheckCircle2, FolderSearch, Plus, Trash2, Eye, EyeOff } from 'lucide-react';

export default function ConfigEditor() {
  const [config, setConfig] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saveSuccess, setSaveSuccess] = useState(false);

  const extractUrl = (text: string): string => {
    if (!text) return text;
    const isIgnored = text.startsWith('#');
    const raw = isIgnored ? text.replace(/^#\s*/, '') : text;
    const match = raw.match(/(https?:\/\/[^\s]+)/);
    const clean = match ? match[1] : raw;
    return isIgnored ? '# ' + clean : clean;
  };

  const fetchConfig = async () => {
    setLoading(true);
    try {
      const res = await axios.get('http://127.0.0.1:8000/api/config/');
      const data = res.data;
      // 对已保存的链接进行提纯
      if (Array.isArray(data.link)) {
        data.link = data.link.map((l: string) => extractUrl(l));
      }
      setConfig(data);
    } catch (e) {
      console.error(e);
      alert('无法连接到后端读取配置。请确认服务已启动。');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchConfig();
  }, []);

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 's') {
        e.preventDefault();
        handleSave();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [config]);

  const handleSave = async () => {
    setSaving(true);
    try {
      await axios.post('http://127.0.0.1:8000/api/config/', config);
      setSaveSuccess(true);
      setTimeout(() => setSaveSuccess(false), 3000);
    } catch (e) {
      console.error(e);
      alert('保存失败。');
    } finally {
      setSaving(false);
    }
  };

  const handleChange = (key: string, value: any, parent?: string) => {
    setConfig((prev: any) => {
      const newConfig = { ...prev };
      if (parent) {
        if (!newConfig[parent]) newConfig[parent] = {};
        newConfig[parent] = { ...newConfig[parent], [key]: value };
      } else {
        newConfig[key] = value;
      }
      return newConfig;
    });
  };

  // ----- Link Handlers -----
  const handleLinkChange = (index: number, val: string) => {
    const cleanVal = extractUrl(val);
    setConfig((prev: any) => {
      const newLinks = [...(prev.link || [])];
      newLinks[index] = cleanVal;
      return { ...prev, link: newLinks };
    });
  };

  const toggleIgnoreLink = (index: number) => {
    setConfig((prev: any) => {
      const newLinks = [...(prev.link || [])];
      let val = newLinks[index];
      if (val.startsWith('#')) {
        val = val.replace(/^#\s*/, ''); // 取消忽略
      } else {
        val = '# ' + val; // 加上忽略前缀
      }
      newLinks[index] = val;
      return { ...prev, link: newLinks };
    });
  };

  const addLink = () => {
    setConfig((prev: any) => {
      return { ...prev, link: [...(prev.link || []), ''] };
    });
  };

  const removeLink = (index: number) => {
    setConfig((prev: any) => {
      const newLinks = [...(prev.link || [])];
      newLinks.splice(index, 1);
      return { ...prev, link: newLinks };
    });
  };

  const pickFolder = async () => {
    try {
      const res = await axios.post('http://127.0.0.1:8000/api/config/pick_folder');
      if (res.data && res.data.path) {
        handleChange('path', res.data.path);
      }
    } catch (e) {
      console.error(e);
      alert('弹出选择框失败！');
    }
  };

  // 前端URL类型解析 (移植自 validators.py)
  const parseUrlType = (url: string): { label: string; color: string } | null => {
    if (!url || url.startsWith('#')) return null;
    try {
      if (url.includes('v.douyin.com')) return { label: '短链', color: '#94a3b8' };
      const u = new URL(url);
      const path = u.pathname;
      if (path.includes('/video/')) return { label: '单视频', color: '#22c55e' };
      if (url.includes('modal_id=')) return { label: '搜索视频', color: '#a78bfa' };
      if (path.includes('/user/')) return { label: '用户主页', color: '#38bdf8' };
      if (path.includes('/note/') || path.includes('/gallery/') || path.includes('/slides/')) return { label: '图集', color: '#fb923c' };
      if (path.includes('/collection/') || path.includes('/mix/')) return { label: '合集', color: '#f472b6' };
      if (path.includes('/music/')) return { label: '音乐', color: '#facc15' };
      if (url.includes('douyin.com')) return { label: '未知类型', color: '#94a3b8' };
    } catch { /* 非法URL */ }
    return null;
  };

  if (loading || !config) {
    return <div style={{ color: '#94a3b8' }}>正在加载神枢配置...</div>;
  }

  // 模式选项（带中文注释）
  const modeOptions = [
    { key: 'post', label: '作品' },
    { key: 'like', label: '喜欢' },
    { key: 'mix', label: '合集' },
    { key: 'allmix', label: '全合集' },
    { key: 'collect', label: '收藏' },
    { key: 'collectmix', label: '收藏合集' },
    { key: 'music', label: '音乐' },
    { key: 'search', label: '搜索' },
  ];

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '20px', width: '100%', paddingBottom: '40px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', position: 'sticky', top: 0, zIndex: 10, background: 'var(--bg-color)', padding: '16px 0', borderBottom: '1px solid rgba(255,255,255,0.05)' }}>
        <p style={{ color: '#94a3b8', margin: 0 }}>改动将立刻覆盖底层的 `config.yml` 并且在下次调度时生效。</p>
        <button 
          className="button-primary" 
          onClick={handleSave}
          disabled={saving}
          style={{ background: saveSuccess ? '#10b981' : undefined }}
        >
          {saving ? <RefreshCw className="spin" size={18} /> : saveSuccess ? <CheckCircle2 size={18} /> : <Save size={18} />}
          {saving ? '保存中...' : saveSuccess ? '已生效' : '全量保存配置'}
        </button>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '24px' }}>
        
        {/* 1. 基础检索设置 - 目标链接 */}
        <div className="glass-panel" style={{ padding: '24px' }}>
          <h3 style={{ marginTop: 0, color: '#00f0ff' }}>1. 目标链接管理 (Links)</h3>
          
          <div className="form-group">
            {(config.link || []).map((url: string, idx: number) => {
              const isIgnored = url.startsWith('#');
              const displayUrl = url.replace(/^#\s*/, '');
              const urlType = parseUrlType(displayUrl);
              return (
                <div key={idx} style={{ display: 'flex', gap: '8px', marginBottom: '8px', opacity: isIgnored ? 0.4 : 1, transition: 'opacity 0.2s', alignItems: 'center' }}>
                  {urlType ? (
                    <span style={{
                      fontSize: '0.75rem',
                      padding: '4px 10px',
                      borderRadius: '4px',
                      background: `${urlType.color}20`,
                      color: urlType.color,
                      border: `1px solid ${urlType.color}40`,
                      whiteSpace: 'nowrap',
                      fontWeight: 600,
                      minWidth: '64px',
                      textAlign: 'center',
                    }}>
                      {urlType.label}
                    </span>
                  ) : (
                    <span style={{ minWidth: '64px' }} />
                  )}
                  <button 
                    onClick={() => toggleIgnoreLink(idx)} 
                    className="button-primary" 
                    style={{ background: 'rgba(255,255,255,0.1)', color: '#fff', padding: '10px' }}
                    title={isIgnored ? "取消忽略" : "忽略此链接"}
                  >
                    {isIgnored ? <EyeOff size={16} /> : <Eye size={16} />}
                  </button>
                  <input 
                    type="text" 
                    value={displayUrl}
                    onChange={(e) => {
                      const newUrl = isIgnored ? '# ' + e.target.value : e.target.value;
                      handleLinkChange(idx, newUrl);
                    }}
                    placeholder="https://v.douyin.com/xxxxxx/"
                    style={{ flex: 1, textDecoration: isIgnored ? 'line-through' : 'none' }}
                  />
                  <button onClick={() => removeLink(idx)} className="button-primary" style={{ background: 'rgba(255,50,50,0.2)', color: '#ff4444', padding: '10px' }}>
                    <Trash2 size={16} />
                  </button>
                </div>
              );
            })}
            <button onClick={addLink} className="button-primary" style={{ alignSelf: 'flex-start', background: 'rgba(0, 240, 255, 0.1)', color: '#00f0ff', padding: '8px 16px', fontSize: '0.9rem', marginTop: '4px' }}>
              <Plus size={16} /> 新增一条下载链接
            </button>
          </div>

          <div className="form-group" style={{ marginTop: '24px' }}>
            <label>统一保存路径 (Path)</label>
            <div style={{ display: 'flex', gap: '8px' }}>
              <input 
                type="text" 
                value={config.path || ''}
                onChange={(e) => handleChange('path', e.target.value)}
                style={{ flex: 1 }}
              />
              <button onClick={pickFolder} className="button-primary" style={{ padding: '10px 16px' }} title="弹出系统目录选择器">
                <FolderSearch size={18} />
              </button>
            </div>
          </div>
        </div>

        {/* 2. 主模式与分发模式开关 */}
        <div className="glass-panel" style={{ padding: '24px' }}>
          <h3 style={{ marginTop: 0, color: '#00f0ff' }}>2. 抓取行为与模式控制 (Modes)</h3>
          
          <div className="form-group">
            <label>全局提取模式集合 (Mode)</label>
            <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap', marginBottom: '20px' }}>
              {modeOptions.map(mode => (
                <label key={mode.key} style={{ display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer' }}>
                  <input 
                    type="checkbox" 
                    className="cyber-checkbox"
                    checked={(config.mode || []).includes(mode.key)}
                    onChange={(e) => {
                      const currentModes = config.mode || [];
                      handleChange('mode', e.target.checked ? [...currentModes, mode.key] : currentModes.filter((m: string) => m !== mode.key));
                    }}
                  /> {mode.label} ({mode.key})
                </label>
              ))}
            </div>
          </div>

          <table style={{ width: '100%', textAlign: 'left', borderCollapse: 'collapse', fontSize: '0.9rem' }}>
            <thead>
              <tr style={{ borderBottom: '1px solid rgba(255,255,255,0.1)' }}>
                <th style={{ paddingBottom: '8px' }}>细分模式</th>
                <th style={{ paddingBottom: '8px' }}>获取限制 (0为无限制)</th>
                <th style={{ paddingBottom: '8px' }}>增量更新</th>
              </tr>
            </thead>
            <tbody>
              {modeOptions.map(mode => (
                <tr key={mode.key} style={{ borderBottom: '1px solid rgba(255,255,255,0.05)' }}>
                  <td style={{ padding: '8px 0' }}>{mode.label} ({mode.key})</td>
                  <td>
                    <input 
                      type="number" 
                      value={config.number?.[mode.key] ?? 0} 
                      onChange={(e) => handleChange(mode.key, parseInt(e.target.value) || 0, 'number')}
                      style={{ padding: '4px 8px', width: '80px', fontSize: '0.9rem' }}
                    />
                  </td>
                  <td>
                    <label style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%' }}>
                      <input 
                        type="checkbox" 
                        className="cyber-checkbox" 
                        checked={config.increase?.[mode.key] || false} 
                        onChange={(e) => handleChange(mode.key, e.target.checked, 'increase')}
                      />
                    </label>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p style={{ fontSize: '0.8rem', color: '#94a3b8', marginTop: '12px' }}>* 增量更新(Increase)开启后，会自动拉取新数据，仅当配置了 SQLite 数据库时有效。</p>
        </div>

        {/* 3. 各种媒体附件和时间过滤 */}
        <div className="glass-panel" style={{ padding: '24px' }}>
          <h3 style={{ marginTop: 0, color: '#00f0ff' }}>3. 媒体附加资源及规则 (Rules)</h3>
          
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px', marginBottom: '24px' }}>
            <label style={{ display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer' }} title="是否下载无水印纯音乐">
              <input type="checkbox" className="cyber-checkbox" checked={config.music ?? true} onChange={(e) => handleChange('music', e.target.checked)} /> 纯音乐下载 (Music)
            </label>
            <label style={{ display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer' }} title="是否下载视频封面图">
              <input type="checkbox" className="cyber-checkbox" checked={config.cover ?? true} onChange={(e) => handleChange('cover', e.target.checked)} /> 封面提取 (Cover)
            </label>
            <label style={{ display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer' }} title="是否下载原作者高清头像">
              <input type="checkbox" className="cyber-checkbox" checked={config.avatar ?? true} onChange={(e) => handleChange('avatar', e.target.checked)} /> 作者头像 (Avatar)
            </label>
            <label style={{ display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer' }} title="保留所有视频参数元数据">
              <input type="checkbox" className="cyber-checkbox" checked={config.json ?? true} onChange={(e) => handleChange('json', e.target.checked)} /> 元数据封存 (JSON)
            </label>
            <label style={{ display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer' }} title="是否每个视频独立创建单独文件夹存放附件">
              <input type="checkbox" className="cyber-checkbox" checked={config.folderstyle ?? true} onChange={(e) => handleChange('folderstyle', e.target.checked)} /> 内容成组隔离 (FolderStyle)
            </label>
            <label style={{ display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer', color: '#10b981' }} title="这对于增量更新至关重要">
              <input type="checkbox" className="cyber-checkbox" checked={config.database ?? true} onChange={(e) => handleChange('database', e.target.checked)} /> DB记录归档 (Database)
            </label>
          </div>

          <div style={{ display: 'flex', gap: '16px', marginBottom: '20px' }}>
            <div className="form-group" style={{ flex: 1, margin: 0 }}>
              <label>视频开始时间限定 (start_time)</label>
              <input type="text" placeholder="YYYY-MM-DD" value={config.start_time || ''} onChange={(e) => handleChange('start_time', e.target.value)} />
            </div>
            <div className="form-group" style={{ flex: 1, margin: 0 }}>
              <label>视频终止时间限定 (end_time)</label>
              <input type="text" placeholder="YYYY-MM-DD" value={config.end_time || ''} onChange={(e) => handleChange('end_time', e.target.value)} />
            </div>
          </div>

          <div style={{ display: 'flex', gap: '16px' }}>
            <div className="form-group" style={{ flex: 1, margin: 0 }}>
              <label>多线程加速控制 (Thread)</label>
              <input type="number" value={config.thread || 5} onChange={(e) => handleChange('thread', parseInt(e.target.value) || 1)} />
            </div>
            <div className="form-group" style={{ flex: 1, margin: 0 }}>
              <label>报错自动重连频数 (Retry)</label>
              <input type="number" value={config.retry_times || 3} onChange={(e) => handleChange('retry_times', parseInt(e.target.value) || 0)} />
            </div>
          </div>
        </div>
        
        {/* 4. 平台反爬与代理 */}
        <div className="glass-panel" style={{ padding: '24px' }}>
          <h3 style={{ marginTop: 0, color: '#facc15' }}>4. 逆向与网络安全 (Network & Fallback)</h3>

          <div className="form-group">
            <label>透明网络代理 (Proxy) - 例如 http://127.0.0.1:7890</label>
            <input 
              type="text" 
              value={config.proxy || ''}
              placeholder="留空为直连"
              onChange={(e) => handleChange('proxy', e.target.value)}
            />
          </div>

          {[
            {key: 'msToken', req: true}, 
            {key: 'ttwid', req: true}, 
            {key: 'odin_tt', req: false}, 
            {key: 'passport_csrf_token', req: false}, 
            {key: 'sid_guard', req: false}
          ].map(cookie => (
            <div className="form-group" style={{ marginBottom: '12px' }} key={cookie.key}>
              <label>{cookie.key} {cookie.req && <span style={{color:'#f87171'}}>*必填建议</span>}</label>
              <input 
                type="text" 
                value={config.cookies?.[cookie.key] || ''}
                placeholder={`抓取浏览器真实 Cookie 中的 ${cookie.key} 字段`}
                onChange={(e) => handleChange(cookie.key, e.target.value, 'cookies')}
                style={{ fontSize: '0.8rem', padding: '8px 12px' }}
              />
            </div>
          ))}

          <div style={{ marginTop: '20px', padding: '16px', background: 'rgba(255, 255, 255, 0.03)', borderRadius: '8px' }}>
            <h4 style={{ margin: '0 0 12px 0' }}>浏览器降级无头反爬保护 (Browser Fallback)</h4>
            <label style={{ display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer', marginBottom: '8px' }}>
              <input type="checkbox" className="cyber-checkbox" checked={config.browser_fallback?.enabled ?? true} onChange={(e) => handleChange('enabled', e.target.checked, 'browser_fallback')} /> 启用网页驱动组件 (Playwright)
            </label>
            <label style={{ display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer' }}>
              <input type="checkbox" className="cyber-checkbox" checked={config.browser_fallback?.headless ?? false} onChange={(e) => handleChange('headless', e.target.checked, 'browser_fallback')} /> 无头模式 (隐藏浏览器窗口) 推荐用于服务器
            </label>
          </div>
        </div>

        {/* 5. 语音AI配置 */}
        <div className="glass-panel" style={{ padding: '24px' }}>
          <h3 style={{ marginTop: 0, color: '#c084fc' }}>5. 基于多模态的离线服务 (Whisper)</h3>
          {config.local_whisper && (
            <>
              <div className="form-group">
                <label>算法推演模型规格 (Model)</label>
                <select 
                  value={config.local_whisper.model || 'turbo'}
                  onChange={(e) => handleChange('model', e.target.value, 'local_whisper')}
                  style={{ background: 'linear-gradient(90deg, rgba(147, 51, 234, 0.2), rgba(0,0,0,0.5))', border: '1px solid rgba(147, 51, 234, 0.5)' }}
                >
                  <option value="tiny">Tiny (轻量极速架构 约39M)</option>
                  <option value="base">Base (基础架构 约74M)</option>
                  <option value="small">Small (中型架构 约244M)</option>
                  <option value="medium">Medium (大型架构 约769M)</option>
                  <option value="large">Large (深潜分析 约1550M)</option>
                  <option value="turbo">Turbo (平衡最优先 极速精准 推荐!)</option>
                </select>
              </div>
              
              <div style={{ display: 'flex', flexDirection: 'column', gap: '16px', marginTop: '16px' }}>
                <label style={{ display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer' }}>
                  <input type="checkbox" className="cyber-checkbox" checked={config.local_whisper.sc || false} onChange={(e) => handleChange('sc', e.target.checked, 'local_whisper')} /> OpenCC 繁体字典自动转简体 (SC)
                </label>
                <label style={{ display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer' }}>
                  <input type="checkbox" className="cyber-checkbox" checked={config.local_whisper.srt || false} onChange={(e) => handleChange('srt', e.target.checked, 'local_whisper')} /> 并发生成标准时间轴字幕 (.srt 文件)
                </label>
                <label style={{ display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer' }}>
                  <input type="checkbox" className="cyber-checkbox" checked={config.local_whisper.skip_existing || false} onChange={(e) => handleChange('skip_existing', e.target.checked, 'local_whisper')} /> 智能跳过已解构 (存在转录遗存) 的视频资产
                </label>
              </div>
            </>
          )}
        </div>

      </div>
    </div>
  );
}
