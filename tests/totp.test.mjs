import test from 'node:test';
import assert from 'node:assert/strict';
import {decodeBase32, generateTotp, resolveVerificationCode, verificationInputKind} from '../src/totp.js';

// Chave pública dos vetores de teste do RFC 6238: ASCII "12345678901234567890".
const publicTestSecret = 'GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ';

test('TOTP coincide com os vetores SHA-1 do RFC 6238, inclusive após 2038', async () => {
  for (const [seconds, expected] of [[59,'94287082'],[1111111109,'07081804'],[1111111111,'14050471'],[1234567890,'89005924'],[2000000000,'69279037'],[20000000000,'65353130']]) {
    assert.equal(await generateTotp(publicTestSecret, seconds * 1000, 8), expected);
    assert.equal(await generateTotp(publicTestSecret, seconds * 1000), expected.slice(-6));
  }
});

test('chaves Base32 aceitam espaços, quebras de linha e letras minúsculas', async () => {
  const formatted = 'gezd gnbv gy3t qojq\n gezd gnbv gy3t qojq';
  assert.deepEqual(decodeBase32(formatted), new TextEncoder().encode('12345678901234567890'));
  assert.equal(await generateTotp(formatted, 59000), '287082');
  assert.equal(verificationInputKind(formatted, 'authenticator'), 'secret');
  assert.equal(decodeBase32('MFRGGZDFMZTWQ2LKME======').length, 11);
});

test('chave Base32 inválida ou incompleta nunca produz um código', async () => {
  for (const value of ['1234567890','GEZD0NBVGY3TQOJQ','GEZDGNBVGY3TQOJQ1','GEZDGNBVGY3TQOJQ=','otpauth://totp/example','GEZDGNBVGY3TQOJQ!']) {
    assert.equal(verificationInputKind(value, 'authenticator'), null);
    await assert.rejects(resolveVerificationCode(value, 'authenticator'));
  }
});

test('código manual preserva zeros iniciais e segue o método solicitado', async () => {
  assert.equal(await resolveVerificationCode('0 0 5 9 2 4','authenticator'), '005924');
  assert.equal(await resolveVerificationCode('12345678','code'), '12345678');
  assert.equal(verificationInputKind('12345678','authenticator'), null);
  assert.equal(verificationInputKind(publicTestSecret,'code'), null);
  await assert.rejects(resolveVerificationCode(publicTestSecret, 'code'), /SMS ou e-mail/);
});

test('chave próxima de expirar aguarda uma nova janela antes de gerar o código', async () => {
  let clock = 58900, waited = 0;
  const code = await resolveVerificationCode(publicTestSecret, 'authenticator', {
    now: () => clock,
    sleep: async milliseconds => { waited = milliseconds; clock += milliseconds; },
  });
  assert.equal(waited, 1150);
  assert.equal(code, await generateTotp(publicTestSecret, 60050));
  const ready = await resolveVerificationCode(publicTestSecret, 'authenticator', {
    now: () => 61000, sleep: async () => { assert.fail('Não deveria aguardar.'); },
  });
  assert.equal(ready, code);
});

test('trocar/encerrar o desafio cancela a geração pendente sem enviar códigos', async () => {
  const controller = new AbortController();
  const pending = resolveVerificationCode(publicTestSecret, 'authenticator', {now: () => 59999, signal: controller.signal});
  controller.abort();
  await assert.rejects(pending, {name: 'AbortError'});
});
