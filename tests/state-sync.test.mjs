import test from 'node:test';
import assert from 'node:assert/strict';
import {createStateFeed, isStateSnapshot} from '../src/state-sync.js';

const snapshot = connected => ({
  sessions: [{id: 'same-session', authenticated: connected, account_id: connected ? 'account' : null}],
  accounts: connected ? [{id: 'account', username: 'teste'}] : [],
  macros: [], logs: [], settings: {}, proxy: {},
});
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
};

test('an old poll cannot restore an account deleted through the app', async () => {
  const old = deferred();
  const updates = [];
  const feed = createStateFeed({read: () => old.promise, onState: state => updates.push(state), onError: assert.fail});
  const pending = feed.refresh();
  await Promise.resolve();
  feed.accept(snapshot(false));
  old.resolve(snapshot(true));
  await pending;
  assert.deepEqual(updates, [snapshot(false)]);
  assert.equal(updates[0].sessions[0].id, 'same-session');
});

test('an old failed poll cannot hide a newly connected Instagram account', async () => {
  const old = deferred();
  const updates = [];
  const feed = createStateFeed({read: () => old.promise, onState: state => updates.push(state), onError: assert.fail});
  const pending = feed.refresh();
  await Promise.resolve();
  feed.accept(snapshot(true));
  old.reject(new Error('old request failed'));
  await pending;
  assert.deepEqual(updates, [snapshot(true)]);
});

test('a refresh requested during a poll waits for a fresh request as well', async () => {
  const first = deferred();
  const updates = [];
  let calls = 0;
  const feed = createStateFeed({
    read: () => ++calls === 1 ? first.promise : Promise.resolve(snapshot(true)),
    onState: state => updates.push(state), onError: assert.fail,
  });
  const beforeMutation = feed.refresh();
  await Promise.resolve();
  const afterMutation = feed.refresh(true);
  first.resolve(snapshot(false));
  await afterMutation;
  await beforeMutation;
  assert.equal(calls, 2);
  assert.deepEqual(updates.at(-1), snapshot(true));
});

test('a failed read does not prevent later updates and malformed snapshots are ignored', async () => {
  let calls = 0;
  const errors = [];
  const updates = [];
  const feed = createStateFeed({
    read: () => { if (++calls === 1) throw new Error('offline'); return snapshot(true); },
    onState: state => updates.push(state), onError: (failure, show) => errors.push([failure.message, show]),
  });
  await feed.refresh(true);
  await feed.refresh();
  assert.deepEqual(errors, [['offline', true]]);
  assert.deepEqual(updates, [snapshot(true)]);
  assert.equal(isStateSnapshot(snapshot(true)), true);
  for (const value of [null, {}, {sessions: [], accounts: []}]) assert.equal(isStateSnapshot(value), false);
});
