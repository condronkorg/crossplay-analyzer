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
  assert.throws(() => play(drained, { action: 'exchange', tiles: 'B', score: 0 }), /seven/);
  const final = play(drained, { action: 'pass', tiles: '', score: 0 });
  assert.equal(final.finalTurns, 1);
  const done = play(final, { action: 'pass', tiles: '', score: 0 });
  assert.equal(done.finished, true);
  assert.equal(inventory(done), inventory(initial));
  assert.throws(() => play(done, move), /finish/);
});
