const test = require('node:test');
const assert = require('node:assert/strict');
const AnalysisCache = require('./analysis-cache.js');
const turn = { grid: Array(15).fill('.'.repeat(15)), rack: 'ACERSTU', mover_score: 0, other_score: 0, bag_count: 86 };

test('concurrent views share one engine request and reload reuses the saved result', async () => {
  const cache = new AnalysisCache();
  let calls = 0;
  const compute = async () => { calls++; return { simulated: false, moves: [{ tiles: 'CAT', equity: 5 }] }; };
  const [grade, details] = await Promise.all([
    cache.request(turn, 'candidates', compute), cache.request(turn, 'candidates', compute)
  ]);
  assert.equal(calls, 1);
  assert.equal(grade, details);
  const restored = new AnalysisCache(JSON.parse(JSON.stringify(cache.results)));
  assert.deepEqual(await restored.request(turn, 'candidates', compute), grade);
  assert.equal(calls, 1);
});

test('position, scores, final turns and analysis mode are part of the cache identity', async () => {
  const cache = new AnalysisCache();
  await cache.request(turn, 'candidates', async () => ({ moves: [] }));
  for (const change of [{ rack: 'ABCDEFG' }, { mover_score: 1 }, { other_score: 2 },
    { bag_count: 0, final_turns_remaining: 1 }, { grid: ['A..............', ...turn.grid.slice(1)] }]) {
    assert.equal(cache.get({ ...turn, ...change }, 'candidates'), null);
  }
  assert.equal(cache.get(turn, 'simulation'), null);
  assert.equal(new AnalysisCache(cache.results, { moveCount: 10 }).get(turn, 'candidates'), null);
  assert.notEqual(cache.key({ ...turn, bag_count: 0, final_turns_remaining: 1 }, 'simulation'),
    cache.key({ ...turn, bag_count: 0, final_turns_remaining: 2 }, 'simulation'));
});

test('failed requests are retryable and never become saved results', async () => {
  const cache = new AnalysisCache();
  await assert.rejects(cache.request(turn, 'simulation', async () => { throw new Error('engine failure'); }));
  assert.equal(cache.get(turn, 'simulation'), null);
  assert.equal(cache.pending.size, 0);
  const result = await cache.request(turn, 'simulation', async () => ({ moves: [{ win: 0.5 }] }));
  assert.equal(result.moves[0].win, 0.5);
  assert.equal(cache.errors.size, 0);
});

test('trend estimates have their own identity and keep full simulation keys unchanged', () => {
  const cache = new AnalysisCache();
  assert.notEqual(cache.key(turn, 'trend'), cache.key(turn, 'simulation'));
  assert.notEqual(cache.key(turn, 'trend'), new AnalysisCache({}, { trendIterations: 40 }).key(turn, 'trend'));
  // Results saved before trend estimates existed must still be found.
  assert.equal(cache.key(turn, 'simulation'), JSON.stringify(['quackle-position-v1', 'simulation', turn.grid,
    turn.rack, 0, 0, 86, 0, 15, 277, 2, 21]));
});

test('cancelled requests are not errors, and only unstarted ones are replaced', async () => {
  const cache = new AnalysisCache();
  const queued = new AbortController();
  const cancelled = cache.request(turn, 'simulation', () => new Promise((_, reject) => {
    queued.signal.addEventListener('abort', () => reject(Object.assign(new Error('cancelled'), { name: 'AbortError' })));
  }), queued.signal);
  await Promise.resolve();
  queued.abort();
  // A new request right after cancelling must not join the cancelled one.
  const replacement = cache.request(turn, 'simulation', async () => ({ moves: [{ win: 0.4 }] }));
  await assert.rejects(cancelled, { name: 'AbortError' });
  assert.equal((await replacement).moves[0].win, 0.4);
  assert.equal(cache.errors.size, 0);

  const other = { ...turn, rack: 'EINORST' };
  const running = new AbortController();
  let finish;
  let calls = 0;
  const first = cache.request(other, 'simulation', onStart => {
    calls++;
    onStart();
    return new Promise(resolve => { finish = resolve; });
  }, running.signal);
  await Promise.resolve();
  running.abort();
  const second = cache.request(other, 'simulation', async () => { calls++; return { moves: [] }; });
  finish({ moves: [{ win: 0.6 }] });
  assert.equal(await second, await first);
  assert.equal(calls, 1);
});
