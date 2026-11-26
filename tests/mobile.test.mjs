import test from 'node:test';
import assert from 'node:assert/strict';
import {requestMobileLogin, appMessage, isNativeApp} from '../src/mobile.js';

const id = '11111111-1111-4111-8111-111111111111';
test('native layout requires the working app bridge and sends no message', () => {
  assert.equal(isNativeApp(null), false);
  assert.equal(isNativeApp({}), false);
  assert.equal(isNativeApp({postMessage: 'not a function'}), false);
  const messages = [];
  assert.equal(isNativeApp({postMessage: value => messages.push(value)}), true);
  assert.deepEqual(messages, []);
});
test('site delegates only the session ID to the native login', () => {
  const messages = [];
  globalThis.RamosApp = {postMessage: text => messages.push(JSON.parse(text))};
  try {
    assert.equal(requestMobileLogin(id), null);
    assert.deepEqual(messages, [{action: 'login', session_id: id}]);
    assert.throws(() => requestMobileLogin('bad&id=secret'));
    assert.equal(appMessage({action: 'accounts'}), true);
  } finally { delete globalThis.RamosApp; }
});
test('desktop opens the app with a deep link containing no credentials', () => {
  assert.equal(isNativeApp(), false);
  assert.equal(requestMobileLogin(id), `ramosbot://login?session_id=${id}`);
});
