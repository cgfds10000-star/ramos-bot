import React, {useEffect, useState} from 'react';
import {Check, Eye, EyeOff, Globe2, LoaderCircle, RefreshCw, ShieldCheck} from 'lucide-react';

export default function ProxyPage({api, proxy, online, busy, refresh}) {
  const [enabled, setEnabled] = useState(Boolean(proxy.enabled));
  const [url, setUrl] = useState('');
  const [visible, setVisible] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  useEffect(() => {setEnabled(Boolean(proxy.enabled));}, [proxy.enabled]);
  const check = proxy.check;
  const checking = pending || proxy.checking;
  const changed = enabled !== Boolean(proxy.enabled) || Boolean(url.trim());

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
      await api.post('/proxy', {enabled, url});
      setUrl(''); setVisible(false);
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

  return <main className="tool-page proxy-page" aria-label="Proxy">
    <section className="tool-page-card">
      <div className="tool-page-heading"><ShieldCheck aria-hidden="true"/><h1>Proxy</h1><span className={`proxy-badge ${proxy.enabled ? 'enabled' : ''}`}>{proxy.enabled ? 'Aplicado' : 'Desativado'}</span></div>
      <p className="proxy-intro">Uma conexão para as APIs das suas sessões.</p>
      <form className="panel-form" onSubmit={save}>
        <label className="toggle-row"><span>Usar proxy<small>Instagram e Viralize</small></span><input type="checkbox" role="switch" checked={enabled} onChange={event => {setEnabled(event.target.checked); setNotice('');}} disabled={pending || busy}/></label>
        {enabled && <label>Endereço do proxy<div className="proxy-input"><input type={visible ? 'text' : 'password'} autoComplete="off" autoCapitalize="none" spellCheck={false} value={url} onChange={event => {setUrl(event.target.value); setNotice('');}} placeholder={proxy.enabled ? 'Deixe vazio para manter o proxy salvo' : 'host:porta:usuário:senha'} maxLength={1000} disabled={pending}/><button type="button" className="proxy-eye" aria-label={visible ? 'Ocultar proxy' : 'Mostrar proxy'} onClick={() => setVisible(value => !value)}>{visible ? <EyeOff size={20}/> : <Eye size={20}/>}</button></div><small className="proxy-hint">Aceita host:porta:usuário:senha e URLs HTTP, HTTPS ou SOCKS5.</small></label>}
        <div className="proxy-scope"><span><Check size={16}/>Autenticação e 2FA</span><span><Check size={16}/>Ações das sessões</span><span><Check size={16}/>Integração Viralize</span></div>
        <button className="primary-button" disabled={!online || pending || busy}>{pending ? <LoaderCircle size={19} className="spin"/> : <ShieldCheck size={19}/>} {pending ? 'Aplicando e verificando…' : 'Salvar e aplicar'}</button>
        {busy && <p className="proxy-hint">Encerre as operações em andamento para alterar a conexão.</p>}
        {notice && <p className="proxy-notice" role="status">{notice}</p>}
      </form>
    </section>
    <section className="tool-page-card proxy-ip-card" aria-labelledby="proxy-ip-title">
      <div className="proxy-ip-heading"><Globe2 size={24}/><div><h2 id="proxy-ip-title">IP de saída</h2><p>Último IP observado pelo servidor através do proxy.</p></div></div>
      <div className={`proxy-ip-value ${check?.status === 'ok' ? 'verified' : ''}`} aria-live="polite">{checking ? <><LoaderCircle size={22} className="spin"/>Verificando conexão…</> : !proxy.enabled ? 'Proxy desativado' : check?.status === 'ok' ? check.ip : check?.status === 'error' ? 'Conexão não verificada' : 'Aguardando verificação'}</div>
      {proxy.enabled && <div className="proxy-details"><span>Servidor do proxy</span><strong>{proxy.host}:{proxy.port}</strong>{check?.checked_at && <><span>Última verificação</span><strong>{new Date(check.checked_at).toLocaleString('pt-BR')}</strong></>}</div>}
      <button type="button" className="proxy-test-button" onClick={test} disabled={!online || !proxy.enabled || checking || changed}><RefreshCw size={18} className={checking ? 'spin' : ''}/>Verificar conexão</button>
      {changed && <p className="proxy-hint">Salve a configuração para verificar o IP.</p>}
      <p className="proxy-hint">Com o proxy ativo, uma falha de conexão interrompe a chamada.</p>
      {(error || (proxy.enabled && check?.status === 'error' && check.message)) && <div className="error-banner" role="alert">{error || check.message}</div>}
    </section>
  </main>;
}
