const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');
const context = vm.createContext({ importScripts() {}, self: { addEventListener() {} } });
vm.runInContext(fs.readFileSync('quackle-worker.js', 'utf8'), context);
const position = customMove => ({ gridJson: JSON.stringify(Array(15).fill('.'.repeat(15))),
  rack: 'AAEIRT?', bagCount: 86, finalTurnsRemaining: 0, customMove });

function reviewResult(moves, samples, bagCount, finalTurnsRemaining, seed, selected = moves[0]) {
  const simulated = moves.map(move => ({ ...move, staticEquity: move.equity, samples }));
  return { moves: simulated, selected: simulated[moves.indexOf(selected)],
    best: simulated.reduce((top, move) => move.win > top.win ? move : top),
    staticMoves: moves, bestStaticEquity: Math.max(...moves.map(move => move.equity)),
    simulated: true, seed, samplingVersion: 'paired-review-v1',
    iterationsRequested: samples, iterationsCompleted: samples,
    bagCount, finalTurnsRemaining, playToEnd: false,
    simulationMode: '2-ply', plies: 2, winModel: 'bogowin',
    candidateCount: moves.length, totalPlayaheads: samples * moves.length };
}

test('custom placements force the selected move into a bounded comparison at any static rank', () => {
  for (const rank of [1, 187, 4755]) {
    const selected = { action: 'place', position: '8G', tiles: 'TEA', equity: 10, win: .70 };
    const alternative = { action: 'place', position: '8D', tiles: 'QUARTES', equity: 70, win: .91 };
    const moves = Array(4755).fill(alternative);
    moves[rank - 1] = selected;
    let calls = 0;
    const module = {
      kibitz: () => ({ moves }),
      simulateKibitz: () => { throw new Error('Must not simulate every higher-ranked move'); },
      simulateReviewMove: (_grid, _rack, _player, _opponent, count, samples, bag, finalTurns, played, seed) => {
        calls++;
        assert.equal(count, 15);
        assert.equal(samples, 277);
        assert.deepEqual(JSON.parse(JSON.stringify(played)), { action: 'place', position: '8G', tiles: 'TEA' });
        return reviewResult([alternative, selected], samples, bag, finalTurns, seed, selected);
      },
    };
    const payload = { ...position({ action: 'place', word: 'TEA', square: 'G8', direction: 'across' }),
      rack: 'AEQRSTU', playerScore: 100, opponentScore: 52, iterations: 277, numMoves: 4755 };
    const result = context.evaluateCustomMove(module, payload, true);
    assert.equal(calls, 1);
    assert.equal(result.staticRank, rank);
    assert.equal(result.legalMoveCount, 4755);
    assert.equal(result.selected.tiles, 'TEA');
    assert.equal(result.selected.samples, 277);
    assert.equal(result.best.tiles, 'QUARTES');
    assert.ok(result.candidateCount <= 16);
  }
});

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
  assert.throws(() => context.customMoveKey(lowBag), /bag/);
});

test('simulated custom placements compare with highest WP while static compares equity', () => {
  const moves = [
    { action: 'place', position: '8G', tiles: 'TEA', equity: 10, win: .70 },
    { action: 'place', position: '8D', tiles: 'QUARTES', equity: 70, win: .91 },
    { action: 'place', position: '8D', tiles: 'QUATRES', equity: 68, win: .92 },
  ];
  const module = {
    kibitz: () => ({ moves }),
    simulateReviewMove: (_grid, _rack, _player, _opponent, _count, samples, bagCount, finalTurnsRemaining, _played, seed) =>
      reviewResult(moves, samples, bagCount, finalTurnsRemaining, seed),
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

test('custom swaps allow fewer than seven bag tiles and filter overdraw alternatives', () => {
  const payload = {...position({action:'exchange', tiles:'AA?'}), bagCount:6,
    gridJson:JSON.stringify(Array.from({length:15}, (_, i) => i < 5 ? 'AAAAAAAAAAAAAAA' : i === 5 ? 'AAAAA..........' : '.'.repeat(15)))};
  assert.equal(context.customMoveKey(payload).move.tiles, '?AA');
  const module = {kibitz: () => ({moves:[
    {action:'exchange', position:'', tiles:'AAEIRT?', equity:100},
    {action:'exchange', position:'', tiles:'?AA', equity:2},
    {action:'pass', position:'', tiles:'', equity:0}
  ]})};
  const result = context.evaluateCustomMove(module, payload, false);
  assert.equal(result.legalMoveCount, 2);
  assert.equal(result.staticRank, 1);
  assert.equal(result.best.tiles, '?AA');
  assert.throws(() => context.customMoveKey({...payload,customMove:{action:'exchange',tiles:'AAEIRT?'}}), /bag/);
});

test('a custom swap can use the last bag tile without overdrawing', () => {
  const board = 'A'.repeat(85).padEnd(225,'.');
  const payload = {...position({action:'exchange',tiles:'?'}), bagCount:1,
    gridJson:JSON.stringify(Array.from({length:15},(_,i)=>board.slice(i*15,i*15+15)))};
  assert.equal(context.customMoveKey(payload).move.tiles, '?');
  assert.throws(()=>context.customMoveKey({...payload,customMove:{action:'exchange',tiles:'AA'}}),/bag/);
});

test('sampled position requests reject stale low-bag engine policies', () => {
  const payload = {bagCount:2, finalTurnsRemaining:0};
  const legacy = {simulated:true};
  assert.throws(() => context.simulate({simulateKibitz:() => legacy}, payload), /endgame policy/);
  const current = {...legacy, endgamePolicy:'crossplay-endgame-v1'};
  assert.equal(context.simulate({simulateKibitz:() => current}, payload), current);
  assert.equal(context.simulate({simulateKibitz:() => legacy}, {...payload, bagCount:22}), legacy);
});
