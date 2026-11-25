export function logSessionName(item, sessions) {
  if (!item.session_id) return 'Sistema';
  return sessions.find(session => session.id === item.session_id)?.name || 'Sessão removida';
}

const normalize = value => String(value ?? '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLocaleLowerCase('pt-BR');

export function formatLogTime(item) {
  const timestamp = Date.parse(item.timestamp);
  return Number.isFinite(timestamp) ? new Date(timestamp).toLocaleTimeString('pt-BR') : item.time;
}

function withinPeriod(item, period, now) {
  const timestamp = Date.parse(item.timestamp);
  if (period === 'all' || !Number.isFinite(timestamp)) return true;
  if (period === '7days') return timestamp >= now.getTime() - 7 * 86400000 && timestamp <= now.getTime();
  const start = new Date(now), end = new Date(now);
  start.setHours(0, 0, 0, 0);
  end.setDate(end.getDate() + 1);
  end.setHours(0, 0, 0, 0);
  return timestamp >= start.getTime() && timestamp < end.getTime();
}

export function filterLogs(logs, { sessions = [], sessionId = '', tone = 'all', query = '', order = 'newest', period = 'all', now = new Date() } = {}) {
  const needle = normalize(query).trim();
  const result = logs.filter(item => {
    if (sessionId === '__system__' ? Boolean(item.session_id) : sessionId && item.session_id !== sessionId) return false;
    if (tone !== 'all' && item.tone !== tone) return false;
    if (!withinPeriod(item, period, now)) return false;
    return !needle || normalize(`${item.message} ${item.time} ${formatLogTime(item)} ${logSessionName(item, sessions)}`).includes(needle);
  });
  return order === 'newest' ? result.reverse() : result;
}

export function logCounts(logs) {
  return { total: logs.length, success: logs.filter(item => item.tone === 'success').length, error: logs.filter(item => item.tone === 'error').length };
}
