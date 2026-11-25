import test from 'node:test';
import assert from 'node:assert/strict';
import { filterLogs, logCounts, logSessionName } from '../src/logs.js';

const sessions = [{ id: 'one', name: 'Sessão 1' }, { id: 'two', name: 'Sessão 2' }];
const logs = [
  { id: 1, time: '23:59:58', tone: 'info', message: 'Módulo inicializado', session_id: null },
  { id: 2, time: '23:59:59', tone: 'success', message: 'Sessão conectada', session_id: 'one' },
  { id: 3, time: '00:00:01', tone: 'error', message: 'Falha de conexão com a API', session_id: 'two' },
  { id: 4, time: '00:00:02', tone: 'warning', message: 'Fila vazia', session_id: 'one' },
];

test('logs preservam a ordem de chegada na virada da meia-noite e não alteram a fonte', () => {
  assert.deepEqual(filterLogs(logs).map(item => item.id), [4, 3, 2, 1]);
  assert.deepEqual(filterLogs(logs, { order: 'oldest' }).map(item => item.id), [1, 2, 3, 4]);
  assert.deepEqual(logs.map(item => item.id), [1, 2, 3, 4]);
});

test('busca ignora acentos e combina a sessão e o tipo sem perder a mensagem exportada', () => {
  const matches = filterLogs(logs, { sessions, query: 'conexao', sessionId: 'two', tone: 'error' });
  assert.deepEqual(matches, [logs[2]]);
  assert.equal(matches[0].message, 'Falha de conexão com a API');
  assert.equal(filterLogs(logs, { sessions, query: 'sessao 1' }).length, 2);
  assert.equal(filterLogs(logs, { sessionId: 'one', tone: 'error' }).length, 0);
});

test('contadores e filtro Sistema usam os registros reais', () => {
  assert.deepEqual(logCounts(logs), { total: 4, success: 1, error: 1 });
  assert.deepEqual(logCounts(filterLogs(logs, { sessionId: 'one' })), { total: 2, success: 1, error: 0 });
  assert.deepEqual(filterLogs(logs, { sessionId: '__system__' }), [logs[0]]);
  assert.equal(logSessionName(logs[0], sessions), 'Sistema');
  assert.equal(logSessionName({ session_id: 'removed' }, sessions), 'Sessão removida');
});
