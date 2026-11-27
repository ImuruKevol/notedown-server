import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Activity, ArrowDownToLine, ArrowRight, ArrowUpRight, Check, Clock3, Database, FileText, GitBranch, GitCommitHorizontal, History, Image, KeyRound, LoaderCircle, Monitor, Paperclip, RefreshCw, RotateCcw, Search, Server, Settings2, ShieldCheck, Trash2, X } from 'lucide-react';
import './admin-panel.css';

const TABS = [
  ['overview', '전체 현황', Activity], ['clients', '연결된 기기', Monitor],
  ['tokens', '액세스 토큰', KeyRound], ['history', '변경 이력', History], ['settings', '설정', Settings2],
];
const ERRORS = {
  auth_update_not_supported: '환경변수로 설정된 계정입니다. 서버 설정에서 변경해 주세요.',
  invalid_current_password: '현재 비밀번호가 올바르지 않습니다.',
  username_required: '관리자 아이디를 입력해 주세요.',
  password_too_short: '새 비밀번호는 8자 이상으로 입력해 주세요.',
  password_confirmation_mismatch: '새 비밀번호와 확인 값이 일치하지 않습니다.',
};
const messageOf = error => ERRORS[error.message] || error.message || '요청을 처리하지 못했습니다. 다시 시도해 주세요.';
const encodePath = path => path.split('/').map(encodeURIComponent).join('/');
const fileTitle = file => file?.note?.title || file?.noteSnapshot?.title || file?.deletedNote?.title || file?.relativePath?.split('/').pop() || '제목 없는 문서';
const fileFolder = file => file?.note?.folder || file?.note?.workspaceName || file?.note?.workspace || file?.relativePath?.split('/').slice(0, -1).join('/') || '기본 폴더';
const attachmentTitle = file => file?.fileName || file?.attachment?.fileName || file?.relativePath?.split('/').pop() || '첨부 파일';
const bytes = content => Uint8Array.from(atob(content || ''), character => character.charCodeAt(0));
const decode = content => new TextDecoder().decode(bytes(content));
const date = value => {
  if (!value) return '기록 없음';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString('ko-KR', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
};
const size = value => {
  const number = Number(value || 0);
  if (number < 1024) return `${number.toLocaleString()} B`;
  if (number < 1024 ** 2) return `${(number / 1024).toFixed(1)} KB`;
  if (number < 1024 ** 3) return `${(number / 1024 ** 2).toFixed(1)} MB`;
  return `${(number / 1024 ** 3).toFixed(1)} GB`;
};
const connectionValue = (record, keys) => {
  for (const object of [record.connectionInfo, record.clientInfo]) {
    for (const key of keys) if (object?.[key]) return String(object[key]);
  }
  return '';
};

function EmptyState({ icon: Icon = FileText, title, children }) {
  return <div className="ap-empty"><div className="ap-empty-icon"><Icon size={24} strokeWidth={1.5} /></div><strong>{title}</strong>{children && <p>{children}</p>}</div>;
}

function Modal({ title, description, children, onClose, busy = false, wide = false }) {
  const ref = useRef(null);
  const titleId = React.useId();
  const descriptionId = React.useId();
  useEffect(() => {
    const element = ref.current;
    const previous = document.activeElement;
    element.showModal();
    return () => { element.close(); if (previous instanceof HTMLElement && previous.isConnected) previous.focus(); };
  }, []);
  return <dialog ref={ref} className={`ap-modal${wide ? ' ap-modal-wide' : ''}`} aria-labelledby={titleId} aria-describedby={description ? descriptionId : undefined}
    onCancel={event => { event.preventDefault(); if (!busy) onClose(); }}
    onClick={event => { if (event.target === event.currentTarget && !busy) onClose(); }}>
    <div className="ap-modal-inner">
      <header className="ap-modal-heading"><div><h2 id={titleId}>{title}</h2>{description && <p id={descriptionId}>{description}</p>}</div><button type="button" className="icon-btn" aria-label="닫기" onClick={onClose} disabled={busy}><X size={19} /></button></header>
      {children}
    </div>
  </dialog>;
}

function ConnectionDetails({ record }) {
  const ip = connectionValue(record, ['ipAddress', 'ip', 'ipv4', 'remoteAddress']);
  const forwarded = connectionValue(record, ['forwardedFor']);
  const agent = connectionValue(record, ['userAgent', 'ua']);
  return <div className="ap-cell-stack"><span className="ap-mono">{ip || 'IP 정보 없음'}</span>{forwarded && <span className="ap-muted ap-small">전달 IP · {forwarded}</span>}{agent && <span className="ap-muted ap-small ap-agent" title={agent}>{agent}</span>}</div>;
}

export default function AdminPanel({ api, notify, onDocumentsChanged, onAccountChanged }) {
  const apiRef = useRef(api);
  const notifyRef = useRef(notify);
  apiRef.current = api;
  notifyRef.current = notify;
  const [tab, setTab] = useState('overview');
  const [manifest, setManifest] = useState(null);
  const [tokens, setTokens] = useState([]);
  const [account, setAccount] = useState(null);
  const [health, setHealth] = useState(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [selectedPath, setSelectedPath] = useState('');
  const [query, setQuery] = useState('');
  const [folder, setFolder] = useState('');
  const [history, setHistory] = useState([]);
  const [selectedCommit, setSelectedCommit] = useState('');
  const [preview, setPreview] = useState('');
  const [historyLoading, setHistoryLoading] = useState(false);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [historyError, setHistoryError] = useState('');
  const [modal, setModal] = useState(null);
  const [busy, setBusy] = useState(false);
  const [modalError, setModalError] = useState('');
  const [resetConfirmation, setResetConfirmation] = useState('');
  const [attachmentView, setAttachmentView] = useState(null);
  const [accountForm, setAccountForm] = useState({ username: '', currentPassword: '', password: '', confirmPassword: '' });
  const [accountError, setAccountError] = useState('');
  const historyRequest = useRef(0);
  const previewRequest = useRef(0);
  const attachmentRequest = useRef(0);
  const attachmentUrl = useRef(null);

  const loadData = useCallback(async () => {
    setLoading(true);
    setLoadError('');
    const results = await Promise.allSettled([
      apiRef.current('/api/manifest'), apiRef.current('/api/admin/tokens'),
      apiRef.current('/api/admin/account'), apiRef.current('/api/health'),
    ]);
    const errors = [];
    if (results[0].status === 'fulfilled') setManifest(results[0].value); else errors.push(messageOf(results[0].reason));
    if (results[1].status === 'fulfilled') setTokens(results[1].value.tokens || []); else errors.push(messageOf(results[1].reason));
    if (results[2].status === 'fulfilled') {
      setAccount(results[2].value);
      setAccountForm(previous => ({ ...previous, username: results[2].value.username || '' }));
    } else errors.push(messageOf(results[2].reason));
    setHealth(results[3].status === 'fulfilled' && results[3].value.status === 'ok');
    setLoadError([...new Set(errors)].join(' · '));
    setLoading(false);
    return results[0].status === 'fulfilled' ? results[0].value : null;
  }, []);

  useEffect(() => { loadData(); return () => { historyRequest.current++; previewRequest.current++; attachmentRequest.current++; if (attachmentUrl.current) URL.revokeObjectURL(attachmentUrl.current); }; }, [loadData]);

  const files = manifest?.files || [];
  const activeFiles = files.filter(file => !file.deleted);
  const attachments = (manifest?.attachments || []).filter(file => !file.deleted);
  const selectedFile = files.find(file => file.relativePath === selectedPath);
  const selectedAttachments = attachments.filter(file => file.noteRelativePath === selectedPath);
  const clients = Object.entries(manifest?.clients || {}).sort((a, b) => new Date(b[1].lastSeenAt || b[1].lastSyncAt || 0) - new Date(a[1].lastSeenAt || a[1].lastSyncAt || 0));
  const folders = [...new Set(files.map(fileFolder))].sort((a, b) => a.localeCompare(b, 'ko'));
  const filteredFiles = files.filter(file => (!folder || folder === fileFolder(file)) && `${fileTitle(file)} ${file.relativePath}`.toLowerCase().includes(query.toLowerCase())).sort((a, b) => fileTitle(a).localeCompare(fileTitle(b), 'ko'));
  const recentFiles = [...activeFiles].sort((a, b) => new Date(b.serverUpdatedAt || 0) - new Date(a.serverUpdatedAt || 0)).slice(0, 5);
  const documentsSize = activeFiles.reduce((sum, file) => sum + Number(file.size || 0), 0);
  const attachmentsSize = attachments.reduce((sum, file) => sum + Number(file.size || 0), 0);
  const totalSize = documentsSize + attachmentsSize;
  const syncTimes = clients.map(([, client]) => Date.parse(client.lastSyncAt)).filter(Number.isFinite);
  const lastSync = syncTimes.length ? Math.max(...syncTimes) : null;
  const accountEditable = account?.editable !== false && account?.source !== 'environment';

  async function selectFile(file) {
    const request = ++historyRequest.current;
    const previewId = ++previewRequest.current;
    setSelectedPath(file.relativePath);
    setSelectedCommit('');
    setHistory([]);
    setHistoryError('');
    setPreview('');
    setHistoryLoading(true);
    setPreviewLoading(true);
    const [historyResult, contentResult] = await Promise.allSettled([
      apiRef.current(`/api/admin/files/${encodePath(file.relativePath)}/history`),
      file.deleted ? Promise.resolve({ deleted: true }) : apiRef.current(`/api/files/${encodePath(file.relativePath)}`),
    ]);
    if (request !== historyRequest.current) return;
    if (historyResult.status === 'fulfilled') setHistory(historyResult.value.commits || []);
    else setHistoryError(messageOf(historyResult.reason));
    if (previewId === previewRequest.current) {
      if (contentResult.status === 'fulfilled') setPreview(contentResult.value.deleted ? '삭제된 문서입니다. 변경 이력에서 이전 버전을 확인하고 복원할 수 있습니다.' : decode(contentResult.value.content));
      else setPreview(messageOf(contentResult.reason));
      setPreviewLoading(false);
    }
    setHistoryLoading(false);
  }

  async function selectVersion(commit) {
    const request = ++previewRequest.current;
    setSelectedCommit(commit.commit);
    setPreviewLoading(true);
    try {
      const result = await apiRef.current(`/api/admin/files/${encodePath(selectedPath)}/history/${encodeURIComponent(commit.commit)}`);
      if (request === previewRequest.current) setPreview(result.deleted ? '이 버전에서는 문서가 삭제된 상태입니다. 이 버전으로 복원하면 현재 문서가 삭제됩니다.' : decode(result.content));
    } catch (error) { if (request === previewRequest.current) setPreview(messageOf(error)); }
    finally { if (request === previewRequest.current) setPreviewLoading(false); }
  }

  function openModal(value) { setModalError(''); setResetConfirmation(''); setModal(value); }
  function closeModal() {
    if (busy) return;
    attachmentRequest.current++;
    if (attachmentUrl.current) { URL.revokeObjectURL(attachmentUrl.current); attachmentUrl.current = null; }
    setAttachmentView(null);
    setModal(null);
  }

  async function runAction() {
    setBusy(true);
    setModalError('');
    try {
      if (modal.type === 'revoke') {
        await apiRef.current(`/api/admin/tokens/${encodeURIComponent(modal.token.id)}`, { method: 'DELETE' });
        notifyRef.current('토큰을 무효화했습니다.');
      } else if (modal.type === 'rollback') {
        await apiRef.current(`/api/admin/files/${encodePath(selectedPath)}/rollback`, { method: 'POST', body: { commit: selectedCommit } });
        notifyRef.current('선택한 버전으로 문서를 복원했습니다.');
      } else if (modal.type === 'reset') {
        if (resetConfirmation !== 'RESET') return;
        await apiRef.current('/api/admin/storage/reset', { method: 'POST', body: { confirm: 'RESET' } });
        historyRequest.current++;
        previewRequest.current++;
        setSelectedPath(''); setHistory([]); setSelectedCommit(''); setPreview('');
        notifyRef.current('저장소를 초기화했습니다.');
      }
      const completedAction = modal.type;
      setModal(null);
      const refreshed = await loadData();
      if (completedAction === 'rollback') {
        const restoredFile = refreshed?.files?.find(file => file.relativePath === selectedPath);
        if (restoredFile) await selectFile(restoredFile);
      }
      if (completedAction !== 'revoke') {
        try { await onDocumentsChanged?.(); }
        catch (error) { notifyRef.current(`변경 사항은 저장되었지만 문서 목록을 새로고침하지 못했습니다. ${messageOf(error)}`, 'error'); }
      }
    } catch (error) { setModalError(messageOf(error)); }
    finally { setBusy(false); }
  }

  async function viewAttachment(attachment) {
    const request = ++attachmentRequest.current;
    setAttachmentView(null);
    openModal({ type: 'attachment', attachment });
    try {
      const result = await apiRef.current(`/api/attachments/${encodePath(attachment.relativePath)}`);
      if (request !== attachmentRequest.current) return;
      if (attachmentUrl.current) URL.revokeObjectURL(attachmentUrl.current);
      const url = URL.createObjectURL(new Blob([bytes(result.content)], { type: result.mimeType || 'application/octet-stream' }));
      attachmentUrl.current = url;
      setAttachmentView({ ...result, url, text: /^text\//.test(result.mimeType || '') || /\.(md|txt|json)$/i.test(attachmentTitle(result)) || result.mimeType === 'application/json' ? decode(result.content) : null });
    } catch (error) { if (request === attachmentRequest.current) setModalError(messageOf(error)); }
  }

  async function saveAccount(event) {
    event.preventDefault();
    setAccountError('');
    if (accountForm.password !== accountForm.confirmPassword) { setAccountError(ERRORS.password_confirmation_mismatch); return; }
    setBusy(true);
    try {
      const result = await apiRef.current('/api/admin/account', { method: 'POST', body: { ...accountForm, username: accountForm.username.trim() } });
      setAccount(result);
      onAccountChanged?.(result);
      setAccountForm({ username: result.username || '', currentPassword: '', password: '', confirmPassword: '' });
      notifyRef.current('관리자 계정을 변경했습니다.');
    } catch (error) { setAccountError(messageOf(error)); }
    finally { setBusy(false); }
  }

  function openHistory(file) { setTab('history'); selectFile(file); }

  return <section className="admin-panel">
    <header className="ap-page-heading"><div><span className="ap-eyebrow">WORKSPACE ADMIN</span><h1>서버 관리</h1><p>워크스페이스의 연결 상태와 데이터를 한곳에서 관리하세요.</p></div><div className="ap-heading-actions"><span className={`ap-status ${health === false ? 'ap-status-offline' : health === null ? 'ap-status-pending' : ''}`}><span />{health === null ? '연결 확인 중' : health ? '서버 정상' : '서버 연결 확인 필요'}</span><button type="button" className="btn ghost" onClick={loadData} disabled={loading}><RefreshCw size={15} className={loading ? 'ap-spin' : ''} /><span>새로고침</span></button></div></header>
    <nav className="ap-tabs" aria-label="관리 메뉴">{TABS.map(([id, label, Icon]) => <button type="button" key={id} className={tab === id ? 'is-active' : ''} aria-current={tab === id ? 'page' : undefined} onClick={() => setTab(id)}><Icon size={16} />{label}{id === 'clients' && clients.length > 0 && <span className="ap-tab-count">{clients.length}</span>}</button>)}</nav>
    {loadError && <div className="ap-alert" role="alert"><span>{loadError}</span><button className="ap-text-button" onClick={loadData}>다시 시도</button></div>}
    {loading && !manifest ? <div className="ap-initial-loading" role="status"><LoaderCircle size={24} className="ap-spin" />서버 정보를 불러오고 있습니다.</div> : !manifest ? <EmptyState icon={Server} title="서버 정보를 불러오지 못했습니다">새로고침하면 연결을 다시 확인합니다.</EmptyState> : <>
      {tab === 'overview' && <div className="ap-tab-content">
        <div className="ap-metric-grid">
          {[
            { label: '전체 문서', value: activeFiles.length.toLocaleString(), suffix: '개', detail: `삭제된 문서 ${files.length - activeFiles.length}개`, icon: FileText, theme: 'purple' },
            { label: '첨부 파일', value: attachments.length.toLocaleString(), suffix: '개', detail: `총 ${size(attachmentsSize)}`, icon: Paperclip, theme: 'blue' },
            { label: '연결된 기기', value: clients.length.toLocaleString(), suffix: '대', detail: '서버에 등록된 클라이언트', icon: Monitor, theme: 'green' },
            { label: '서버 리비전', value: (manifest?.serverRevision ?? 0).toLocaleString(), suffix: '', detail: '현재 동기화 버전', icon: GitBranch, theme: 'orange' },
          ].map(({ label, value, suffix, detail, icon: Icon, theme }) => <article key={label} className="ap-metric"><div className="ap-metric-top"><span>{label}</span><span className={`ap-metric-icon ${theme}`}><Icon size={18} /></span></div><div className="ap-metric-value">{value}<span>{suffix}</span></div><p>{detail}</p></article>)}
        </div>
        <div className="ap-overview-grid">
          <section className="ap-card ap-recent-card"><header className="ap-card-heading"><div><h2>최근 변경한 문서</h2><p>문서의 변경 기록과 이전 버전을 확인하세요.</p></div><button className="ap-text-button" onClick={() => setTab('history')}>전체 보기<ArrowRight size={14} /></button></header>{recentFiles.length ? <div className="ap-table-wrap"><table className="ap-table ap-recent-table"><thead><tr><th>문서</th><th>마지막 변경</th><th>리비전</th><th><span className="ap-sr-only">변경 이력</span></th></tr></thead><tbody>{recentFiles.map(file => <tr key={file.relativePath}><td><button className="ap-document-link" onClick={() => openHistory(file)}><span className="ap-document-icon"><FileText size={18} /></span><span className="ap-cell-stack"><strong>{fileTitle(file)}</strong><span className="ap-muted ap-small">{fileFolder(file)}</span></span></button></td><td className="ap-small ap-muted ap-nowrap">{date(file.serverUpdatedAt)}</td><td><span className="ap-revision">r{file.revision ?? 0}</span></td><td><button className="icon-btn" aria-label={`${fileTitle(file)} 변경 이력 보기`} onClick={() => openHistory(file)}><ArrowUpRight size={17} /></button></td></tr>)}</tbody></table></div> : <EmptyState title="아직 문서가 없습니다">새 문서를 작성하거나 앱에서 문서를 동기화해 보세요.</EmptyState>}</section>
          <section className="ap-card ap-storage-card"><header className="ap-card-heading"><div><h2>저장소</h2><p>현재 문서와 첨부 파일의 용량</p></div><Database size={18} className="ap-muted" /></header><div className="ap-storage-total">{size(totalSize)}</div><div className="ap-storage-bar" aria-label={`문서 ${size(documentsSize)}, 첨부 ${size(attachmentsSize)}`}><span style={{ width: `${totalSize ? documentsSize / totalSize * 100 : 0}%` }} /><span style={{ width: `${totalSize ? attachmentsSize / totalSize * 100 : 0}%` }} /></div><div className="ap-storage-legend"><div><i /><span>문서</span><strong>{size(documentsSize)}</strong></div><div><i /><span>첨부 파일</span><strong>{size(attachmentsSize)}</strong></div></div><div className="ap-sync-info"><Clock3 size={17} /><div><span>마지막 클라이언트 동기화</span><strong>{date(lastSync)}</strong></div></div></section>
        </div>
        <section className="ap-system-strip"><span className="ap-system-icon"><ShieldCheck size={23} /></span><div><h3>워크스페이스 연결 관리</h3><p>접속 중인 기기와 발급된 토큰을 확인하고 접근 권한을 관리하세요.</p></div><button className="btn ghost" onClick={() => setTab('tokens')}>액세스 토큰 관리<ArrowRight size={15} /></button></section>
      </div>}

      {tab === 'clients' && <section className="ap-card ap-tab-content"><header className="ap-card-heading"><div><h2>연결된 기기 <span className="ap-heading-count">{clients.length}</span></h2><p>최근 서버와 통신한 클라이언트의 접속 정보입니다.</p></div><Monitor size={20} className="ap-muted" /></header>{clients.length ? <div className="ap-table-wrap"><table className="ap-table"><thead><tr><th>클라이언트 / 사용자</th><th>연결 정보</th><th>최근 활동</th><th>리비전</th></tr></thead><tbody>{clients.map(([id, client]) => <tr key={id}><td><div className="ap-device-name"><span className="ap-device-icon"><Monitor size={19} /></span><div className="ap-cell-stack"><strong className="ap-break">{id}</strong><span className="ap-muted ap-small">{client.user || '사용자 정보 없음'}</span></div></div></td><td><ConnectionDetails record={client} /></td><td><div className="ap-cell-stack ap-small"><span>{date(client.lastSeenAt || client.lastSyncAt || client.lastPlanAt)}</span><span className="ap-muted">동기화 · {date(client.lastSyncAt)}</span></div></td><td><span className="ap-revision">{client.lastSeenRevision == null ? '—' : `r${client.lastSeenRevision}`}</span></td></tr>)}</tbody></table></div> : <EmptyState icon={Monitor} title="연결된 기기가 없습니다">Notedown 앱에서 서버에 연결하면 기기 정보가 표시됩니다.</EmptyState>}</section>}

      {tab === 'tokens' && <section className="ap-card ap-tab-content"><header className="ap-card-heading"><div><h2>액세스 토큰 <span className="ap-heading-count">{tokens.length}</span></h2><p>앱 로그인 시 발급된 토큰입니다. 무효화한 토큰으로는 동기화할 수 없습니다.</p></div><KeyRound size={20} className="ap-muted" /></header>{tokens.length ? <div className="ap-table-wrap"><table className="ap-table"><thead><tr><th>토큰 / 사용자</th><th>연결 정보</th><th>발급 / 최근 사용</th><th>관리</th></tr></thead><tbody>{tokens.map(token => <tr key={token.id}><td><div className="ap-cell-stack"><strong className="ap-mono" title={token.id}>{token.id?.length > 20 ? `${token.id.slice(0, 8)}…${token.id.slice(-6)}` : token.id}</strong><span className="ap-muted ap-small">{token.username || '사용자 정보 없음'}</span><details className="ap-token-details"><summary>전체 토큰 ID</summary><code>{token.id}</code></details></div></td><td><ConnectionDetails record={token} /></td><td><div className="ap-cell-stack ap-small"><span>발급 · {date(token.issuedAt)}</span><span className="ap-muted">사용 · {date(token.lastUsedAt)}</span></div></td><td><button className="ap-revoke" onClick={() => openModal({ type: 'revoke', token })}><Trash2 size={14} />무효화</button></td></tr>)}</tbody></table></div> : <EmptyState icon={KeyRound} title="발급된 토큰이 없습니다">앱에서 로그인하면 발급된 액세스 토큰이 표시됩니다.</EmptyState>}</section>}

      {tab === 'history' && <div className="ap-history-layout ap-tab-content">
        <section className="ap-card ap-file-list"><header className="ap-card-heading"><div><h2>문서 기록</h2><p>삭제한 문서의 이력도 확인할 수 있습니다.</p></div><span className="ap-heading-count">{files.length}</span></header><div className="ap-file-filters"><label className="ap-search"><Search size={16} /><input value={query} onChange={event => setQuery(event.target.value)} aria-label="이력 문서 검색" placeholder="문서 검색…" />{query && <button className="icon-btn" aria-label="검색 지우기" onClick={() => setQuery('')}><X size={14} /></button>}</label><select value={folder} onChange={event => setFolder(event.target.value)} aria-label="이력 폴더 필터"><option value="">모든 폴더</option>{folders.map(name => <option key={name} value={name}>{name}</option>)}</select></div><div className="ap-file-items">{filteredFiles.length ? filteredFiles.map(file => <button key={file.relativePath} className={`ap-file-item${selectedPath === file.relativePath ? ' is-selected' : ''}`} onClick={() => selectFile(file)} aria-pressed={selectedPath === file.relativePath}><FileText size={18} /><span><strong>{fileTitle(file)}</strong><span>{fileFolder(file)} · {size(file.size)}</span></span>{file.deleted ? <span className="ap-pill deleted">삭제됨</span> : <span className="ap-file-revision">r{file.revision ?? 0}</span>}</button>) : <EmptyState icon={Search} title="문서를 찾을 수 없습니다">검색어나 폴더를 변경해 보세요.</EmptyState>}</div></section>
        <div className="ap-history-main">{selectedFile ? <>
          <section className="ap-card"><header className="ap-card-heading"><div><h2 className="ap-break">{fileTitle(selectedFile)}</h2><p className="ap-break">{fileFolder(selectedFile)} · {size(selectedFile.size)} · 현재 리비전 {selectedFile.revision ?? 0}</p></div><span className={`ap-pill${selectedFile.deleted ? ' deleted' : ''}`}>{selectedFile.deleted ? '삭제됨' : '활성'}</span></header><div className="ap-history-heading"><span><GitBranch size={16} />변경 이력 <b>{history.length}</b></span><button className="btn ghost ap-small-button" disabled={!selectedCommit || previewLoading || historyLoading} onClick={() => openModal({ type: 'rollback' })}><RotateCcw size={14} />이 버전으로 복원</button></div>{historyError ? <div className="ap-inline-error" role="alert">{historyError}<button className="ap-text-button" onClick={() => selectFile(selectedFile)}>다시 시도</button></div> : historyLoading ? <div className="ap-loading" role="status"><LoaderCircle className="ap-spin" size={18} />변경 이력 불러오는 중</div> : history.length ? <div className="ap-history-items">{history.map(commit => <button className={`ap-history-item${selectedCommit === commit.commit ? ' is-selected' : ''}`} key={commit.commit} onClick={() => selectVersion(commit)} aria-pressed={selectedCommit === commit.commit}><GitCommitHorizontal size={19} /><span className="ap-history-description"><strong>{commit.message || '문서 변경'}</strong><span>{date(commit.committedAt)} · {commit.author || '작성자 정보 없음'} · {commit.deleted ? '삭제됨' : size(commit.size)}</span></span><code>{commit.shortCommit || commit.commit.slice(0, 7)}</code>{selectedCommit === commit.commit && <Check size={16} />}</button>)}</div> : <div className="ap-quiet-empty">이 문서의 변경 이력이 없습니다.</div>}</section>
          <section className="ap-card ap-preview-card"><header className="ap-card-heading"><div><h2>내용 미리보기</h2><p>{selectedCommit ? `선택한 버전 · ${selectedCommit.slice(0, 8)}` : '현재 저장된 문서'}</p></div>{selectedCommit && <button className="ap-text-button" onClick={() => selectFile(selectedFile)}>현재 버전 보기<ArrowRight size={14} /></button>}</header>{previewLoading ? <div className="ap-loading" role="status"><LoaderCircle size={18} className="ap-spin" />내용을 불러오는 중</div> : <pre className="ap-document-preview" tabIndex={0}>{preview || '내용이 없는 문서입니다.'}</pre>}
          <div className="ap-attachment-section"><h3><Paperclip size={15} />현재 첨부 파일 <span>{selectedAttachments.length}</span></h3>{selectedAttachments.length ? <div className="ap-attachment-list">{selectedAttachments.map(attachment => <button key={attachment.relativePath} className="ap-attachment" onClick={() => viewAttachment(attachment)}>{attachment.mimeType?.startsWith('image/') ? <Image size={18} /> : <Paperclip size={18} />}<span><strong>{attachmentTitle(attachment)}</strong><small>{size(attachment.size)}</small></span><ArrowUpRight size={15} /></button>)}</div> : <p className="ap-muted ap-small">첨부된 파일이 없습니다.</p>}</div></section>
        </> : <section className="ap-card ap-history-placeholder"><EmptyState icon={History} title="문서가 쌓아온 기록">왼쪽에서 문서를 선택하면 변경 이력을 확인하고 이전 버전으로 복원할 수 있습니다.</EmptyState></section>}</div>
      </div>}

      {tab === 'settings' && <div className="ap-settings-layout ap-tab-content"><section className="ap-card ap-account-card"><header className="ap-card-heading"><div><h2>관리자 계정</h2><p>워크스페이스에 로그인할 계정 정보를 관리하세요.</p></div><ShieldCheck size={20} className="ap-muted" /></header>{account ? <form className="ap-account-form" onSubmit={saveAccount}><div className={`ap-account-source${!accountEditable ? ' readonly' : ''}`}><ShieldCheck size={16} /><span>{accountEditable ? '서버에 저장된 관리자 계정입니다.' : '환경변수로 관리하는 계정입니다. 변경하려면 서버의 계정 설정을 수정해 주세요.'}</span></div><fieldset disabled={!accountEditable || busy}><label>관리자 아이디<input name="username" autoComplete="username" required value={accountForm.username} onChange={event => setAccountForm({ ...accountForm, username: event.target.value })} /></label>{accountEditable && <><label>현재 비밀번호<input name="currentPassword" type="password" autoComplete="current-password" required value={accountForm.currentPassword} onChange={event => setAccountForm({ ...accountForm, currentPassword: event.target.value })} placeholder="현재 비밀번호 입력" /></label><div className="ap-password-grid"><label>새 비밀번호<input name="password" type="password" autoComplete="new-password" required minLength={8} value={accountForm.password} onChange={event => setAccountForm({ ...accountForm, password: event.target.value })} placeholder="8자 이상" /></label><label>새 비밀번호 확인<input name="confirmPassword" type="password" autoComplete="new-password" required minLength={8} value={accountForm.confirmPassword} onChange={event => setAccountForm({ ...accountForm, confirmPassword: event.target.value })} placeholder="한 번 더 입력" /></label></div></>}</fieldset>{accountError && <p className="ap-form-error" role="alert">{accountError}</p>}{accountEditable && <div className="ap-form-footer"><span>변경하려면 현재 비밀번호가 필요합니다.</span><button className="btn primary" type="submit" disabled={busy}>{busy ? <LoaderCircle size={16} className="ap-spin" /> : <Check size={16} />}변경 사항 저장</button></div>}</form> : <EmptyState icon={ShieldCheck} title="계정 정보를 불러오지 못했습니다">새로고침해 다시 시도해 주세요.</EmptyState>}</section><aside className="ap-settings-aside"><section className="ap-card"><header className="ap-card-heading"><div><h2>개발자 도구</h2><p>서버의 API를 살펴보세요.</p></div></header><a href="/api/docs" target="_blank" rel="noreferrer" className="ap-api-link"><span className="ap-device-icon"><Server size={19} /></span><span><strong>API 문서</strong><small>엔드포인트와 요청 형식 확인</small></span><ArrowUpRight size={17} /></a></section><section className="ap-card ap-danger-zone"><header className="ap-card-heading"><div><h2>저장소 초기화</h2><p>문서, 첨부 파일과 변경 이력을 삭제합니다. 관리자 계정과 토큰은 유지됩니다.</p></div></header><div className="ap-danger-actions"><button type="button" className="btn danger" onClick={() => openModal({ type: 'reset' })}><Trash2 size={15} />저장소 초기화</button></div></section></aside></div>}
    </>}

    {modal && <Modal title={modal.type === 'reset' ? '저장소를 초기화할까요?' : modal.type === 'revoke' ? '액세스 토큰을 무효화할까요?' : modal.type === 'rollback' ? '이 버전으로 복원할까요?' : attachmentTitle(modal.attachment)} description={modal.type === 'attachment' ? `${modal.attachment.mimeType || '첨부 파일'} · ${size(modal.attachment.size)}` : undefined} onClose={closeModal} busy={busy} wide={modal.type === 'attachment'}>
      {modal.type === 'revoke' && <><div className="ap-modal-notice"><KeyRound size={23} /><p><strong>{modal.token.username || '사용자'}</strong>의 토큰을 무효화하면 해당 토큰을 사용하는 앱은 다시 로그인해야 합니다.</p></div><code className="ap-modal-code">{modal.token.id}</code></>}
      {modal.type === 'rollback' && <div className="ap-modal-notice"><History size={23} /><p><strong>{fileTitle(selectedFile)}</strong> 문서를 <code>{selectedCommit.slice(0, 8)}</code> 버전으로 복원합니다. 변경된 내용은 새로운 이력으로 기록됩니다.{history.find(commit => commit.commit === selectedCommit)?.deleted && <span className="ap-form-error">선택한 버전은 삭제 상태이므로 현재 문서도 삭제됩니다.</span>}</p></div>}
      {modal.type === 'reset' && <><div className="ap-modal-notice ap-reset-notice"><Trash2 size={23} /><p>모든 문서, 첨부 파일, 동기화 정보와 변경 이력이 영구적으로 삭제됩니다. 이 작업은 되돌릴 수 없습니다.</p></div><p className="ap-muted ap-small">관리자 계정과 액세스 토큰은 유지됩니다.</p><label className="ap-reset-label">계속하려면 <strong>RESET</strong>을 입력하세요.<input value={resetConfirmation} onChange={event => setResetConfirmation(event.target.value)} placeholder="RESET" autoComplete="off" spellCheck={false} disabled={busy} /></label></>}
      {modal.type === 'attachment' && <div className="ap-attachment-viewer">{attachmentView ? attachmentView.mimeType?.startsWith('image/') ? <img src={attachmentView.url} alt={attachmentTitle(modal.attachment)} /> : attachmentView.text !== null ? <pre>{attachmentView.text}</pre> : <EmptyState icon={Paperclip} title="파일을 다운로드해 확인하세요">이 형식은 미리보기를 지원하지 않습니다.</EmptyState> : !modalError && <div className="ap-loading" role="status"><LoaderCircle className="ap-spin" size={20} />첨부 파일을 불러오는 중</div>}</div>}
      {modalError && <p className="ap-form-error" role="alert">{modalError}</p>}
      <footer className="ap-modal-actions"><button type="button" className="btn ghost" disabled={busy} onClick={closeModal}>{modal.type === 'attachment' ? '닫기' : '취소'}</button>{modal.type === 'attachment' ? attachmentView && <a className="btn primary" href={attachmentView.url} download={attachmentTitle(modal.attachment)}><ArrowDownToLine size={16} />다운로드</a> : <button type="button" className={`btn ${modal.type === 'rollback' ? 'primary' : 'danger'}`} onClick={runAction} disabled={busy || (modal.type === 'reset' && resetConfirmation !== 'RESET')}>{busy && <LoaderCircle size={15} className="ap-spin" />}{modal.type === 'reset' ? '저장소 초기화' : modal.type === 'revoke' ? '토큰 무효화' : '이 버전으로 복원'}</button>}</footer>
    </Modal>}
  </section>;
}
