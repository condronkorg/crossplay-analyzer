/* Opening rack estimates use the first mover's view, before any recorded move. */
(function(root) {
  'use strict';
  const VERSION = 'quackle-opening-draw-v1';
  const POLICY = 'static-best-v1';
  const SAMPLING = 'initial-deal-v1';
  const TILE_COUNTS = {
    A: 9, B: 2, C: 2, D: 4, E: 12, F: 2, G: 3, H: 3, I: 8,
    J: 1, K: 1, L: 4, M: 2, N: 5, O: 8, P: 2, Q: 1, R: 6,
    S: 5, T: 6, U: 3, V: 2, W: 2, X: 1, Y: 2, Z: 1, '?': 3
  };
  const unavailable = reason => ({ unavailable: true, reason });
  const canonical = rack => [...rack].sort().join('');
  const validSamples = samples => samples === 64 || samples === 277;

  function validRacks(first, second) {
    if (typeof first !== 'string' || typeof second !== 'string' ||
        !/^[A-Z?]{7}$/.test(first) || !/^[A-Z?]{7}$/.test(second)) return false;
    const remaining = { ...TILE_COUNTS };
    for (const tile of first + second) if (--remaining[tile] < 0) return false;
    return true;
  }

  function validInput(input) {
    return !!input && !input.unavailable && validRacks(input.firstRack, input.secondRack);
  }

  function describe(review) {
    const turns = review?.turns;
    if (!Array.isArray(turns) || !turns.length || !turns[0] || typeof turns[0].mine !== 'boolean') {
      return unavailable('The opening turn is unavailable.');
    }
    const first = turns[0];
    if (!Array.isArray(first.grid) || first.grid.length !== 15 ||
        !Array.from(first.grid).every(row => row === '.'.repeat(15)) ||
        first.mover_score !== 0 || first.other_score !== 0) {
      return unavailable('The opening board or scores are unavailable.');
    }
    // A missing first rack must never fall through to a later refill.
    const second = turns.find(turn => turn && turn.mine === !first.mine);
    if (!second || !validRacks(first.rack, second.rack)) {
      return unavailable('Both complete physical opening racks are required.');
    }
    return { available: true, firstMine: first.mine,
      firstRack: canonical(first.rack), secondRack: canonical(second.rack) };
  }

  function key(input, iterations) {
    if (!validInput(input) || !validSamples(iterations)) return null;
    return JSON.stringify([VERSION, canonical(input.firstRack), canonical(input.secondRack),
      iterations, 2, POLICY, SAMPLING]);
  }

  function seed() {
    let value = 2166136261;
    for (const char of JSON.stringify([VERSION, 2, POLICY, SAMPLING])) {
      value = Math.imul(value ^ char.charCodeAt(0), 16777619) >>> 0;
    }
    return value;
  }

  function validResult(result, input, iterations, expectedSeed = seed()) {
    if (!validInput(input) || !validSamples(iterations) || !result || result.error ||
        !Number.isInteger(expectedSeed) || expectedSeed < 0 || expectedSeed >= 2 ** 32 ||
        result.simulated !== true || result.samplingVersion !== SAMPLING || result.policy !== POLICY ||
        result.firstRack !== canonical(input.firstRack) || result.secondRack !== canonical(input.secondRack) ||
        result.seed !== expectedSeed || result.iterationsRequested !== iterations || result.iterationsCompleted !== iterations ||
        result.plies !== 2 || result.playToEnd !== false || result.simulationMode !== '2-ply' || result.winModel !== 'bogowin') return false;
    if (!['baselineWin', 'firstOnlyWin', 'secondOnlyWin', 'afterWin'].every(field =>
      Number.isFinite(result[field]) && result[field] >= 0 && result[field] <= 1) ||
      !Number.isFinite(result.firstWpa) || !Number.isFinite(result.secondWpa)) return false;
    const { baselineWin: B, firstOnlyWin: A, secondOnlyWin: C, afterWin: J } = result;
    return Math.abs(result.firstWpa - 100 * ((A - B) + (J - C)) / 2) <= 1e-8 &&
      Math.abs(result.secondWpa + 100 * ((C - B) + (J - A)) / 2) <= 1e-8;
  }

  function point(result, input) {
    if (input?.unavailable) return unavailable(input.reason || 'The opening racks are unavailable.');
    if (typeof input?.firstMine !== 'boolean' || !validResult(result, input, result?.iterationsRequested)) {
      return unavailable('The opening estimate is unavailable.');
    }
    return { firstMine: input.firstMine, firstRack: result.firstRack, secondRack: result.secondRack,
      beforeWin: result.baselineWin, firstOnlyWin: result.firstOnlyWin, secondOnlyWin: result.secondOnlyWin,
      afterWin: result.afterWin, firstWpa: result.firstWpa, secondWpa: result.secondWpa,
      samples: result.iterationsCompleted, provisional: result.iterationsCompleted === 64 };
  }

  const api = { describe, key, seed, validResult, point };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.CrossplayOpening = api;
})(globalThis);
