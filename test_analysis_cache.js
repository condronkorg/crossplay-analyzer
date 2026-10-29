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

test('review results include the recorded action and paired sampling version in their identity', () => {
  const cache = new AnalysisCache();
  const played = { ...turn, action: 'CAT 8G 10', swapped_tiles: null };
  const key = cache.key(played, 'review');
  assert.deepEqual(JSON.parse(key), ['quackle-review-v1', 'review', played.grid, played.rack,
    0, 0, 86, 0, 15, 277, 2, 21, 'CAT 8G 10', '', 'paired-review-v1']);
  assert.notEqual(key, cache.key({ ...played, action: 'CAT 8H 10' }, 'review'));
  assert.notEqual(key, cache.key({ ...played, action: 'cAT 8G 10' }, 'review'));
  assert.notEqual(cache.key({ ...played, action: 'SWAP 0', swapped_tiles: 'AB' }, 'review'),
    cache.key({ ...played, action: 'SWAP 0', swapped_tiles: 'AE' }, 'review'));
  assert.notEqual(key, cache.key(played, 'simulation'));
  assert.equal(cache.seed(played), new AnalysisCache().seed(played));
  assert.notEqual(cache.seed(played), cache.seed({ ...played, other_score: 50 }));
  assert.ok(Number.isInteger(cache.seed(played)) && cache.seed(played) >= 0 && cache.seed(played) <= 0xffffffff);
});

test('preview results use a separate profile without changing full review keys or paired seeds', async () => {
  const cache = new AnalysisCache();
  const played = { ...turn, action: 'CAT 8G 10' };
  const fullKey = JSON.stringify(['quackle-review-v1', 'review', played.grid, played.rack,
    0, 0, 86, 0, 15, 277, 2, 21, 'CAT 8G 10', '', 'paired-review-v1']);
  assert.equal(cache.key(played, 'review'), fullKey);
  assert.deepEqual(JSON.parse(cache.key(played, 'preview')), ['quackle-review-v1', 'preview',
    played.grid, played.rack, 0, 0, 86, 0, 5, 64, 2, 21, 'CAT 8G 10', '', 'paired-review-v1']);
  let fullSeed = 2166136261;
  for (const character of fullKey) fullSeed = Math.imul(fullSeed ^ character.charCodeAt(0), 16777619);
  assert.equal(cache.seed(played), fullSeed >>> 0);
  const custom = new AnalysisCache({}, { previewMoveCount: 3, previewIterations: 32 });
  assert.notEqual(custom.key(played, 'preview'), cache.key(played, 'preview'));
  assert.equal(custom.key(played, 'review'), fullKey);
  assert.equal(custom.seed(played), cache.seed(played));
  const preview = { moves: [{ win: .4 }], iterationsCompleted: 64 };
  const full = { moves: [{ win: .45 }], iterationsCompleted: 277 };
  await cache.request(played, 'preview', async () => preview);
  assert.equal(cache.get(played, 'review'), null);
  await cache.request(played, 'review', async () => full);
  const restored = new AnalysisCache(JSON.parse(JSON.stringify(cache.results)));
  assert.deepEqual(restored.get(played, 'preview'), preview);
  assert.deepEqual(restored.get(played, 'review'), full);
});

test('a viewed turn promotes pending background work without a duplicate computation', async () => {
  const cache = new AnalysisCache();
  let finish;
  let calls = 0;
  const promotions = [];
  const first = cache.request(turn, 'review', () => {
    calls++;
    return new Promise(resolve => { finish = resolve; });
  }, undefined, { priority: 1, onPromote: priority => promotions.push(priority) });
  await Promise.resolve();
  const second = cache.request(turn, 'review', () => { calls++; }, undefined, { priority: 100 });
  const third = cache.request(turn, 'review', () => { calls++; }, undefined, { priority: 2 });
  finish({ moves: [] });
  assert.equal(await first, await second);
  assert.equal(await first, await third);
  assert.equal(calls, 1);
  assert.deepEqual(promotions, [100]);
});
