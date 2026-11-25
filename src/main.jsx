import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Bot, Bell, CalendarDays, ChevronLeft, CircleHelp, Code2, Copy, Download, Ellipsis, EllipsisVertical, Eye, EyeOff, Fingerprint, Layers3, Link2, List, LoaderCircle, Menu, Monitor, Play, Plus, Power, Radio, RefreshCw, Search, Settings, Settings2, ShieldCheck, Square, Terminal, Trash2, Upload, UsersRound, UserRound, Workflow, X } from 'lucide-react';
import { RamosApi, parseCommand, selectSession, sessionBusy, statusLabel } from './api.js';
import VerificationForm from './VerificationForm.jsx';
import MacrosPage from './MacrosPage.jsx';
import LogsPage from './LogsPage.jsx';
import './styles.css';
import './reference.css';
import './macros.css';
import './logs.css';
import './app-layout.css';

const api = new RamosApi();
const empty = { sessions: [], accounts: [], macros: [], logs: [], proxy: { enabled: false }, settings: { two_factor_method: 'autenticador' } };
const helpText = 'Comandos disponíveis:\nbuscar "nome" --limite 10\nbuscar "nome" --geral --limite 10\nperfil usuario\nperfil --id 123456\nseguir usuario\nseguir --id 123456\ncurtir https://www.instagram.com/p/CODIGO/\ncurtir 123456789\nstatus · login · sair · limpar · ajuda';

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
  const [command, setCommand] = useState('');
  const [localLogs, setLocalLogs] = useState([]);
  const [proxyUrl, setProxyUrl] = useState('');
  const [proxyEnabled, setProxyEnabled] = useState(false);
  const [macroName, setMacroName] = useState('');
  const [macroText, setMacroText] = useState('');
  const [newAccount, setNewAccount] = useState('');
  const [twoFactor, setTwoFactor] = useState('autenticador');
  const terminalRef = useRef(null);
  const poll = useRef(false);
  const visibleChallenge = useRef('');
  const session = selectSession(state, selected);
  const account = state.accounts.find(item => item.id === session?.account_id);
  const busy = sessionBusy(session);
  const sessionLogs = [...state.logs.filter(item => item.session_id === session?.id || !item.session_id), ...localLogs.filter(item => item.session_id === session?.id)].sort((a, b) => (a.order || a.id) - (b.order || b.id));

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
    setPassword(''); setCommand(''); setError('');
  }, [selected, account?.id]);
  useEffect(() => { if (terminalRef.current) terminalRef.current.scrollTop = terminalRef.current.scrollHeight; }, [state.logs.length, localLogs.length, session?.id]);
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

  function localLog(message, tone = 'info') {
    setLocalLogs(items => [...items.slice(-100), { id: crypto.randomUUID(), order: Date.now(), time: new Date().toLocaleTimeString('pt-BR'), tone, message, session_id: session?.id }]);
  }

  async function connect(event) {
    event?.preventDefault();
    if (!session || !online || busy) return;
    const suppliedPassword = password;
    setPassword(''); setVisiblePassword(false);
    await act(() => api.post(`/sessions/${session.id}/login`, { username: handle, password: suppliedPassword }));
  }

  async function runCommand(event) {
    event?.preventDefault();
    if (!session || busy || pending) return;
    let parsed;
    try { parsed = parseCommand(command); }
    catch (failure) { setError(failure.message); return; }
    if (!parsed) return;
    setCommand('');
    if (['ajuda', 'help'].includes(parsed.command)) return localLog(helpText);
    if (['limpar', 'clear'].includes(parsed.command)) return clearLogs();
    if (parsed.command === 'status') return localLog(`${session.name}: ${statusLabel(session.status)}${account ? ' · @' + account.username : ''}`);
    if (parsed.command === 'login') { setPanel(null); return document.getElementById('instagram-user')?.focus(); }
    if (parsed.command === 'sair') return act(() => api.post(`/sessions/${session.id}/disconnect`));
    await act(() => api.post(`/sessions/${session.id}/command`, parsed));
  }

  async function clearLogs() {
    if (!session) return;
    const result = await act(() => api.post('/logs/clear', { session_id: session.id }));
    if (result) setLocalLogs(items => items.filter(item => item.session_id !== session.id));
  }

  function exportLogs(entries) {
    const logs = Array.isArray(entries) ? entries : sessionLogs;
    const blob = new Blob([logs.map(item => `[${item.time}] ${item.message}`).join('\n')], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a'); link.href = url; link.download = 'ramos-bot-logs.txt'; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function openPanel(value) {
    setError('');
    if (value === 'proxy') { setProxyEnabled(state.proxy.enabled); setProxyUrl(''); }
    if (value === 'settings') setTwoFactor(state.settings.two_factor_method);
    if (['macros', 'logs', 'proxy', 'settings'].includes(value)) { setPage(value); setPanel(null); return; }
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
      setHandle(values.user.trim()); setPassword(values.password); setToast('Dados importados. Toque em Conectar conta.');
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
    proxy: <form className="panel-form" onSubmit={async event => { event.preventDefault(); const result = await act(() => api.post('/proxy', { enabled: proxyEnabled, url: proxyUrl }), 'Proxy atualizado.'); if (result) { setProxyUrl(''); setPanel(null); } }}><p className="panel-intro">Use a mesma conexão nas contas desta instalação. Alterar o proxy desconecta as sessões.</p><label className="toggle-row"><span>Usar proxy<small>{state.proxy.enabled ? `${state.proxy.host}:${state.proxy.port}` : 'Conexão direta'}</small></span><input type="checkbox" role="switch" checked={proxyEnabled} onChange={event => setProxyEnabled(event.target.checked)}/></label>{proxyEnabled && <label>Endereço do proxy<input type="password" autoComplete="off" value={proxyUrl} onChange={event => setProxyUrl(event.target.value)} placeholder={state.proxy.enabled ? 'Deixe vazio para manter o atual' : 'http://usuario:senha@host:porta'} maxLength={1000}/></label>}<button className="primary-button" disabled={!online || pending || busy}><ShieldCheck size={17}/>Salvar proxy</button></form>,
    settings: <form className="panel-form" onSubmit={async event => { event.preventDefault(); const result = await act(() => api.post('/settings', { two_factor_method: twoFactor }), 'Configurações salvas.'); if (result) setPanel(null); }}><label>Verificação em duas etapas<select value={twoFactor} onChange={event => setTwoFactor(event.target.value)}><option value="autenticador">Aplicativo autenticador (TOTP)</option></select></label><p className="panel-intro">O login utiliza o código do aplicativo autenticador quando o Instagram fornece o contexto 2FA. Códigos são informados por você e usados uma única vez.</p><div className="setting-info"><span>Servidor</span><strong>{online ? 'Conectado' : 'Offline'}</strong></div><div className="setting-info"><span>Módulo Instagram</span><strong>{state.engine_ready ? 'Instalado' : 'Aguardando instalação'}</strong></div><button className="primary-button" disabled={!online || pending}><Settings2 size={17}/>Salvar configurações</button></form>,
  };

  const title = { accounts: 'Contas abertas', proxy: 'Proxy', 'command-macros': 'Sequências de comandos', logs: 'Logs', settings: 'Configurações', help: 'Comandos do terminal', menu: 'Sua sessão', delete: 'Remover sessão?' }[panel];

  if (accessLocked) return <div className="panel-access"><div className="access-glow"/><div className="access-brand"><span>R</span>RAMOS BOT</div><form className="access-card" onSubmit={event => { event.preventDefault(); const entered = panelPassword; setPanelPassword(''); act(() => api.post('/access/login', {password:entered})); }}><div className="access-icon"><ShieldCheck size={30}/></div><span className="eyebrow">SEU ESPAÇO DE TRABALHO</span><h1>Bem-vindo ao<br/><span>Ramos Bot.</span></h1><p>Entre para gerenciar suas sessões e contas.</p><label>Senha do painel<input type="password" autoComplete="current-password" placeholder="Digite sua senha" value={panelPassword} onChange={event => setPanelPassword(event.target.value)} required maxLength={200}/></label><button className="primary-button" disabled={pending || !panelPassword}>{pending ? <LoaderCircle className="spin" size={20}/> : <Fingerprint size={20}/>}Entrar no painel</button>{error && <div className="error-banner" role="alert">{error}</div>}<span className="access-footnote"><ShieldCheck size={14}/>Acesso protegido</span></form></div>;

  return <div className={`app-shell ${page === 'sessions' ? workspaceOpen ? 'is-workspace' : 'is-home' : `is-${page}`}`}>
    <header className="header">
      <a className="brand" href="#" onClick={event => { event.preventDefault(); setPanel(null); setPage('sessions'); setWorkspaceOpen(false); }} aria-label="Ramos Bot, início"><span className="brand-mark"><svg viewBox="0 0 48 48" aria-hidden="true"><path d="M5 5h24c20 0 20 26 3 26l14 14H32L14 26h14c9 0 9-11 0-11H15z" fill="currentColor"/><path d="M6 15l10 10v20H6z" fill="#00c8ed"/></svg></span><span>RAMOS <strong>BOT</strong></span></a>
      <div className="header-actions">
        <IconButton label="Atividade recente" onClick={() => openPanel('logs')}><Bell size={23}/>{state.logs.some(item => item.tone === 'error') && <i className="notification-dot"/>}</IconButton>
        <IconButton label="Mais opções" onClick={() => openPanel('menu')}><Menu size={25}/></IconButton>
      </div>
    </header>

    {page === 'macros' ? <MacrosPage sessions={state.sessions} api={api} refresh={refresh} status={state.viralize || {}} online={online} selectedIds={macroSelection} onSelectionChange={setMacroSelection}/> : page === 'logs' ? <LogsPage logs={state.logs} sessions={state.sessions} online={online} checking={checking} onExport={exportLogs}/> : ['proxy', 'settings'].includes(page) ? <main className="tool-page" aria-label={page === 'proxy' ? 'Proxy' : 'Configurações'}><section className="tool-page-card"><div className="tool-page-heading">{page === 'proxy' ? <ShieldCheck aria-hidden="true"/> : <Settings aria-hidden="true"/>}<h1>{page === 'proxy' ? 'Proxy' : 'Configurações'}</h1></div>{toolForms[page]}{error && <div className="error-banner" role="alert">{error}</div>}</section></main> : <main>
      <section className="welcome"><h1>Bem-vindo, <span>Carlos</span></h1><p>Gerencie suas sessões, contas, proxy e macros.</p></section>
      <section className="stats" aria-label="Resumo do Ramos Bot">
        <button onClick={() => setWorkspaceOpen(false)}><Monitor/><span>Sessões</span><strong>{state.sessions.length}</strong></button>
        <button onClick={() => openPanel('accounts')}><Link2/><span>Contas abertas</span><strong>{state.sessions.filter(item => item.authenticated).length}</strong></button>
        <button onClick={() => openPanel('proxy')}><ShieldCheck/><span>Proxy</span><strong className="stat-text">{state.proxy.enabled ? 'Ativado' : 'Desativado'}</strong></button>
        <button onClick={() => openPanel('macros')}><Play/><span>Macros</span><strong>{state.macros.length}</strong></button>
      </section>

      <button className="new-session-button" onClick={addSession} disabled={!online || pending}><Plus size={27}/>Nova sessão</button>

      {!workspaceOpen && <section className="sessions-home">
        <div className="sessions-heading"><h2><Monitor size={23}/>Minhas Sessões <span>({state.sessions.length})</span></h2><span className={`connection ${online ? 'is-online' : ''}`}><i/>{checking ? 'Conectando' : online ? 'Online' : 'Offline'}</span></div>
        <label className="session-search"><Search size={20}/><input aria-label="Buscar sessão" placeholder="Buscar sessão..." value={filter} onChange={event => setFilter(event.target.value)}/></label>
        <div className="session-list">{state.sessions.filter(item => item.name.toLowerCase().includes(filter.toLowerCase())).map(item => <div className="session-row" key={item.id}>
          <button className="session-open" onClick={() => { setSelected(item.id); setWorkspaceOpen(true); }}><strong>{item.name}</strong><span><CalendarDays size={14}/>{item.created_at ? 'Criada em ' + new Date(item.created_at).toLocaleDateString('pt-BR') + ' às ' + new Date(item.created_at).toLocaleTimeString('pt-BR',{hour:'2-digit',minute:'2-digit'}) : 'Pronta para conectar'}</span></button>
          <span className={`session-pill ${item.authenticated ? 'active' : sessionBusy(item) ? 'working' : ''}`}><i/>{item.authenticated ? 'Ativa' : sessionBusy(item) ? (item.status === 'awaiting_input' ? '2FA' : 'Em uso') : 'Pronta'}</span>
          <IconButton label={'Opções de ' + item.name} onClick={() => { setSelected(item.id); openPanel('menu'); }}><EllipsisVertical size={23}/></IconButton>
        </div>)}{filter && !state.sessions.some(item => item.name.toLowerCase().includes(filter.toLowerCase())) && <p className="search-empty">Nenhuma sessão encontrada.</p>}</div>
        <div className="session-illustration"><span className="reference-illustration" role="img" aria-label="Janelas roxas com uma conta e símbolo de adicionar"/><p>Selecione uma sessão para<br/>conectar sua conta.</p></div>
        {error && <div className="error-banner" role="alert">{error}</div>}
      </section>}

      {workspaceOpen && <>
      <button className="back-button" onClick={() => setWorkspaceOpen(false)}><ChevronLeft size={18}/>Minhas sessões</button>

      <div className="workspace-heading"><span className="eyebrow">SEU ESPAÇO DE TRABALHO</span><span className={`connection ${online ? 'is-online' : ''}`}><i/>{checking ? 'Conectando' : online ? 'Servidor conectado' : 'Servidor offline'}</span></div>

      <section className="session-card" aria-label="Sessão do Instagram">
        <div className="session-toolbar">
          <div className="session-tabs" id="session-tabs" tabIndex={-1} role="tablist" aria-label="Sessões">
            {state.sessions.map(item => <button type="button" role="tab" aria-selected={item.id === session?.id} className={item.id === session?.id ? 'active' : ''} key={item.id} onClick={() => setSelected(item.id)}><Terminal size={16}/>{item.name}{item.authenticated && <i/>}</button>)}
            {!state.sessions.length && <span className="empty-tab">Sessões</span>}
          </div>
          <IconButton label="Opções da sessão" onClick={() => openPanel('menu')}><Ellipsis size={21}/></IconButton>
        </div>

        <div className="session-content">
          {!online && !checking && <div className="offline-note"><Radio size={19}/><div><strong>Conecte o servidor do Ramos Bot</strong><p>Inicie o Ramos Bot no computador para acessar suas contas e sessões.</p></div></div>}
          {checking && <div className="loading-state"><LoaderCircle className="spin" size={24}/><span>Conectando ao Ramos Bot...</span></div>}
          {session && !session.authenticated && !busy && <form className="login-form" onSubmit={connect}>
            <div className="login-emblem"><Fingerprint size={29}/></div>
            <h1>Conectar Instagram</h1><p>Acesse sua conta pela sessão do Ramos Bot.</p>
            {state.accounts.length > 0 && <label className="saved-account">Conta salva<select aria-label="Escolher conta salva" value={state.accounts.some(item => item.username === handle) ? handle : ''} onChange={event => { setHandle(event.target.value); setPassword(''); }}><option value="">Nova conta</option>{state.accounts.map(item => <option value={item.username} key={item.id}>@{item.username}</option>)}</select></label>}
            <label className="terminal-field"><span>&gt; user:</span><input id="instagram-user" autoComplete="username" placeholder="seu_usuario" aria-label="Usuário do Instagram" value={handle} onChange={event => setHandle(event.target.value)} maxLength={30} required autoCapitalize="none" spellCheck="false"/></label>
            <label className="terminal-field"><span>&gt; password:</span><input type={visiblePassword ? 'text' : 'password'} autoComplete="current-password" aria-label="Senha do Instagram" placeholder={state.accounts.some(item => item.username === handle && item.has_session) ? 'Sessão salva · senha opcional' : 'Sua senha'} value={password} onChange={event => setPassword(event.target.value)} maxLength={1000}/><IconButton label={visiblePassword ? 'Ocultar senha' : 'Mostrar senha'} onClick={() => setVisiblePassword(value => !value)}>{visiblePassword ? <EyeOff size={17}/> : <Eye size={17}/>}</IconButton></label>
            <button className="connect-button" disabled={!online || pending || !handle.trim()}>{pending ? <LoaderCircle size={18} className="spin"/> : <Fingerprint size={18}/>}Conectar conta</button>
            <label className="import-credentials"><Upload size={16}/>Importar login.txt<input type="file" accept=".txt,text/plain" onChange={importCredentials}/></label>
            <span className="form-note"><ShieldCheck size={13}/>Sua senha não é salva pelo Ramos Bot.</span>
          </form>}

          {(busy || session?.authenticated || sessionLogs.some(item => item.session_id === session?.id)) && <div className={`terminal-output ${!busy && !session?.authenticated ? 'compact' : ''}`} ref={terminalRef} role="log" aria-label="Terminal da sessão" aria-live="polite" aria-relevant="additions text">
            {sessionLogs.map(item => <div className={`log-line tone-${item.tone}`} key={item.id}><time>[{item.time}]</time><span>{item.message}</span></div>)}
            {busy && <div className="terminal-running"><span className="terminal-cursor"/>{session.status === 'awaiting_input' ? 'Aguardando verificação...' : 'Aguardando resposta...'}</div>}
            {session?.authenticated && !busy && <div className="terminal-ready"><span>&gt;</span> Pronto para o próximo comando<span className="terminal-cursor"/></div>}
          </div>}

          {session?.challenge && <VerificationForm key={`${session.id}:${session.challenge.id}`} api={api} sessionId={session.id} challenge={session.challenge} pending={pending} online={online} onSubmitted={refresh}/>}
        </div>

        {session?.authenticated && <form className="command-bar" onSubmit={runCommand}><span>&gt;</span><input aria-label="Comando do terminal" placeholder="Digite um comando ou ajuda" value={command} onChange={event => setCommand(event.target.value)} disabled={busy || !online || pending} autoComplete="off" spellCheck="false"/><IconButton label="Executar comando" type="submit" disabled={busy || !online || pending || !command.trim()}><Terminal size={18}/></IconButton></form>}

        <footer className="session-footer"><span className={`session-status ${session?.authenticated ? 'connected' : busy ? 'working' : ''}`}><i/>{session ? statusLabel(session.status) : 'Nenhuma sessão'}</span><span>{account ? '@' + account.username : 'Ramos Bot'}</span></footer>
      </section>

      <div className="workspace-actions"><button onClick={() => openPanel('help')}><CircleHelp size={15}/>Comandos disponíveis</button>{busy && <button onClick={() => act(() => api.post(`/sessions/${session.id}/stop`))} disabled={pending}><Square size={13}/>Interromper</button>}{session?.authenticated && !busy && <button onClick={() => act(() => api.post(`/sessions/${session.id}/disconnect`))} disabled={pending}><Power size={14}/>Desconectar</button>}</div>
      {session?.last_result && <button className="result-export" onClick={() => downloadJson(session.last_result, 'ramos-resultado.json')}><Download size={16}/>Exportar último resultado em JSON</button>}
      {error && <div className="error-banner" role="alert"><span>{error}</span><IconButton label="Fechar aviso" onClick={() => setError('')}><X size={17}/></IconButton></div>}
      {!state.engine_ready && online && <div className="engine-note">Instale o módulo de Instagram no servidor para conectar uma conta. Os controles de sessões, contas e macros já estão disponíveis.</div>}
      <div className="bottom-wordmark"><span className="wordmark-line"/><Bot size={17}/><span>RAMOS BOT</span><span className="wordmark-line"/></div>
      </>}
    </main>}

    <nav className="bottom-nav" aria-label="Ferramentas"><button className={!panel && page === 'sessions' ? 'active' : ''} aria-current={!panel && page === 'sessions' ? 'page' : undefined} onClick={() => { setPanel(null); setPage('sessions'); setWorkspaceOpen(false); }}><Monitor size={24}/><span>Sessões</span></button><button className={!panel && page === 'proxy' ? 'active' : ''} aria-current={!panel && page === 'proxy' ? 'page' : undefined} onClick={() => openPanel('proxy')}><ShieldCheck size={24}/><span>Proxy</span></button><button className={!panel && page === 'macros' ? 'active' : ''} aria-current={!panel && page === 'macros' ? 'page' : undefined} onClick={() => openPanel('macros')}><Bot size={24}/><span>Macros</span></button><button className={!panel && page === 'logs' ? 'active' : ''} aria-current={!panel && page === 'logs' ? 'page' : undefined} onClick={() => openPanel('logs')}><List size={24}/><span>Logs</span></button><button className={!panel && page === 'settings' ? 'active' : ''} aria-current={!panel && page === 'settings' ? 'page' : undefined} onClick={() => openPanel('settings')}><Settings size={24}/><span>Config.</span></button></nav>

    {panel && <div className="modal-backdrop" onClick={event => { if (event.target === event.currentTarget) setPanel(null); }} onKeyDown={event => { if (event.key === 'Escape') setPanel(null); }}><section className="modal" role="dialog" aria-modal="true" aria-labelledby="modal-title" tabIndex={-1} ref={node => { if (node && !node.contains(document.activeElement)) node.focus(); }}><div className="modal-header"><div><span className="eyebrow">RAMOS BOT</span><h2 id="modal-title">{title}</h2></div><IconButton label="Fechar" onClick={() => setPanel(null)}><X size={22}/></IconButton></div>
      {panel === 'accounts' && <><p className="panel-intro">Contas salvas no seu Ramos Bot. Escolha uma para conectar na sessão atual.</p><div className="item-list">{state.accounts.map(item => <div className="list-item" key={item.id}><span className="avatar"><UserRound size={20}/></span><div><strong>@{item.username}</strong><small>{state.sessions.some(s => s.account_id === item.id && s.authenticated) ? 'Conectada' : item.has_session ? 'Sessão salva' : 'Aguardando login'}</small></div><button className="text-button" disabled={busy} onClick={() => { setHandle(item.username); setPassword(''); setPanel(null); }}>Usar</button><IconButton label={'Remover @' + item.username} disabled={pending || !online} onClick={() => act(() => api.remove(`/accounts/${item.id}`), 'Conta removida.')}><Trash2 size={16}/></IconButton></div>)}{!state.accounts.length && <div className="empty-state"><UsersRound size={28}/><strong>Nenhuma conta salva</strong><p>Adicione o usuário ou conecte sua primeira conta.</p></div>}</div><form className="panel-form" onSubmit={async event => { event.preventDefault(); const result = await act(() => api.post('/accounts', { username: newAccount }), 'Conta adicionada.'); if (result) setNewAccount(''); }}><label>Novo usuário<input value={newAccount} onChange={event => setNewAccount(event.target.value)} placeholder="usuario do Instagram" autoCapitalize="none" maxLength={30} required/></label><button className="primary-button" disabled={!online || pending}><Plus size={17}/>Adicionar conta</button></form></>}
      {panel === 'command-macros' && <><p className="panel-intro">Salve uma sequência de comandos. Ela só será executada quando você tocar em Iniciar.</p><div className="item-list">{state.macros.map(item => <div className="list-item" key={item.id}><span className="avatar"><Workflow size={20}/></span><div><strong>{item.name}</strong><small>{item.steps.length} comando{item.steps.length > 1 ? 's' : ''}</small></div><button className="text-button" disabled={!online || !session?.authenticated || busy || pending} onClick={async () => { const result = await act(() => api.post(`/sessions/${session.id}/macro`, { macro_id: item.id })); if (result) setPanel(null); }}>Iniciar</button><IconButton label={'Excluir macro ' + item.name} disabled={!online || pending} onClick={() => act(() => api.remove(`/macros/${item.id}`))}><Trash2 size={16}/></IconButton></div>)}{!state.macros.length && <div className="empty-state"><Workflow size={27}/><strong>Sua primeira macro</strong><p>Organize seus comandos em uma sequência.</p></div>}</div><form className="panel-form" onSubmit={saveMacro}><label>Nome<input value={macroName} onChange={event => setMacroName(event.target.value)} placeholder="Consultar perfis" maxLength={60} required/></label><label>Comandos<textarea rows={4} value={macroText} onChange={event => setMacroText(event.target.value)} placeholder={'perfil usuario\nbuscar "nome" --limite 5'} required/></label><span className="form-note">Um comando por linha, até 20 comandos.</span><button className="primary-button" disabled={!online || pending}><Plus size={17}/>Salvar macro</button></form></>}
      {panel === 'help' && <><p className="panel-intro">Execute um comando por vez na sessão conectada.</p><pre className="help-block">{helpText}</pre><p className="panel-intro">Seguir e curtir enviam uma ação à sua conta. O terminal mostra o estado confirmado pelo Instagram.</p></>}
      {panel === 'menu' && <div className="menu-list"><button onClick={async () => { const result = await act(() => api.post('/sessions')); if (result) { setPage('sessions'); setSelected(result.session_id); setWorkspaceOpen(true); setPanel(null); } }} disabled={!online || pending}><Plus size={19}/>Nova sessão</button><button onClick={() => openPanel('accounts')}><UsersRound size={19}/>Minhas contas</button><button onClick={() => openPanel('command-macros')}><Workflow size={19}/>Sequências de comandos</button><button onClick={exportDiagnostic} disabled={!session?.account_id}><ShieldCheck size={19}/>Exportar diagnóstico</button><button onClick={exportLogs} disabled={!sessionLogs.length}><Download size={19}/>Exportar terminal</button><button onClick={async () => { await clearLogs(); setPanel(null); }} disabled={!session || !online || pending}><Trash2 size={19}/>Limpar terminal</button><button className="danger" onClick={() => setPanel('delete')} disabled={!session || !online || pending}><X size={19}/>Remover sessão</button></div>}
      {panel === 'delete' && <><p className="panel-intro">A atividade de {session?.name} será encerrada. A conta continuará na sua lista.</p><button className="primary-button danger-button" disabled={pending} onClick={async () => { const result = await act(() => api.remove(`/sessions/${session.id}`), 'Sessão removida.'); if (result) setPanel(null); }}><Trash2 size={17}/>Remover sessão</button></>}
      {error && <div className="error-banner modal-error" role="alert">{error}</div>}
    </section></div>}
    {toast && <div className="toast" role="status"><ShieldCheck size={17}/>{toast}</div>}
  </div>;
}

createRoot(document.getElementById('root')).render(<App/>);
