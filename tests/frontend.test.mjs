import test from 'node:test';
import assert from 'node:assert/strict';
import { RamosApi, parseCommand, selectSession, sessionBusy } from '../src/api.js';

test('terminal aceita somente comandos previstos e não executa shell', () => {
  assert.deepEqual(parseCommand('buscar "Carlos Farias" --limite 20'), { command: 'buscar', target: 'Carlos Farias', limit: 20 });
  assert.deepEqual(parseCommand('perfil @carlos'), { command: 'perfil', target: '@carlos', limit: 10 });
  for (const text of ['rm -rf /', 'curl example.com', 'buscar x --limite 0', 'buscar x --limite 2.5', 'perfil um dois', 'status x', 'curtir']) {
    assert.throws(() => parseCommand(text));
  }
  assert.equal(parseCommand('  '), null);
});

test('seleção recupera uma sessão válida após excluir a selecionada', () => {
  const state = { sessions: [{ id: 'one' }, { id: 'two' }] };
  assert.equal(selectSession(state, 'two').id, 'two');
  assert.equal(selectSession(state, 'deleted').id, 'one');
  assert.equal(selectSession({ sessions: [] }, 'one'), null);
  assert.equal(sessionBusy({ status: 'awaiting_input' }), true);
  assert.equal(sessionBusy({ status: 'connected' }), false);
});

test('API recusa uma página HTML em vez de anunciar sucesso', async () => {
  const original = global.fetch;
  global.fetch = async () => new Response('<html>offline</html>', { headers: { 'content-type': 'text/html' } });
  try { await assert.rejects(new RamosApi().state(), /não está disponível/); }
  finally { global.fetch = original; }
});

test('API envia o token de proteção recebido do servidor', async () => {
  const original = global.fetch;
  const calls = [];
  global.fetch = async (url, init) => {
    calls.push({ url, init });
    return new Response(JSON.stringify(calls.length === 1 ? { csrf: 'test-csrf' } : { ok: true }), { headers: { 'content-type': 'application/json' } });
  };
  try {
    const api = new RamosApi('http://localhost:8765/');
    await api.state(); await api.post('/sessions', {});
    assert.equal(calls[1].url, 'http://localhost:8765/api/sessions');
    assert.equal(calls[1].init.headers['X-Ramos-CSRF'], 'test-csrf');
    assert.equal(calls[1].init.method, 'POST');
  } finally { global.fetch = original; }
});


test('comandos preservam busca geral e seleção por ID', () => {
  assert.deepEqual(parseCommand('buscar "Carlos" --geral --limite 5'), {command:'buscar',target:'Carlos',limit:5,general:true});
  assert.deepEqual(parseCommand('perfil --id 123456'), {command:'perfil',target:'123456',limit:10,by_id:true});
  assert.deepEqual(parseCommand('seguir --id 123456'), {command:'seguir',target:'123456',limit:10,by_id:true});
  for(const value of ['perfil --id abc','seguir --id','seguir usuario --geral','buscar nome --limite 5 --limite 6']) assert.throws(()=>parseCommand(value));
});
