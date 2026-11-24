const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');
const context = vm.createContext({ importScripts() {}, self: { addEventListener() {} } });
vm.runInContext(fs.readFileSync('quackle-worker.js', 'utf8'), context);
const position = customMove => ({ gridJson: JSON.stringify(Array(15).fill('.'.repeat(15))),
  rack: 'AAEIRT?', bagCount: 86, finalTurnsRemaining: 0, customMove });

test('custom swaps match physical repeated tiles and blanks independently of order', () => {
  for (const tiles of ['AA?', '?AA']) {
    const result = context.customMoveKey(position({ action: 'exchange', tiles }));
    assert.equal(result.key, 'exchange:?AA');
    assert.equal(result.move.action, 'exchange');
    assert.equal(result.move.tiles, '?AA');
    assert.equal(context.candidateKey({ action: 'exchange', tiles: 'AA?' }, result.grid), result.key);
    assert.notEqual(context.candidateKey({ action: 'pass', tiles: '' }, result.grid), result.key);
  }
});

test('custom swaps reject missing physical tiles, invalid letters, and low bags', () => {
  for (const tiles of ['AAA', '??', 'Z']) {
    assert.throws(() => context.customMoveKey(position({ action: 'exchange', tiles })), /rack does not contain/);
  }
  for (const tiles of ['', 'A!', 'AAAAAAAA']) {
    assert.throws(() => context.customMoveKey(position({ action: 'exchange', tiles })), /1 to 7 letters/);
  }
  const lowBag = position({ action: 'exchange', tiles: 'A' });
  lowBag.gridJson = JSON.stringify(Array(15).fill('AAAAAA.........'));
  lowBag.bagCount = 0;
  lowBag.finalTurnsRemaining = 1;
  assert.throws(() => context.customMoveKey(lowBag), /at least seven/);
});

test('simulated custom placements compare with highest WP while static compares equity', () => {
  const moves = [
    { action: 'place', position: '8G', tiles: 'TEA', equity: 10, win: .70 },
    { action: 'place', position: '8D', tiles: 'QUARTES', equity: 70, win: .91 },
    { action: 'place', position: '8D', tiles: 'QUATRES', equity: 68, win: .92 },
  ];
  const module = {
    kibitz: () => ({ moves }),
    simulateKibitz: (_grid, _rack, _player, _opponent, _count, samples, bagCount, finalTurnsRemaining) => ({
      moves: moves.map(move => ({ ...move, samples })), simulated: true,
      iterationsRequested: samples, iterationsCompleted: samples,
      bagCount, finalTurnsRemaining, playToEnd: false,
      simulationMode: '2-ply', plies: 2, winModel: 'bogowin',
      candidateCount: moves.length, totalPlayaheads: samples * moves.length,
    }),
  };
  const payload = { ...position({ action: 'place', word: 'TEA', square: 'G8', direction: 'across' }),
    rack: 'AEQRSTU', playerScore: 100, opponentScore: 52, iterations: 10 };
  assert.equal(context.evaluateCustomMove(module, payload, false).best.tiles, 'QUARTES');
  const result = context.evaluateCustomMove(module, payload, true);
  assert.equal(result.best.tiles, 'QUATRES');
  assert.equal(result.best.win, Math.max(...result.moves.map(move => move.win)));
});

test('custom swaps reject Unicode before case expansion can change physical tiles', () => {
  for (const tiles of ['ß', 'ßS', 'ßßßß']) {
    assert.throws(() => context.customMoveKey({ ...position({ action: 'exchange', tiles }), rack: 'SSSSSSS' }), /1 to 7 letters/);
  }
});
