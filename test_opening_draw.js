const assert = require('node:assert/strict');
const { test } = require('node:test');
let opening;
try { opening = require('./opening-draw.js'); } catch (error) {
  if (error.code !== 'MODULE_NOT_FOUND') throw error;
  opening = {};
}

const emptyGrid = () => Array(15).fill('.'.repeat(15));
const review = (mine = true) => ({ turns: [
  { mine, rack: 'TSRNEA?', action: 'PASS 0', grid: emptyGrid(), mover_score: 0, other_score: 0 },
  { mine: !mine, rack: 'DOGILOU', action: 'DOG 8G 10', grid: emptyGrid(), mover_score: 0, other_score: 0 }
] });
const input = { available: true, firstMine: true, firstRack: '?AENRST', secondRack: 'DGILOOU' };
const raw = (values = {}, iterations = 277) => ({
  simulated: true, samplingVersion: 'initial-deal-v1', policy: 'static-best-v1',
  firstRack: '?AENRST', secondRack: 'DGILOOU', seed: 806877465,
  baselineWin: .55, firstOnlyWin: .65, secondOnlyWin: .45, afterWin: .6,
  firstWpa: 12.5, secondWpa: 7.5, iterationsRequested: iterations,
  iterationsCompleted: iterations, plies: 2, playToEnd: false,
  simulationMode: '2-ply', winModel: 'bogowin', ...values
});

test('opening WPA averages both reveal orders and retains first-move baseline', () => {
  assert.equal(typeof opening.validResult, 'function');
  for (const [B, A, C, J, first, second] of [
    [.55, .65, .45, .6, 12.5, 7.5],
    [.55, .55, .55, .55, 0, 0],
    [.55, .45, .65, .5, -12.5, -7.5],
    [.55, .55, .55, .75, 10, -10],
    [.55, .65, .55, .65, 10, 0]
  ]) {
    const result = raw({ baselineWin: B, firstOnlyWin: A, secondOnlyWin: C, afterWin: J,
      firstWpa: first, secondWpa: second });
    assert.equal(opening.validResult(result, input, 277), true);
    const point = opening.point(result, input);
    assert.equal(point.beforeWin, B);
    assert.equal(point.afterWin, J);
    assert.equal(point.firstWpa, first);
    assert.equal(point.secondWpa, second);
    assert.ok(Math.abs((first - second) - 100 * (J - B)) < 1e-8);
    assert.equal(opening.validResult({ ...result, firstWpa: first + 1 }, input, 277), false);
    assert.equal(opening.validResult({ ...result, secondWpa: second + 1 }, input, 277), false);
  }
});

test('opening uses each first recorded rack, including a pass or opponent opener', () => {
  assert.deepEqual(opening.describe(review()), input);
  assert.deepEqual(opening.describe(review(false)), { ...input, firstMine: false });
  const played = review();
  played.turns[0].action = 'ANTE 8G 12';
  played.turns[1].grid[7] = '......ANTE.....';
  played.turns[1].other_score = 12;
  assert.deepEqual(opening.describe(played), input);
});

test('missing first rack stays unavailable even when a later rack exists', () => {
  for (const missingIndex of [0, 1]) {
    const game = review();
    game.turns.push({ ...game.turns[missingIndex], rack: 'AEINRST' });
    game.turns[missingIndex].rack = null;
    const result = opening.describe(game);
    assert.equal(result.unavailable, true);
    assert.equal(typeof result.reason, 'string');
    assert.ok(result.reason.length);
    assert.equal(Object.hasOwn(result, 'firstWpa'), false);
  }
});

test('opening requires an empty full board, zero scores, and two physical full racks', () => {
  const mutations = [
    game => { game.turns[0].grid = Array(15); },
    game => { game.turns[0].grid = ['.']; },
    game => { game.turns[0].grid[7] = '.......A.......'; },
    game => { game.turns[0].mover_score = 1; },
    game => { delete game.turns[0].other_score; },
    game => { game.turns[0].mine = 1; },
    game => { game.turns[1].rack = 'AEINRT'; },
    game => { game.turns[0].rack = 'AAAAAAA'; game.turns[1].rack = 'AAAAAAA'; },
    game => { game.turns[0].rack = '??ABCDE'; game.turns[1].rack = '??FGHIL'; },
    game => { game.turns[0].rack = 'aBCDEFG'; },
    game => { game.turns.pop(); }
  ];
  for (const change of mutations) {
    const game = review();
    change(game);
    assert.equal(opening.describe(game).unavailable, true);
  }
  const blanks = review();
  blanks.turns[0].rack = '??ABCDE';
  blanks.turns[1].rack = '?FGHILO';
  assert.equal(opening.describe(blanks).available, true);
  for (const game of [null, {}, { turns: [] }, { turns: [null] }]) {
    assert.equal(opening.describe(game).unavailable, true);
  }
});

test('cache identity uses canonical racks and a common seed independent of moves and sample count', () => {
  assert.equal(opening.key(input, 277), '["quackle-opening-draw-v1","?AENRST","DGILOOU",277,2,"static-best-v1","initial-deal-v1"]');
  assert.equal(opening.key({ ...input, firstRack: 'TSRNEA?' }, 277), opening.key(input, 277));
  const changed = { ...input, firstRack: 'AEINRST', secondRack: 'ABCDEFG', firstMine: false };
  assert.notEqual(opening.key(changed, 277), opening.key(input, 277));
  assert.notEqual(opening.key(input, 64), opening.key(input, 277));
  assert.equal(opening.seed(input), 806877465);
  assert.equal(opening.seed(changed), 806877465);
  const game = review();
  game.turns[0].action = 'SWAP 0';
  assert.equal(opening.key(opening.describe(game), 277), opening.key(input, 277));
});

test('raw validation rejects malformed metadata, probabilities, physical racks, and biased attribution', () => {
  assert.equal(opening.validResult(raw(), { firstRack: input.firstRack, secondRack: input.secondRack }, 277), true);
  assert.equal(opening.validResult(raw({ seed: 123 }), input, 277, 123), true);
  assert.equal(opening.validResult(raw({ seed: 123 }), input, 277), false);
  for (const changes of [
    { simulated: 1 }, { samplingVersion: 'rack-draw-v1' }, { policy: 'best' },
    { seed: 1 }, { seed: true }, { iterationsRequested: 64 }, { iterationsCompleted: 64 },
    { plies: -1 }, { playToEnd: true }, { simulationMode: 'play-to-end' }, { winModel: 'terminal' },
    { firstRack: 'AEINRST' }, { secondRack: 'DGILOU' }, { baselineWin: -.01 },
    { afterWin: 1.01 }, { firstOnlyWin: NaN }, { secondOnlyWin: Infinity },
    { baselineWin: true }, { firstWpa: 5 }, { secondWpa: -7.5 }
  ]) assert.equal(opening.validResult(raw(changes), input, 277), false, JSON.stringify(changes));
  assert.equal(opening.validResult(raw(), input, 99), false);
  assert.equal(opening.validResult(null, input, 277), false);
  assert.equal(opening.validResult(raw(), { ...input, firstRack: '???????' }, 277), false);
});

test('derived points keep orientation and preview status separate from real moves', () => {
  const point = opening.point(raw(), { ...input, firstMine: false });
  assert.deepEqual(point, { firstMine: false, firstRack: '?AENRST', secondRack: 'DGILOOU',
    beforeWin: .55, firstOnlyWin: .65, secondOnlyWin: .45, afterWin: .6,
    firstWpa: 12.5, secondWpa: 7.5, samples: 277, provisional: false });
  assert.equal(opening.point(raw({}, 64), input).provisional, true);
  assert.equal(opening.point(null, input).unavailable, true);
  assert.equal(opening.point(raw(), { unavailable: true, reason: 'Opening rack missing.' }).unavailable, true);
});

test('failed native results cannot be reused as opening estimates', () => {
  assert.equal(opening.validResult(raw({error:'failed'}), input, 277), false);
});
