import { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import { RefreshCw, Search, ChevronLeft, ChevronRight, Database, User, Video, Image, Music, FolderOpen, Pencil, Check, X, ExternalLink } from 'lucide-react';

interface AwemeItem {
  aweme_id: string;
  aweme_type: string;
  title: string;
  author_id: string;
  author_name: string;
  create_time: number;
  download_time: number;
  file_path: string;
}

interface Stats {
  total_aweme: number;
  total_authors: number;
  total_history: number;
}

const PAGE_SIZE = 20;

// 类型图标映射
const typeConfig: Record<string, { icon: typeof Video; label: string; color: string }> = {
  video: { icon: Video, label: '视频', color: '#22c55e' },
  gallery: { icon: Image, label: '图集', color: '#fb923c' },
  music: { icon: Music, label: '音乐', color: '#facc15' },
};

function formatTime(ts: number): string {
  if (!ts) return '-';
  const d = new Date(ts * 1000);
  return d.toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}

export default function HistoryViewer() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [items, setItems] = useState<AwemeItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [loading, setLoading] = useState(true);
  const [keyword, setKeyword] = useState('');
  const [author, setAuthor] = useState('');
  const [searchInput, setSearchInput] = useState('');
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState('');
  const [authorInput, setAuthorInput] = useState('');

  const fetchStats = async () => {
    try {
      const res = await axios.get('http://127.0.0.1:8000/api/history/stats');
      setStats(res.data);
    } catch { /* 忽略 */ }
  };

  const fetchItems = useCallback(async () => {
    setLoading(true);
    try {
      const res = await axios.get('http://127.0.0.1:8000/api/history/aweme', {
        params: { limit: PAGE_SIZE, offset: page * PAGE_SIZE, keyword, author },
      });
      setItems(res.data.items);
      setTotal(res.data.total);
    } catch {
      setItems([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [page, keyword, author]);

  useEffect(() => {
    fetchStats();
  }, []);

  useEffect(() => {
    fetchItems();
  }, [fetchItems]);

  const handleSearch = () => {
    setKeyword(searchInput);
    setAuthor(authorInput);
    setPage(0);
  };

  const openFolder = async (filePath: string) => {
    try {
      await axios.post('http://127.0.0.1:8000/api/history/open_folder', { file_path: filePath });
    } catch {
      alert('无法打开文件夹，文件可能已被移动或删除');
    }
  };

  const startEdit = (item: AwemeItem) => {
    setEditingId(item.aweme_id);
    setEditingTitle(item.title || '');
  };

  const cancelEdit = () => {
    setEditingId(null);
    setEditingTitle('');
  };

  const saveRename = async () => {
    if (!editingId || !editingTitle.trim()) return;
    try {
      await axios.post('http://127.0.0.1:8000/api/history/rename', {
        aweme_id: editingId,
        new_title: editingTitle.trim(),
      });
      setEditingId(null);
      fetchItems();
    } catch {
      alert('重命名失败');
    }
  };

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const statCards = [
    { label: '已归档作品', value: stats?.total_aweme ?? '-', icon: Database, color: '#00f0ff' },
    { label: '覆盖作者数', value: stats?.total_authors ?? '-', icon: User, color: '#a78bfa' },
    { label: '历史任务数', value: stats?.total_history ?? '-', icon: FolderOpen, color: '#fb923c' },
  ];

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '24px', paddingBottom: '40px' }}>

      {/* 统计卡片 */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '16px' }}>
        {statCards.map((card) => {
          const Icon = card.icon;
          return (
            <div key={card.label} className="glass-panel" style={{ padding: '20px', display: 'flex', alignItems: 'center', gap: '16px' }}>
              <div style={{
                width: '48px', height: '48px', borderRadius: '12px',
                background: `${card.color}15`, display: 'flex', alignItems: 'center', justifyContent: 'center',
              }}>
                <Icon size={24} color={card.color} />
              </div>
              <div>
                <div style={{ fontSize: '0.85rem', color: '#94a3b8' }}>{card.label}</div>
                <div style={{ fontSize: '1.8rem', fontWeight: 700, color: card.color }}>{card.value}</div>
              </div>
            </div>
          );
        })}
      </div>

      {/* 搜索栏 */}
      <div className="glass-panel" style={{ padding: '16px 24px', display: 'flex', gap: '12px', alignItems: 'center' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flex: 1 }}>
          <Search size={18} color="#94a3b8" />
          <input
            type="text"
            placeholder="按标题搜索..."
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
            style={{ flex: 1, fontSize: '0.9rem' }}
          />
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flex: 1 }}>
          <User size={18} color="#94a3b8" />
          <input
            type="text"
            placeholder="按作者筛选..."
            value={authorInput}
            onChange={(e) => setAuthorInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
            style={{ flex: 1, fontSize: '0.9rem' }}
          />
        </div>
        <button className="button-primary" onClick={handleSearch} style={{ padding: '10px 20px' }}>
          <Search size={16} /> 检索
        </button>
        <button
          className="button-primary"
          onClick={() => { fetchStats(); fetchItems(); }}
          style={{ padding: '10px', background: 'rgba(255,255,255,0.1)' }}
          title="刷新数据"
        >
          <RefreshCw size={16} />
        </button>
      </div>

      {/* 数据表格 */}
      <div className="glass-panel" style={{ padding: '0', overflow: 'hidden' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.88rem' }}>
          <thead>
            <tr style={{ borderBottom: '1px solid rgba(255,255,255,0.1)', background: 'rgba(255,255,255,0.03)' }}>
              <th style={{ padding: '14px 16px', textAlign: 'left', width: '60px' }}>类型</th>
              <th style={{ padding: '14px 16px', textAlign: 'left' }}>标题</th>
              <th style={{ padding: '14px 16px', textAlign: 'left', width: '120px' }}>作者</th>
              <th style={{ padding: '14px 16px', textAlign: 'left', width: '150px' }}>下载时间</th>
              <th style={{ padding: '14px 16px', textAlign: 'center', width: '100px' }}>操作</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={6} style={{ padding: '60px', textAlign: 'center', color: '#94a3b8' }}>
                <RefreshCw size={20} className="spin" style={{ marginRight: '8px' }} /> 加载中...
              </td></tr>
            ) : items.length === 0 ? (
              <tr><td colSpan={6} style={{ padding: '60px', textAlign: 'center', color: '#94a3b8' }}>
                {total === 0 ? '暂无下载记录，去控制面板发起一次下载吧' : '未找到匹配的记录'}
              </td></tr>
            ) : items.map((item) => {
              const tc = typeConfig[item.aweme_type] || typeConfig['video'];
              const TypeIcon = tc.icon;
              return (
                <tr key={item.aweme_id} style={{ borderBottom: '1px solid rgba(255,255,255,0.04)', transition: 'background 0.15s' }}
                  onMouseEnter={(e) => (e.currentTarget.style.background = 'rgba(255,255,255,0.03)')}
                  onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
                >
                  <td style={{ padding: '12px 16px' }}>
                    <span style={{
                      display: 'inline-flex', alignItems: 'center', gap: '4px',
                      fontSize: '0.75rem', padding: '3px 8px', borderRadius: '4px',
                      background: `${tc.color}20`, color: tc.color, border: `1px solid ${tc.color}40`,
                    }}>
                      <TypeIcon size={12} /> {tc.label}
                    </span>
                  </td>
                  <td style={{ padding: '12px 16px', maxWidth: '400px' }}>
                    {editingId === item.aweme_id ? (
                      <div style={{ display: 'flex', gap: '6px', alignItems: 'center' }}>
                        <input
                          type="text"
                          value={editingTitle}
                          onChange={(e) => setEditingTitle(e.target.value)}
                          onKeyDown={(e) => { if (e.key === 'Enter') saveRename(); if (e.key === 'Escape') cancelEdit(); }}
                          autoFocus
                          style={{ flex: 1, fontSize: '0.85rem', padding: '4px 8px' }}
                        />
                        <button onClick={saveRename} style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#22c55e', padding: '4px' }} title="确认"><Check size={16} /></button>
                        <button onClick={cancelEdit} style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#f87171', padding: '4px' }} title="取消"><X size={16} /></button>
                      </div>
                    ) : (
                      <span
                        style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', display: 'block', cursor: 'pointer' }}
                        title={`${item.title || '-'}（双击编辑）`}
                        onDoubleClick={() => startEdit(item)}
                      >
                        {item.title || <span style={{ color: '#64748b' }}>无标题</span>}
                      </span>
                    )}
                  </td>
                  <td style={{ padding: '12px 16px', color: '#a78bfa' }}>{item.author_name || '-'}</td>
                  <td style={{ padding: '12px 16px', color: '#94a3b8', fontSize: '0.82rem' }}>
                    {formatTime(item.download_time)}
                  </td>
                  <td style={{ padding: '12px 16px', textAlign: 'center' }}>
                    <div style={{ display: 'flex', gap: '6px', justifyContent: 'center' }}>
                      {item.file_path && (
                        <button
                          onClick={() => openFolder(item.file_path)}
                          style={{ background: 'none', border: '1px solid rgba(255,255,255,0.15)', borderRadius: '4px', cursor: 'pointer', color: '#38bdf8', padding: '4px 8px', display: 'flex', alignItems: 'center', gap: '4px', fontSize: '0.75rem' }}
                          title="在资源管理器中打开"
                        >
                          <ExternalLink size={13} />
                        </button>
                      )}
                      <button
                        onClick={() => startEdit(item)}
                        style={{ background: 'none', border: '1px solid rgba(255,255,255,0.15)', borderRadius: '4px', cursor: 'pointer', color: '#facc15', padding: '4px 8px', display: 'flex', alignItems: 'center', gap: '4px', fontSize: '0.75rem' }}
                        title="重命名"
                      >
                        <Pencil size={13} />
                      </button>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* 分页 */}
      {total > PAGE_SIZE && (
        <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', gap: '16px' }}>
          <button
            className="button-primary"
            style={{ padding: '8px 16px', background: 'rgba(255,255,255,0.08)' }}
            disabled={page === 0}
            onClick={() => setPage(p => p - 1)}
          >
            <ChevronLeft size={16} /> 上一页
          </button>
          <span style={{ color: '#94a3b8', fontSize: '0.9rem' }}>
            {page + 1} / {totalPages}（共 {total} 条）
          </span>
          <button
            className="button-primary"
            style={{ padding: '8px 16px', background: 'rgba(255,255,255,0.08)' }}
            disabled={page + 1 >= totalPages}
            onClick={() => setPage(p => p + 1)}
          >
            下一页 <ChevronRight size={16} />
          </button>
        </div>
      )}
    </div>
  );
}
