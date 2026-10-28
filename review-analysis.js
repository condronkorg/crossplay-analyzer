/* Background review work is account-scoped. Navigation changes priority, not ownership. */
(function(root) {
  'use strict';
  function playedMove(turn) {
    const action = String(turn.action || '').trim();
    if (/^PASS(?:\s+0)?$/i.test(action)) return { action: 'pass', position: '', tiles: '' };
    if (/^(SWAP|EXCHANGE)(?:\s+0)?$/i.test(action)) {
      const tiles = [...String(turn.swapped_tiles || '')].map(tile => /[?*_ a-z]/.test(tile) ? '?' : tile).join('').toUpperCase();
      if (!/^[A-Z?]{1,7}$/.test(tiles)) throw new Error('The recorded exchanged tiles are missing.');
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
    constructor({ load, run, complete = () => {}, change = () => {}, concurrency = 2 }) {
      Object.assign(this, { load, run, complete, change, concurrency });
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
        const index = context.states.indexOf('pending');
        if (index >= 0) {
          context.states[index] = 'running';
          return async () => {
            let succeeded = false;
            try { await this.run(context, index); succeeded = true; }
            catch (error) {
              if (!this.closed && !this.controller.signal.aborted) context.error = error.message;
            } finally {
              context.states[index] = succeeded ? 'done' : 'error';
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
          context.paused = false;
          context.error = null;
          context.states = context.states.map(state => state === 'running' ? state : 'pending');
        }
      }
      this.pump();
    }
    retryTurn(id, index) {
      const context = this.contexts.get(id);
      if (!context || index < 0 || index >= context.states.length) return;
      context.paused = false;
      context.error = null;
      if (context.states[index] !== 'running') context.states[index] = 'pending';
      this.pump();
    }
    close() { this.closed = true; this.controller.abort(); }
  }
  const api = { playedMove, qualityBand, validResult, ReviewQueue };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.CrossplayReview = api;
})(typeof window !== 'undefined' ? window : globalThis);
