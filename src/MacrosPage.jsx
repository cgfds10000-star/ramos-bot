import React, { useEffect, useRef, useState } from 'react';
import { Check, Eye, EyeOff, KeyRound, Minus, Monitor, Play, Plus } from 'lucide-react';

export default function MacrosPage({ sessions }) {
  const [apiKey, setApiKey] = useState('');
  const [showKey, setShowKey] = useState(false);
  const [selectedIds, setSelectedIds] = useState([]);
  const allCheckbox = useRef(null);
  const selected = sessions.filter(session => selectedIds.includes(session.id));
  const allSelected = sessions.length > 0 && selected.length === sessions.length;
  const someSelected = selected.length > 0 && !allSelected;

  useEffect(() => {
    if (allCheckbox.current) allCheckbox.current.indeterminate = someSelected;
  }, [someSelected]);

  function toggleSession(id) {
    setSelectedIds(ids => ids.includes(id) ? ids.filter(value => value !== id) : [...ids, id]);
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
          onChange={event => setApiKey(event.target.value)}
          autoComplete="off"
          autoCapitalize="none"
          spellCheck="false"
          maxLength={512}
        />
        <button type="button" className="macro-key-toggle" aria-label={showKey ? 'Ocultar chave da API' : 'Mostrar chave da API'} aria-pressed={showKey} onClick={() => setShowKey(value => !value)}>
          {showKey ? <EyeOff aria-hidden="true"/> : <Eye aria-hidden="true"/>}
        </button>
      </div>

      <fieldset className="macro-sessions">
        <legend>Sessões para executar</legend>
        <label className="macro-session-option macro-select-all">
          <input ref={allCheckbox} type="checkbox" checked={allSelected} disabled={!sessions.length} onChange={() => setSelectedIds(allSelected ? [] : sessions.map(session => session.id))}/>
          <span className="macro-session-name">Todas as sessões</span>
          <span className={`macro-checkbox ${someSelected || allSelected ? 'is-checked' : ''}`} aria-hidden="true">{someSelected ? <Minus/> : allSelected ? <Check/> : null}</span>
        </label>
        <div className="macro-session-options">
          {sessions.map(session => <label className="macro-session-option" key={session.id}>
            <input type="checkbox" checked={selectedIds.includes(session.id)} onChange={() => toggleSession(session.id)}/>
            <Monitor className="macro-session-icon" aria-hidden="true"/>
            <span className="macro-session-name">{session.name}</span>
            <span className={`macro-checkbox ${selectedIds.includes(session.id) ? 'is-checked' : ''}`} aria-hidden="true">{selectedIds.includes(session.id) && <Check/>}</span>
          </label>)}
        </div>
        {!sessions.length && <p className="macro-empty-sessions">Suas sessões aparecerão aqui.</p>}
      </fieldset>

      <p className="macro-selection-count" aria-live="polite">{selected.length === 1 ? '1 sessão selecionada' : `${selected.length} sessões selecionadas`}</p>
      <button type="button" className="macro-apply-button" disabled title="Disponível apenas como interface visual nesta versão." aria-describedby="macro-preview-note"><Plus aria-hidden="true"/>Aplicar plugin</button>
      <span id="macro-preview-note" className="macro-visually-hidden">Interface visual. A chave não é salva ou enviada e o plugin não é executado.</span>
    </section>

    <section className="macro-execution-card" aria-labelledby="macro-execution-title">
      <div className="macro-execution-state">
        <h2 id="macro-execution-title">Execução</h2>
        <span className="macro-stopped">Parada</span>
      </div>
      <button type="button" className="macro-start-button" disabled aria-describedby="macro-preview-note"><Play aria-hidden="true"/>Iniciar</button>
      <p>Configure a API para começar.</p>
    </section>
  </main>;
}
