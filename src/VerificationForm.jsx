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
  const active = useRef(true);
  const inFlight = useRef(false);
  const controller = useRef(null);
  const afterSend = useRef(onSubmitted);
  afterSend.current = onSubmitted;
  const kind = verificationInputKind(value, challenge.kind);
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
    setSending(true); setError('');
    controller.current = new AbortController();
    let entered = value;
    setValue('');
    try {
      const body = kind === 'secret'
        ? {secret: entered}
        : {code: await resolveVerificationCode(entered, challenge.kind, {now: () => api.serverNow(), signal: controller.current.signal})};
      entered = '';
      if (!active.current) return;
      try {
        await api.post(`/sessions/${sessionId}/input`, {...body, challenge_id: challenge.id});
      } finally { delete body.secret; delete body.code; }
      if (active.current) { setSent(true); await afterSend.current?.(); }
    } catch (failure) {
      if (active.current && failure.name !== 'AbortError') setError(failure.message);
    } finally {
      entered = '';
      inFlight.current = false;
      if (active.current) setSending(false);
    }
  }, [api, challenge.id, challenge.kind, sessionId, value, sent, kind]);

  return <form className="verification" onSubmit={event => { event.preventDefault(); if (!pending && online && kind) submit(); }}>
    <ShieldCheck size={21}/><div><strong>Verificação em duas etapas</strong>
      <p>{acceptsSecret ? 'Digite o codigo 2FA ou cole sua chave Base32 com espacos. O codigo e gerado automaticamente.' : challenge.message}</p>
      <div className="verification-row"><input autoFocus type={kind === 'secret' ? 'password' : 'text'} autoComplete={acceptsSecret ? 'off' : 'one-time-code'} inputMode={acceptsSecret ? 'text' : 'numeric'} autoCapitalize={acceptsSecret ? 'characters' : 'none'} spellCheck={false} maxLength={acceptsSecret ? 256 : 8} aria-label={acceptsSecret ? 'Codigo ou chave 2FA' : 'Codigo de verificacao'} placeholder={acceptsSecret ? 'Codigo ou chave 2FA' : '000000'} value={value} onChange={event => { setValue(event.target.value); setError(''); }} disabled={sending || sent} required/>
        <button className="small-button" disabled={pending || sending || sent || !online || !kind}>{sending ? <LoaderCircle className="spin" size={17}/> : 'Confirmar'}</button>
      </div>
      {preview && kind === 'secret' && !sending && !sent && <div className="verification-totp">
        <span>Código gerado</span>
        <output aria-label="Código 2FA gerado">{preview.code}</output>
        <span className="verification-totp-validity">Ativo · {preview.remaining}s</span>
      </div>}
      <p className="verification-hint" aria-live="polite">{sent ? 'Código enviado. Aguardando o Instagram…' : sending ? 'Preparando e enviando o código 2FA…' : kind === 'secret' ? 'Ao confirmar, o Railway gera o codigo no momento do envio. A chave nao e salva.' : 'Digite o codigo solicitado e toque em Confirmar.'}</p>
      {error && <div className="error-banner" role="alert">{error}</div>}
    </div>
  </form>;
}
