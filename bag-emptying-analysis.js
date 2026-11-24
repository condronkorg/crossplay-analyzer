/* Combines bounded, paired-sample batches without hiding incomplete coverage. */
(function (root) {
  'use strict';
  const POLICY = 'bag-emptying-v1';
  function create(plan) {
    if (!Array.isArray(plan.moves) || !plan.moves.length || !Array.isArray(plan.bagEmptyingIndices))
      throw new Error('Invalid candidate plan.');
    return {
      plan,
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
  function incorporate(state, batch) {
    if (
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
        !Number.isFinite(move.equity)
      )
        throw new Error('Incomplete candidate batch.');
    });
    batch.moves.forEach((move, i) => {
      const sum = state.sums[batch.start + i];
      sum.samples += batch.completed;
      sum.wins += move.win * batch.completed;
      sum.equity += move.equity * batch.completed;
      sum.terminal += move.terminalSamples;
      sum.statistics = Array.isArray(move.equityStatistics);
      if (sum.statistics) {
        sum.residual += move.residualSum;
        for (const [level, player, total, count] of move.equityStatistics) {
          const key = level + ':' + player;
          const term = sum.terms[key] || (sum.terms[key] = { player, total: 0, count: 0 });
          term.total += total;
          term.count += count;
        }
      }
    });
  }
  function finish(state, iterations) {
    const { plan, sums } = state;
    if (sums.some((sum) => sum.samples !== iterations || sum.terminal !== iterations))
      throw new Error('Candidate search is incomplete.');
    const all = plan.moves.map((move, i) => ({
      ...move,
      staticEquity: move.equity,
      win: sums[i].wins / iterations,
      equity: sums[i].statistics
        ? Object.values(sums[i].terms).reduce(
            (total, term) => total + ((term.player === 0 ? 1 : -1) * term.total) / term.count,
            0,
          ) +
          sums[i].residual / iterations
        : sums[i].equity / iterations,
      samples: iterations,
      terminalSamples: iterations,
    }));
    const ranked = [...all].sort((a, b) => b.win - a.win || b.equity - a.equity);
    const staticBest = all.reduce((a, b) => (a.staticEquity >= b.staticEquity ? a : b));
    const selected = plan.actualIndex >= 0 ? all[plan.actualIndex] : null;
    const retained = [
      ...new Set([...ranked.slice(0, 15), ...(selected ? [selected] : []), staticBest]),
    ];
    const result = {
      moves: retained,
      best: ranked[0],
      staticMoves: retained.map((move) => ({ ...move, equity: move.staticEquity })),
      bestStaticEquity: staticBest.staticEquity,
      simulated: true,
      error: '',
      equityMoves: plan.equityMoves,
      seed: plan.seed,
      samplingVersion: 'paired-review-v1',
      candidatePolicy: POLICY,
      searchedCandidateCount: all.length,
      requiredBagEmptyingCount: plan.bagEmptyingIndices.length,
      evaluatedBagEmptyingCount: plan.bagEmptyingIndices.length,
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
    if (selected) result.selected = selected;
    return { ...plan.evaluation, ...result };
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
  const api = { POLICY, create, incorporate, finish, validCoverage, equityCandidates };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.CrossplayBagEmptying = api;
})(typeof self !== 'undefined' ? self : globalThis);
