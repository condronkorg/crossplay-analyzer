const test = require('node:test');
const assert = require('node:assert/strict');
const { start, play } = require('./move-continuation.js');
const { TILE_COUNTS } = require('./luck-analysis.js');

const turn = {
  grid: Array(15).fill('.'.repeat(15)), rack: 'ACERSTU',
  mover_score: 10, other_score: 20, bag_count: 86, final_turns_remaining: 0
};
const move = { action: 'place', position: '8G', tiles: 'ACT', score: 5 };
function inventory(position) {
  const tiles = [...position.grid.join('')].filter(tile => tile !== '.')
    .map(tile => tile === tile.toLowerCase() ? '?' : tile);
  return [...tiles, ...position.racks.join(''), ...position.bag].sort().join('');
}

test('a branch preserves tiles, original state, scores and both player racks', () => {
  const original = start(turn, 'AEINRST', 123);
  const snapshot = structuredClone(original);
  const next = play(original, move);
  assert.equal(play({ ...original, moves: [move] }, move).moves, undefined,
    'Replies from the previous position must be regenerated for the new board and rack');
  assert.deepEqual(original, snapshot);
  assert.equal(next.grid[7], '......ACT......');
  assert.equal(next.racks[1], 'AEINRST');
  assert.equal(next.racks[0].length, 7);
  assert.equal(next.bag.length, 83);
  assert.deepEqual(next.scores, [15, 20]);
  assert.equal(next.actor, 1);
  assert.equal(inventory(original), inventory(next));
  const reply = play(next, { action: 'place', position: 'H8', tiles: '.RE', score: 4 });
  assert.equal(reply.grid[7][7], 'C');
  assert.equal(reply.grid[8][7], 'R');
  assert.equal(reply.actor, 0);
  assert.deepEqual(reply.scores, [15, 24]);
  assert.equal(inventory(original), inventory(reply));
  assert.deepEqual(play(next, { action: 'place', position: 'H8', tiles: 'CRE', score: 4 }), reply,
    'Quackle also spells out existing letters in kibitz results');
  // Undo followed by choosing the same move reproduces the draw.
  assert.deepEqual(play(original, move), next);
});

test('draws and missing opponent racks are reproducible; invalid inventory is rejected', () => {
  assert.deepEqual(start(turn, null, 77), start(turn, null, 77));
  assert.equal(start(turn, null, 77).racks[1].length, 7);
  assert.equal(start(turn, null, 77).sampledOpponent, true);
  assert.throws(() => start(turn, 'ZZZZZZZ', 77), /more Z/);
  assert.throws(() => start({ ...turn, bag_count: 99 }, null, 77), /counts/);
  assert.throws(() => play(start(turn, 'AEINRST', 77), { ...move, tiles: 'ZZ' }), /more Z/);
});

test('assigned blanks, exchanges and passes keep physical tiles and board state', () => {
  const original = start({ ...turn, rack: '?CERSTU' }, 'AEINRST', 9);
  const blank = play(original, { ...move, tiles: 'aCT' });
  assert.equal(blank.grid[7][6], 'a');
  assert.equal(inventory(blank), inventory(original));
  const exchange = play(original, { action: 'exchange', tiles: '?C', score: 0 });
  assert.equal(exchange.bag.length, original.bag.length);
  assert.deepEqual(exchange.grid, original.grid);
  assert.equal(inventory(exchange), inventory(original));
  const pass = play(original, { action: 'pass', tiles: '', score: 0 });
  assert.deepEqual(pass.bag, original.bag);
  assert.deepEqual(pass.racks, original.racks);
});

test('emptying the bag starts two final turns, then ends the line', () => {
  const racks = ['AEIRST?', 'BCDEFGH'];
  const bag = ['E'];
  const pool = Object.entries(TILE_COUNTS).flatMap(([tile, count]) => Array(count).fill(tile));
  for (const tile of racks.join('') + bag.join('')) pool.splice(pool.indexOf(tile), 1);
  const cells = pool.map(tile => tile === '?' ? 'a' : tile);
  while (cells.length < 225) cells.push('.');
  const grid = Array.from({length: 15}, (_, i) => cells.slice(i * 15, (i + 1) * 15).join(''));
  const initial = start({ ...turn, grid, rack: racks[0], bag_count: 1 }, racks[1], 1);
  const drained = play(initial, { action: 'place', position: '15O', tiles: 'A', score: 1 });
  assert.equal(drained.bag.length, 0);
  assert.equal(drained.finalTurns, 2);
  assert.equal(drained.finished, false);
  assert.throws(() => play(drained, { action: 'exchange', tiles: 'B', score: 0 }), /bag/);
  const final = play(drained, { action: 'pass', tiles: '', score: 0 });
  assert.equal(final.finalTurns, 1);
  const done = play(final, { action: 'pass', tiles: '', score: 0 });
  assert.equal(done.finished, true);
  assert.equal(inventory(done), inventory(initial));
  assert.throws(() => play(done, move), /finish/);
});

const { analyze } = require('./move-continuation.js');

function simulated(payload) {
  const end = payload.bagCount <= 21;
  const pass = { action: 'pass', position: '', tiles: '', score: 0, equity: -5,
    staticEquity: -10, win: .7, samples: payload.iterations, terminalSamples: end ? payload.iterations : 0 };
  const placement = { ...pass, action: 'place', position: '8G', tiles: 'AT', score: 5,
    equity: 20, staticEquity: 25, win: .4 };
  const moves = [placement, pass];
  return { moves, selected: pass, best: pass, simulated: true,
    ...(end ? {endgamePolicy:'crossplay-endgame-v1'} : {}),
    seed: payload.seed, samplingVersion: 'paired-review-v1',
    iterationsRequested: payload.iterations, iterationsCompleted: payload.iterations,
    candidateCount: moves.length, totalPlayaheads: moves.length * payload.iterations,
    bagCount: payload.bagCount, finalTurnsRemaining: payload.finalTurnsRemaining,
    plies: end ? -1 : 2, playToEnd: end, simulationMode: end ? 'play-to-end' : '2-ply',
    winModel: end ? 'terminal' : 'bogowin', bestStaticEquity: 25,
    staticMoves: moves.map(move => ({ ...move, equity: move.staticEquity })) };
}

test('continuation simulations use the new position and rank win chance ahead of equity', async () => {
  const position = play(start(turn, 'AEINRST', 123), move);
  const snapshot = structuredClone(position);
  const controller = new AbortController();
  const result = await analyze(position, async (type, payload, options) => {
    assert.equal(type, 'simulateReview');
    assert.equal(payload.gridJson, JSON.stringify(position.grid));
    assert.equal(payload.rack, position.racks[1]);
    assert.equal(payload.playerScore, 20);
    assert.equal(payload.opponentScore, 15);
    assert.equal(payload.bagCount, 83);
    assert.equal(payload.finalTurnsRemaining, 0);
    assert.equal(payload.iterations, 277);
    assert.equal(payload.seed, position.seed);
    assert.equal(payload.played.action, 'pass');
    assert.equal(options.signal, controller.signal);
    return simulated(payload);
  }, { signal: controller.signal });
  assert.equal(result.moves[0].action, 'pass');
  assert.ok(result.moves[0].staticEquity < result.moves[1].staticEquity);
  assert.deepEqual(position, snapshot);
  const next = play({ ...position, moves: result.moves, analysis: result }, result.moves[0]);
  assert.equal(next.moves, undefined);
  assert.equal(next.analysis, undefined);
});

test('final-turn context is passed through and incomplete or invalid win estimates are rejected', async () => {
  const position = { ...start(turn, 'AEINRST', 123), bag: [], finalTurns: 1 };
  const result = await analyze(position, async (_, payload) => {
    assert.equal(payload.bagCount, 0);
    assert.equal(payload.finalTurnsRemaining, 1);
    return simulated(payload);
  });
  assert.equal(result.playToEnd, true);
  for (const corrupt of [
    result => { result.moves[0].win = NaN; },
    result => { result.moves[0].samples--; },
    result => { result.moves[0].terminalSamples = 0; },
    result => { result.seed++; },
    result => { result.bagCount = 1; },
    result => { result.finalTurnsRemaining = 2; },
    result => { result.selected = result.moves[0]; },
  ]) {
    await assert.rejects(analyze(position, async (_, payload) => {
      const response = simulated(payload);
      corrupt(response);
      return response;
    }), /incomplete/);
  }
  await assert.rejects(analyze(position, async () => { throw new Error('Worker cancelled'); }), /cancelled/);
});

test('empty racks force a pass, preserving win perspective and terminal ties', async () => {
  const position = { ...start(turn, 'AEINRST', 123), racks: ['', 'AEINRST'],
    bag: [], finalTurns: 2 };
  const result = await analyze(position, async (_, payload) => {
    assert.equal(payload.rack, 'AEINRST');
    assert.equal(payload.finalTurnsRemaining, 1);
    return simulated(payload);
  });
  assert.equal(result.moves[0].action, 'pass');
  assert.ok(Math.abs(result.moves[0].win - .3) < 1e-10);
  for (const [scores, win] of [[[20, 10], 1], [[10, 20], 0], [[20, 20], .5]]) {
    const terminal = await analyze({ ...position, scores, finalTurns: 1 }, async () => {
      assert.fail('A forced final pass needs no simulation');
    });
    assert.equal(terminal.moves[0].win, win);
  }
});

test('low-bag continuation swaps preserve all tiles and reject overdraws', () => {
  const racks = ['AAEIRT?', 'EINORST'], bag = 'BCDEFG';
  const pool = Object.entries(TILE_COUNTS).flatMap(([tile,count])=>Array(count).fill(tile));
  for (const tile of racks.join('') + bag) pool.splice(pool.indexOf(tile),1);
  const board = pool.map(tile=>tile === '?' ? 'a' : tile).join('').padEnd(225,'.');
  const grid = Array.from({length:15},(_,i)=>board.slice(i*15,i*15+15));
  const initial = start({...turn,grid,rack:racks[0],bag_count:6},racks[1],1);
  const next = play(initial,{action:'exchange',tiles:'AA?',score:0});
  assert.equal(next.bag.length,6);
  assert.equal(next.racks[0].length,7);
  assert.equal(inventory(next),inventory(initial));
  assert.deepEqual(next.grid,initial.grid);
  assert.throws(()=>play(initial,{action:'exchange',tiles:'AAEIRT?',score:0}),/bag/);
});
