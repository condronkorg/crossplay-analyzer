/* Combines bounded, paired-sample batches without hiding incomplete coverage. */
(function (root) {
  'use strict';
  const POLICY = 'bag-emptying-v1';
  function create(plan) {
    if (!Array.isArray(plan.moves) || !plan.moves.length || !Array.isArray(plan.bagEmptyingIndices) ||
        !Number.isInteger(plan.actualIndex) || plan.actualIndex < -1 || plan.actualIndex >= plan.moves.length)
      throw new Error('Invalid candidate plan.');
    return {
      plan,
      tieRanks: candidateTieRanks(plan),
      sums: plan.moves.map(() => ({
        samples: 0,
        wins: 0,
        equity: 0,
        terminal: 0,
        statistics: null,
        terms: {},
        residual: 0,
      })),
    };
  }
  function candidateTieRanks(plan) {
    if (plan.tieOrder === undefined) return plan.moves.map((_, index) => index);
    if (!Array.isArray(plan.tieOrder) || plan.tieOrder.length !== plan.moves.length ||
        new Set(plan.tieOrder).size !== plan.moves.length ||
        !plan.tieOrder.every(index => Number.isInteger(index) && index >= 0 && index < plan.moves.length)) {
      throw new Error('Invalid candidate tie order.');
    }
    const ranks = [];
    plan.tieOrder.forEach((index, rank) => { ranks[index] = rank; });
    return ranks;
  }
  function incorporate(state, batch) {
    if (
      !Number.isInteger(batch.start) ||
      !Number.isInteger(batch.offset) ||
      !Number.isInteger(batch.completed) ||
      batch.completed < 1 ||
      !Array.isArray(batch.moves) ||
      !batch.moves.length ||
      batch.error
    )
      throw new Error('Invalid candidate batch.');
    batch.moves.forEach((move, i) => {
      const sum = state.sums[batch.start + i];
      const statistics = Array.isArray(move.equityStatistics);
      if (sum && sum.statistics !== null && sum.statistics !== statistics)
        throw new Error('Missing equity statistics.');
      if (
        statistics &&
        (!Number.isFinite(move.residualSum) ||
          move.equityStatistics.some(
            (entry) =>
              !Array.isArray(entry) ||
              entry.length !== 4 ||
              !Number.isInteger(entry[0]) ||
              entry[0] < 0 ||
              !Number.isInteger(entry[1]) ||
              entry[1] < 0 ||
              entry[1] > 1 ||
              !Number.isFinite(entry[2]) ||
              !Number.isInteger(entry[3]) ||
              entry[3] < 1 ||
              entry[3] > batch.completed,
          ))
      ) {
        throw new Error('Invalid equity statistics.');
      }
      if (!sum || sum.samples !== batch.offset) throw new Error('Invalid candidate sample offset.');
      if (
        move.samples !== batch.completed ||
        move.terminalSamples !== batch.completed ||
        !Number.isFinite(move.win) ||
        move.win < 0 ||
        move.win > 1 ||
        (move.winSum !== undefined && !validWinSum(move.winSum, move.win, batch.completed)) ||
        !Number.isFinite(move.equity)
      )
        throw new Error('Incomplete candidate batch.');
    });
    batch.moves.forEach((move, i) => {
      const sum = state.sums[batch.start + i];
      sum.samples += batch.completed;
      sum.wins += move.winSum ?? move.win * batch.completed;
      sum.equity += move.equity * batch.completed;
      sum.terminal += move.terminalSamples;
      sum.statistics = Array.isArray(move.equityStatistics);
      if (sum.statistics) {
        sum.residual += move.residualSum;
        for (const [level, player, total, count] of move.equityStatistics) {
          const key = level + ':' + player;
          const term = sum.terms[key] || (sum.terms[key] = { level, player, total: 0, count: 0 });
          term.total += total;
          term.count += count;
        }
      }
    });
  }
  function validWinSum(sum, win, completed) {
    return Number.isFinite(sum) && sum >= 0 && sum <= completed &&
      Number.isFinite(win) && win >= 0 && win <= 1 && Math.abs(sum / completed - win) <= 1e-12;
  }
  function finish(state, iterations, expanded = true) {
    const { plan, sums } = state;
    if (sums.some((sum) => sum.samples !== iterations || sum.terminal !== iterations))
      throw new Error('Candidate search is incomplete.');
    const all = plan.moves.map((move, i) => ({
      ...move,
      staticEquity: move.equity,
      win: sums[i].wins / iterations,
      equity: sums[i].statistics
        ? Object.values(sums[i].terms).sort((a, b) => a.level - b.level || a.player - b.player).reduce(
            (total, term) => total + ((term.player === 0 ? 1 : -1) * term.total) / term.count,
            0,
          ) +
          sums[i].residual / iterations
        : sums[i].equity / iterations,
      samples: iterations,
      terminalSamples: iterations,
    }));
    const ranked = all.map((_, index) => index)
      .sort((a, b) => all[b].win - all[a].win || all[b].equity - all[a].equity ||
        state.tieRanks[a] - state.tieRanks[b])
      .map(index => all[index]);
    const staticBest = all.reduce((a, b) => (a.staticEquity >= b.staticEquity ? a : b));
    const selected = plan.actualIndex >= 0 ? all[plan.actualIndex] : null;
    const retained = expanded ? [
      ...new Set([...ranked.slice(0, 15), ...(selected ? [selected] : []), staticBest]),
    ] : ranked;
    const result = {
      moves: retained,
      best: ranked[0],
      staticMoves: expanded ? retained.map((move) => ({ ...move, equity: move.staticEquity })) : plan.moves,
      bestStaticEquity: staticBest.staticEquity,
      simulated: true,
      error: '',
      seed: plan.seed,
      samplingVersion: 'paired-review-v1',
      candidateCount: retained.length,
      totalPlayaheads: iterations * all.length,
      iterationsRequested: iterations,
      iterationsCompleted: iterations,
      bagCount: plan.bagCount,
      finalTurnsRemaining: plan.finalTurnsRemaining,
      plies: -1,
      playToEnd: true,
      simulationMode: 'play-to-end',
      winModel: 'terminal',
      endgamePolicy: 'crossplay-endgame-v1',
    };
    if (expanded) Object.assign(result, {
      equityMoves: plan.equityMoves,
      candidatePolicy: POLICY,
      searchedCandidateCount: all.length,
      requiredBagEmptyingCount: plan.bagEmptyingIndices.length,
      evaluatedBagEmptyingCount: plan.bagEmptyingIndices.length,
    });
    if (selected) result.selected = selected;
    return { ...plan.evaluation, ...result };
  }
  function drawTurn(payload, move) {
    const turn = {grid: JSON.parse(payload.gridJson), rack: payload.rack, rack_after: payload.rackAfter,
      bag_count: payload.bagCount, final_turns_remaining: payload.finalTurnsRemaining,
      action: move.action === 'pass' ? 'PASS 0' : move.action === 'exchange' ? 'SWAP 0' : '',
      swapped_tiles: move.action === 'exchange' ? move.tiles : ''};
    // Reconstruct the import notation needed by the physical-placement validator.
    if (move.action !== 'place') return turn;
    const across = move.position.match(/^(\d{1,2})([A-O])$/);
    const down = move.position.match(/^([A-O])(\d{1,2})$/);
    if (!across && !down) throw new Error('Invalid recorded move position.');
    let row = Number(across ? across[1] : down[2]) - 1;
    let col = 'ABCDEFGHIJKLMNO'.indexOf(across ? across[2] : down[1]);
    let word = '';
    for (const tile of move.tiles) {
      if (!turn.grid[row] || col < 0 || col > 14) throw new Error('Invalid recorded move position.');
      word += tile === '.' ? '(' + turn.grid[row][col] + ')' : tile;
      if (across) col++; else row++;
    }
    turn.action = word + ' ' + move.position + ' 0';
    return turn;
  }
  function validDrawBatch(batch, offset, count, knownBeforeWin) {
    return Boolean(batch && !batch.error && batch.offset === offset && batch.iterationsRequested === count &&
      Number.isInteger(batch.completed) && batch.completed > 0 && batch.completed <= count &&
      batch.iterationsCompleted === batch.completed &&
      validWinSum(batch.beforeWinSum, batch.beforeWin, batch.completed) &&
      validWinSum(batch.afterWinSum, batch.afterWin, batch.completed) &&
      (knownBeforeWin === -1 || batch.beforeWin === knownBeforeWin));
  }
  function validCoverage(result, bag, iterations) {
    const required = bag >= 1 && bag <= 7 && iterations === 277;
    if (!required) return result.candidatePolicy === undefined;
    return (
      result.candidatePolicy === POLICY &&
      Array.isArray(result.equityMoves) &&
      result.equityMoves.length >= 1 &&
      result.equityMoves.length <= 16 &&
      result.equityMoves.every((move) => Number.isFinite(move.equity)) &&
      Number.isInteger(result.searchedCandidateCount) &&
      result.searchedCandidateCount >= result.moves?.length &&
      result.searchedCandidateCount <= 1000000 &&
      Number.isInteger(result.requiredBagEmptyingCount) &&
      result.requiredBagEmptyingCount >= 0 &&
      result.requiredBagEmptyingCount <= result.searchedCandidateCount &&
      result.evaluatedBagEmptyingCount === result.requiredBagEmptyingCount &&
      result.totalPlayaheads === iterations * result.searchedCandidateCount
    );
  }
  function equityCandidates(result) {
    return result.equityMoves || result.staticMoves;
  }
  const api = { POLICY, create, incorporate, finish, validWinSum, drawTurn, validDrawBatch, validCoverage, equityCandidates };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.CrossplayBagEmptying = api;
})(typeof self !== 'undefined' ? self : globalThis);
