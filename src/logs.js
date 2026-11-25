export function logSessionName(item, sessions) {
  if (!item.session_id) return 'Sistema';
  return sessions.find(session => session.id === item.session_id)?.name || 'Sessão removida';
}

const normalize = value => String(value ?? '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLocaleLowerCase('pt-BR');

export function filterLogs(logs, { sessions = [], sessionId = '', tone = 'all', query = '', order = 'newest' } = {}) {
  const needle = normalize(query).trim();
  const result = logs.filter(item => {
    if (sessionId === '__system__' ? Boolean(item.session_id) : sessionId && item.session_id !== sessionId) return false;
    if (tone !== 'all' && item.tone !== tone) return false;
    return !needle || normalize(`${item.message} ${item.time} ${logSessionName(item, sessions)}`).includes(needle);
  });
  return order === 'newest' ? result.reverse() : result;
}

export function logCounts(logs) {
  return { total: logs.length, success: logs.filter(item => item.tone === 'success').length, error: logs.filter(item => item.tone === 'error').length };
}
