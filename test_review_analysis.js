const test = require('node:test');
const assert = require('node:assert/strict');
const { playedMove, qualityBand, ReviewQueue } = require('./review-analysis.js');

test('recorded actions preserve physical blanks, existing tiles, pass and exchange', () => {
  assert.deepEqual(playedMove({ action: 's(CAT) 8F 5' }), { action: 'place', position: '8F', tiles: 's...' });
  assert.deepEqual(playedMove({ action: 'PASS 0' }), { action: 'pass', position: '', tiles: '' });
  assert.deepEqual(playedMove({ action: 'SWAP 0', swapped_tiles: 'U?' }), { action: 'exchange', position: '', tiles: 'U?' });
  assert.deepEqual(playedMove({ action: 'SWAP 0', swapped_tiles: 'U*_a ' }), { action: 'exchange', position: '', tiles: 'U????' });
  assert.throws(() => playedMove({ action: 'SWAP 0' }), /exchanged tiles/);
  assert.throws(() => playedMove({ action: 'UNKNOWN 0' }), /recorded move/);
});

test('quality bands use percentage points', () => {
  assert.deepEqual([0, 1.999, 2, 4.999, 5, 9.999, 10].map(qualityBand),
    ['best', 'best', 'inaccuracy', 'inaccuracy', 'mistake', 'mistake', 'blunder']);
});

const tick = () => new Promise(resolve => setImmediate(resolve));
test('queue gives selected game priority between turns and does not load unfinished games', async () => {
  const calls = [], releases = [];
  const queue = new ReviewQueue({ concurrency: 1,
    load: async id => ({ review: { id, turns: [{}, {}] } }),
    run: async (context, index) => { calls.push([context.review.id, index]); await new Promise(resolve => releases.push(resolve)); }
  });
  queue.add([{ id: 'one', status: 'completed' }, { id: 'two', status: 'completed' }, { id: 'live', status: 'active' }]);
  await tick();
  queue.select('two');
  releases.shift()(); await tick();
  assert.deepEqual(calls, [['one', 0], ['two', 0]]);
  queue.close(); releases.shift()(); await tick();
  assert.equal(calls.length, 2);
});

test('failed review loads do not loop and retry can resume them', async () => {
  let loads = 0;
  const queue = new ReviewQueue({ concurrency: 1, load: async () => { loads++; throw new Error('Connect NYT to import'); }, run: async () => {} });
  queue.add([{ id: 'one', status: 'completed' }]); await tick();
  assert.equal(loads, 1);
  assert.match(queue.errors.get('one'), /Connect NYT/);
  queue.retry(); await tick();
  assert.equal(loads, 2);
  queue.close();
});

test('saved simulation validation rejects old units, incomplete samples, and the wrong physical blank', () => {
  const { validResult } = require('./review-analysis.js');
  const grid = Array(15).fill('.'.repeat(15));
  const turn = { action: 'cAT 8G 5', rack: '?ATERSU', grid, bag_count: 86, final_turns_remaining: 0 };
  const selected = { action: 'place', position: '8G', tiles: 'cAT', win: .4, equity: 1, staticEquity: 2, samples: 277 };
  const best = { action: 'pass', position: '', tiles: '', win: .6, equity: 3, staticEquity: 4, samples: 277 };
  const result = { simulated: true, plies: 2, playToEnd: false, simulationMode: '2-ply', winModel: 'bogowin',
    bagCount: 86, finalTurnsRemaining: 0, samplingVersion: 'paired-review-v1', seed: 42,
    iterationsRequested: 277, iterationsCompleted: 277, candidateCount: 2, totalPlayaheads: 554,
    selected, best, moves: [selected, best], staticMoves: [selected, best].map(move => ({ ...move, equity: move.staticEquity })), bestStaticEquity: 4 };
  assert.equal(validResult(result, turn, 42), true);
  for (const bad of [
    { ...result, samplingVersion: 'legacy' },
    { ...result, iterationsCompleted: 80 },
    { ...result, seed: 41 },
    { ...result, staticMoves: [] },
    { ...result, bestStaticEquity: 20 },
    { ...result, staticMoves: result.staticMoves.map(move => ({ ...move, tiles: 'BAD' })) },
    { ...result, best: selected },
    { ...result, selected: { ...selected, tiles: 'CAT' } },
    { ...result, selected: { ...selected, win: 40 } },
  ]) assert.equal(validResult(bad, turn, 42), false);
  const preview = JSON.parse(JSON.stringify(result));
  preview.iterationsRequested = preview.iterationsCompleted = 64;
  preview.totalPlayaheads = 128;
  for (const move of [...preview.moves, preview.selected, preview.best]) move.samples = 64;
  assert.equal(validResult(preview, turn, 42, 64, 5), true);
  assert.equal(validResult(preview, turn, 42), false);
  assert.equal(validResult(result, turn, 42, 64, 5), false);
});

test('opening a completed cached context does not run engine work again', async () => {
  let runs = 0;
  const queue = new ReviewQueue({
    load: async id => ({ review: { id, turns: [{}] }, states: ['done'] }),
    run: async () => { runs++; }
  });
  queue.add([{ id: 'saved', status: 'completed' }]); await tick();
  queue.select('saved'); await tick();
  assert.equal(runs, 0);
  queue.close();
});

test('an auth abort on an open queue stops retrying until an explicit retry', async () => {
  let loads = 0;
  const queue = new ReviewQueue({
    load: async () => {
      loads++;
      if (loads === 10) queue.close(); // Bound this regression if the queue spins.
      throw Object.assign(new Error('Account session expired.'), { name: 'AbortError' });
    },
    run: async () => {}
  });
  queue.add([{ id: 'saved', status: 'completed' }]); await tick();
  assert.equal(loads, 1);
  assert.match(queue.errors.get('saved'), /session expired/);
  queue.retry(); await tick();
  assert.equal(loads, 2);
  queue.close();
});

test('a turn abort on an open queue is an error and cannot complete a game', async () => {
  let completed = 0;
  const queue = new ReviewQueue({ load: async id => ({ review: { id, turns: [{}] } }),
    run: async () => { throw Object.assign(new Error('Unexpected cancellation'), { name: 'AbortError' }); },
    complete: () => { completed++; }
  });
  queue.add([{ id: 'saved', status: 'completed' }]); await tick();
  assert.equal(completed, 0);
  assert.equal(queue.contexts.get('saved').states[0], 'error');
  assert.match(queue.contexts.get('saved').error, /cancellation/);
  queue.close();
});

test('a directly opened game remains queued when another game is selected', async () => {
  const calls = [];
  const queue = new ReviewQueue({ concurrency: 1,
    load: async id => ({ review: { id, turns: [{}] } }),
    run: async context => { calls.push(context.review.id); }
  });
  await queue.get('first');
  await queue.get('second');
  queue.select('second');
  await tick();
  assert.deepEqual(calls, ['second', 'first']);
  queue.close();
});

test('preview queue estimates every pending turn before full refinement and completes only at the end', async () => {
  const calls = [], releases = [], completed = [];
  const queue = new ReviewQueue({ preview: true, concurrency: 1,
    load: async id => ({ review: { id, turns: [{}, {}, {}] }, states: ['done', 'pending', 'pending-preview'] }),
    run: async (context, index, phase) => {
      calls.push([index, phase]);
      await new Promise(resolve => releases.push(resolve));
    },
    complete: context => completed.push(context.review.id)
  });
  queue.add([{ id: 'one', status: 'completed' }]); await tick();
  assert.deepEqual(calls, [[1, 'preview']]);
  assert.deepEqual(queue.contexts.get('one').states, ['done', 'running-preview', 'pending-preview']);
  releases.shift()(); await tick();
  assert.deepEqual(calls, [[1, 'preview'], [2, 'preview']]);
  assert.equal(completed.length, 0);
  releases.shift()(); await tick();
  assert.deepEqual(calls[2], [1, 'review']);
  assert.equal(completed.length, 0);
  releases.shift()(); await tick();
  assert.deepEqual(calls[3], [2, 'review']);
  assert.equal(completed.length, 0);
  releases.shift()(); await tick();
  assert.deepEqual(completed, ['one']);
  assert.deepEqual(queue.contexts.get('one').states, ['done', 'done', 'done']);
  queue.close();
});

test('preview queue skips refinement when a final result is reused or the turn is permanently unrated', async () => {
  const calls = [];
  let completed = 0;
  const queue = new ReviewQueue({ preview: true, concurrency: 1,
    load: async id => ({ review: { id, turns: [{ cached: true }, { unrated: true }] } }),
    run: async (context, index, phase) => { calls.push([index, phase]); return { complete: true }; },
    complete: () => { completed++; }
  });
  queue.add([{ id: 'saved', status: 'completed' }]); await tick();
  assert.deepEqual(calls, [[0, 'preview'], [1, 'preview']]);
  assert.equal(completed, 1);
  queue.close();
});

test('preview retry preserves running work and resumes failed turns from the preview phase', async () => {
  const calls = [], releases = [];
  const queue = new ReviewQueue({ preview: true, concurrency: 1,
    load: async id => ({ review: { id, turns: [{}] } }),
    run: async (context, index, phase) => {
      calls.push(phase);
      await new Promise((resolve, reject) => releases.push({ resolve, reject }));
    }
  });
  queue.add([{ id: 'one', status: 'completed' }]); await tick();
  queue.retry('one'); queue.retryTurn('one', 0); await tick();
  assert.deepEqual(calls, ['preview']);
  assert.equal(queue.contexts.get('one').states[0], 'running-preview');
  releases.shift().reject(Object.assign(new Error('Session expired'), { name: 'AbortError' })); await tick();
  assert.equal(queue.contexts.get('one').states[0], 'error');
  assert.match(queue.contexts.get('one').error, /Session expired/);
  await tick(); assert.equal(calls.length, 1);
  queue.retryTurn('one', 0); await tick();
  assert.deepEqual(calls, ['preview', 'preview']);
  releases.shift().resolve(); await tick();
  assert.deepEqual(calls, ['preview', 'preview', 'review']);
  queue.retry('one'); queue.retryTurn('one', 0); await tick();
  assert.equal(calls.length, 3);
  assert.equal(queue.contexts.get('one').states[0], 'running');
  releases.shift().reject(new Error('Engine failed')); await tick();
  queue.retry('one'); await tick();
  assert.deepEqual(calls, ['preview', 'preview', 'review', 'preview']);
  queue.close(); releases.shift().resolve(); await tick();
  assert.equal(calls.length, 4);
});

test('preview navigation keeps selected game priority and retains directly loaded games', async () => {
  const calls = [], releases = [];
  const queue = new ReviewQueue({ preview: true, concurrency: 1,
    load: async id => ({ review: { id, turns: [{}] } }),
    run: async (context, index, phase) => {
      calls.push([context.review.id, phase]);
      await new Promise(resolve => releases.push(resolve));
    }
  });
  await queue.get('first'); await queue.get('second');
  queue.select('first'); await tick();
  queue.select('second'); releases.shift()(); await tick();
  assert.deepEqual(calls, [['first', 'preview'], ['second', 'preview']]);
  releases.shift()(); await tick();
  assert.deepEqual(calls[2], ['second', 'review']);
  releases.shift()(); await tick();
  assert.deepEqual(calls[3], ['first', 'review']);
  releases.shift()(); await tick();
  queue.close();
});
