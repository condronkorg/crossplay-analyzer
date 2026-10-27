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
