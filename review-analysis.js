/* Background review work is account-scoped. Navigation changes priority, not ownership. */
(function(root) {
  'use strict';
  function playedMove(turn) {
    const action = String(turn.action || '').trim();
    if (/^PASS(?:\s+-?\d+)?$/i.test(action)) return { action: 'pass', position: '', tiles: '' };
    if (/^(SWAP|EXCHANGE)(?:\s+(?:--\s+)?-?\d+)?$/i.test(action)) {
      const tiles = [...String(turn.swapped_tiles || '')].map(tile => /[?*_ a-z]/.test(tile) ? '?' : tile).join('').toUpperCase();
      if (!/^[A-Z?]{1,7}$/.test(tiles)) {
        throw Object.assign(new Error('The recorded exchanged tiles are missing.'), { code: 'MISSING_EXCHANGE_TILES' });
      }
      return { action: 'exchange', position: '', tiles };
    }
    const match = action.match(/^([A-Za-z()]+)\s+((?:1[0-5]|[1-9])[A-O]|[A-O](?:1[0-5]|[1-9]))\s+-?\d+$/);
    if (!match) throw new Error('The recorded move could not be read.');
    let existing = false, tiles = '';
    for (const letter of match[1]) {
      if (letter === '(') { if (existing) throw new Error('The recorded move could not be read.'); existing = true; }
      else if (letter === ')') { if (!existing) throw new Error('The recorded move could not be read.'); existing = false; }
      else tiles += existing ? '.' : letter;
    }
    if (existing || !/[A-Za-z]/.test(tiles)) throw new Error('The recorded move could not be read.');
    return { action: 'place', position: match[2], tiles };
  }

  function qualityBand(loss) {
    return loss < 2 ? 'best' : loss < 5 ? 'inaccuracy' : loss < 10 ? 'mistake' : 'blunder';
  }

  // Work with physical tile multisets: an assigned lowercase letter is a blank.
  function rackDraw(turn) {
    const move = playedMove(turn);
    const grid = turn.grid;
    if (!Array.isArray(grid) || grid.length !== 15 ||
        !grid.every(row => typeof row === 'string' && /^[A-Za-z.]{15}$/.test(row)) ||
        !/^[A-Z?]{1,7}$/.test(turn.rack) || !Number.isInteger(turn.bag_count) || turn.bag_count < 0) {
      throw new Error('The recorded rack, board, or bag count is unavailable.');
    }
    const remove = (pool, tiles, message) => {
      for (const tile of tiles) {
        const index = pool.indexOf(tile);
        if (index < 0) throw new Error(message);
        pool.splice(index, 1);
      }
    };
    const counts = typeof module !== 'undefined' && module.exports
      ? require('./luck-analysis.js').TILE_COUNTS : root.CrossplayLuck.TILE_COUNTS;
    const unseen = Object.entries(counts).flatMap(([tile, count]) => Array(count).fill(tile));
    const physical = tile => tile === tile.toLowerCase() ? '?' : tile;
    remove(unseen, [...grid.join('')].filter(tile => tile !== '.').map(physical), 'The board contains too many physical tiles.');
    remove(unseen, turn.rack, 'The rack contains too many physical tiles.');
    const opponentCount = unseen.length - turn.bag_count;
    if (opponentCount < 0 || opponentCount > 7) throw new Error('The board, racks, and bag tiles do not add up.');
    let used = '';
    if (move.action === 'exchange') {
      if (move.tiles.length > turn.bag_count) throw new Error('The bag does not contain enough tiles for this exchange.');
      used = move.tiles;
    } else if (move.action === 'place') {
      const across = move.position.match(/^(1[0-5]|[1-9])([A-O])$/);
      const down = move.position.match(/^([A-O])(1[0-5]|[1-9])$/);
      let row = Number(across ? across[1] : down[2]) - 1;
      let col = 'ABCDEFGHIJKLMNO'.indexOf(across ? across[2] : down[1]);
      for (const tile of move.tiles) {
        if (!grid[row] || col >= 15) throw new Error('The recorded move runs off the board.');
        const existing = grid[row][col];
        if (existing === '.') {
          if (tile === '.') throw new Error('The recorded move refers to a missing tile.');
          used += physical(tile);
        } else if (tile !== '.' && existing.toUpperCase() !== tile.toUpperCase()) {
          throw new Error('The recorded move conflicts with the board.');
        }
        if (across) col++; else row++;
      }
      if (!used) throw new Error('The recorded move places no tiles.');
    }
    const retained = [...turn.rack];
    remove(retained, used, 'The recorded move uses tiles missing from the rack.');
    const drawCount = move.action === 'pass' ? 0 : Math.min(7 - retained.length, turn.bag_count);
    const rackAfter = turn.rack_after ?? (drawCount === 0 ? retained.join('') : null);
    if (typeof rackAfter !== 'string' || !/^[A-Z?]{0,7}$/.test(rackAfter)) throw new Error('The recorded rack after the draw is unavailable.');
    const drawn = [...rackAfter];
    remove(drawn, retained, 'The recorded rack after the draw does not contain the retained tiles.');
    if (drawn.length !== drawCount) throw new Error('The recorded rack has the wrong number of drawn tiles.');
    remove(unseen, drawn, 'The recorded draw contains unavailable physical tiles.');
    return { rackAfter, drawCount, drawn: drawn.join(''), retained: retained.join('') };
  }

  function validDrawResult(result, turn, seed, iterations = 277) {
    let draw, move;
    try { draw = rackDraw(turn); move = playedMove(turn); } catch (_) { return false; }
    const bag = turn.bag_count, end = bag <= 21;
    return Boolean(result && result.simulated === true && !result.error &&
      result.samplingVersion === 'rack-draw-v1' && result.seed === seed &&
      result.rackAfter === draw.rackAfter && result.drawCount === draw.drawCount &&
      result.iterationsRequested === iterations && result.iterationsCompleted === iterations &&
      result.bagCount === bag && result.finalTurnsRemaining === (bag === 0 ? turn.final_turns_remaining : 0) &&
      result.plies === (end ? -1 : 2) && result.playToEnd === end &&
      result.simulationMode === (end ? 'play-to-end' : '2-ply') && result.winModel === (end ? 'terminal' : 'bogowin') &&
      [result.beforeWin, result.afterWin].every(value => Number.isFinite(value) && value >= 0 && value <= 1) &&
      (draw.drawCount > 0 || result.beforeWin === result.afterWin) &&
      placementKey(result.selected, turn.grid) !== null &&
      placementKey(result.selected, turn.grid) === placementKey(move, turn.grid));
  }

  function sweepEvent(turn, candidates = []) {
    const grid = turn?.grid;
    if (!Array.isArray(grid) || grid.length !== 15 ||
        !Array.from(grid).every(row => typeof row === 'string' && /^[A-Za-z.]{15}$/.test(row))) return null;
    const placedTiles = move => {
      if (move?.action !== 'place' || typeof move.tiles !== 'string' || !/^[A-Za-z.]{1,15}$/.test(move.tiles)) return null;
      const across = String(move.position).match(/^(1[0-5]|[1-9])([A-O])$/);
      const down = String(move.position).match(/^([A-O])(1[0-5]|[1-9])$/);
      if (!across && !down) return null;
      let row = Number(across ? across[1] : down[2]) - 1;
      let col = 'ABCDEFGHIJKLMNO'.indexOf(across ? across[2] : down[1]);
      let count = 0;
      for (const tile of move.tiles) {
        if (row >= 15 || col >= 15) return null;
        const existing = grid[row][col];
        if (existing === '.') {
          if (tile === '.') return null;
          count++;
        } else if (tile !== '.' && tile.toUpperCase() !== existing.toUpperCase()) return null;
        if (across) col++; else row++;
      }
      return count || null;
    };
    let count = 0;
    try {
      const played = playedMove(turn);
      if (played.action === 'place') {
        const word = String(turn.action).trim().split(/\s+/)[0];
        if (!/^(?:[A-Za-z]|\([A-Za-z]+\))+$/.test(word)) return null;
        count = placedTiles(played);
      }
    } catch (error) {
      // This code is emitted only for a recognized swap/exchange action.
      if (error.code !== 'MISSING_EXCHANGE_TILES') return null;
    }
    if (count === null || count > 7) return null;
    if (count === 7) return 'played';
    return Array.isArray(candidates) && candidates.some(move => placedTiles(move) === 7) ? 'missed' : null;
  }

  function placementKey(move, grid) {
    if (!move || !['place', 'pass', 'exchange'].includes(move.action)) return null;
    if (move.action === 'pass') return 'pass';
    if (move.action === 'exchange') return 'exchange:' + String(move.tiles || '').split('').sort().join('');
    const across = String(move.position).match(/^(\d{1,2})([A-O])$/);
    const down = String(move.position).match(/^([A-O])(\d{1,2})$/);
    if (!across && !down) return null;
    let row = Number(across ? across[1] : down[2]) - 1;
    let col = 'ABCDEFGHIJKLMNO'.indexOf(across ? across[2] : down[1]);
    const cells = [];
    for (const tile of String(move.tiles || '')) {
      if (!grid[row] || col < 0 || col > 14) return null;
      if (grid[row][col] === '.' && tile !== '.') cells.push(row + ',' + col + ':' + tile);
      if (across) col++; else row++;
    }
    return cells.length ? cells.sort().join('|') : null;
  }

  function playedCandidateIndex(turn, moves) {
    if (!Array.isArray(turn.grid)) return -1;
    let played;
    try { played = playedMove(turn); } catch (_) { return -1; }
    const key = placementKey(played, turn.grid);
    return key === null ? -1 : moves.findIndex(move => placementKey(move, turn.grid) === key);
  }

  function validResult(result, turn, seed, iterations = 277, moveCount = 15) {
    if (!Array.isArray(turn.grid) || turn.grid.length !== 15 || !turn.grid.every(row => typeof row === 'string' && /^[A-Za-z.]{15}$/.test(row))) return false;
    const bag = Number(turn.bag_count), end = bag <= 21;
    let played;
    try { played = playedMove(turn); } catch (_) { return false; }
    if (!result || result.error || !Array.isArray(result.moves) || !result.moves.length || result.moves.length > moveCount + 1 ||
        result.simulated !== true || result.plies !== (end ? -1 : 2) || result.playToEnd !== end ||
        result.simulationMode !== (end ? 'play-to-end' : '2-ply') || result.winModel !== (end ? 'terminal' : 'bogowin') ||
        result.bagCount !== bag || result.finalTurnsRemaining !== (bag === 0 ? Number(turn.final_turns_remaining) : 0) ||
        result.samplingVersion !== 'paired-review-v1' || result.seed !== seed ||
        result.iterationsRequested !== iterations || result.iterationsCompleted !== iterations ||
        result.candidateCount !== result.moves.length || result.totalPlayaheads !== iterations * result.moves.length ||
        !Number.isFinite(result.bestStaticEquity) || !Array.isArray(result.staticMoves) || result.staticMoves.length !== result.moves.length ||
        !result.staticMoves.every(move => move && Number.isFinite(move.equity)) ||
        result.bestStaticEquity !== Math.max(...result.staticMoves.map(move => move.equity))) return false;
    const validMove = move => move && move.samples === iterations && (!end || move.terminalSamples === iterations) &&
      Number.isFinite(move.win) && move.win >= 0 && move.win <= 1 && Number.isFinite(move.staticEquity) && Number.isFinite(move.equity);
    if (!result.moves.every(validMove) || !validMove(result.selected) || !validMove(result.best)) return false;
    for (const move of result.moves) {
      const matches = result.staticMoves.filter(candidate => ['action', 'position', 'tiles'].every(field => candidate[field] === move[field]));
      if (matches.length !== 1 || matches[0].equity !== move.staticEquity) return false;
    }
    const sameMove = (left, right) => ['action', 'position', 'tiles', 'win', 'equity', 'staticEquity', 'samples', 'terminalSamples'].every(field => left[field] === right[field]);
    if (!result.moves.some(move => sameMove(move, result.selected)) || !result.moves.some(move => sameMove(move, result.best))) return false;
    const selectedKey = placementKey(result.selected, turn.grid);
    const bestKey = placementKey(result.best, turn.grid);
    return selectedKey !== null && selectedKey === placementKey(played, turn.grid) && bestKey !== null &&
      result.moves.some(move => placementKey(move, turn.grid) === selectedKey && move.win === result.selected.win) &&
      result.moves.some(move => placementKey(move, turn.grid) === bestKey && move.win === result.best.win) &&
      result.best.win === Math.max(...result.moves.map(move => move.win));
  }

  class ReviewQueue {
    constructor({ load, run, complete = () => {}, change = () => {}, concurrency = 2, preview = false }) {
      Object.assign(this, { load, run, complete, change, concurrency, preview });
      this.contexts = new Map();
      this.loading = new Map();
      this.errors = new Map();
      this.ids = [];
      this.activeId = null;
      this.running = 0;
      this.closed = false;
      this.controller = new AbortController();
    }
    add(games) {
      for (const game of games) if (game.status === 'completed' && !this.ids.includes(game.id)) this.ids.push(game.id);
      this.pump();
    }
    select(id) { this.activeId = id; this.pump(); }
    async get(id) {
      if (this.contexts.has(id)) return this.contexts.get(id);
      if (this.loading.has(id)) return this.loading.get(id);
      const promise = Promise.resolve().then(() => this.load(id)).then(context => {
        if (this.closed) throw Object.assign(new Error('Account changed.'), { name: 'AbortError' });
        context.states ||= context.review.turns.map(() => 'pending');
        if (this.preview) context.states = context.states.map(state => state === 'pending' ? 'pending-preview' : state);
        context.paused = false;
        this.contexts.set(id, context);
        this.errors.delete(id);
        return context;
      }).catch(error => {
        if (!this.closed && !this.controller.signal.aborted) this.errors.set(id, error.message);
        throw error;
      }).finally(() => this.loading.delete(id));
      this.loading.set(id, promise);
      return promise;
    }
    orderedIds() { return [...new Set([this.activeId, ...this.ids, ...this.contexts.keys()].filter(Boolean))]; }
    next() {
      for (const id of this.orderedIds()) {
        if (this.errors.has(id)) continue;
        const context = this.contexts.get(id);
        if (!context) {
          if (!this.loading.has(id)) return () => this.get(id);
          continue;
        }
        if (context.paused) continue;
        const phase = this.preview && context.states.includes('pending-preview') ? 'preview' : 'review';
        const index = context.states.indexOf(phase === 'preview' ? 'pending-preview' : 'pending');
        if (index >= 0) {
          context.states[index] = phase === 'preview' ? 'running-preview' : 'running';
          return async () => {
            let succeeded = false;
            let result;
            try { result = await this.run(context, index, phase); succeeded = true; }
            catch (error) {
              if (!this.closed && !this.controller.signal.aborted) context.error = error.message;
            } finally {
              context.states[index] = succeeded ? (phase === 'preview' && result?.complete !== true ? 'pending' : 'done') : 'error';
              if (!this.closed && context.states.every(state => state === 'done')) await this.complete(context);
            }
          };
        }
        if (!context.states.length && !context.completedEmpty) {
          context.completedEmpty = true;
          return () => this.complete(context);
        }
      }
      return null;
    }
    pump() {
      if (this.closed || this.controller.signal.aborted) return;
      let job;
      while (this.running < this.concurrency && (job = this.next())) {
        this.running++;
        Promise.resolve().then(job).catch(() => {}).finally(() => {
          this.running--;
          if (!this.closed) { this.change(); this.pump(); }
        });
      }
      this.change();
    }
    retry(id) {
      for (const gameId of id ? [id] : this.orderedIds()) {
        this.errors.delete(gameId);
        const context = this.contexts.get(gameId);
        if (context) {
          const retryable = new Set((context.quality?.points || [])
            .filter(point => point.retryable || point.draw?.retryable).map(point => point.turnIndex));
          if (!id && !context.states.some((state, index) => state === 'error' || retryable.has(index))) continue;
          context.paused = false;
          context.error = null;
          context.states = context.states.map((state, index) => {
            if (['running', 'running-preview'].includes(state)) return state;
            // An explicit game ID requests full reanalysis. Background retry
            // keeps successful and permanently unrated turns complete.
            if (!id && state !== 'error' && !retryable.has(index)) return state;
            return this.preview ? 'pending-preview' : 'pending';
          });
        }
      }
      this.pump();
    }
    retryTurn(id, index) {
      const context = this.contexts.get(id);
      if (!context || index < 0 || index >= context.states.length) return;
      context.paused = false;
      context.error = null;
      if (!['running', 'running-preview'].includes(context.states[index])) context.states[index] = this.preview ? 'pending-preview' : 'pending';
      this.pump();
    }
    close() { this.closed = true; this.controller.abort(); }
  }
  const api = { playedMove, playedCandidateIndex, sweepEvent, qualityBand, rackDraw, validDrawResult, validResult, ReviewQueue };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.CrossplayReview = api;
})(typeof window !== 'undefined' ? window : globalThis);
