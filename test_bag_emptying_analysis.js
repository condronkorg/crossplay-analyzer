const test = require('node:test');
const assert = require('node:assert/strict');
const Bag = require('./bag-emptying-analysis.js');

test('complete search retains a winner outside the display shortlist and static best', () => {
  const moves = Array.from({ length: 20 }, (_, i) => ({
    action: 'place',
    position: '1A',
    tiles: String(i),
    equity: 100 - i,
    score: i,
    leave: '',
  }));
  const plan = {
    moves,
    equityMoves: moves.slice(0, 15),
    actualIndex: 0,
    bagEmptyingIndices: [19],
    seed: 4,
    bagCount: 5,
    finalTurnsRemaining: 0,
  };
  const state = Bag.create(plan);
  assert.throws(() => Bag.finish(state, 277), /incomplete/);
  for (let i = 0; i < moves.length; i++)
    Bag.incorporate(state, {
      start: i,
      offset: 0,
      completed: 277,
      moves: [
        { ...moves[i], win: i === 19 ? 1 : 0, equity: i, samples: 277, terminalSamples: 277 },
      ],
    });
  const result = Bag.finish(state, 277);
  assert.equal(result.best.tiles, '19');
  assert.equal(result.selected.tiles, '0');
  assert.equal(result.searchedCandidateCount, 20);
  assert.equal(result.requiredBagEmptyingCount, 1);
  assert.equal(result.evaluatedBagEmptyingCount, 1);
  assert.equal(result.totalPlayaheads, 5540);
  assert.ok(result.moves.length <= 17);
});

test('partial batches must use contiguous offsets and finish every sample', () => {
  const state = Bag.create({
    moves: [{ action: 'pass', position: '', tiles: '', equity: 0 }],
    actualIndex: 0,
    bagEmptyingIndices: [],
    seed: 0,
    bagCount: 1,
    finalTurnsRemaining: 0,
  });
  const result = {
    start: 0,
    offset: 0,
    completed: 2,
    moves: [{ win: 0.5, equity: 3, samples: 2, terminalSamples: 2 }],
  };
  Bag.incorporate(state, result);
  assert.throws(() => Bag.incorporate(state, result), /offset/);
  assert.throws(() => Bag.finish(state, 277), /incomplete/);
});

test('coverage validation rejects an omitted policy, partial coverage, and false counts', () => {
  const result = {
    moves: [{}],
    equityMoves: [{ equity: 0 }],
    candidatePolicy: Bag.POLICY,
    searchedCandidateCount: 3,
    requiredBagEmptyingCount: 2,
    evaluatedBagEmptyingCount: 2,
    totalPlayaheads: 831,
  };
  assert.equal(Bag.validCoverage(result, 5, 277), true);
  for (const changes of [
    { candidatePolicy: undefined },
    { evaluatedBagEmptyingCount: 1 },
    { searchedCandidateCount: 0 },
    { requiredBagEmptyingCount: 4 },
    { totalPlayaheads: 277 },
  ]) {
    assert.equal(Bag.validCoverage({ ...result, ...changes }, 5, 277), false);
  }
});

test('common-word equity opportunity keeps the original static list after display compaction', () => {
  const Luck = require('./luck-analysis.js');
  const moves = Array.from({ length: 20 }, (_, i) => ({
    action: 'place',
    position: '1A',
    tiles: String(i),
    equity: 100 - i,
    score: 0,
    word: i === 1 ? 'CAT' : 'QAT',
  }));
  const state = Bag.create({
    moves,
    equityMoves: moves.slice(0, 15),
    actualIndex: 0,
    bagEmptyingIndices: [19],
    seed: 1,
    bagCount: 5,
    finalTurnsRemaining: 0,
  });
  moves.forEach((move, i) =>
    Bag.incorporate(state, {
      start: i,
      offset: 0,
      completed: 277,
      moves: [{ win: i === 19 ? 1 : 0, equity: i, samples: 277, terminalSamples: 277 }],
    }),
  );
  const result = Bag.finish(state, 277);
  const frequencies = new Map([
    ['cat', 4.5],
    ['qat', 2],
  ]);
  const evaluate = (candidates) =>
    Luck.opportunityContext({}, candidates, frequencies, (_, move) => move.word);
  assert.equal(
    result.staticMoves.some((move) => move.word === 'CAT'),
    false,
  );
  assert.equal(evaluate(Bag.equityCandidates(result)).accessibleEquity, 99);
  assert.equal(evaluate(result.staticMoves).accessibleEquity, 96);
  assert.deepEqual(Bag.equityCandidates({ staticMoves: moves }), moves);
});

test('variable-length games combine conditional per-ply equity statistics correctly', () => {
  const move = { action: 'pass', position: '', tiles: '', equity: 0 };
  const state = Bag.create({
    moves: [move],
    equityMoves: [move],
    actualIndex: 0,
    bagEmptyingIndices: [],
    seed: 1,
    bagCount: 7,
    finalTurnsRemaining: 0,
  });
  Bag.incorporate(state, {
    start: 0,
    offset: 0,
    completed: 2,
    moves: [
      {
        samples: 2,
        terminalSamples: 2,
        win: 0.5,
        equity: 30,
        equityStatistics: [
          [0, 0, 20, 2],
          [1, 0, 20, 1],
        ],
        residualSum: 0,
      },
    ],
  });
  Bag.incorporate(state, {
    start: 0,
    offset: 2,
    completed: 2,
    moves: [
      {
        samples: 2,
        terminalSamples: 2,
        win: 0.5,
        equity: 30,
        equityStatistics: [[0, 0, 60, 2]],
        residualSum: 0,
      },
    ],
  });
  assert.equal(Bag.finish(state, 4).selected.equity, 40);
});
