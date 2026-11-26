importScripts('quackle.js');
importScripts('bag-emptying-analysis.js', 'luck-analysis.js', 'review-analysis.js', 'opening-draw.js');

const integerBetween = (value, min, max) => Number.isInteger(value) && value >= min && value <= max;
const DEFAULT_REVIEW_SEED = 123456789;
const ENDGAME_POLICY = 'crossplay-endgame-v3';
let modulePromise = null;

async function getModule() {
  if (!modulePromise) {
    modulePromise = (async () => {
      const module = await QuackleModule();
      if (!module.initEngine('/data')) throw new Error('Quackle engine initialization failed');
      return module;
    })();
  }
  return modulePromise;
}

function customMoveKey(payload) {
  let grid;
  try { grid = JSON.parse(payload.gridJson); }
  catch (_) { throw new Error('A valid 15×15 board is required.'); }
  if (!Array.isArray(grid) || grid.length !== 15 ||
      !grid.every(row => typeof row === 'string' && /^[A-Za-z.]{15}$/.test(row))) {
    throw new Error('A valid 15×15 board is required.');
  }
  const rack = String(payload.rack || '');
  if (!/^[A-Z?]{1,7}$/.test(rack)) throw new Error('A rack with 1 to 7 letters or blanks is required.');
  const { bagCount, finalTurnsRemaining } = payload;
  if (!Number.isInteger(bagCount) || bagCount < 0 ||
      !Number.isInteger(finalTurnsRemaining) ||
      (bagCount === 0 ? ![1, 2].includes(finalTurnsRemaining) : finalTurnsRemaining !== 0)) {
    throw new Error('Enter a valid bag count and Crossplay final-turn state first.');
  }
  const unseen = 100 - grid.join('').replace(/\./g, '').length - rack.length;
  if (unseen - bagCount < 0 || unseen - bagCount > 7) {
    throw new Error('The board, rack, and bag count must leave 0 to 7 tiles for the opponent.');
  }
  const move = payload.customMove || {};
  if (move.action === 'exchange') {
    const enteredTiles = String(move.tiles || '').trim();
    if (!/^[A-Za-z?]{1,7}$/.test(enteredTiles)) throw new Error('Enter 1 to 7 letters to swap; use ? for a blank.');
    const tiles = enteredTiles.toUpperCase();
    if (tiles.length > bagCount) throw new Error('The bag must contain at least as many tiles as the swap.');
    const remaining = [...rack];
    for (const tile of tiles) {
      const index = remaining.indexOf(tile);
      if (index < 0) throw new Error('The rack does not contain the tiles to swap.');
      remaining.splice(index, 1);
    }
    const canonical = [...tiles].sort().join('');
    return { grid, key: 'exchange:' + canonical,
      move: { action: 'exchange', position: '', tiles: canonical } };
  }
  if (move.action && move.action !== 'place') throw new Error('Choose Play a word or Swap tiles.');
  const word = String(move.word || '').trim();
  const square = String(move.square || '').trim().toUpperCase().match(/^([A-O])(1[0-5]|[1-9])$/);
  if (!/^[A-Za-z.]{1,15}$/.test(word)) throw new Error('Enter a word using letters, or . for existing tiles.');
  if (!square) throw new Error('Enter a start square from A1 to O15, such as G8.');
  if (!['across', 'down'].includes(move.direction)) throw new Error('Choose Across or Down.');
  let row = Number(square[2]) - 1;
  let col = 'ABCDEFGHIJKLMNO'.indexOf(square[1]);
  const remaining = rack.split('');
  const placed = [];
  for (const letter of word) {
    if (row >= 15 || col >= 15) throw new Error('The move runs off the board.');
    const existing = grid[row][col];
    if (existing !== '.') {
      if (letter !== '.' && letter.toUpperCase() !== existing.toUpperCase()) {
        throw new Error('The word conflicts with an existing tile. Include that letter or use . for it.');
      }
    } else {
      if (letter === '.') throw new Error('Use . only on a square that already has a tile.');
      const tile = letter === letter.toLowerCase() ? '?' : letter;
      const index = remaining.indexOf(tile);
      if (index < 0) throw new Error('The rack does not contain the required tiles. Use lowercase for a blank.');
      remaining.splice(index, 1);
      placed.push(`${row},${col}:${letter}`);
    }
    if (move.direction === 'across') col++;
    else row++;
  }
  if (!placed.length) throw new Error('The move must place at least one new tile.');
  return { grid, key: placed.sort().join('|') };
}

// Match the physical placements, including assigned blanks. This also recognizes
// one-tile plays when Quackle writes them in the perpendicular direction.
function candidateKey(move, grid) {
  if (move.action === 'exchange') return 'exchange:' + [...move.tiles].sort().join('');
  if (move.action !== 'place') return '';
  const across = String(move.position).match(/^(\d{1,2})([A-O])$/);
  const down = String(move.position).match(/^([A-O])(\d{1,2})$/);
  if (!across && !down) return '';
  let row = Number(across ? across[1] : down[2]) - 1;
  let col = 'ABCDEFGHIJKLMNO'.indexOf(across ? across[2] : down[1]);
  const placed = [];
  for (const letter of move.tiles) {
    if (!grid[row] || col >= 15) return '';
    if (grid[row][col] === '.' && letter !== '.') placed.push(`${row},${col}:${letter}`);
    if (across) col++;
    else row++;
  }
  return placed.sort().join('|');
}

function simulate(module, payload, numMoves = payload.numMoves, iterations = payload.iterations) {
  const result = module.simulateKibitz(
    payload.gridJson, payload.rack, payload.playerScore, payload.opponentScore,
    numMoves, iterations, payload.bagCount, payload.finalTurnsRemaining,
  );
  if (!result.error && payload.bagCount <= 21 && result.endgamePolicy !== ENDGAME_POLICY) {
    throw new Error('Quackle returned an unsupported endgame policy.');
  }
  return result;
}

function evaluateCustomMove(module, payload, simulateWin) {
  const { grid, key } = customMoveKey(payload);
  // Validate and rank a custom move against the complete legal list.
  // Recorded game moves use the bounded simulateReview path below.
  const limit = 1000000;
  const generated = module.kibitz(payload.gridJson, payload.rack,
    payload.playerScore, payload.opponentScore, limit);
  if (generated.error) throw new Error(generated.error);
  if (!Array.isArray(generated.moves) || generated.moves.length >= limit) {
    throw new Error('Quackle could not return the complete move list for this position.');
  }
  generated.moves = generated.moves.filter(move => move.action !== 'exchange' ||
    (move.tiles.length > 0 && move.tiles.length <= payload.bagCount));
  const index = generated.moves.findIndex(move => candidateKey(move, grid) === key);
  if (index < 0) {
    throw new Error('This move is not legal in Quackle’s dictionary at this position. Check the word, connecting words, start square, and direction.');
  }
  const selected = generated.moves[index];
  const best = generated.moves.reduce((top, move) => move.equity > top.equity ? move : top, selected);
  const evaluation = { selected, best, staticRank: index + 1, legalMoveCount: generated.moves.length, simulated: false };
  if (!simulateWin) return evaluation;
  if (!Number.isInteger(payload.iterations) || payload.iterations < 1 || payload.iterations > 277) {
    throw new Error('Simulation iterations must be between 1 and 277.');
  }
  // Force the canonical legal move into a bounded comparison, preserving
  // existing letters and assigned blanks without simulating its whole rank.
  const result = simulateRecordedMove(module, { ...payload, numMoves: 15,
    played: selected, seed: DEFAULT_REVIEW_SEED });
  return { ...evaluation, ...result };
}

function recordedMove(payload, requirePosition = false) {
  const move = payload.played || {};
  if (!['place', 'pass', 'exchange'].includes(move.action) || typeof move.tiles !== 'string' ||
      ((requirePosition || move.position !== undefined) && typeof move.position !== 'string')) {
    throw new Error('The recorded move must specify its action, position, and tiles.');
  }
  return { action: move.action, position: move.position || '', tiles: move.tiles };
}

function simulateRecordedMove(module, payload) {
  if (!integerBetween(payload.seed, 0, 0xffffffff)) throw new Error('Review seed must be an unsigned 32-bit integer.');
  if (!integerBetween(payload.numMoves, 1, 15)) throw new Error('Review candidate count must be between 1 and 15.');
  if (!integerBetween(payload.iterations, 1, 277)) throw new Error('Simulation iterations must be between 1 and 277.');
  if (!integerBetween(payload.bagCount, 0, 100) || !integerBetween(payload.finalTurnsRemaining, 0, 2) ||
      !Number.isInteger(payload.playerScore) || !Number.isInteger(payload.opponentScore)) {
    throw new Error('Review scores, bag count, and final-turn state must be integers.');
  }
  const result = module.simulateReviewMove(
    payload.gridJson, payload.rack, payload.playerScore, payload.opponentScore,
    payload.numMoves, payload.iterations, payload.bagCount, payload.finalTurnsRemaining,
    recordedMove(payload), payload.seed,
  );
  if (result.error) throw new Error(result.error);
  const playToEnd = payload.bagCount <= 21;
  const moveKey = move => `${move.action}:${move.position}:${move.tiles}`;
  const validMove = move => move && move.samples === payload.iterations &&
    (!playToEnd || move.terminalSamples === payload.iterations) &&
    Number.isFinite(move.equity) && Number.isFinite(move.staticEquity) &&
    Number.isFinite(move.win) && move.win >= 0 && move.win <= 1;
  if (result.simulated !== true || result.seed !== payload.seed || result.samplingVersion !== 'paired-review-v1' ||
      (playToEnd && result.endgamePolicy !== ENDGAME_POLICY) ||
      result.iterationsRequested !== payload.iterations || result.iterationsCompleted !== payload.iterations ||
      result.bagCount !== payload.bagCount || result.finalTurnsRemaining !== payload.finalTurnsRemaining ||
      result.plies !== (playToEnd ? -1 : 2) || result.playToEnd !== playToEnd ||
      result.simulationMode !== (playToEnd ? 'play-to-end' : '2-ply') || result.winModel !== (playToEnd ? 'terminal' : 'bogowin') ||
      !Array.isArray(result.moves) || !result.moves.length || result.moves.length > payload.numMoves + 1 ||
      result.candidateCount !== result.moves.length || result.totalPlayaheads !== payload.iterations * result.moves.length ||
      !result.moves.every(validMove) || !validMove(result.selected) || !validMove(result.best) ||
      !result.moves.some(move => moveKey(move) === moveKey(result.selected)) ||
      !result.moves.some(move => moveKey(move) === moveKey(result.best)) ||
      result.best.win !== Math.max(...result.moves.map(move => move.win)) ||
      !Array.isArray(result.staticMoves) || result.staticMoves.length !== result.moves.length ||
      !result.staticMoves.every(move => Number.isFinite(move.equity) && result.moves.some(simulated =>
        moveKey(move) === moveKey(simulated) && move.equity === simulated.staticEquity)) ||
      result.bestStaticEquity !== Math.max(...result.staticMoves.map(move => move.equity))) {
    throw new Error('Quackle returned incomplete recorded-move simulation results.');
  }
  return result;
}

function validTerminalPosition(payload) {
  return integerBetween(payload.bagCount, 0, 21) &&
    (payload.bagCount === 0 ? [1, 2].includes(payload.finalTurnsRemaining) : payload.finalTurnsRemaining === 0) &&
    Number.isInteger(payload.playerScore) && Number.isInteger(payload.opponentScore);
}

function planTerminalReview(module, payload, expanded) {
  const {requestType, bagCount, finalTurnsRemaining} = payload;
  const seed = requestType === 'simulateMove' ? DEFAULT_REVIEW_SEED :
    requestType === 'simulateKibitz' ? payload.seed ?? DEFAULT_REVIEW_SEED : payload.seed;
  if (!validTerminalPosition(payload) || !integerBetween(seed, 0, 0xffffffff) ||
      !integerBetween(payload.iterations, 1, 277) ||
      (expanded && !(integerBetween(bagCount, 1, 7) && [192, 277].includes(payload.iterations)))) {
    throw new Error('Candidate search requires valid scores, seed, samples, and bag state.');
  }
  let played = null, evaluation;
  if (requestType === 'simulateMove') {
    evaluation = evaluateCustomMove(module, payload, false);
    played = evaluation.selected;
  } else if (requestType === 'simulateReview') {
    played = recordedMove(payload);
  } else if (requestType !== 'simulateKibitz') {
    throw new Error('Invalid candidate search request.');
  }
  const numMoves = evaluation ? 15 : payload.numMoves;
  if (!expanded && !integerBetween(numMoves, 1, 15))
    throw new Error('Review candidate count must be between 1 and 15.');
  const args = [payload.gridJson, payload.rack, payload.playerScore, payload.opponentScore];
  const result = expanded ? module.planBagEmptying(...args, played, seed) :
    module.planReviewSimulation(...args, numMoves, bagCount, finalTurnsRemaining, played, seed);
  if (result.error) throw new Error(result.error);
  if (result.seed !== seed || result.bagCount !== bagCount || result.finalTurnsRemaining !== finalTurnsRemaining ||
      !Array.isArray(result.moves) || !Array.isArray(result.tieOrder) ||
      (played ? !integerBetween(result.actualIndex, 0, result.moves.length - 1) : result.actualIndex !== -1) ||
      (!expanded && (result.moves.length > numMoves + Number(Boolean(played)) || result.bagEmptyingIndices?.length !== 0))) {
    throw new Error('Quackle returned an invalid candidate plan.');
  }
  if (evaluation) result.evaluation = evaluation;
  return result;
}

function validBatchBounds(payload) {
  return integerBetween(payload.seed, 0, 0xffffffff) && integerBetween(payload.count, 1, 16) &&
    integerBetween(payload.offset, 0, 277 - payload.count);
}

function simulateTerminalReviewBatch(module, payload, legacy) {
  if (!validBatchBounds(payload) || !Array.isArray(payload.candidates) ||
      !integerBetween(payload.candidates.length, 1, 8) || (!legacy && !validTerminalPosition(payload))) {
    throw new Error('Invalid candidate batch bounds.');
  }
  const args = [payload.gridJson, payload.rack, payload.playerScore, payload.opponentScore, payload.bagCount];
  const batchArgs = [payload.candidates, payload.seed, payload.offset, payload.count];
  const result = legacy ? module.simulateCandidateBatch(...args, ...batchArgs) :
    module.simulateReviewBatch(...args, payload.finalTurnsRemaining, ...batchArgs);
  if (result.error) throw new Error(result.error);
  if (!legacy && (result.offset !== payload.offset || !integerBetween(result.completed, 1, payload.count) ||
      !Array.isArray(result.moves) || result.moves.length !== payload.candidates.length ||
      !result.moves.every(move => move.samples === result.completed && move.terminalSamples === result.completed &&
        Array.isArray(move.equityStatistics) && Number.isFinite(move.residualSum) &&
        self.CrossplayBagEmptying.validWinSum(move.winSum, move.win, result.completed)))) {
    throw new Error('Quackle returned incomplete candidate batch results.');
  }
  return result;
}

function simulateRackDraw(module, payload, batched = false) {
  if (!integerBetween(payload.seed, 0, 0xffffffff) || !integerBetween(payload.iterations, 1, 277) ||
      !Number.isInteger(payload.playerScore) || !Number.isInteger(payload.opponentScore) ||
      (payload.bagCount === 0 ? ![1, 2].includes(payload.finalTurnsRemaining) : payload.finalTurnsRemaining !== 0)) {
    throw new Error('Rack draw simulation requires valid scores, seed, samples, and final-turn state.');
  }
  const move = recordedMove(payload, true);
  const args = [payload.gridJson, payload.rack, payload.playerScore, payload.opponentScore];
  const drawArgs = [payload.bagCount, payload.finalTurnsRemaining, move, payload.seed, payload.rackAfter];
  let result;
  if (batched) {
    const {offset, count, knownBeforeWin} = payload;
    if (!validTerminalPosition(payload) || !validBatchBounds(payload) || offset + count > payload.iterations ||
        (knownBeforeWin !== -1 && !(Number.isFinite(knownBeforeWin) && knownBeforeWin >= 0 && knownBeforeWin <= 1))) {
      throw new Error('Invalid rack draw batch bounds.');
    }
    result = module.simulateDrawBatch(...args, ...drawArgs, offset, count, knownBeforeWin);
    if (result.error) throw new Error(result.error);
    if (!self.CrossplayBagEmptying.validDrawBatch(result, offset, count, knownBeforeWin))
      throw new Error('Quackle returned incomplete rack draw batch results.');
  } else {
    result = module.simulateReviewDraw(...args, 1, payload.iterations, ...drawArgs);
    if (result.error) throw new Error(result.error);
  }
  const turn = self.CrossplayBagEmptying.drawTurn(payload, move);
  const completed = batched ? result.completed : payload.iterations;
  const validated = batched ? {...result, iterationsRequested: completed} : result;
  if (!CrossplayReview.validDrawResult(validated, turn, payload.seed, completed))
    throw new Error('Quackle returned incomplete rack draw results.');
  return result;
}

function simulateOpeningDeal(module, payload) {
  if (!Number.isInteger(payload.seed) || payload.seed < 0 || payload.seed > 0xffffffff ||
      ![64, 277].includes(payload.iterations) ||
      typeof payload.firstRack !== 'string' || !/^[A-Z?]{7}$/.test(payload.firstRack) ||
      typeof payload.secondRack !== 'string' || !/^[A-Z?]{7}$/.test(payload.secondRack)) {
    throw new Error('Opening simulation requires two seven-tile racks, a seed, and samples.');
  }
  const counts = [9, 2, 2, 4, 12, 2, 3, 3, 8, 1, 1, 4, 2, 5, 8, 2, 1, 6, 5, 6, 3, 2, 2, 1, 2, 1, 3];
  for (const tile of payload.firstRack + payload.secondRack) {
    if (--counts[tile === '?' ? 26 : tile.charCodeAt(0) - 65] < 0) {
      throw new Error('The opening racks exceed the Crossplay physical tile distribution.');
    }
  }
  const input = {firstRack: [...payload.firstRack].sort().join(''),
    secondRack: [...payload.secondRack].sort().join('')};
  const result = module.simulateInitialDeal(payload.firstRack, payload.secondRack, payload.iterations, payload.seed);
  if (result.error) throw new Error(result.error);
  // The worker accepts an explicit seed. The page uses the shared fixed seed.
  if (result.seed !== payload.seed || !CrossplayOpening.validResult(result, input, payload.iterations, payload.seed)) {
    throw new Error('Quackle returned incomplete opening simulation results.');
  }
  return result;
}

self.addEventListener('message', async event => {
  const { id, type, payload = {} } = event.data || {};
  try {
    const module = await getModule();
    let result;

    if (type === 'planBagEmptying' || type === 'planReviewSimulation') {
      result = planTerminalReview(module, payload, type === 'planBagEmptying');
    } else if (type === 'simulateCandidateBatch' || type === 'simulateReviewBatch') {
      result = simulateTerminalReviewBatch(module, payload, type === 'simulateCandidateBatch');
    } else if (type === 'init') {
      result = { info: module.getEngineInfo() };
    } else if (type === 'evaluateMove' || type === 'simulateMove') {
      result = evaluateCustomMove(module, payload, type === 'simulateMove');
    } else if (type === 'simulateReview') {
      result = simulateRecordedMove(module, payload);
    } else if (type === 'simulateDraw' || type === 'simulateDrawBatch') {
      result = simulateRackDraw(module, payload, type === 'simulateDrawBatch');
    } else if (type === 'simulateOpening') {
      result = simulateOpeningDeal(module, payload);
    } else if (type === 'kibitz') {
      result = module.kibitz(
        payload.gridJson,
        payload.rack,
        payload.playerScore,
        payload.opponentScore,
        payload.numMoves,
      );
    } else if (type === 'simulateKibitz') {
      if (!Number.isInteger(payload.bagCount) || !Number.isInteger(payload.finalTurnsRemaining)) {
        throw new Error('Bag count and final-turn state must be integers');
      }
      result = simulate(module, payload);
    } else {
      throw new Error(`Unknown Quackle worker request: ${type}`);
    }

    self.postMessage({ id, result });
  } catch (error) {
    self.postMessage({
      id,
      error: error instanceof Error ? error.message : String(error),
      stack: error instanceof Error ? error.stack : '',
    });
  }
});
