const test = require('node:test');
const assert = require('node:assert/strict');
const Bag = require('./bag-emptying-analysis.js');

test('ordinary top15 plus actual retain all assessed candidates and static best', () => {
  const moves = Array.from({ length: 16 }, (_, i) => ({
    action: 'place',
    position: '1A',
    tiles: String(i),
    equity: 100 - i,
    score: i,
    leave: '',
  }));
  const plan = {
    moves,
    equityMoves: moves,
    generatedCandidateCount: 200,
    totalBagEmptyingCount: 100,
    actualIndex: 15,
    bagEmptyingIndices: [15],
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
        { ...moves[i], win: i === 15 ? 1 : 0, equity: i, samples: 277, terminalSamples: 277 },
      ],
    });
  const result = Bag.finish(state, 277);
  assert.equal(result.best.tiles, '15');
  assert.equal(result.selected.tiles, '15');
  assert.equal(result.searchedCandidateCount, 16);
  assert.equal(result.requiredBagEmptyingCount, 1);
  assert.equal(result.evaluatedBagEmptyingCount, 1);
  assert.equal(result.totalPlayaheads, 4432);
  assert.equal(result.generatedCandidateCount, 200);
  assert.equal(result.totalBagEmptyingCount, 100);
  assert.equal(result.candidatePolicy, 'crossplay-low-bag-equity-v2');
  assert.ok(result.moves.length <= 16);
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
    moves: [{},{},{}],
    equityMoves: [{equity:0},{equity:0},{equity:0}],
    candidatePolicy: Bag.POLICY,
    searchedCandidateCount: 3,
    generatedCandidateCount: 100,
    totalBagEmptyingCount: 90,
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
    { generatedCandidateCount: 2 },
    { totalBagEmptyingCount: 1 },
    { totalBagEmptyingCount: 101 },
    { generatedCandidateCount: undefined },
    { candidatePolicy: "bag-emptying-v1" },
    { searchedCandidateCount: 17, generatedCandidateCount:100, totalPlayaheads:17*277 },
  ]) {
    assert.equal(Bag.validCoverage({ ...result, ...changes }, 5, 277), false);
  }
});

test('common-word equity opportunity uses the authoritative ordinary candidate list', () => {
  const Luck = require('./luck-analysis.js');
  const moves = Array.from({ length: 15 }, (_, i) => ({
    action: 'place',
    position: '1A',
    tiles: String(i),
    equity: 100 - i,
    score: 0,
    word: i === 1 ? 'CAT' : 'QAT',
  }));
  const state = Bag.create({
    moves,
    equityMoves: moves,
    generatedCandidateCount: 200,
    totalBagEmptyingCount: 100,
    actualIndex: 0,
    bagEmptyingIndices: [14],
    seed: 1,
    bagCount: 5,
    finalTurnsRemaining: 0,
  });
  moves.forEach((move, i) =>
    Bag.incorporate(state, {
      start: i,
      offset: 0,
      completed: 277,
      moves: [{ win: i === 14 ? 1 : 0, equity: i, samples: 277, terminalSamples: 277 }],
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
    true,
  );
  assert.equal(evaluate(Bag.equityCandidates(result)).accessibleEquity, 99);
  assert.equal(evaluate(result.staticMoves).accessibleEquity, 99);
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

test('generic terminal results apply native tie order and retain the full raw static list', () => {
  const moves = [
    {action: 'pass', position: '', tiles: '', score: 0, equity: -999},
    {action: 'place', position: 'B9', tiles: 'IN', score: 14, equity: 18},
    {action: 'place', position: '9B', tiles: 'iN', score: 13, equity: 16},
    {action: 'exchange', position: '', tiles: 'A?', score: 0, equity: 3},
  ];
  const plan = {moves, actualIndex: 0, bagEmptyingIndices: [], tieOrder: [2, 1, 3, 0],
    bagCount: 8, seed: 2, finalTurnsRemaining: 0};
  const state = Bag.create(plan);
  for (const [offset, completed] of [[0, 3], [3, 61]]) {
    Bag.incorporate(state, {start: 0, offset, completed, moves: moves.map(() => ({
      samples: completed, terminalSamples: completed, win: .5, winSum: completed / 2,
      equity: 2, equityStatistics: [[0, 0, 2 * completed, completed]], residualSum: 0,
    }))});
  }
  const result = Bag.finish(state, 64, false);
  assert.deepEqual(result.moves.map(move => move.tiles), ['iN', 'IN', 'A?', '']);
  assert.equal(result.best.tiles, 'iN');
  assert.equal(result.selected.action, 'pass');
  assert.equal(result.candidatePolicy, undefined);
  assert.equal(result.equityMoves, undefined);
  assert.deepEqual(result.staticMoves, moves);
});


test('shortlist coverage distinguishes selected placements from all generated placements', () => {
  assert.equal(Bag.coverageNote({candidatePolicy: Bag.POLICY, searchedCandidateCount:25,
    evaluatedBagEmptyingCount:10,totalBagEmptyingCount:352}),
    'Compared 25 selected moves, including 10 of 352 legal bag-emptying placements.');
  assert.equal(Bag.coverageNote({}), '');
});


test('192sample lowbag full profile requires selected coverage just like277', () => {
 const r={moves:Array.from({length:16},()=>({})),equityMoves:Array.from({length:16},()=>({equity:0})),candidatePolicy:Bag.POLICY,
   searchedCandidateCount:16,generatedCandidateCount:500,totalBagEmptyingCount:352,
   requiredBagEmptyingCount:10,evaluatedBagEmptyingCount:10,totalPlayaheads:16*192};
 assert.equal(Bag.validCoverage(r,5,192),true);
 assert.equal(Bag.validCoverage({...r,evaluatedBagEmptyingCount:9},5,192),false);
 assert.equal(Bag.validCoverage({...r,candidatePolicy:undefined},5,192),false);
});
