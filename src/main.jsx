import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Bot, Bell, CalendarDays, Download, EllipsisVertical, Eye, EyeOff, Fingerprint, Link2, List, LoaderCircle, LockKeyhole, Menu, Monitor, Play, Plus, Power, Radio, Search, Settings, Settings2, ShieldCheck, Square, Trash2, Upload, UsersRound, UserRound, Workflow, X } from 'lucide-react';
import { RamosApi, parseCommand, selectSession, sessionBusy, statusLabel, executionActive } from './api.js';
import VerificationForm from './VerificationForm.jsx';
import MacrosPage from './MacrosPage.jsx';
import LogsPage from './LogsPage.jsx';
import ProxyPage from './ProxyPage.jsx';
import SessionTabs from './SessionTabs.jsx';
import SessionLoginDialog from './SessionLoginDialog.jsx';
import {formatLogTime} from './logs.js';
import './styles.css';
import './reference.css';
import './macros.css';
import './logs.css';
import './app-layout.css';
import './proxy.css';
import './session-login.css';

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
  const [workspaceOpen, setWorkspaceOpen] = useState(false);
  const [accessLocked, setAccessLocked] = useState(false);
  const [panelPassword, setPanelPassword] = useState('');
  const [filter, setFilter] = useState('');
  const [password, setPassword] = useState('');
  const [handle, setHandle] = useState('');
  const [visiblePassword, setVisiblePassword] = useState(false);
  const [macroName, setMacroName] = useState('');
  const [macroText, setMacroText] = useState('');
  const [newAccount, setNewAccount] = useState('');
  const [twoFactor, setTwoFactor] = useState('autenticador');
  const [continuousExecution, setContinuousExecution] = useState(true);
  const poll = useRef(false);
  const visibleChallenge = useRef('');
  const session = selectSession(state, selected);
  const account = state.accounts.find(item => item.id === session?.account_id);
  const busy = sessionBusy(session);
  const sessionLogs = state.logs.filter(item => item.session_id === session?.id || !item.session_id).sort((a, b) => (a.order || a.id) - (b.order || b.id));

  async function refresh(showError = false) {
    if (poll.current) return;
    poll.current = true;
    try {
      const next = await api.state();
      setState(next); setOnline(true);
      setAccessLocked(false);
      if (showError) setError('');
    } catch (failure) {
      setOnline(false);
      if (failure.locked) setAccessLocked(true);
      if (showError) setError(failure.message);
    } finally { poll.current = false; setChecking(false); }
  }

  useEffect(() => {
    refresh(true);
    const timer = setInterval(() => { if (!document.hidden) refresh(); }, 1500);
    const resume = () => { if (!document.hidden) refresh(); };
    document.addEventListener('visibilitychange', resume);
    return () => { clearInterval(timer); document.removeEventListener('visibilitychange', resume); };
  }, []);

  useEffect(() => {
    const valid = selectSession(state, selected);
    if (valid && selected !== valid.id) setSelected(valid.id);
  }, [state.sessions, selected]);
  useEffect(() => {
    const waiting = state.sessions.find(item => item.challenge);
    if (!waiting || visibleChallenge.current === waiting.challenge.id) return;
    visibleChallenge.current = waiting.challenge.id;
    setSelected(waiting.id);
    setPage('sessions');
    setWorkspaceOpen(true);
    setPanel(null);
  }, [state.sessions]);
  useEffect(() => {
    localStorage.setItem('ramos.selected', selected);
    const current = selectSession(state, selected);
    setHandle(state.accounts.find(item => item.id === current?.account_id)?.username || '');
    setPassword(''); setVisiblePassword(false); setError('');
  }, [selected, account?.id]);
  useEffect(() => { document.querySelector('.app-shell > main')?.scrollTo({ top: 0, left: 0 }); }, [page, workspaceOpen]);
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

  async function connect(event) {
    event?.preventDefault();
    if (!session || !online || busy) return;
    const suppliedPassword = password;
    setPassword(''); setVisiblePassword(false);
    await act(() => api.post(`/sessions/${session.id}/login`, { username: handle, password: suppliedPassword }));
  }

  async function clearLogs() {
    if (session) await act(() => api.post('/logs/clear', { session_id: session.id }));
  }

  function exportLogs(entries) {
    const logs = Array.isArray(entries) ? entries : sessionLogs;
    const blob = new Blob([logs.map(item => `[${formatLogTime(item)}] ${item.message}`).join('\n')], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a'); link.href = url; link.download = 'ramos-bot-logs.txt'; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function openPanel(value) {
    setError('');
    if (value === 'settings') {setTwoFactor(state.settings.two_factor_method); setContinuousExecution(state.settings.continuous_execution !== false);}
    if (['macros', 'logs', 'proxy', 'settings'].includes(value)) { setWorkspaceOpen(false); setPassword(''); setVisiblePassword(false); setPage(value); setPanel(null); return; }
    setPanel(value);
  }

  async function addSession() {
    const result = await act(() => api.post('/sessions'));
    if (result) { setPage('sessions'); setSelected(result.session_id); setWorkspaceOpen(true); }
  }

  async function importCredentials(event) {
    const file = event.target.files?.[0];
    if (!file) return;
    try {
      if (file.size > 16000) throw new Error('Escolha um TXT de até 16 KB.');
      const bytes = new Uint8Array(await file.arrayBuffer());
      const encoding = bytes[0] === 255 && bytes[1] === 254 ? 'utf-16le' : bytes[0] === 254 && bytes[1] === 255 ? 'utf-16be' : 'utf-8';
      const text = new TextDecoder(encoding).decode(bytes);
      const values = {};
      for (const line of text.split('\n')) {
        if (!line.trim() || line.trimStart().startsWith('#')) continue;
        const index = line.indexOf('=');
        const key = line.slice(0, index).trim().toLowerCase();
        const mapped = ['user', 'usuario', 'username'].includes(key) ? 'user' : key === 'senha' ? 'password' : '';
        if (index < 0 || !mapped || values[mapped] !== undefined) throw new Error('Formato inválido. Use user= e senha= em linhas separadas.');
        values[mapped] = line.slice(index + 1).replace(/\r$/, '');
      }
      if (!values.user?.trim() || !values.password) throw new Error('Preencha user= e senha= no TXT.');
      setHandle(values.user.trim()); setPassword(values.password); setToast('Dados importados. Toque em Login.'); setPage('sessions'); setWorkspaceOpen(true); setPanel(null);
    } catch (failure) { setError(failure.message); }
    event.target.value = '';
  }

  function downloadJson(value, name) {
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

  const toolForms = {
    settings: <form className="panel-form" onSubmit={async event => { event.preventDefault(); const result = await act(() => api.post('/settings', { two_factor_method: twoFactor, execution_mode: 'server', continuous_execution: continuousExecution }), 'Configurações salvas.'); if (result) setPanel(null); }}><div className="server-execution-setting"><span className="server-setting-title"><Radio size={22}/><strong>Execução no servidor</strong><span>Ativa</span></span><p>Depois de iniciar, suas sessões continuam no servidor se você ficar sem internet ou fechar o site.</p><p>Ao voltar, acompanhe o andamento e use Interromper para parar. No modo contínuo, o executor aguarda quando a fila está vazia e começa novas rodadas até você interromper. Erros e verificações da conta pausam o fluxo.</p></div><label className="toggle-row"><span>Execução contínua<small>Continuar aguardando tarefas entre as rodadas</small></span><input type="checkbox" role="switch" checked={continuousExecution} onChange={event => setContinuousExecution(event.target.checked)}/></label><p className="panel-intro">Esta configuração vale para a próxima execução iniciada. O servidor salva o andamento para retomar após reinícios quando não houver uma ação de resultado incerto.</p><label>Verificação em duas etapas<select value={twoFactor} onChange={event => setTwoFactor(event.target.value)}><option value="auto">Automático (igual ao script Python)</option></select></label><p className="panel-intro">O Instagram determina o método solicitado. O painel usa a mesma continuação do script Python para autenticador, SMS/e-mail ou código de backup. Cada código é enviado uma única vez.</p><div className="setting-info"><span>Servidor</span><strong>{online ? 'Conectado' : 'Offline'}</strong></div><div className="setting-info"><span>Módulo Instagram</span><strong>{state.engine_ready ? 'Instalado' : 'Aguardando instalação'}</strong></div><button className="primary-button" disabled={!online || pending}><Settings2 size={17}/>Salvar configurações</button></form>,
  };

  const title = { accounts: 'Contas abertas', proxy: 'Proxy', 'command-macros': 'Sequências de comandos', logs: 'Logs', settings: 'Configurações', menu: 'Sua sessão', delete: 'Remover sessão?' }[panel];

  if (accessLocked) return <div className="panel-access"><div className="access-glow"/><div className="access-brand"><span>R</span>RAMOS BOT</div><form className="access-card" onSubmit={event => { event.preventDefault(); const entered = panelPassword; setPanelPassword(''); act(() => api.post('/access/login', {password:entered})); }}><div className="access-icon"><ShieldCheck size={30}/></div><span className="eyebrow">SEU ESPAÇO DE TRABALHO</span><h1>Bem-vindo ao<br/><span>Ramos Bot.</span></h1><p>Entre para gerenciar suas sessões e contas.</p><label>Senha do painel<input type="password" autoComplete="current-password" placeholder="Digite sua senha" value={panelPassword} onChange={event => setPanelPassword(event.target.value)} required maxLength={200}/></label><button className="primary-button" disabled={pending || !panelPassword}>{pending ? <LoaderCircle className="spin" size={20}/> : <Fingerprint size={20}/>}Entrar no painel</button>{error && <div className="error-banner" role="alert">{error}</div>}<span className="access-footnote"><ShieldCheck size={14}/>Acesso protegido</span></form></div>;

  return <div className={`app-shell ${page === 'sessions' ? 'is-home' : `is-${page}`}`}>
    <header className="header" inert={workspaceOpen || Boolean(panel)}>
      <a className="brand" href="#" onClick={event => { event.preventDefault(); setPanel(null); setPage('sessions'); setWorkspaceOpen(false); }} aria-label="Ramos Bot, início"><span className="brand-mark"><svg viewBox="0 0 48 48" aria-hidden="true"><path d="M5 5h24c20 0 20 26 3 26l14 14H32L14 26h14c9 0 9-11 0-11H15z" fill="currentColor"/><path d="M6 15l10 10v20H6z" fill="#00c8ed"/></svg></span><span>RAMOS <strong>BOT</strong></span></a>
      <div className="header-actions">
        <IconButton label="Atividade recente" onClick={() => openPanel('logs')}><Bell size={23}/>{state.logs.some(item => item.tone === 'error') && <i className="notification-dot"/>}</IconButton>
        <IconButton label="Mais opções" onClick={() => openPanel('menu')}><Menu size={25}/></IconButton>
      </div>
    </header>

    {page === 'macros' ? <MacrosPage sessions={state.sessions} api={api} refresh={refresh} status={state.viralize || {}} pacingProfiles={state.viralize_pacing} online={online} selectedIds={macroSelection} onSelectionChange={setMacroSelection}/> : page === 'logs' ? <LogsPage logs={state.logs} sessions={state.sessions} online={online} checking={checking} onExport={exportLogs}/> : page === 'proxy' ? <ProxyPage api={api} proxy={state.proxy} online={online} busy={state.sessions.some(sessionBusy) || Object.values(state.viralize || {}).some(executionActive)} refresh={refresh}/> : page === 'settings' ? <main className="tool-page" aria-label={page === 'proxy' ? 'Proxy' : 'Configurações'}><section className="tool-page-card"><div className="tool-page-heading">{page === 'proxy' ? <ShieldCheck aria-hidden="true"/> : <Settings aria-hidden="true"/>}<h1>{page === 'proxy' ? 'Proxy' : 'Configurações'}</h1></div>{toolForms[page]}{error && <div className="error-banner" role="alert">{error}</div>}</section></main> : <main inert={workspaceOpen || Boolean(panel)}>
      <section className="welcome"><h1>Bem-vindo, <span>Carlos</span></h1><p>Gerencie suas sessões, contas, proxy e macros.</p></section>
      <section className="stats" aria-label="Resumo do Ramos Bot">
        <button onClick={() => setWorkspaceOpen(false)}><Monitor/><span>Sessões</span><strong>{state.sessions.length}</strong></button>
        <button onClick={() => openPanel('accounts')}><Link2/><span>Contas abertas</span><strong>{state.sessions.filter(item => item.authenticated).length}</strong></button>
        <button onClick={() => openPanel('proxy')}><ShieldCheck/><span>Proxy</span><strong className="stat-text">{state.proxy.enabled ? 'Ativado' : 'Desativado'}</strong></button>
        <button onClick={() => openPanel('macros')}><Play/><span>Macros</span><strong>{state.macros.length}</strong></button>
      </section>

      <button className="new-session-button" onClick={addSession} disabled={!online || pending}><Plus size={27}/>Nova sessão</button>

      <section className="sessions-home">
        <div className="sessions-heading"><h2><Monitor size={23}/>Minhas Sessões <span>({state.sessions.length})</span></h2><span className={`connection ${online ? 'is-online' : ''}`}><i/>{checking ? 'Conectando' : online ? 'Online' : 'Offline'}</span></div>
        <label className="session-search"><Search size={20}/><input aria-label="Buscar sessão" placeholder="Buscar sessão..." value={filter} onChange={event => setFilter(event.target.value)}/></label>
        <div className="session-list">{state.sessions.filter(item => item.name.toLowerCase().includes(filter.toLowerCase())).map(item => <div className="session-row" key={item.id}>
          <button className="session-open" onClick={() => { setSelected(item.id); setWorkspaceOpen(true); }}><strong>{item.name}</strong><span><CalendarDays size={14}/>{item.created_at ? 'Criada em ' + new Date(item.created_at).toLocaleDateString('pt-BR') + ' às ' + new Date(item.created_at).toLocaleTimeString('pt-BR',{hour:'2-digit',minute:'2-digit'}) : 'Pronta para conectar'}</span></button>
          <span className={`session-pill ${item.authenticated ? 'active' : sessionBusy(item) ? 'working' : ''}`}><i/>{item.authenticated ? 'Ativa' : sessionBusy(item) ? (item.status === 'awaiting_input' ? '2FA' : 'Em uso') : 'Pronta'}</span>
          <IconButton label={'Opções de ' + item.name} onClick={() => { setSelected(item.id); openPanel('menu'); }}><EllipsisVertical size={23}/></IconButton>
        </div>)}{filter && !state.sessions.some(item => item.name.toLowerCase().includes(filter.toLowerCase())) && <p className="search-empty">Nenhuma sessão encontrada.</p>}</div>
        <div className="session-illustration"><span className="reference-illustration" role="img" aria-label="Janelas roxas com uma conta e símbolo de adicionar"/><p>Selecione uma sessão para<br/>conectar sua conta.</p></div>
        {error && <div className="error-banner" role="alert">{error}</div>}
      </section>


    </main>}

    <nav className="bottom-nav" aria-label="Ferramentas" inert={workspaceOpen || Boolean(panel)}><button className={!panel && page === 'sessions' ? 'active' : ''} aria-current={!panel && page === 'sessions' ? 'page' : undefined} onClick={() => { setPanel(null); setPage('sessions'); setWorkspaceOpen(false); }}><Monitor size={24}/><span>Sessões</span></button><button className={!panel && page === 'proxy' ? 'active' : ''} aria-current={!panel && page === 'proxy' ? 'page' : undefined} onClick={() => openPanel('proxy')}><ShieldCheck size={24}/><span>Proxy</span></button><button className={!panel && page === 'macros' ? 'active' : ''} aria-current={!panel && page === 'macros' ? 'page' : undefined} onClick={() => openPanel('macros')}><Bot size={24}/><span>Macros</span></button><button className={!panel && page === 'logs' ? 'active' : ''} aria-current={!panel && page === 'logs' ? 'page' : undefined} onClick={() => openPanel('logs')}><List size={24}/><span>Logs</span></button><button className={!panel && page === 'settings' ? 'active' : ''} aria-current={!panel && page === 'settings' ? 'page' : undefined} onClick={() => openPanel('settings')}><Settings size={24}/><span>Config.</span></button></nav>

    {workspaceOpen && <SessionLoginDialog active={!panel} onClose={() => {setWorkspaceOpen(false); setPassword(''); setVisiblePassword(false);}}>
      <SessionTabs sessions={state.sessions} selectedId={session?.id} onSelect={setSelected} onOptions={() => openPanel('menu')}/>
      <section className="session-card" id="session-panel" role="tabpanel" aria-labelledby={session ? `session-tab-${session.id}` : undefined}>
        <div className="session-content">
          {checking && <div className="session-progress" role="status"><LoaderCircle className="spin" size={24}/><p>Conectando ao Ramos Bot...</p></div>}
          {!online && !checking && <div className="offline-note"><Radio size={19}/><div><strong>Servidor offline</strong><p>Verifique a conexão para entrar na sua conta.</p></div></div>}
          {session && !session.authenticated && !busy && <form className="login-form" onSubmit={connect}>
            <h1>Login</h1>
            <label className="saved-account"><span>Conta salva:</span><select aria-label="Escolher conta salva" value={state.accounts.some(item => item.username === handle) ? handle : ''} onChange={event => { setHandle(event.target.value); setPassword(''); }}><option value="">Nova conta</option>{state.accounts.map(item => <option value={item.username} key={item.id}>@{item.username}</option>)}</select></label>
            <label className="login-field"><UserRound aria-hidden="true"/><input id="instagram-user" autoComplete="username" placeholder="seu_usuario" aria-label="Usuário do Instagram" value={handle} onChange={event => setHandle(event.target.value)} maxLength={30} required autoCapitalize="none" spellCheck="false"/></label>
            <label className="login-field"><LockKeyhole aria-hidden="true"/><input type={visiblePassword ? 'text' : 'password'} autoComplete="current-password" aria-label="Senha do Instagram" placeholder={state.accounts.some(item => item.username === handle && item.has_session) ? 'Sessão salva · senha opcional' : 'Sua senha'} value={password} onChange={event => setPassword(event.target.value)} maxLength={1000}/><IconButton label={visiblePassword ? 'Ocultar senha' : 'Mostrar senha'} onClick={() => setVisiblePassword(value => !value)}>{visiblePassword ? <EyeOff size={18}/> : <Eye size={18}/>}</IconButton></label>
            <button className="connect-button" disabled={!online || pending || !handle.trim()}>{pending ? <LoaderCircle size={18} className="spin"/> : 'Login'}</button>
          </form>}
          {busy && !session?.challenge && <div className="session-progress" role="status"><LoaderCircle className="spin" size={30}/><h1>{session.status === 'connecting' ? 'Conectando sua conta' : 'Sessão em atividade'}</h1><p>Acompanhe os detalhes na aba Logs.</p><button type="button" onClick={() => openPanel('logs')}><List size={17}/>Ver logs</button></div>}
          {session?.authenticated && !busy && <div className="session-connected"><ShieldCheck size={40}/><h1>@{account?.username}</h1><p>Conta conectada</p><div><button onClick={() => openPanel('logs')}><List size={17}/>Ver logs</button><button onClick={() => openPanel('macros')}><Bot size={17}/>Macros</button></div></div>}
          {session?.challenge && <VerificationForm key={`${session.id}:${session.challenge.id}`} api={api} sessionId={session.id} challenge={session.challenge} pending={pending} online={online} onSubmitted={refresh}/>}
        </div>
        <footer className="session-footer"><span className={`session-status ${session?.authenticated ? 'connected' : busy ? 'working' : ''}`}><i/>{session ? statusLabel(session.status) : 'Nenhuma sessão'}</span><span className="session-footer-brand"><i/>Ramos Bot</span></footer>
      </section>
      {(busy || session?.authenticated) && <div className="workspace-actions">{busy ? <button onClick={() => act(() => api.post(`/sessions/${session.id}/stop`))} disabled={pending}><Square size={13}/>Interromper</button> : <button onClick={() => act(() => api.post(`/sessions/${session.id}/disconnect`))} disabled={pending}><Power size={14}/>Desconectar</button>}</div>}
      {error && <div className="error-banner" role="alert">{error}</div>}
    </SessionLoginDialog>}

    {panel && <div className="modal-backdrop" onClick={event => { if (event.target === event.currentTarget) setPanel(null); }} onKeyDown={event => { if (event.key === 'Escape') setPanel(null); }}><section className="modal" role="dialog" aria-modal="true" aria-labelledby="modal-title" tabIndex={-1} ref={node => { if (node && !node.contains(document.activeElement)) node.focus(); }}><div className="modal-header"><div><span className="eyebrow">RAMOS BOT</span><h2 id="modal-title">{title}</h2></div><IconButton label="Fechar" onClick={() => setPanel(null)}><X size={22}/></IconButton></div>
      {panel === 'accounts' && <><p className="panel-intro">Contas salvas no seu Ramos Bot. Escolha uma para conectar na sessão atual.</p><div className="item-list">{state.accounts.map(item => <div className="list-item" key={item.id}><span className="avatar"><UserRound size={20}/></span><div><strong>@{item.username}</strong><small>{state.sessions.some(s => s.account_id === item.id && s.authenticated) ? 'Conectada' : item.has_session ? 'Sessão salva' : 'Aguardando login'}</small></div><button className="text-button" disabled={busy} onClick={() => { setHandle(item.username); setPassword(''); setPage('sessions'); setWorkspaceOpen(true); setPanel(null); }}>Usar</button><IconButton label={'Remover @' + item.username} disabled={pending || !online} onClick={() => act(() => api.remove(`/accounts/${item.id}`), 'Conta removida.')}><Trash2 size={16}/></IconButton></div>)}{!state.accounts.length && <div className="empty-state"><UsersRound size={28}/><strong>Nenhuma conta salva</strong><p>Adicione o usuário ou conecte sua primeira conta.</p></div>}</div><form className="panel-form" onSubmit={async event => { event.preventDefault(); const result = await act(() => api.post('/accounts', { username: newAccount }), 'Conta adicionada.'); if (result) setNewAccount(''); }}><label>Novo usuário<input value={newAccount} onChange={event => setNewAccount(event.target.value)} placeholder="usuario do Instagram" autoCapitalize="none" maxLength={30} required/></label><button className="primary-button" disabled={!online || pending}><Plus size={17}/>Adicionar conta</button></form></>}
      {panel === 'command-macros' && <><p className="panel-intro">Salve uma sequência de comandos. Ela só será executada quando você tocar em Iniciar.</p><div className="item-list">{state.macros.map(item => <div className="list-item" key={item.id}><span className="avatar"><Workflow size={20}/></span><div><strong>{item.name}</strong><small>{item.steps.length} comando{item.steps.length > 1 ? 's' : ''}</small></div><button className="text-button" disabled={!online || !session?.authenticated || busy || pending} onClick={async () => { const result = await act(() => api.post(`/sessions/${session.id}/macro`, { macro_id: item.id })); if (result) setPanel(null); }}>Iniciar</button><IconButton label={'Excluir macro ' + item.name} disabled={!online || pending} onClick={() => act(() => api.remove(`/macros/${item.id}`))}><Trash2 size={16}/></IconButton></div>)}{!state.macros.length && <div className="empty-state"><Workflow size={27}/><strong>Sua primeira macro</strong><p>Organize seus comandos em uma sequência.</p></div>}</div><form className="panel-form" onSubmit={saveMacro}><label>Nome<input value={macroName} onChange={event => setMacroName(event.target.value)} placeholder="Consultar perfis" maxLength={60} required/></label><label>Comandos<textarea rows={4} value={macroText} onChange={event => setMacroText(event.target.value)} placeholder={'perfil usuario\nbuscar "nome" --limite 5'} required/></label><span className="form-note">Um comando por linha, até 20 comandos.</span><button className="primary-button" disabled={!online || pending}><Plus size={17}/>Salvar macro</button></form></>}
      {panel === 'menu' && <div className="menu-list"><button onClick={async () => { const result = await act(() => api.post('/sessions')); if (result) { setPage('sessions'); setSelected(result.session_id); setWorkspaceOpen(true); setPanel(null); } }} disabled={!online || pending}><Plus size={19}/>Nova sessão</button><button onClick={() => openPanel('accounts')}><UsersRound size={19}/>Minhas contas</button><label className="menu-import"><Upload size={19}/>Importar login.txt<input type="file" accept=".txt,text/plain" onChange={importCredentials}/></label><button onClick={() => openPanel('command-macros')}><Workflow size={19}/>Sequências de comandos</button><button onClick={exportDiagnostic} disabled={!session?.account_id}><ShieldCheck size={19}/>Exportar diagnóstico</button><button onClick={exportLogs} disabled={!sessionLogs.length}><Download size={19}/>Exportar logs</button><button onClick={async () => { await clearLogs(); setPanel(null); }} disabled={!session || !online || pending}><Trash2 size={19}/>Limpar logs</button><button className="danger" onClick={() => setPanel('delete')} disabled={!session || !online || pending}><X size={19}/>Remover sessão</button></div>}
      {panel === 'delete' && <><p className="panel-intro">A atividade de {session?.name} será encerrada. A conta continuará na sua lista.</p><button className="primary-button danger-button" disabled={pending} onClick={async () => { const result = await act(() => api.remove(`/sessions/${session.id}`), 'Sessão removida.'); if (result) setPanel(null); }}><Trash2 size={17}/>Remover sessão</button></>}
      {error && <div className="error-banner modal-error" role="alert">{error}</div>}
    </section></div>}
    {toast && <div className="toast" role="status"><ShieldCheck size={17}/>{toast}</div>}
  </div>;
}

createRoot(document.getElementById('root')).render(<App/>);
