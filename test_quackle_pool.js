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

test('refresh cancellation stops workers without turning it into an engine failure', async () => {
  const {pool, log} = makePool(1);
  const running = pool.run('simulateReview');
  const queued = pool.run('simulateDraw');
  await settle();
  const cancellation = Object.assign(new Error('Analysis interrupted by refresh.'), {name: 'AbortError'});
  pool.stop(cancellation);
  await assert.rejects(running, {name: 'AbortError'});
  await assert.rejects(queued, {name: 'AbortError'});
  assert.ok(log.workers.every(worker => worker.terminated));
  const replacement = pool.run('kibitz');
  await settle();
  reply(log, log.requests.at(-1), 'fresh');
  assert.equal(await replacement, 'fresh');
});

test('expanded jobs coalesce and resume completed samples after a failed batch', async () => {
  const {pool,log}=makePool(2);
  const payload={bagCount:5,iterations:277,seed:9};
  const first=pool.run('simulateReview',payload);
  const joined=pool.run('simulateReview',payload);
  await settle();
  assert.equal(log.requests.length,1);
  reply(log,log.requests[0],{moves:[{action:'pass',position:'',tiles:'',equity:0}],actualIndex:0,bagEmptyingIndices:[],seed:9,bagCount:5,finalTurnsRemaining:0});
  await settle();
  const complete=request=>reply(log,request,{offset:request.payload.offset,completed:request.payload.count,moves:[{win:.5,equity:2,samples:request.payload.count,terminalSamples:request.payload.count}]});
  complete(log.requests[0]);
  await settle();
  const failure=log.requests.shift();
  failure.worker.emit({id:failure.id,error:'Transient worker failure'});
  await assert.rejects(first,/Transient/);
  await assert.rejects(joined,/Transient/);
  const retry=pool.run('simulateReview',payload);
  await settle();
  assert.equal(log.requests[0].payload.offset,16);
  while(log.requests.length){complete(log.requests[0]);await settle();}
  const result=await retry;
  assert.equal(result.selected.samples,277);
  assert.equal(result.selected.win,.5);
  assert.equal(pool.partial.size,0);
  pool.stop();
});

test('expanded cancellation checkpoints a completed batch and keeps promoted priority on retry', async () => {
  const {pool,log}=makePool(1);
  const payload={bagCount:2,iterations:277,seed:4};
  const abort=new AbortController();
  const job=pool.run('simulateKibitz',payload,{signal:abort.signal,taskKey:'expanded',priority:1});
  await settle();
  reply(log,log.requests[0],{moves:[{action:'pass',position:'',tiles:'',equity:0}],actualIndex:-1,bagEmptyingIndices:[],seed:4,bagCount:2,finalTurnsRemaining:0});
  await settle();
  abort.abort();
  const batch=log.requests[0];
  reply(log,batch,{offset:0,completed:16,moves:[{win:.25,equity:1,samples:16,terminalSamples:16}]});
  await assert.rejects(job,{name:'AbortError'});
  const resumed=pool.run('simulateKibitz',payload,{taskKey:'expanded',priority:1});
  await settle();
  assert.equal(log.requests[0].payload.offset,16);
  assert.equal(pool.promote('expanded',99),true);
  while(log.requests.length){
    const request=log.requests[0];
    const n=request.payload.count;
    reply(log,request,{offset:request.payload.offset,completed:n,moves:[{win:.25,equity:1,samples:n,terminalSamples:n}]});
    await settle();
    if(log.requests.length)assert.equal(pool.slots[0].task.priority,99);
  }
  assert.equal((await resumed).best.win,.25);
  pool.stop();
});

test('stopping shared expanded jobs cannot restart workers or save late checkpoints', async () => {
  for(const stopAt of ['pending','plan','batch']) {
    const {pool,log}=makePool(1);
    const payload={bagCount:2,iterations:277,seed:4};
    const first=pool.run('simulateKibitz',payload);
    const shared=pool.run('simulateKibitz',payload);
    await settle();
    if(stopAt!=='pending') {
      reply(log,log.requests[0],{moves:[{action:'pass',position:'',tiles:'',equity:0}],actualIndex:-1,bagEmptyingIndices:[],seed:4,bagCount:2,finalTurnsRemaining:0});
      if(stopAt==='batch') {
        await settle();
        reply(log,log.requests[0],{offset:0,completed:16,moves:[{win:.25,equity:1,samples:16,terminalSamples:16}]});
      }
    }
    const error=new Error('Refresh cancelled');error.name='AbortError';
    pool.stop(error);
    await assert.rejects(first,{name:'AbortError'});
    await assert.rejects(shared,{name:'AbortError'});
    await settle();
    assert.equal(pool.slots.length,0);
    assert.equal(pool.partial.size,0);
    assert.equal(log.workers.length,1);
  }
});
