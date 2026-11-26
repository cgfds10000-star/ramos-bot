import Select from './Select.jsx';
import React, {useEffect, useState} from 'react';
import {Eye, EyeOff, Globe2, LoaderCircle, LockKeyhole, MonitorCog, Network, RefreshCw, Server, ShieldCheck, UserRound} from 'lucide-react';

function savedFields(proxy) {
  return {host: proxy.host || '', port: String(proxy.port || ''), scheme: proxy.scheme || 'http', username: '', password: ''};
}

export default function ProxyPage({api, proxy, online, busy, refresh}) {
  const [enabled, setEnabled] = useState(Boolean(proxy.enabled));
  const [fields, setFields] = useState(() => savedFields(proxy));
  const [authEdits, setAuthEdits] = useState({});
  const [clearAuth, setClearAuth] = useState(false);
  const [visible, setVisible] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  useEffect(() => {
    setEnabled(Boolean(proxy.enabled));
    setFields(savedFields(proxy)); setAuthEdits({}); setClearAuth(false); setVisible(false);
  }, [proxy.enabled, proxy.host, proxy.port, proxy.scheme, proxy.has_auth]);
  const check = proxy.check;
  const checking = pending || proxy.checking;
  const changed = enabled !== Boolean(proxy.enabled) || fields.host.trim() !== (proxy.host || '')
    || fields.port !== String(proxy.port || '') || fields.scheme !== (proxy.scheme || 'http') || Object.keys(authEdits).length > 0 || clearAuth;

  function edit(name, value) {
    setFields(previous => ({...previous, [name]: value}));
    if (name === 'username' || name === 'password') { setAuthEdits(previous => ({...previous, [name]: value})); setClearAuth(false); }
    setNotice(''); setError('');
  }

  async function verify() {
    const result = await api.post('/proxy/test');
    await refresh();
    if (!result.ok) setError(result.check?.message || 'Não foi possível verificar o proxy.');
    return result;
  }

  async function save(event) {
    event.preventDefault();
    if (pending) return;
    setPending(true); setError(''); setNotice('');
    try {
      await api.post('/proxy', {enabled, fields: {host: fields.host.trim(), port: fields.port, scheme: fields.scheme, ...authEdits, clear_auth: clearAuth}});
      setFields(previous => ({...previous, username: '', password: ''})); setAuthEdits({}); setClearAuth(false); setVisible(false);
      await refresh();
      setNotice(enabled ? 'Proxy aplicado às conexões das sessões.' : 'Proxy desativado.');
      if (enabled) await verify();
    } catch (failure) {setError(failure.message);}
    finally {setPending(false);}
  }

  async function test() {
    if (pending) return;
    setPending(true); setError(''); setNotice('');
    try {await verify();}
    catch (failure) {setError(failure.message);}
    finally {setPending(false);}
  }

  return <main className="proxy-page" aria-label="Proxy">
    <section className="proxy-card" aria-label="Conexão do proxy">
      <form className="proxy-form" onSubmit={save}>
        <label className="proxy-toggle"><MonitorCog aria-hidden="true"/><span>Usar proxy</span><input type="checkbox" role="switch" checked={enabled} onChange={event => {setEnabled(event.target.checked); setNotice('');}} disabled={pending || busy}/><span className="proxy-switch" aria-hidden="true"/></label>
        <div className="proxy-fields">
          <label className="proxy-field"><Server aria-hidden="true"/><input aria-label="Host do proxy" placeholder="Host (ex: 127.0.0.1)" value={fields.host} onChange={event => edit('host', event.target.value)} autoComplete="off" autoCapitalize="none" spellCheck={false} maxLength={253} disabled={pending || busy} required={enabled}/></label>
          <label className="proxy-field"><Network aria-hidden="true"/><input aria-label="Porta do proxy" placeholder="Porta (ex: 8080)" value={fields.port} onChange={event => edit('port', event.target.value)} inputMode="numeric" pattern="[0-9]{1,5}" maxLength={5} disabled={pending || busy} required={enabled}/></label>
          <label className="proxy-field"><UserRound aria-hidden="true"/><input aria-label="Usuário do proxy" placeholder={proxy.has_auth && !clearAuth ? 'Usuário salvo' : 'Username'} value={fields.username} onChange={event => edit('username', event.target.value)} autoComplete="off" autoCapitalize="none" spellCheck={false} maxLength={256} disabled={pending || busy}/></label>
          <label className="proxy-field"><LockKeyhole aria-hidden="true"/><input type={visible ? 'text' : 'password'} aria-label="Senha do proxy" placeholder={proxy.has_auth && !clearAuth ? 'Senha salva' : 'Password'} value={fields.password} onChange={event => edit('password', event.target.value)} autoComplete="new-password" maxLength={500} disabled={pending || busy}/><button type="button" className="proxy-eye" aria-label={visible ? 'Ocultar senha do proxy' : 'Mostrar senha do proxy'} onClick={() => setVisible(value => !value)}>{visible ? <EyeOff/> : <Eye/>}</button></label>
        </div>
        <details className="proxy-advanced"><summary>Configuração avançada</summary><label>Tipo de proxy<Select aria-label="Tipo de proxy" value={fields.scheme} onChange={value => edit('scheme', value)} disabled={pending || busy}><option value="http">HTTP</option><option value="https">HTTPS</option><option value="socks5h">SOCKS5</option></Select></label>{proxy.has_auth && <button type="button" disabled={pending || busy || clearAuth} onClick={() => {setClearAuth(true); setAuthEdits({}); setFields(previous => ({...previous, username: '', password: ''}));}}>{clearAuth ? 'Credenciais serão removidas ao salvar' : 'Remover credenciais salvas'}</button>}</details>
        <div className="proxy-ip-row" aria-live="polite"><Globe2 aria-hidden="true"/><strong>IP de saída</strong><span className={check?.status === 'ok' && proxy.enabled ? 'verified' : ''}>{checking ? 'Verificando…' : !proxy.enabled ? 'Proxy desativado' : check?.status === 'ok' ? check.ip : check?.status === 'error' ? 'Não verificado' : 'Aguardando verificação'}</span></div>
        {proxy.enabled && <button type="button" className="proxy-test-button" onClick={test} disabled={!online || checking || changed}><RefreshCw size={17} className={checking ? 'spin' : ''}/>Verificar conexão</button>}
        <button className="proxy-save" disabled={!online || pending || busy}>{pending ? <LoaderCircle className="spin"/> : <ShieldCheck/>}<span>{pending ? 'Aplicando…' : 'Salvar e aplicar'}</span></button>
        {busy && <p className="proxy-hint">Encerre as operações em andamento para alterar a conexão.</p>}
        {proxy.has_auth && !clearAuth && <p className="proxy-hint">Os campos de autenticação vazios mantêm as credenciais salvas.</p>}
        {notice && <p className="proxy-notice" role="status">{notice}</p>}
        {(error || (proxy.enabled && check?.status === 'error' && check.message)) && <div className="error-banner" role="alert">{error || check.message}</div>}
      </form>
    </section>
  </main>;
}
