import React, {useCallback, useEffect, useRef, useState} from 'react';
import {LoaderCircle, ShieldCheck} from 'lucide-react';
import {acceptsAuthenticatorSecret, generateTotp, resolveVerificationCode, verificationInputKind} from './totp.js';
import './hi3d-verification.css';

export default function VerificationForm({api, sessionId, challenge, pending, online, onSubmitted}) {
  const [value, setValue] = useState('');
  const [sending, setSending] = useState(false);
  const [error, setError] = useState('');
  const [sent, setSent] = useState(false);
  const [preview, setPreview] = useState(null);
  const [entryMode, setEntryMode] = useState('code');
  const active = useRef(true);
  const inFlight = useRef(false);
  const autoAttempted = useRef(false);
  const controller = useRef(null);
  const afterSend = useRef(onSubmitted);
  afterSend.current = onSubmitted;
  const detectedKind = verificationInputKind(value, challenge.kind);
  const kind = detectedKind === entryMode ? detectedKind : null;
  const acceptsSecret = acceptsAuthenticatorSecret(challenge.kind);

  useEffect(() => { active.current = true; return () => { active.current = false; controller.current?.abort(); }; }, []);

  useEffect(() => {
    setPreview(null);
    if (kind !== 'secret' || sending || sent) return;
    let current = true;
    let previousWindow = null;
    let currentCode = '';
    const update = async () => {
      const time = api.serverNow();
      const window = Math.floor(time / 30000);
      try {
        if (previousWindow !== window) {
          currentCode = await generateTotp(value, time);
          previousWindow = window;
        }
        if (current) setPreview({code: currentCode, remaining: 30 - Math.floor((time % 30000) / 1000)});
      } catch (failure) {
        if (current) setError(failure.message);
      }
    };
    update();
    const timer = setInterval(update, 1000);
    return () => { current = false; clearInterval(timer); };
  }, [api, value, kind, sending, sent]);

  const submit = useCallback(async () => {
    if (inFlight.current || sent || !active.current) return;
    inFlight.current = true;
    autoAttempted.current = true;
    setSending(true); setError('');
    controller.current = new AbortController();
    let entered = value;
    setValue('');
    try {
      const code = await resolveVerificationCode(entered, challenge.kind, {now: () => api.serverNow(), signal: controller.current.signal});
      entered = '';
      if (!active.current) return;
      await api.post(`/sessions/${sessionId}/input`, {code, challenge_id: challenge.id});
      if (active.current) { setSent(true); await afterSend.current?.(); }
    } catch (failure) {
      if (active.current && failure.name !== 'AbortError') setError(failure.message);
    } finally {
      entered = '';
      inFlight.current = false;
      if (active.current) setSending(false);
    }
  }, [api, challenge.id, challenge.kind, sessionId, value, sent]);

  useEffect(() => {
    if (kind !== 'secret' || pending || sending || sent || !online || autoAttempted.current) return;
    const timer = setTimeout(submit, 1200);
    return () => clearTimeout(timer);
  }, [kind, value, pending, sending, sent, online, submit]);

  return <form className="verification" onSubmit={event => { event.preventDefault(); if (!pending && online && kind) submit(); }}>
    <ShieldCheck size={21}/><div><strong>Verificação em duas etapas</strong>
      <p>{entryMode === 'secret' ? 'Cole sua chave secreta Base32, com ou sem espaços, para gerar o código de 6 dígitos.' : challenge.message}</p>
      <div className="verification-row"><input autoFocus type={entryMode === 'secret' ? 'password' : 'text'} autoComplete={entryMode === 'secret' ? 'off' : 'one-time-code'} inputMode={entryMode === 'secret' ? 'text' : 'numeric'} autoCapitalize={entryMode === 'secret' ? 'characters' : 'none'} spellCheck={false} maxLength={entryMode === 'secret' ? 256 : challenge.kind === 'authenticator' ? 6 : 8} aria-label={entryMode === 'secret' ? 'Chave secreta Base32' : 'Código de verificação'} placeholder={entryMode === 'secret' ? 'Chave secreta Base32' : '000000'} value={value} onChange={event => { setValue(event.target.value); setError(''); }} disabled={sending || sent} required/>
        <button className="small-button" disabled={pending || sending || sent || !online || !kind}>{sending ? <LoaderCircle className="spin" size={17}/> : 'Confirmar'}</button>
      </div>
      {preview && kind === 'secret' && !sending && !sent && <div className="verification-totp">
        <span>Código gerado</span>
        <output aria-label="Código 2FA gerado">{preview.code}</output>
        <span className="verification-totp-validity">Ativo · {preview.remaining}s</span>
      </div>}
      <p className="verification-hint" aria-live="polite">{sent ? 'Código enviado. Aguardando o Instagram…' : sending ? 'Preparando e enviando o código 2FA…' : entryMode === 'secret' ? 'A chave é convertida aqui no navegador. Somente o código gerado é enviado automaticamente ao Instagram.' : 'Digite o código solicitado e toque em Confirmar.'}</p>
      {acceptsSecret && <button type="button" className="text-button" disabled={sending || sent} onClick={() => { setEntryMode(mode => mode === 'code' ? 'secret' : 'code'); setValue(''); setError(''); setPreview(null); }}>{entryMode === 'secret' ? 'Digitar código' : 'Usar chave secreta'}</button>}
      {error && <div className="error-banner" role="alert">{error}</div>}
    </div>
  </form>;
}
