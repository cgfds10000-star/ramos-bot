import React, {useState} from 'react';
import {AlignLeft, CalendarDays, ChevronDown, CircleCheck, CircleX, Clock3, FileText, History, Search, TriangleAlert} from 'lucide-react';
import {filterLogs, formatLogTime, logCounts, logSessionName} from './logs.js';

const types = {info: 'Informação', success: 'Sucesso', warning: 'Aviso', error: 'Erro', command: 'Comando'};
const filters = [['all', 'Todos', null], ['success', 'Sucesso', CircleCheck], ['warning', 'Avisos', TriangleAlert], ['error', 'Erros', CircleX]];

export default function LogsPage({logs, sessions, online, checking}) {
  const [query, setQuery] = useState('');
  const [sessionId, setSessionId] = useState('');
  const [period, setPeriod] = useState('today');
  const [tone, setTone] = useState('all');
  const [order, setOrder] = useState('newest');
  const scoped = filterLogs(logs, {sessionId, period});
  const counts = logCounts(scoped);
  const visible = filterLogs(logs, {sessions, sessionId, period, tone, query, order});

  return <main className="logs-page" aria-label="Logs">
    <h1 className="logs-sr-only">Logs</h1>
    <div className="logs-live-row"><span className={`logs-live ${online ? 'is-live' : ''}`} role="status"><i/>{checking ? 'Conectando' : online ? 'Ao vivo' : 'Offline'}</span></div>
    <section className="logs-summary" aria-label="Resumo dos registros">
      <div className="logs-summary-total"><FileText aria-hidden="true"/><span><strong>{counts.total}</strong><span>{counts.total === 1 ? 'registro' : 'registros'}</span></span></div>
      <div className="logs-summary-success"><CircleCheck aria-hidden="true"/><span><strong>{counts.success}</strong><span>{counts.success === 1 ? 'sucesso' : 'sucessos'}</span></span></div>
      <div className="logs-summary-error"><CircleX aria-hidden="true"/><span><strong>{counts.error}</strong><span>{counts.error === 1 ? 'erro' : 'erros'}</span></span></div>
    </section>
    <label className="logs-search"><Search aria-hidden="true"/><input aria-label="Buscar no histórico" placeholder="Buscar no histórico..." value={query} onChange={event => setQuery(event.target.value)}/></label>
    <div className="logs-tools">
      <span className="logs-select"><select aria-label="Filtrar logs por sessão" value={sessionId} onChange={event => setSessionId(event.target.value)}>
        <option value="">Todas as sessões</option><option value="__system__">Sistema</option>
        {sessions.map(session => <option key={session.id} value={session.id}>{session.name}</option>)}
      </select><ChevronDown aria-hidden="true"/></span>
      <span className="logs-select logs-period"><CalendarDays aria-hidden="true"/><select aria-label="Período do histórico" value={period} onChange={event => setPeriod(event.target.value)}>
        <option value="today">Hoje</option><option value="7days">Últimos 7 dias</option><option value="all">Todo o histórico</option>
      </select><ChevronDown aria-hidden="true"/></span>
    </div>
    <div className="logs-filters" role="group" aria-label="Filtrar por tipo de registro">
      {filters.map(([value, label, Icon]) => <button type="button" key={value} aria-pressed={tone === value}
        className={`${tone === value ? 'active ' : ''}logs-filter-${value}`} onClick={() => setTone(value)}>
        {Icon && <Icon aria-hidden="true"/>}{label}
      </button>)}
    </div>
    <section className="logs-activity" aria-labelledby="logs-activity-title">
      <div className="logs-activity-heading">
        <h2 id="logs-activity-title"><span className="logs-history-icon"><History aria-hidden="true"/></span><span>Histórico de atividades ({visible.length})</span></h2>
        <span className="logs-select logs-order"><select aria-label="Ordem dos registros" value={order} onChange={event => setOrder(event.target.value)}><option value="newest">Mais recentes</option><option value="oldest">Mais antigos</option></select><ChevronDown aria-hidden="true"/></span>
      </div>
      <div className="logs-feed" tabIndex={0} role="region" aria-label="Registros de atividade">
        <table className="logs-table">
          <thead><tr><th scope="col">Hora</th><th scope="col">Descrição</th></tr></thead>
          <tbody>{visible.map(item => {
            const kind = Object.hasOwn(types, item.tone) ? item.tone : 'info';
            return <tr className={`logs-entry logs-tone-${kind}`} key={item.id}>
              <td className="logs-entry-time"><Clock3 aria-hidden="true"/><time dateTime={item.timestamp}>{formatLogTime(item)}</time><span className="logs-sr-only">{types[kind]}</span></td>
              <td className="logs-entry-body"><p>{item.message}</p>{item.session_id && <span>{logSessionName(item, sessions)}</span>}</td>
            </tr>;
          })}</tbody>
        </table>
        {!visible.length && <div className="logs-empty"><AlignLeft aria-hidden="true"/><strong>{logs.length ? 'Nenhum registro encontrado' : 'Nenhum registro ainda'}</strong><p>{logs.length ? 'Ajuste a busca ou os filtros para ver outras atividades.' : 'As atividades do sistema aparecerão aqui.'}</p></div>}
      </div>
      {(query || sessionId || tone !== 'all') && <p className="logs-filter-result" role="status">{visible.length} de {scoped.length} registros</p>}
    </section>
  </main>;
}
