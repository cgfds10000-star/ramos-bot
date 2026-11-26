import {executionActive} from './api.js';
import React, { useEffect, useRef, useState } from 'react';
import { Check, ChevronDown, Clock3, Layers3, LoaderCircle, Minus, Monitor, Play, Plus, RefreshCw, Square } from 'lucide-react';

const SPEED_STORAGE = 'ramos.viralize.speed';
const DEFAULT_PROFILES = {moderate: {label: 'Moderado'}, calm: {label: 'Pausado'}, slow: {label: 'Lento'}};

export default function MacrosPage({ sessions, api, refresh, status, online, selectedIds, onSelectionChange, pacingProfiles, apiKey, validKey, busy, setBusy, onConfigure }) {
  const [linkedIds, setLinkedIds] = useState([]);
  const [taskType, setTaskType] = useState('follow');
  const [taskLimit, setTaskLimit] = useState('50');
  const [speed, setSpeed] = useState(() => {
    try { const value = localStorage.getItem(SPEED_STORAGE); return Object.hasOwn(DEFAULT_PROFILES, value) ? value : 'moderate'; }
    catch { return 'moderate'; }
  });
  const [actionPending, setActionPending] = useState('');
  const [messages, setMessages] = useState({});
  const allCheckbox = useRef(null);
  const selected = sessions.filter(session => selectedIds.includes(session.id));
  const disconnected = selected.filter(session => !session.authenticated);
  const allSelected = sessions.length > 0 && selected.length === sessions.length;
  const someSelected = selected.length > 0 && !allSelected;
  const activeSessions = sessions.filter(session => executionActive(status[session.id]));
  const running = activeSessions.length > 0;
  const profiles = pacingProfiles || DEFAULT_PROFILES;
  const profile = profiles[speed];
  const numericLimit = Number(taskLimit);
  const validLimit = Number.isInteger(numericLimit) && numericLimit >= 1 && numericLimit <= 500;
  const canApply = online && !busy && validKey && selected.length > 0 && !disconnected.length && !running;
  const canStart = canApply && validLimit && selected.every(session => linkedIds.includes(session.id));

  useEffect(() => {
    if (allCheckbox.current) allCheckbox.current.indeterminate = someSelected;
  }, [someSelected]);

  useEffect(() => {
    try { localStorage.setItem(SPEED_STORAGE, speed); } catch { /* armazenamento opcional */ }
  }, [speed]);


  useEffect(() => {
    const activeIds = new Set(sessions.filter(session => session.authenticated).map(session => session.id));
    const existingIds = new Set(sessions.map(session => session.id));
    onSelectionChange(ids => ids.filter(id => existingIds.has(id)));
    setLinkedIds(ids => ids.filter(id => activeIds.has(id)));
  }, [sessions]);

  function toggleSession(id) {
    onSelectionChange(ids => ids.includes(id) ? ids.filter(value => value !== id) : [...ids, id]);
  }

  async function send(action, items) {
    if (busy || !online || !items.length) return;
    setBusy(true); setActionPending(action);
    try {
      if (action === 'start') {
        try {
          const ids = items.map(session => session.id);
          setMessages(previous => ({...previous, ...Object.fromEntries(ids.map(id => [id, '']))}));
          await api.post('/viralize/start', {session_ids: ids, token: apiKey.trim(), type: taskType, limit: numericLimit, speed});
          setMessages(previous => ({...previous, ...Object.fromEntries(ids.map(id => [id, '']))}));
        } catch (error) {
          setMessages(previous => ({...previous, ...Object.fromEntries(items.map(session => [session.id, error.message || 'Não foi possível iniciar no servidor.']))}));
        } finally {await refresh();}
        return;
      }
      for (const session of items) {
        try {
          const path = `/sessions/${session.id}/${action === 'stop' ? 'stop' : `viralize/${action}`}`;
          setMessages(previous => ({ ...previous, [session.id]: '' }));
          const body = action === 'stop' ? {} : { token: apiKey.trim(), type: taskType, speed, ...(action === 'start' ? { limit: numericLimit } : {}) };
          await api.post(path, body);
          if (action === 'link') setLinkedIds(ids => [...new Set([...ids, session.id])]);
          setMessages(previous => ({ ...previous, [session.id]: '' }));
        } catch (error) {
          if (action === 'link') setLinkedIds(ids => ids.filter(id => id !== session.id));
          setMessages(previous => ({ ...previous, [session.id]: error.message || 'Não foi possível concluir esta operação.' }));
        } finally {
          await refresh();
        }
      }
    } finally {
      setBusy(false); setActionPending('');
    }
  }

  return <main className="macros-page" aria-label="Macros">

    <section className="macro-execution-card" aria-labelledby="macro-execution-title" aria-busy={busy || running}>
      <div className="macro-execution-state">
        <h2 id="macro-execution-title">Execução</h2>
        <span className={`macro-stopped ${running ? 'is-running' : ''}`}><Square aria-hidden="true" fill="currentColor"/>{running ? Object.values(status).some(run => run.status === 'running') ? 'Em andamento' : Object.values(status).some(run => run.status === 'recovering') ? 'Restaurando' : Object.values(status).some(run => run.status === 'waiting' && (run.phase?.startsWith('pace_') || run.phase === 'between_rounds')) ? 'Em intervalo' : 'Aguardando tarefas' : 'Parada'}</span>
      </div>
    <div className="macro-session-picker">
      <fieldset className="macro-sessions" disabled={busy || running}>
        <legend>Sessões para executar</legend>
        <label className="macro-session-option macro-select-all">
          <input ref={allCheckbox} type="checkbox" checked={allSelected} disabled={!sessions.length} onChange={() => onSelectionChange(allSelected ? [] : sessions.map(session => session.id))}/>
          <span className="macro-session-name">Todas as sessões</span>
          <span className={`macro-checkbox ${someSelected || allSelected ? 'is-checked' : ''}`} aria-hidden="true">{someSelected ? <Minus/> : allSelected ? <Check/> : null}</span>
        </label>
        <div className="macro-session-options">
          {sessions.map(session => <label className="macro-session-option" key={session.id}>
            <input type="checkbox" checked={selectedIds.includes(session.id)} onChange={() => toggleSession(session.id)}/>
            <Monitor className="macro-session-icon" aria-hidden="true"/>
            <span className="macro-session-name">{session.name}{!session.authenticated ? ' · desconectada' : linkedIds.includes(session.id) ? ' · vinculada' : ''}</span>
            <span className={`macro-checkbox ${selectedIds.includes(session.id) ? 'is-checked' : ''}`} aria-hidden="true">{selectedIds.includes(session.id) && <Check/>}</span>
          </label>)}
        </div>
        {!sessions.length && <p className="macro-empty-sessions">Crie uma sessão na aba Sessões para começar.</p>}
      </fieldset>

      {!validKey && <button type="button" className="macro-configure-key" onClick={onConfigure}>Adicionar chave da API nas configurações</button>}
      <div className="macro-selection-footer"><p className="macro-selection-count" aria-live="polite">{selected.length === 1 ? '1 sessão selecionada' : `${selected.length} sessões selecionadas`}</p>
      <button type="button" className="macro-apply-button" disabled={!canApply} onClick={() => send('link', selected)}>{actionPending === 'link' ? <LoaderCircle className="spin" aria-hidden="true"/> : <Plus aria-hidden="true"/>}{actionPending === 'link' ? 'Vinculando…' : 'Aplicar plugin'}</button></div>
      {disconnected.length > 0 && <p className="macro-flow-note" role="status">Conecte {disconnected.length === 1 ? disconnected[0].name : 'as sessões selecionadas'} antes de aplicar o plugin.</p>}
    </div>

      <label className="macro-task-type">
        <span className="macro-control-caption"><Layers3 aria-hidden="true"/><span>Tipo de tarefa</span></span>
        <span className="macro-select-wrap"><select value={taskType} onChange={event => setTaskType(event.target.value)} disabled={busy || running}>
          <option value="follow">Seguir perfis</option>
          <option value="like">Curtir publicações</option>
          <option value="follow,like">Seguir e curtir</option>
        </select><ChevronDown aria-hidden="true"/></span>
      </label>
      <label className="macro-task-limit">
        <span className="macro-control-caption"><RefreshCw aria-hidden="true"/><span>Limite da rodada</span></span>
        <input type="number" min="1" max="500" step="1" inputMode="numeric" value={taskLimit} onChange={event => setTaskLimit(event.target.value)} disabled={busy || running}/>
      </label>
      <div className="macro-speed-control">
        <label className="macro-speed">
          <span className="macro-control-caption"><Clock3 aria-hidden="true"/><span>Ritmo das ações</span></span>
          <span className="macro-select-wrap"><select value={speed} onChange={event => setSpeed(event.target.value)} disabled={busy || running} aria-describedby="macro-speed-help">
            {Object.entries(profiles).map(([value, item]) => <option key={value} value={value}>{item.label}</option>)}
          </select><ChevronDown aria-hidden="true"/></span>
        </label>
        <p id="macro-speed-help" className="macro-flow-note">Pausas variáveis ao aceitar, executar e confirmar.{profile?.task && <> Intervalo de {profile.task[0]}–{profile.task[1]} s entre tarefas e {profile.round[0]}–{profile.round[1]} s entre rodadas.</>}</p>
      </div>
      <button type="button" className={`macro-start-button ${running || actionPending === 'stop' ? 'is-stop' : ''}`} disabled={busy || !online || (!running && !canStart)} onClick={() => send(running ? 'stop' : 'start', running ? activeSessions : selected)}>
        {actionPending === 'start' || actionPending === 'stop' ? <LoaderCircle className="spin" aria-hidden="true"/> : running ? <Square aria-hidden="true" fill="currentColor"/> : <Play aria-hidden="true"/>}
        {actionPending === 'stop' ? 'Parando…' : actionPending === 'start' ? 'Iniciando…' : running ? 'Parar' : 'Iniciar'}
      </button>
      <hr className="macro-divider"/>
      {sessions.some(session => messages[session.id] || ['error', 'attention'].includes(status[session.id]?.status)) && <ul className="macro-run-list" aria-live="polite">
        {sessions.filter(session => messages[session.id] || ['error', 'attention'].includes(status[session.id]?.status)).map(session => {
          const run = status[session.id];
          return <li key={session.id}>
            <strong>{session.name}</strong>
            <p role="status">{messages[session.id] || run?.message}</p>
            {run?.can_skip && <button type="button" className="text-button" disabled={busy || !online || !validKey || !session.authenticated || !linkedIds.includes(session.id)} onClick={() => send('skip', [session])}>Pular tarefa</button>}
          </li>;
        })}
      </ul>}

    </section>
  </main>;
}
