import React, { useState } from 'react';
import { AlignLeft, CircleCheck, CircleX, Download, FileText, Info, Search, Terminal, TriangleAlert } from 'lucide-react';
import { filterLogs, logCounts, logSessionName } from './logs.js';

const types = {
  info: { label: 'INFO', Icon: Info },
  success: { label: 'SUCESSO', Icon: CircleCheck },
  warning: { label: 'AVISO', Icon: TriangleAlert },
  error: { label: 'ERRO', Icon: CircleX },
  command: { label: 'COMANDO', Icon: Terminal },
};

export default function LogsPage({ logs, sessions, online, checking, onExport }) {
  const [query, setQuery] = useState('');
  const [sessionId, setSessionId] = useState('');
  const [tone, setTone] = useState('all');
  const [order, setOrder] = useState('newest');
  const scoped = filterLogs(logs, { sessionId });
  const counts = logCounts(scoped);
  const visible = filterLogs(logs, { sessions, sessionId, tone, query, order });

  return <main className="logs-page" aria-label="Logs">
    <h1 className="logs-sr-only">Logs</h1>
    <div className="logs-live-row"><span className={`logs-live ${online ? 'is-live' : ''}`} role="status"><i/>{checking ? 'Conectando' : online ? 'Ao vivo' : 'Offline'}</span></div>

    <section className="logs-summary" aria-label="Resumo dos registros">
      <div><FileText aria-hidden="true"/><span><strong>{counts.total}</strong><span>{counts.total === 1 ? 'registro' : 'registros'}</span></span></div>
      <div className="logs-summary-success"><CircleCheck aria-hidden="true"/><span><strong>{counts.success}</strong><span>{counts.success === 1 ? 'sucesso' : 'sucessos'}</span></span></div>
      <div className="logs-summary-error"><CircleX aria-hidden="true"/><span><strong>{counts.error}</strong><span>{counts.error === 1 ? 'erro' : 'erros'}</span></span></div>
    </section>

    <label className="logs-search"><Search aria-hidden="true"/><input aria-label="Buscar nos logs" placeholder="Buscar nos logs..." value={query} onChange={event => setQuery(event.target.value)}/></label>

    <div className="logs-tools">
      <select aria-label="Filtrar logs por sessão" value={sessionId} onChange={event => setSessionId(event.target.value)}>
        <option value="">Todas as sessões</option><option value="__system__">Sistema</option>
        {sessions.map(session => <option key={session.id} value={session.id}>{session.name}</option>)}
      </select>
      <button type="button" className="logs-export" disabled={!visible.length} onClick={() => onExport(visible)}><Download aria-hidden="true"/>Exportar</button>
    </div>

    <div className="logs-filters" role="group" aria-label="Filtrar por tipo de registro">
      {[['all', 'Todos'], ['success', 'Sucesso'], ['warning', 'Avisos'], ['error', 'Erros']].map(([value, label]) => <button type="button" key={value} aria-pressed={tone === value} className={tone === value ? 'active' : ''} onClick={() => setTone(value)}>{label}</button>)}
    </div>

    <section className="logs-activity" aria-labelledby="logs-activity-title">
      <div className="logs-activity-heading"><h2 id="logs-activity-title">Atividade recente</h2><select aria-label="Ordem dos registros" value={order} onChange={event => setOrder(event.target.value)}><option value="newest">Mais recentes</option><option value="oldest">Mais antigos</option></select></div>
      <div className="logs-feed" tabIndex={0} role="region" aria-label="Registros de atividade">
        {visible.length > 0 ? <ol>{visible.map(item => {
          const type = types[item.tone] || types.info;
          const Icon = type.Icon;
          return <li className={`logs-entry logs-tone-${types[item.tone] ? item.tone : 'info'}`} key={item.id}>
            <Icon className="logs-entry-icon" aria-hidden="true"/>
            <div className="logs-entry-meta"><span>{type.label}</span><time>{item.time}</time></div>
            <div className="logs-entry-body"><p>{item.message}</p><span>{logSessionName(item, sessions)}</span></div>
          </li>;
        })}</ol> : <div className="logs-empty"><AlignLeft aria-hidden="true"/><strong>{logs.length ? 'Nenhum registro encontrado' : 'Nenhum registro ainda'}</strong><p>{logs.length ? 'Ajuste a busca ou os filtros para ver outras atividades.' : 'As atividades do sistema aparecerão aqui.'}</p></div>}
      </div>
      {(query || sessionId || tone !== 'all') && <p className="logs-filter-result" role="status">{visible.length} de {scoped.length} registros</p>}
    </section>
  </main>;
}
