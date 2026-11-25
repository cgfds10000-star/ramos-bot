import {executionActive} from './api.js';
import React, { useEffect, useRef, useState } from 'react';
import { Check, ChevronDown, Eye, EyeOff, Info, KeyRound, Layers3, Minus, Monitor, Play, Plus, RefreshCw, Square } from 'lucide-react';

const VIRALIZE_KEY_STORAGE = 'ramos.viralize.apiKey';
const VIRALIZE_KEY_PATTERN = /^vzs_[A-Za-z0-9_-]{1,480}$/;

export default function MacrosPage({ sessions, api, refresh, status, online, selectedIds, onSelectionChange }) {
  const [apiKey, setApiKey] = useState(() => {
    try { return localStorage.getItem(VIRALIZE_KEY_STORAGE) || ''; }
    catch { return ''; }
  });
  const [showKey, setShowKey] = useState(false);
  const [linkedIds, setLinkedIds] = useState([]);
  const [taskType, setTaskType] = useState('follow');
  const [taskLimit, setTaskLimit] = useState('50');
  const [busy, setBusy] = useState(false);
  const [messages, setMessages] = useState({});
  const allCheckbox = useRef(null);
  const selected = sessions.filter(session => selectedIds.includes(session.id));
  const disconnected = selected.filter(session => !session.authenticated);
  const allSelected = sessions.length > 0 && selected.length === sessions.length;
  const someSelected = selected.length > 0 && !allSelected;
  const running = Object.values(status).some(executionActive);
  const validKey = VIRALIZE_KEY_PATTERN.test(apiKey.trim());
  const numericLimit = Number(taskLimit);
  const validLimit = Number.isInteger(numericLimit) && numericLimit >= 1 && numericLimit <= 500;
  const canApply = online && !busy && validKey && selected.length > 0 && !disconnected.length && !running;
  const canStart = canApply && validLimit && selected.every(session => linkedIds.includes(session.id));

  useEffect(() => {
    if (allCheckbox.current) allCheckbox.current.indeterminate = someSelected;
  }, [someSelected]);

  useEffect(() => {
    const activeIds = new Set(sessions.filter(session => session.authenticated).map(session => session.id));
    const existingIds = new Set(sessions.map(session => session.id));
    onSelectionChange(ids => ids.filter(id => existingIds.has(id)));
    setLinkedIds(ids => ids.filter(id => activeIds.has(id)));
  }, [sessions]);

  useEffect(() => {
    try {
      const normalized = apiKey.trim();
      if (VIRALIZE_KEY_PATTERN.test(normalized)) localStorage.setItem(VIRALIZE_KEY_STORAGE, normalized);
      else if (!normalized) localStorage.removeItem(VIRALIZE_KEY_STORAGE);
    } catch { /* armazenamento local pode estar bloqueado pelo navegador */ }
  }, [apiKey]);

  function toggleSession(id) {
    onSelectionChange(ids => ids.includes(id) ? ids.filter(value => value !== id) : [...ids, id]);
  }

  async function send(action, items) {
    if (busy || !online || !items.length) return;
    setBusy(true);
    try {
      if (action === 'start') {
        try {
          const ids = items.map(session => session.id);
          setMessages(previous => ({...previous, ...Object.fromEntries(ids.map(id => [id, 'Iniciando no servidor…']))}));
          await api.post('/viralize/start', {session_ids: ids, token: apiKey.trim(), type: taskType, limit: numericLimit});
          setMessages(previous => ({...previous, ...Object.fromEntries(ids.map(id => [id, '']))}));
        } catch (error) {
          setMessages(previous => ({...previous, ...Object.fromEntries(items.map(session => [session.id, error.message || 'Não foi possível iniciar no servidor.']))}));
        } finally {await refresh();}
        return;
      }
      for (const session of items) {
        try {
          const path = `/sessions/${session.id}/${action === 'stop' ? 'stop' : `viralize/${action}`}`;
          setMessages(previous => ({ ...previous, [session.id]: action === 'link' ? 'Vinculando conta…' : action === 'start' ? 'Iniciando rodada…' : action === 'skip' ? 'Pulando tarefa…' : 'Interrompendo…' }));
          const body = action === 'stop' ? {} : { token: apiKey.trim(), type: taskType, ...(action === 'start' ? { limit: numericLimit } : {}) };
          await api.post(path, body);
          if (action === 'link') setLinkedIds(ids => [...new Set([...ids, session.id])]);
          setMessages(previous => ({ ...previous, [session.id]: action === 'link' ? 'Conta vinculada no Viralize.' : '' }));
        } catch (error) {
          if (action === 'link') setLinkedIds(ids => ids.filter(id => id !== session.id));
          setMessages(previous => ({ ...previous, [session.id]: error.message || 'Não foi possível concluir esta operação.' }));
        } finally {
          await refresh();
        }
      }
    } finally {
      setBusy(false);
    }
  }

  return <main className="macros-page" aria-label="Macros">
    <section className="macro-plugin-card" aria-labelledby="api-key-title">
      <div className="macro-card-heading">
        <h1 id="api-key-title">Chave da API</h1>
        <span className="viralize-logo" role="img" aria-label="Viralize Sociais"/>
      </div>

      <div className="macro-api-field">
        <KeyRound aria-hidden="true"/>
        <input
          type={showKey ? 'text' : 'password'}
          aria-labelledby="api-key-title"
          placeholder="Cole sua chave de API"
          value={apiKey}
          onChange={event => { setApiKey(event.target.value); setLinkedIds([]); setMessages({}); }}
          autoComplete="off"
          autoCapitalize="none"
          spellCheck="false"
          maxLength={512}
          disabled={busy || running}
        />
        <button type="button" className="macro-key-toggle" aria-label={showKey ? 'Ocultar chave da API' : 'Mostrar chave da API'} aria-pressed={showKey} onClick={() => setShowKey(value => !value)}>
          {showKey ? <EyeOff aria-hidden="true"/> : <Eye aria-hidden="true"/>}
        </button>
      </div>

      <p className="macro-flow-note">A chave fica salva neste navegador. Ao vincular ou iniciar, o servidor a utiliza na integração com o Viralize.</p>

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

      <p className="macro-selection-count" aria-live="polite">{selected.length === 1 ? '1 sessão selecionada' : `${selected.length} sessões selecionadas`}</p>
      {disconnected.length > 0 && <p className="macro-flow-note" role="status">Conecte {disconnected.length === 1 ? disconnected[0].name : 'as sessões selecionadas que estão desconectadas'} na aba Sessões antes de aplicar o plugin.</p>}
      <button type="button" className="macro-apply-button" disabled={!canApply} onClick={() => send('link', selected)}><Plus aria-hidden="true"/>Aplicar plugin</button>
    </section>

    <section className="macro-execution-card" aria-labelledby="macro-execution-title" aria-busy={busy || running}>
      <div className="macro-execution-state">
        <h2 id="macro-execution-title">Execução</h2>
        <p className="macro-execution-subtitle">Configurar e iniciar a execução das tarefas</p>
        <span className={`macro-stopped ${running ? 'is-running' : ''}`}><Square aria-hidden="true" fill="currentColor"/>{running ? Object.values(status).some(run => run.status === 'running') ? 'Em andamento' : Object.values(status).some(run => run.status === 'recovering') ? 'Restaurando' : 'Aguardando tarefas' : 'Parada'}</span>
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
        <input type="number" min="1" max="500" step="1" inputMode="numeric" value={taskLimit} onChange={event => setTaskLimit(event.target.value)} disabled={busy || running} aria-describedby="macro-limit-help"/>
      </label>
      <button type="button" className="macro-start-button" disabled={!canStart} onClick={() => send('start', selected)}><Play aria-hidden="true"/>Iniciar</button>
      <div id="macro-limit-help" className="macro-execution-note"><Info aria-hidden="true"/><p>Defina o limite de cada rodada. No modo contínuo, o servidor aguarda novas tarefas e inicia a próxima rodada automaticamente. Você pode interromper a execução a qualquer momento.</p></div>
      <ul className="macro-run-list" aria-live="polite">
        {sessions.filter(session => status[session.id] || messages[session.id]).map(session => {
          const run = status[session.id];
          const stateLabel = run?.status === 'waiting' ? 'Aguardando tarefas' : run?.status === 'recovering' ? 'Restaurando' : run?.status === 'running' ? 'Em execução' : run?.status === 'done' ? 'Concluído' : run?.status === 'empty' ? 'Fila vazia' : run?.status === 'attention' ? 'Atenção' : run?.status === 'error' ? 'Erro' : '';
          return <li key={session.id}>
            <div className="macro-run-heading"><strong>{session.name}</strong>{stateLabel && <span className={`macro-run-status is-${run.status}`}>{stateLabel}</span>}</div>
            <p>{messages[session.id] || run?.message}</p>{run && <p>{run.completed_total || 0} tarefas enviadas para análise · Rodada {run.round || 1} · Execução no servidor</p>}
            {executionActive(run) && <button type="button" className="text-button" disabled={busy || !online} onClick={() => send('stop', [session])}>Interromper</button>}
            {run?.can_skip && <button type="button" className="text-button" disabled={busy || !online || !validKey || !session.authenticated || !linkedIds.includes(session.id)} onClick={() => send('skip', [session])}>Pular tarefa</button>}
          </li>;
        })}
      </ul>
    </section>
  </main>;
}
