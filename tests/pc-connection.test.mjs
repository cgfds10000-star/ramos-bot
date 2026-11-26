import test from 'node:test';
import assert from 'node:assert/strict';
import {consumePcConnection, PC_BASE, clearPcConnection} from '../src/pc-connection.js';
import {RamosApi} from '../src/api.js';

test('conexão local remove o segredo do endereço e nunca escolhe outro destino', () => {
  const token='a'.repeat(43), saved=new Map(), changed=[];
  const store={getItem:k=>saved.get(k),setItem:(k,v)=>saved.set(k,v),removeItem:k=>saved.delete(k)};
  const connection=consumePcConnection({hash:'#ramos_pc='+token,pathname:'/',search:''},store,{replaceState:(_a,_b,url)=>changed.push(url)});
  assert.deepEqual(connection,{token});
  assert.deepEqual(changed,['/']);
  assert.equal(PC_BASE,'http://127.0.0.1:8765');
  assert.deepEqual(consumePcConnection({hash:'',pathname:'/',search:''},store,{}),{token});
  clearPcConnection(store); assert.equal(saved.size,0);
  assert.ok(consumePcConnection({hash:'#ramos_pc=https://example.com',pathname:'/',search:''},store,{replaceState(){}}).error);
});

test('credenciais do login vão ao loopback e uma falha não tenta o Railway', async () => {
  const original=global.fetch, calls=[];
  global.fetch=async(url,init)=>{calls.push({url,init});throw new TypeError('offline');};
  try {
    const api=new RamosApi(PC_BASE,{token:'synthetic-local-key'});
    await assert.rejects(api.post('/sessions/abc/login',{username:'teste',password:'synthetic-password'}),/Conector PC/);
    assert.equal(calls.length,1);
    assert.equal(calls[0].url,PC_BASE+'/api/sessions/abc/login');
    assert.equal(calls[0].init.targetAddressSpace,'loopback');
    assert.equal(calls[0].init.headers.Authorization,'Bearer synthetic-local-key');
  } finally {global.fetch=original;}
});
