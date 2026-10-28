const test = require('node:test');
const assert = require('node:assert/strict');
const QuackleWorkerPool = require('./quackle-pool.js');

// Synthetic workers record requests; tests reply to them explicitly.
class FakeWorker {
  constructor(log, { failInit = false } = {}) {
    this.log = log;
    this.failInit = failInit;
    this.listeners = { message: [], error: [] };
    this.terminated = false;
    log.workers.push(this);
  }
  addEventListener(type, listener) { this.listeners[type].push(listener); }
  postMessage(message) {
    if (message.type === 'init') {
      queueMicrotask(() => this.emit(this.failInit ? { id: message.id, error: 'no engine' } : { id: message.id, result: true }));
    } else {
      this.log.requests.push({ worker: this, ...message });
    }
  }
  emit(data) { for (const listener of this.listeners.message) listener({ data }); }
  terminate() { this.terminated = true; }
}

const settle = () => new Promise(resolve => setTimeout(resolve, 0));

function makePool(size, options = {}) {
  const log = { workers: [], requests: [] };
  const pool = new QuackleWorkerPool({ size, createWorker: () => new FakeWorker(log, options) });
  return { pool, log };
}

function reply(log, request, result) {
  log.requests.splice(log.requests.indexOf(request), 1);
  request.worker.emit({ id: request.id, result });
}

test('workers start only for waiting work and run requests in parallel', async () => {
  const { pool, log } = makePool(3);
  const first = pool.run('kibitz', { turn: 1 });
  await settle();
  assert.equal(log.workers.length, 1);
  const rest = [2, 3, 4].map(turn => pool.run('kibitz', { turn }));
  await settle();
  assert.equal(log.workers.length, 3);
  assert.equal(log.requests.length, 3);
  assert.deepEqual(new Set(log.requests.map(request => request.worker)).size, 3);
  for (const request of [...log.requests]) reply(log, request, request.payload.turn);
  await settle();
  assert.equal(log.requests.length, 1);
  reply(log, log.requests[0], 4);
  assert.deepEqual(await Promise.all([first, ...rest]), [1, 2, 3, 4]);
});

test('higher priorities run first and cancelled queued work never reaches a worker', async () => {
  const { pool, log } = makePool(1);
  const busy = pool.run('kibitz', { name: 'busy' });
  await settle();
  const controller = new AbortController();
  const low = pool.run('simulateKibitz', { name: 'low' }, { priority: 1 });
  const cancelled = pool.run('simulateKibitz', { name: 'cancelled' }, { priority: 5, signal: controller.signal });
  let started = false;
  const high = pool.run('simulateKibitz', { name: 'high' }, { priority: 100, onStart: () => { started = true; } });
  controller.abort();
  await assert.rejects(cancelled, { name: 'AbortError' });
  reply(log, log.requests[0], 'busy');
  await settle();
  assert.equal(started, true);
  assert.equal(log.requests[0].payload.name, 'high');
  reply(log, log.requests[0], 'high');
  await settle();
  assert.equal(log.requests[0].payload.name, 'low');
  reply(log, log.requests[0], 'low');
  assert.deepEqual(await Promise.all([busy, high, low]), ['busy', 'high', 'low']);
});

test('engine startup failure rejects queued work instead of retrying forever', async () => {
  const { pool, log } = makePool(2, { failInit: true });
  const requests = [pool.run('kibitz'), pool.run('kibitz')];
  for (const request of requests) await assert.rejects(request, /no engine/);
  assert.equal(log.workers.length, 2);
  assert.ok(log.workers.every(worker => worker.terminated));
});

test('stop rejects queued and running work and terminates workers', async () => {
  const { pool, log } = makePool(1);
  const running = pool.run('kibitz');
  const queued = pool.run('kibitz');
  await settle();
  pool.stop();
  await assert.rejects(running, /stopped/);
  await assert.rejects(queued, /stopped/);
  assert.ok(log.workers[0].terminated);
});

test('foreground promotion selects the same queued task ahead of other games', async () => {
  const { pool, log } = makePool(1);
  const busy = pool.run('simulateReview', { name: 'busy' });
  await settle();
  const old = pool.run('simulateReview', { name: 'old' }, { priority: 2, taskKey: 'account/game/old' });
  const viewed = pool.run('simulateReview', { name: 'viewed' }, { priority: 1, taskKey: 'account/game/viewed' });
  assert.equal(pool.promote('other-account/game/viewed', 100), false);
  assert.equal(pool.promote('account/game/viewed', 100), true);
  reply(log, log.requests[0], 'busy');
  await settle();
  assert.equal(log.requests[0].payload.name, 'viewed');
  reply(log, log.requests[0], 'viewed');
  await settle();
  reply(log, log.requests[0], 'old');
  assert.deepEqual(await Promise.all([busy, old, viewed]), ['busy', 'old', 'viewed']);
});
