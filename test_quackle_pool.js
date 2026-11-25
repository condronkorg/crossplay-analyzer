const test = require('node:test');
const assert = require('node:assert/strict');
const QuackleWorkerPool = require('./quackle-pool.js');

// Synthetic workers record requests; tests reply to them explicitly.
class FakeWorker {
  constructor(log, { failInit = false } = {}) {
    this.log = log;
    this.failInit = failInit;
    this.listeners = { message: [], error: [], messageerror: [] };
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

test('an unreadable worker response rejects the request and allows a retry', async () => {
  const {pool, log} = makePool(1);
  pool.timeout = 50;
  const first = pool.run('kibitz');
  await settle();
  for (const listener of log.workers[0].listeners.messageerror) listener({});
  await assert.rejects(first, /unreadable response/);
  assert.equal(log.workers[0].terminated, true);

  const retry = pool.run('kibitz');
  await settle();
  const request = log.requests.at(-1);
  assert.notEqual(request.worker, log.workers[0]);
  reply(log, request, 'recovered');
  assert.equal(await retry, 'recovered');
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

const pass = {action: 'pass', position: '', tiles: '', equity: -999, score: 0, leave: 'AEIRSTT', win: null, samples: 0, terminalSamples: 0};

function terminalPlan(payload, count = 1) {
  const moves = count === 1 ? [pass] : expandedPlan(count).moves;
  return {moves, equityMoves: moves, actualIndex: 0, bagEmptyingIndices: [],
    tieOrder: moves.map((_, index) => index),
    seed: payload.seed, bagCount: payload.bagCount, finalTurnsRemaining: payload.finalTurnsRemaining};
}

function terminalBatch(request, completed = request.payload.count) {
  const {offset, candidates} = request.payload;
  return {offset, completed, moves: candidates.map(move => ({...move, win: .5, winSum: completed / 2,
    equity: 35, equityStatistics: [[0, 0, 35 * completed, completed]], residualSum: 0,
    samples: completed, terminalSamples: completed}))};
}

test('terminal previews and generic full reviews keep all planned candidates', async () => {
  for (const [bagCount, iterations, type] of [[0, 277, 'simulateReview'], [1, 64, 'simulateKibitz'],
    [7, 64, 'simulateMove'], [8, 277, 'simulateReview'], [21, 277, 'simulateKibitz']]) {
    const {pool, log} = makePool(3);
    const payload = {bagCount, iterations, seed: 35, finalTurnsRemaining: bagCount ? 0 : 2};
    const job = pool.run(type, payload);
    job.catch(() => {});
    try {
      await settle();
      const plan = log.requests[0];
      assert.equal(plan.type, 'planReviewSimulation');
      assert.equal(plan.payload.requestType, type);
      const data = terminalPlan(payload, 15);
      if (type === 'simulateKibitz') data.actualIndex = -1;
      if (type === 'simulateMove') data.evaluation = {staticRank: 187, legalMoveCount: 987};
      reply(log, plan, data);
      await settle();
      assert.equal(log.workers.length, 3);
      assert.deepEqual(log.requests.map(request => request.payload.candidates.length), [5, 5, 5]);
      const offsets = new Map();
      while (log.requests.length) {
        for (const request of [...log.requests].reverse()) {
          assert.equal(request.type, 'simulateReviewBatch');
          const {candidates, offset, count} = request.payload;
          assert.ok(count <= 16 && offset + count <= iterations);
          // Exercise an early budget yield on the first request of every lane.
          const completed = offset === 0 ? 3 : count;
          for (const move of candidates) {
            assert.equal(offset, offsets.get(move.tiles) || 0);
            offsets.set(move.tiles, offset + completed);
          }
          reply(log, request, terminalBatch(request, completed));
        }
        await settle();
      }
      const result = await job;
      assert.equal(result.candidatePolicy, undefined);
      assert.equal(result.moves.length, 15);
      assert.equal(result.totalPlayaheads, iterations * 15);
      assert.equal(result.iterationsCompleted, iterations);
      assert.equal(result.moves.every(move => move.samples === iterations &&
        move.terminalSamples === iterations && move.win === .5 && move.equity === 35), true);
      assert.equal(result.staticMoves[0].equity, 15);
      assert.equal(result.selected?.tiles, type === 'simulateKibitz' ? undefined : 'M0');
      assert.equal(result.staticRank, type === 'simulateMove' ? 187 : undefined);
      assert.equal(result.legalMoveCount, type === 'simulateMove' ? 987 : undefined);
    } finally {
      pool.stop();
      await job.catch(() => {});
    }
  }
});

const drawPayload = {gridJson: JSON.stringify(['AAAAAAAABBCCDDD', 'DEEEEEEEEEEEFFG', 'GGHHHIIIIIIIJKL', 'LLLMMNNNNNOOOOO', 'OOOPP..........', '...............', '...............', '...............', '...............', '...............', '...............', '...............', '...............', '...............', '...............']),
  rack: 'AEIRSTT', rackAfter: 'AEIRSTT', playerScore: 0, opponentScore: 0,
  bagCount: 21, finalTurnsRemaining: 0, seed: 33, iterations: 64,
  played: {action: 'pass', position: '', tiles: ''}};

function drawBatch(request, completed = request.payload.count, win = .75) {
  return {offset: request.payload.offset, completed, iterationsRequested: request.payload.count,
    iterationsCompleted: completed, simulated: true, samplingVersion: 'rack-draw-v1',
    beforeWin: win, afterWin: win, beforeWinSum: win * completed, afterWinSum: win * completed,
    selected: drawPayload.played, drawCount: 0, rackAfter: drawPayload.rackAfter,
    seed: drawPayload.seed, bagCount: drawPayload.bagCount, finalTurnsRemaining: 0, plies: -1,
    playToEnd: true, endgamePolicy: 'crossplay-endgame-v1', simulationMode: 'play-to-end', winModel: 'terminal'};
}

test('terminal draw retries the missing offset and reuses a matching baseline', async () => {
  const {pool, log} = makePool(3);
  const payload = {...drawPayload, beforeWin: {win: .75, samples: 64, seed: 33}};
  const first = pool.run('simulateDraw', payload);
  const joined = pool.run('simulateDraw', payload);
  first.catch(() => {});
  joined.catch(() => {});
  try {
    await settle();
    assert.equal(log.requests.length, 1);
    assert.equal(log.requests[0].type, 'simulateDrawBatch');
    assert.equal(log.requests[0].payload.offset, 0);
    assert.equal(log.requests[0].payload.knownBeforeWin, .75);
    reply(log, log.requests[0], drawBatch(log.requests[0], 7));
    await settle();
    const failure = log.requests.shift();
    assert.equal(failure.payload.offset, 7);
    failure.worker.emit({id: failure.id, error: 'Transient worker failure'});
    await assert.rejects(first, /Transient/);
    await assert.rejects(joined, /Transient/);

    let started = false;
    const retry = pool.run('simulateDraw', payload, {taskKey: 'draw', onStart: () => { started = true; }});
    retry.catch(() => {});
    assert.equal(pool.promote('draw', 99), true);
    await settle();
    assert.equal(started, true);
    for (const offset of [7, 23, 39, 55]) {
      assert.equal(log.requests.length, 1);
      assert.equal(log.requests[0].payload.offset, offset);
      assert.equal(pool.slots[0].task.priority, offset === 7 ? 0 : 99);
      reply(log, log.requests[0], drawBatch(log.requests[0]));
      await settle();
    }
    const result = await retry;
    assert.equal(result.iterationsRequested, 64);
    assert.equal(result.iterationsCompleted, 64);
    assert.equal(result.samplingVersion, 'rack-draw-v1');
    assert.equal(result.beforeWin, .75);
    assert.equal(result.afterWin, .75);
    assert.equal(result.offset, undefined);
    assert.equal(result.beforeWinSum, undefined);
    assert.equal(log.workers.length, 1);
  } finally {
    pool.stop();
    await Promise.allSettled([first, joined]);
  }
});

test('terminal draws ignore incompatible baselines', async () => {
  for (const beforeWin of [undefined, .5, {win: .5, samples: 277, seed: 33},
    {win: .5, samples: 64, seed: 34}, {win: NaN, samples: 64, seed: 33},
    {win: -1, samples: 64, seed: 33}]) {
    const {pool, log} = makePool(3);
    const job = pool.run('simulateDraw', {...drawPayload, beforeWin});
    job.catch(() => {});
    try {
      await settle();
      assert.equal(log.requests[0].payload.knownBeforeWin, -1);
    } finally {
      pool.stop();
      await job.catch(() => {});
    }
  }
});

test('terminal draw stop discards a late batch and does not restart duplicate requests', async () => {
  const {pool, log} = makePool(3);
  const first = pool.run('simulateDraw', drawPayload);
  const joined = pool.run('simulateDraw', drawPayload);
  first.catch(() => {});
  joined.catch(() => {});
  try {
    await settle();
    assert.equal(log.requests[0].type, 'simulateDrawBatch');
    reply(log, log.requests[0], drawBatch(log.requests[0], 4));
    pool.stop(Object.assign(new Error('Refresh cancelled'), {name: 'AbortError'}));
    await assert.rejects(first, {name: 'AbortError'});
    await assert.rejects(joined, {name: 'AbortError'});
    assert.equal(pool.partial.size, 0);
    assert.equal(log.workers.length, 1);
  } finally {
    pool.stop();
    await Promise.allSettled([first, joined]);
  }
});

function workerContext(engine) {
  const fs = require('node:fs');
  const vm = require('node:vm');
  let handler, message;
  const context = vm.createContext({
    QuackleModule: async () => ({initEngine: () => true, ...engine}),
    self: {addEventListener(_type, fn) { handler = fn; }, postMessage(value) { message = value; }},
  });
  context.importScripts = (...files) => files.filter(file => file !== 'quackle.js')
    .forEach(file => vm.runInContext(fs.readFileSync(file, 'utf8'), context));
  vm.runInContext(fs.readFileSync('quackle-worker.js', 'utf8'), context);
  return async (type, payload) => {
    await handler({data: {id: 1, type, payload}});
    return message;
  };
}

test('worker forwards paired plans and enforces full terminal sample counts in partial batches', async () => {
  const payload = {...drawPayload, numMoves: 15, requestType: 'simulateKibitz', seed: undefined};
  const expected = {...terminalPlan({...payload, seed: 123456789}), actualIndex: -1, tieOrder: [0]};
  const batch = {offset: 2, count: 16, candidates: [pass], seed: 123456789};
  let invalid = false;
  const ask = workerContext({
    planReviewSimulation(...args) {
      assert.deepEqual(args, [payload.gridJson, 'AEIRSTT', 0, 0, 15, 21, 0, null, 123456789]);
      return expected;
    },
    simulateReviewBatch(...args) {
      assert.deepEqual(args, [payload.gridJson, 'AEIRSTT', 0, 0, 21, 0, [pass], 123456789, 2, 16]);
      const result = terminalBatch({payload: batch}, 3);
      if (invalid) result.moves[0].terminalSamples = 2;
      return result;
    },
  });
  assert.equal((await ask('planReviewSimulation', payload)).result, expected);
  assert.equal((await ask('simulateReviewBatch', {...payload, ...batch})).result?.completed, 3);
  invalid = true;
  assert.match((await ask('simulateReviewBatch', {...payload, ...batch})).error, /batch/i);
});

test('worker checks partial draw metadata and still asks native to validate a reused baseline', async () => {
  const payload = {...drawPayload, offset: 1, count: 16, knownBeforeWin: .75};
  let result = drawBatch({payload}, 4);
  const ask = workerContext({
    simulateDrawBatch(...args) {
      assert.deepEqual(JSON.parse(JSON.stringify(args)), [payload.gridJson, 'AEIRSTT', 0, 0, 21, 0,
        drawPayload.played, 33, 'AEIRSTT', 1, 16, .75]);
      return result;
    },
  });
  assert.equal((await ask('simulateDrawBatch', payload)).result, result);
  for (const change of [{offset: 2}, {completed: 1}, {iterationsRequested: 4},
    {beforeWinSum: 2}, {afterWinSum: 5}, {rackAfter: 'AAAAAAA'}, {seed: 34}]) {
    result = {...drawBatch({payload}, 4), ...change};
    assert.match((await ask('simulateDrawBatch', payload)).error, /rack draw/i);
  }
  result = {error: 'Impossible physical draw'};
  assert.match((await ask('simulateDrawBatch', payload)).error, /Impossible physical draw/);
});

test('generic review worker failure preserves sibling checkpoints and retry priority', async () => {
  const {pool, log} = makePool(3);
  const payload = {bagCount: 8, finalTurnsRemaining: 0, iterations: 64, seed: 17};
  const first = pool.run('simulateReview', payload);
  let failed = false;
  first.catch(() => { failed = true; });
  try {
    await settle();
    const plan = terminalPlan(payload, 15);
    reply(log, log.requests[0], plan);
    await settle();
    const crashed = log.requests.shift();
    crashed.worker.listeners.error[0]({message: 'Worker crashed'});
    await settle();
    assert.equal(failed, false);
    assert.equal(crashed.worker.terminated, true);
    assert.equal(log.requests.length, 2);
    const coverage = new Map();
    for (const request of [...log.requests]) {
      for (const move of request.payload.candidates) coverage.set(move.tiles, 5);
      reply(log, request, terminalBatch(request, 5));
    }
    await assert.rejects(first, /Worker crashed/);
    let starts = 0;
    const retry = pool.run('simulateReview', payload, {priority: 1, taskKey: 'generic', onStart: () => { starts++; }});
    retry.catch(() => {});
    assert.equal(pool.promote('generic', 98), true);
    await settle();
    assert.equal(log.workers.length, 4);
    assert.equal(log.workers.filter(worker => !worker.terminated).length, 3);
    const firstRetryRequests = new Set(log.requests);
    while (log.requests.length) {
      for (const request of [...log.requests].reverse()) {
        assert.equal(request.type, 'simulateReviewBatch');
        assert.ok(firstRetryRequests.has(request) || pool.slots.find(slot =>
          slot.task?.id === request.id).task.priority === 98);
        for (const move of request.payload.candidates) {
          assert.equal(request.payload.offset, coverage.get(move.tiles) || 0);
          coverage.set(move.tiles, request.payload.offset + request.payload.count);
        }
        reply(log, request, terminalBatch(request));
      }
      await settle();
    }
    assert.equal(starts, 1);
    assert.equal(coverage.size, 15);
    assert.ok([...coverage.values()].every(count => count === 64));
    assert.equal((await retry).iterationsCompleted, 64);
  } finally {
    pool.stop();
    await first.catch(() => {});
  }
});

test('terminal full draw sums each partial conditioned estimate and completes 277 samples', async () => {
  const {pool, log} = makePool(3);
  const payload = {...drawPayload, iterations: 277, played: {action: 'exchange', position: '', tiles: 'T'},
    rackAfter: 'AEIRSTZ'};
  const job = pool.run('simulateDraw', payload);
  job.catch(() => {});
  try {
    let offset = 0;
    await settle();
    while (log.requests.length) {
      assert.equal(log.requests.length, 1);
      const request = log.requests[0];
      assert.equal(request.payload.offset, offset);
      assert.equal(request.payload.knownBeforeWin, -1);
      const completed = offset === 0 ? 3 : request.payload.count;
      // First batch wins every baseline and loses every conditioned trial.
      // Every later batch has the opposite outcome.
      const beforeWin = offset === 0 ? 1 : 0;
      const afterWin = 1 - beforeWin;
      reply(log, request, {...drawBatch(request, completed), drawCount: 1, selected: payload.played,
        rackAfter: payload.rackAfter, beforeWin, afterWin,
        beforeWinSum: beforeWin * completed, afterWinSum: afterWin * completed});
      offset += completed;
      await settle();
    }
    const result = await job;
    assert.equal(offset, 277);
    assert.equal(result.iterationsRequested, 277);
    assert.equal(result.iterationsCompleted, 277);
    assert.equal(result.beforeWin, 3 / 277);
    assert.equal(result.afterWin, 274 / 277);
    assert.equal(result.drawCount, 1);
    assert.deepEqual(result.selected, payload.played);
  } finally {
    pool.stop();
    await job.catch(() => {});
  }
});
