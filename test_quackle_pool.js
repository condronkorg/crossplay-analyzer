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

function expandedPlan(count) {
  const moves = Array.from({length: count}, (_, index) => ({
    action: 'place', position: '1A', tiles: 'M' + index, equity: count - index,
    score: index, leave: ''
  }));
  return {moves, equityMoves: moves.slice(0, 15), actualIndex: 0,
    bagEmptyingIndices: moves.map((_, index) => index), seed: 17,
    bagCount: 5, finalTurnsRemaining: 0};
}

function expandedBatch(request) {
  const {offset, count, candidates} = request.payload;
  return {offset, completed: count, moves: candidates.map(move => {
    const index = Number(move.tiles.slice(1));
    let wins = 0;
    for (let trial = offset; trial < offset + count; trial++) wins += (index + trial) % 2;
    return {win: wins / count, equity: index, samples: count, terminalSamples: count};
  })};
}

async function completeExpanded(log, coverage = new Map()) {
  while (log.requests.length) {
    const requests = [...log.requests].reverse();
    const outstanding = requests.flatMap(request => request.payload.candidates.map(move => move.tiles));
    assert.equal(new Set(outstanding).size, outstanding.length, 'candidate lanes must be disjoint');
    for (const request of requests) {
      for (const move of request.payload.candidates) {
        const next = coverage.get(move.tiles) || 0;
        assert.equal(request.payload.offset, next, 'sample offsets must be contiguous');
        coverage.set(move.tiles, next + request.payload.count);
      }
      reply(log, request, expandedBatch(request));
    }
    await settle();
  }
  return coverage;
}

test('one expanded job uses available workers with identical results after out-of-order batches', async () => {
  const run = async size => {
    const {pool, log} = makePool(size);
    const job = pool.run('simulateReview', {bagCount: 5, iterations: 277, seed: 17});
    job.catch(() => {});
    try {
      await settle();
      reply(log, log.requests[0], expandedPlan(25));
      await settle();
      assert.equal(log.requests.length, size);
      assert.equal(log.workers.length, size);
      const coverage = await completeExpanded(log);
      assert.equal(coverage.size, 25);
      assert.ok([...coverage.values()].every(samples => samples === 277));
      return await job;
    } finally {
      pool.stop();
      await job.catch(() => {});
    }
  };
  assert.deepEqual(await run(3), await run(1));
});

test('expanded failure waits for other lanes before retry resumes their checkpoints', async () => {
  const {pool, log} = makePool(3);
  const payload = {bagCount: 5, iterations: 277, seed: 17};
  const first = pool.run('simulateReview', payload);
  let rejected = false;
  first.catch(() => { rejected = true; });
  try {
    await settle();
    reply(log, log.requests[0], expandedPlan(24));
    await settle();
    assert.equal(log.requests.length, 3);
    const failed = log.requests.shift();
    failed.worker.emit({id: failed.id, error: 'Transient lane failure'});
    await settle();
    assert.equal(rejected, false, 'a retry must not overlap active old lanes');
    assert.equal(log.requests.length, 2);
    for (const request of [...log.requests]) reply(log, request, expandedBatch(request));
    await assert.rejects(first, /Transient lane failure/);
    const state = [...pool.partial.values()][0];
    assert.deepEqual(state.sums.map(sum => sum.samples), Array(8).fill(0).concat(Array(16).fill(16)));
    const retry = pool.run('simulateReview', payload);
    retry.catch(() => {});
    await settle();
    const coverage = new Map(state.plan.moves.map((move, index) => [move.tiles, state.sums[index].samples]));
    await completeExpanded(log, coverage);
    assert.equal((await retry).searchedCandidateCount, 24);
    assert.equal(pool.partial.size, 0);
  } finally {
    pool.stop();
    await first.catch(() => {});
  }
});

test('promoting an expanded job promotes every queued lane', async () => {
  const {pool, log} = makePool(3);
  const busy = [pool.run('kibitz', {name: 'first'}), pool.run('kibitz', {name: 'second'})];
  busy.forEach(job => job.catch(() => {}));
  const expanded = pool.run('simulateReview', {bagCount: 5, iterations: 277, seed: 17},
    {priority: 1, taskKey: 'expanded'});
  expanded.catch(() => {});
  try {
    await settle();
    reply(log, log.requests.find(request => request.type === 'planBagEmptying'), expandedPlan(24));
    await settle();
    assert.equal(pool.queue.filter(task => task.taskKey === 'expanded').length, 2);
    assert.equal(pool.promote('expanded', 99), true);
    assert.ok(pool.queue.filter(task => task.taskKey === 'expanded').every(task => task.priority === 99));
  } finally {
    pool.stop();
    await Promise.allSettled([...busy, expanded]);
  }
});

test('expanded lanes finish when extra workers fail to start', async () => {
  const log = {workers: [], requests: []};
  const pool = new QuackleWorkerPool({size: 3,
    createWorker: () => new FakeWorker(log, {failInit: log.workers.length > 0})});
  const job = pool.run('simulateReview', {bagCount: 5, iterations: 277, seed: 17});
  job.catch(() => {});
  try {
    await settle();
    reply(log, log.requests[0], expandedPlan(24));
    await settle();
    assert.equal(pool.size, 1);
    assert.equal(log.workers.filter(worker => worker.terminated).length, 2);
    await completeExpanded(log);
    assert.equal((await job).searchedCandidateCount, 24);
  } finally {
    pool.stop();
    await job.catch(() => {});
  }
});

test('cancelling parallel lanes checkpoints completed work before a clean resume', async () => {
  const {pool, log} = makePool(3);
  const controller = new AbortController();
  const payload = {bagCount: 5, iterations: 277, seed: 17};
  const first = pool.run('simulateReview', payload, {signal: controller.signal});
  first.catch(() => {});
  try {
    await settle();
    reply(log, log.requests[0], expandedPlan(24));
    await settle();
    assert.equal(log.requests.length, 3);
    controller.abort();
    for (const request of [...log.requests].reverse()) reply(log, request, expandedBatch(request));
    await assert.rejects(first, {name: 'AbortError'});
    const state = [...pool.partial.values()][0];
    assert.ok(state.sums.every(sum => sum.samples === 16));
    const retry = pool.run('simulateReview', payload);
    retry.catch(() => {});
    await settle();
    assert.ok(log.requests.every(request => request.payload.offset === 16));
    const coverage = new Map(state.plan.moves.map(move => [move.tiles, 16]));
    await completeExpanded(log, coverage);
    assert.equal((await retry).iterationsCompleted, 277);
  } finally {
    pool.stop();
    await first.catch(() => {});
  }
});

test('stopping shared parallel lanes cannot retain or restart obsolete work', async () => {
  const {pool, log} = makePool(3);
  const payload = {bagCount: 5, iterations: 277, seed: 17};
  const first = pool.run('simulateReview', payload);
  const joined = pool.run('simulateReview', payload);
  first.catch(() => {});
  joined.catch(() => {});
  await settle();
  reply(log, log.requests[0], expandedPlan(24));
  await settle();
  const obsolete = [...log.requests];
  assert.equal(obsolete.length, 3);
  // A completed result can already be queued in the microtask pipeline when
  // refresh stops the pool. It must not become a new saved checkpoint.
  reply(log, obsolete[2], expandedBatch(obsolete[2]));
  pool.stop(Object.assign(new Error('Refresh cancelled'), {name: 'AbortError'}));
  await assert.rejects(first, {name: 'AbortError'});
  await assert.rejects(joined, {name: 'AbortError'});
  for (const request of obsolete.slice(0, 2)) request.worker.emit({id: request.id, result: expandedBatch(request)});
  await settle();
  assert.equal(pool.partial.size, 0);
  assert.equal(pool.slots.length, 0);
  assert.equal(log.workers.length, 3);
  assert.ok(log.workers.every(worker => worker.terminated));
});
