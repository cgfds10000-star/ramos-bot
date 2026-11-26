import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Bot, CalendarDays, Download, Fingerprint, Instagram, Link2, List, LoaderCircle, Monitor, Play, Plus, Power, Search, Settings, ShieldCheck, Square, Trash2, UsersRound, Workflow, X } from 'lucide-react';
import { RamosApi, parseCommand, selectSession, sessionBusy, executionActive } from './api.js';
import MacrosPage from './MacrosPage.jsx';
import LogsPage from './LogsPage.jsx';
import ProxyPage from './ProxyPage.jsx';
import {formatLogTime} from './logs.js';
import {appMessage} from './mobile.js';
import {createStateFeed, isStateSnapshot} from './state-sync.js';
import './styles.css';
import './reference.css';
import './macros.css';
import './logs.css';
import './app-layout.css';
import './proxy.css';
import './premium.css';

const api = new RamosApi();
const empty = { sessions: [], accounts: [], macros: [], logs: [], proxy: { enabled: false }, settings: { two_factor_method: 'auto' } };


function IconButton({ label, children, ...props }) {
  return <button className="icon-button" type="button" aria-label={label} title={label} {...props}>{children}</button>;
}

function App() {
  const [state, setState] = useState(empty);
  const [selected, setSelected] = useState(() => localStorage.getItem('ramos.selected') || '');
  const [online, setOnline] = useState(false);
  const [checking, setChecking] = useState(true);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const [toast, setToast] = useState('');
  const [panel, setPanel] = useState(null);
  const [page, setPage] = useState('sessions');
  const [macroSelection, setMacroSelection] = useState([]);
  const [accessLocked, setAccessLocked] = useState(false);
  const [panelPassword, setPanelPassword] = useState('');
  const [filter, setFilter] = useState('');
  const [macroName, setMacroName] = useState('');
  const [macroText, setMacroText] = useState('');
  const session = selectSession(state, selected);
  const busy = sessionBusy(session);
  const sessionLogs = state.logs.filter(item => item.session_id === session?.id || !item.session_id).sort((a, b) => (a.order || a.id) - (b.order || b.id));

  const feed = useRef(null);
  if (!feed.current) feed.current = createStateFeed({
    read: () => api.state(),
    onState: (next, showError) => {
      if (next.csrf) api.csrf = next.csrf;
      setState(next); setOnline(true); setAccessLocked(false); setChecking(false);
      if (showError) setError('');
    },
    onError: (failure, showError) => {
      setOnline(false); setChecking(false);
      if (failure.locked) setAccessLocked(true);
      if (showError) setError(failure.message);
    },
  });
  function refresh(showError = false) { return feed.current.refresh(showError); }

  useEffect(() => {
    refresh(true);
    const timer = setInterval(() => { if (!document.hidden) refresh(); }, 1500);
    const resume = () => { if (!document.hidden) refresh(); };
    const mobileConnected = event => {
      if (event.detail?.session_id) setSelected(event.detail.session_id);
      refresh();
    };
    const mobileState = event => {
      if (!isStateSnapshot(event.detail?.state)) return;
      feed.current.accept(event.detail.state);
      if (event.detail.session_id) setSelected(event.detail.session_id);
    };
    document.addEventListener('visibilitychange', resume);
    window.addEventListener('ramos:session', mobileConnected);
    window.addEventListener('ramos:state', mobileState);
    return () => {
      clearInterval(timer);
      document.removeEventListener('visibilitychange', resume);
      window.removeEventListener('ramos:session', mobileConnected);
      window.removeEventListener('ramos:state', mobileState);
    };
  }, []);

  useEffect(() => {
    const valid = selectSession(state, selected);
    if (valid && selected !== valid.id) setSelected(valid.id);
  }, [state.sessions, selected]);
  useEffect(() => { localStorage.setItem('ramos.selected', selected); }, [selected]);
  useEffect(() => { document.querySelector('.app-shell > main')?.scrollTo({ top: 0, left: 0 }); }, [page]);
  useEffect(() => { if (!toast) return; const timer = setTimeout(() => setToast(''), 3500); return () => clearTimeout(timer); }, [toast]);

  async function act(action, success) {
    if (pending) return;
    setPending(true); setError('');
    try {
      const result = await action();
      await refresh();
      if (success) setToast(success);
      return result;
    } catch (failure) { setError(failure.message); }
    finally { setPending(false); }
  }

  async function clearLogs() {
    if (session) await act(() => api.post('/logs/clear', { session_id: session.id }));
  }

  function exportLogs(entries) {
    const logs = Array.isArray(entries) ? entries : sessionLogs;
    const text = logs.map(item => `[${formatLogTime(item)}] ${item.message}`).join('\n');
    if (appMessage({action:'export', name:'ramos-bot-logs.txt', text})) return;
    const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a'); link.href = url; link.download = 'ramos-bot-logs.txt'; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function openPanel(value) {
    setError('');
    if (value === 'accounts') {
      setPanel(null);
      if (!appMessage({action: 'accounts'})) setToast('Gerencie suas contas pela bolinha de contas no aplicativo.');
      return;
    }
    if (['macros', 'logs', 'proxy', 'settings'].includes(value)) { setPage(value); setPanel(null); return; }
    setPanel(value);
  }

  function downloadJson(value, name) {
    if (appMessage({action:'export', name, text:JSON.stringify(value, null, 2)})) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], {type:'application/json'}));
    const link = document.createElement('a'); link.href = url; link.download = name; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  async function exportDiagnostic() {
    const result = await act(() => api.request(`/diagnostic/${session.id}`));
    if (result) downloadJson(result, 'ramos-diagnostico.json');
  }

  async function saveMacro(event) {
    event.preventDefault();
    let steps;
    try {
      steps = macroText.split('\n').filter(line => line.trim()).map(parseCommand);
      if (!steps.length || steps.some(item => !['buscar', 'perfil', 'seguir', 'curtir'].includes(item.command))) throw new Error('Use apenas buscar, perfil, seguir e curtir, um comando por linha.');
    } catch (failure) { setError(failure.message); return; }
    const result = await act(() => api.post('/macros', { name: macroName, steps }), 'Macro salva.');
    if (result) { setMacroName(''); setMacroText(''); }
  }

  async function saveContinuousExecution(enabled) {
    await act(() => api.post('/settings', {
      two_factor_method: state.settings.two_factor_method || 'auto',
      execution_mode: 'server',
      continuous_execution: enabled,
    }), 'Configuração salva.');
  }

  const title = { proxy: 'Proxy', 'command-macros': 'Sequências de comandos', logs: 'Logs', settings: 'Configurações', menu: 'Sua sessão', delete: 'Remover sessão?' }[panel];

  if (accessLocked) return <div className="panel-access"><div className="access-glow"/><div className="access-brand"><span>R</span>RAMOS BOT</div><form className="access-card" onSubmit={event => { event.preventDefault(); const entered = panelPassword; setPanelPassword(''); act(() => api.post('/access/login', {password:entered})); }}><div className="access-icon"><ShieldCheck size={30}/></div><span className="eyebrow">SEU ESPAÇO DE TRABALHO</span><h1>Bem-vindo ao<br/><span>Ramos Bot.</span></h1><p>Entre para gerenciar suas sessões e contas.</p><label>Senha do painel<input type="password" autoComplete="current-password" placeholder="Digite sua senha" value={panelPassword} onChange={event => setPanelPassword(event.target.value)} required maxLength={200}/></label><button className="primary-button" disabled={pending || !panelPassword}>{pending ? <LoaderCircle className="spin" size={20}/> : <Fingerprint size={20}/>}Entrar no painel</button>{error && <div className="error-banner" role="alert">{error}</div>}<span className="access-footnote"><ShieldCheck size={14}/>Acesso protegido</span></form></div>;

  return <div className={`app-shell ${page === 'sessions' ? 'is-home' : `is-${page}`}`}>
    <header className="header" inert={Boolean(panel)}>
      <a className="brand" href="#" onClick={event => { event.preventDefault(); setPanel(null); setPage('sessions'); }} aria-label="Ramos Bot, início"><span className="brand-mark"><svg viewBox="0 0 48 48" aria-hidden="true"><path d="M5 5h24c20 0 20 26 3 26l14 14H32L14 26h14c9 0 9-11 0-11H15z" fill="currentColor"/><path d="M6 15l10 10v20H6z" fill="#00c8ed"/></svg></span><span>RAMOS <strong>BOT</strong></span></a>
    </header>

    {page === 'macros' ? <MacrosPage sessions={state.sessions} api={api} refresh={refresh} status={state.viralize || {}} pacingProfiles={state.viralize_pacing} online={online} selectedIds={macroSelection} onSelectionChange={setMacroSelection}/> : page === 'logs' ? <LogsPage logs={state.logs} sessions={state.sessions} online={online} checking={checking}/> : page === 'proxy' ? <ProxyPage api={api} proxy={state.proxy} online={online} busy={state.sessions.some(sessionBusy) || Object.values(state.viralize || {}).some(executionActive)} refresh={refresh}/> : page === 'settings' ? <main className="settings-page" aria-label="Configurações">
      <label className="continuous-execution-card" aria-busy={pending}>
        <span>Execução contínua</span>
        <input type="checkbox" role="switch" checked={state.settings.continuous_execution !== false} disabled={!online || checking || pending} onChange={event => saveContinuousExecution(event.target.checked)}/>
      </label>
      {error && <div className="error-banner" role="alert">{error}</div>}
    </main> : <main inert={Boolean(panel)}>
      <section className="welcome"><h1>Bem-vindo, <span>Carlos</span></h1><p>Gerencie suas sessões, contas, proxy e macros.</p></section>
      <section className="stats" aria-label="Resumo do Ramos Bot">
        <button onClick={() => { setPage('sessions'); setPanel(null); }}><Monitor/><span>Sessões</span><strong>{state.sessions.length}</strong></button>
        <button onClick={() => openPanel('accounts')}><Link2/><span>Contas abertas</span><strong>{state.sessions.filter(item => item.authenticated).length}</strong></button>
        <button onClick={() => openPanel('proxy')}><ShieldCheck/><span>Proxy</span><strong className="stat-text">{state.proxy.enabled ? 'Ativado' : 'Desativado'}</strong></button>
        <button onClick={() => openPanel('macros')}><Play/><span>Macros</span><strong>{state.macros.length}</strong></button>
      </section>

      <section className="sessions-home">
        <div className="sessions-heading"><h2><Monitor size={23}/>Minhas Sessões <span>({state.sessions.length})</span></h2><span className={`connection ${online ? 'is-online' : ''}`}><i/>{checking ? 'Conectando' : online ? 'Online' : 'Offline'}</span></div>
        <label className="session-search"><Search size={20}/><input aria-label="Buscar sessão" placeholder="Buscar sessão..." value={filter} onChange={event => setFilter(event.target.value)}/></label>
        <div className="session-list">{state.sessions.filter(item => item.name.toLowerCase().includes(filter.toLowerCase())).map(item => <div className="session-row" key={item.id}>
          <button className="session-open" onClick={() => setSelected(item.id)} aria-pressed={selected === item.id}><strong>{item.name}</strong><span>{item.authenticated ? <><Instagram size={14}/>@{state.accounts.find(value => value.id === item.account_id)?.username}</> : <><CalendarDays size={14}/>{item.created_at ? 'Criada em ' + new Date(item.created_at).toLocaleDateString('pt-BR') + ' às ' + new Date(item.created_at).toLocaleTimeString('pt-BR',{hour:'2-digit',minute:'2-digit'}) : 'Pronta para conectar'}</>}</span></button>
          <span className={`session-pill ${item.authenticated ? 'active' : sessionBusy(item) ? 'working' : ''}`}><i/>{item.authenticated ? 'Ativa' : sessionBusy(item) ? (item.status === 'awaiting_input' ? '2FA' : 'Em uso') : 'Pronta'}</span>
        </div>)}{filter && !state.sessions.some(item => item.name.toLowerCase().includes(filter.toLowerCase())) && <p className="search-empty">Nenhuma sessão encontrada.</p>}</div>

        {error && <div className="error-banner" role="alert">{error}</div>}
      </section>


    </main>}

    <nav className="bottom-nav" aria-label="Ferramentas" inert={Boolean(panel)}><button className={!panel && page === 'sessions' ? 'active' : ''} aria-current={!panel && page === 'sessions' ? 'page' : undefined} onClick={() => { setPanel(null); setPage('sessions'); }}><Monitor size={24}/><span>Sessões</span></button><button className={!panel && page === 'proxy' ? 'active' : ''} aria-current={!panel && page === 'proxy' ? 'page' : undefined} onClick={() => openPanel('proxy')}><ShieldCheck size={24}/><span>Proxy</span></button><button className={!panel && page === 'macros' ? 'active' : ''} aria-current={!panel && page === 'macros' ? 'page' : undefined} onClick={() => openPanel('macros')}><Bot size={24}/><span>Macros</span></button><button className={!panel && page === 'logs' ? 'active' : ''} aria-current={!panel && page === 'logs' ? 'page' : undefined} onClick={() => openPanel('logs')}><List size={24}/><span>Logs</span></button><button className={!panel && page === 'settings' ? 'active' : ''} aria-current={!panel && page === 'settings' ? 'page' : undefined} onClick={() => openPanel('settings')}><Settings size={24}/><span>Config.</span></button></nav>

    {panel && <div className="modal-backdrop" onClick={event => { if (event.target === event.currentTarget) setPanel(null); }} onKeyDown={event => { if (event.key === 'Escape') setPanel(null); }}><section className="modal" role="dialog" aria-modal="true" aria-labelledby="modal-title" tabIndex={-1} ref={node => { if (node && !node.contains(document.activeElement)) node.focus(); }}><div className="modal-header"><div><span className="eyebrow">RAMOS BOT</span><h2 id="modal-title">{title}</h2></div><IconButton label="Fechar" onClick={() => setPanel(null)}><X size={22}/></IconButton></div>
      {panel === 'command-macros' && <><p className="panel-intro">Salve uma sequência de comandos. Ela só será executada quando você tocar em Iniciar.</p><div className="item-list">{state.macros.map(item => <div className="list-item" key={item.id}><span className="avatar"><Workflow size={20}/></span><div><strong>{item.name}</strong><small>{item.steps.length} comando{item.steps.length > 1 ? 's' : ''}</small></div><button className="text-button" disabled={!online || !session?.authenticated || busy || pending} onClick={async () => { const result = await act(() => api.post(`/sessions/${session.id}/macro`, { macro_id: item.id })); if (result) setPanel(null); }}>Iniciar</button><IconButton label={'Excluir macro ' + item.name} disabled={!online || pending} onClick={() => act(() => api.remove(`/macros/${item.id}`))}><Trash2 size={16}/></IconButton></div>)}{!state.macros.length && <div className="empty-state"><Workflow size={27}/><strong>Sua primeira macro</strong><p>Organize seus comandos em uma sequência.</p></div>}</div><form className="panel-form" onSubmit={saveMacro}><label>Nome<input value={macroName} onChange={event => setMacroName(event.target.value)} placeholder="Consultar perfis" maxLength={60} required/></label><label>Comandos<textarea rows={4} value={macroText} onChange={event => setMacroText(event.target.value)} placeholder={'perfil usuario\nbuscar "nome" --limite 5'} required/></label><span className="form-note">Um comando por linha, até 20 comandos.</span><button className="primary-button" disabled={!online || pending}><Plus size={17}/>Salvar macro</button></form></>}
      {panel === 'menu' && <div className="menu-list">{session && (busy || session.authenticated) && <button onClick={async () => { const result = await act(() => api.post(`/sessions/${session.id}/${busy ? 'stop' : 'disconnect'}`)); if (result) setPanel(null); }} disabled={!online || pending}>{busy ? <Square size={19}/> : <Power size={19}/>}<span>{busy ? 'Interromper' : 'Desconectar'} {session.name}</span></button>}<button onClick={async () => { const result = await act(() => api.post('/sessions')); if (result) { setPage('sessions'); setSelected(result.session_id); setPanel(null); } }} disabled={!online || pending}><Plus size={19}/>Nova sessão</button><button onClick={() => openPanel('accounts')}><UsersRound size={19}/>Minhas contas</button><button onClick={() => openPanel('command-macros')}><Workflow size={19}/>Sequências de comandos</button><button onClick={exportDiagnostic} disabled={!session?.account_id}><ShieldCheck size={19}/>Exportar diagnóstico</button><button onClick={exportLogs} disabled={!sessionLogs.length}><Download size={19}/>Exportar logs</button><button onClick={async () => { await clearLogs(); setPanel(null); }} disabled={!session || !online || pending}><Trash2 size={19}/>Limpar logs</button><button className="danger" onClick={() => setPanel('delete')} disabled={!session || !online || pending}><X size={19}/>Remover sessão</button></div>}
      {panel === 'delete' && <><p className="panel-intro">A atividade de {session?.name} será encerrada. A conta continuará na sua lista.</p><button className="primary-button danger-button" disabled={pending} onClick={async () => { const result = await act(() => api.remove(`/sessions/${session.id}`), 'Sessão removida.'); if (result) setPanel(null); }}><Trash2 size={17}/>Remover sessão</button></>}
      {error && <div className="error-banner modal-error" role="alert">{error}</div>}
    </section></div>}
    {toast && <div className="toast" role="status"><ShieldCheck size={17}/>{toast}</div>}
  </div>;
}

createRoot(document.getElementById('root')).render(<App/>);
