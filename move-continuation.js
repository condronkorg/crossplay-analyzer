/* A local hypothetical line. Recorded review positions are never mutated. */
(function(root) {
  'use strict';
  const counts = typeof module !== 'undefined' && module.exports
    ? require('./luck-analysis.js').TILE_COUNTS : root.CrossplayLuck.TILE_COUNTS;

  function removeTiles(pool, tiles) {
    for (const tile of tiles) {
      const index = pool.indexOf(tile);
      if (index < 0) throw new Error('The racks and board contain more ' + tile + ' tiles than the Crossplay set.');
      pool.splice(index, 1);
    }
  }

  function shuffle(tiles, seed) {
    for (let i = tiles.length - 1; i > 0; i--) {
      seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
      const j = Math.floor(seed / 4294967296 * (i + 1));
      [tiles[i], tiles[j]] = [tiles[j], tiles[i]];
    }
    return seed;
  }

  function start(turn, opponentRack, seed) {
    if (!Array.isArray(turn.grid) || turn.grid.length !== 15 ||
        !turn.grid.every(row => typeof row === 'string' && /^[A-Za-z.]{15}$/.test(row)) ||
        !/^[A-Z?]{1,7}$/.test(turn.rack) || !Number.isInteger(turn.bag_count) || turn.bag_count < 0 ||
        (turn.bag_count === 0 && ![1, 2].includes(turn.final_turns_remaining))) {
      throw new Error('This turn is missing a valid board, rack, bag count, or final-turn state.');
    }
    const pool = Object.entries(counts).flatMap(([tile, count]) => Array(count).fill(tile));
    removeTiles(pool, [...turn.grid.join('')].filter(tile => tile !== '.')
      .map(tile => tile === tile.toLowerCase() ? '?' : tile));
    removeTiles(pool, turn.rack);
    const opponentCount = pool.length - turn.bag_count;
    if (opponentCount < 0 || opponentCount > 7) throw new Error('The tile counts do not add up for this turn.');
    if (opponentRack !== null && opponentRack !== undefined) {
      if (!/^[A-Z?]{0,7}$/.test(opponentRack) || opponentRack.length !== opponentCount) {
        throw new Error('The recorded opponent rack does not match the remaining tile count.');
      }
      removeTiles(pool, opponentRack);
    }
    seed = shuffle(pool, seed);
    const sampledOpponent = opponentRack === null || opponentRack === undefined;
    if (sampledOpponent) opponentRack = pool.splice(0, opponentCount).join('');
    return {
      grid: turn.grid.slice(), racks: [turn.rack, opponentRack], bag: pool,
      scores: [Number(turn.mover_score) || 0, Number(turn.other_score) || 0],
      actor: 0, finalTurns: turn.bag_count === 0 ? turn.final_turns_remaining : null,
      finished: false, action: '', seed, sampledOpponent
    };
  }

  function play(position, move) {
    if (position.finished) throw new Error('This line has reached the finish.');
    const next = { ...position, grid: position.grid.slice(), racks: position.racks.slice(),
      scores: position.scores.slice(), bag: position.bag.slice() };
    delete next.moves;
    delete next.analysis;
    const actor = position.actor;
    const rack = [...position.racks[actor]];
    let used = '', action = '';
    if (move.action === 'place') {
      const across = String(move.position).match(/^(1[0-5]|[1-9])([A-O])$/);
      const down = String(move.position).match(/^([A-O])(1[0-5]|[1-9])$/);
      if ((!across && !down) || !/^[A-Za-z.]{1,15}$/.test(move.tiles)) throw new Error('Invalid move position or tiles.');
      let row = Number(across ? across[1] : down[2]) - 1;
      let col = 'ABCDEFGHIJKLMNO'.indexOf(across ? across[2] : down[1]);
      let word = '';
      for (const tile of move.tiles) {
        if (row >= 15 || col >= 15) throw new Error('The move runs off the board.');
        const existing = next.grid[row][col];
        if (existing !== '.') {
          if (tile !== '.' && tile.toUpperCase() !== existing.toUpperCase()) throw new Error('The move conflicts with an existing tile.');
          word += '(' + existing + ')';
        } else {
          if (tile === '.') throw new Error('The move refers to a missing board tile.');
          used += tile === tile.toLowerCase() ? '?' : tile;
          next.grid[row] = next.grid[row].slice(0, col) + tile + next.grid[row].slice(col + 1);
          word += tile;
        }
        if (across) col++; else row++;
      }
      if (!used) throw new Error('The move must place a tile.');
      action = word + ' ' + move.position + ' ' + move.score;
    } else if (move.action === 'exchange') {
      used = String(move.tiles || '');
      if (next.bag.length < 7 || !/^[A-Z?]{1,7}$/.test(used)) throw new Error('Exchanging requires at least seven tiles in the bag.');
      action = 'EXCHANGE ' + used;
    } else if (move.action === 'pass') {
      action = 'PASS';
    } else throw new Error('Unknown move action.');
    if (!Number.isFinite(move.score) || (move.action !== 'place' && move.score !== 0)) throw new Error('Invalid move score.');
    removeTiles(rack, used);
    const drawn = next.bag.splice(0, move.action === 'pass' ? 0 : Math.min(7 - rack.length, next.bag.length));
    next.racks[actor] = rack.join('') + drawn.join('');
    if (move.action === 'exchange') {
      next.bag.push(...used);
      next.seed = shuffle(next.bag, next.seed);
    }
    next.scores[actor] += move.score;
    if (position.bag.length === 0) next.finalTurns--;
    else if (next.bag.length === 0) next.finalTurns = 2;
    next.finished = next.finalTurns === 0;
    next.actor = 1 - actor;
    next.action = action;
    return next;
  }

  async function analyze(position, request, options = {}) {
    const pass = { action: 'pass', position: '', tiles: '', score: 0, leave: '' };
    const actor = position.actor;
    if (!position.racks[actor]) {
      // An empty rack still consumes a Crossplay final turn. Evaluate the
      // forced pass from the next player's perspective, or the final score.
      const next = play(position, pass);
      const win = next.finished
        ? position.scores[actor] === position.scores[1 - actor] ? .5
          : Number(position.scores[actor] > position.scores[1 - actor])
        : 1 - (await analyze(next, request, options)).moves[0].win;
      return { moves: [{ ...pass, win }], forcedPass: true };
    }
    const payload = {
      gridJson: JSON.stringify(position.grid), rack: position.racks[actor],
      playerScore: position.scores[actor], opponentScore: position.scores[1 - actor],
      bagCount: position.bag.length, finalTurnsRemaining: position.finalTurns || 0,
      numMoves: 15, iterations: 277, seed: position.seed,
      played: { action: 'pass', position: '', tiles: '' }
    };
    // Include pass even when it falls outside the static shortlist. This uses
    // the same paired samples and validation as recorded-move win estimates.
    const result = await request('simulateReview', payload, options);
    const review = typeof module !== 'undefined' && module.exports
      ? require('./review-analysis.js') : root.CrossplayReview;
    if (!review.validResult(result, { grid: position.grid, action: 'PASS',
      bag_count: payload.bagCount, final_turns_remaining: payload.finalTurnsRemaining },
      payload.seed, payload.iterations, payload.numMoves)) {
      throw new Error(result?.error || 'Quackle returned incomplete continuation simulations.');
    }
    return { ...result, moves: result.moves.slice().sort((a, b) => b.win - a.win || b.equity - a.equity) };
  }

  class Explorer {
    constructor({ turn, opponentRack, seed, move, names, request, renderBoard }) {
      this.root = document.createElement('section');
      this.root.className = 'continuation-panel';
      this.root.setAttribute('aria-label', 'Hypothetical continuation');
      this.root.tabIndex = -1;
      this.names = names;
      this.request = request;
      this.renderBoard = renderBoard;
      this.controller = new AbortController();
      this.history = [];
      this.sequence = 0;
      try {
        this.history = [play(start(turn, opponentRack, seed), move)];
        this.load();
      } catch (error) {
        this.error = error.message;
        this.render();
      }
    }

    dispose() {
      this.sequence++;
      this.controller.abort();
      this.root.remove();
    }

    async load() {
      const sequence = ++this.sequence;
      const position = this.history.at(-1);
      this.error = null;
      this.busy = !position.finished;
      this.render();
      if (position.finished) return;
      try {
        if (!position.moves) {
          const result = await analyze(position, this.request, { signal: this.controller.signal });
          if (sequence !== this.sequence) return;
          position.analysis = result;
          position.moves = result.moves;
        }
      } catch (error) {
        if (sequence !== this.sequence || this.controller.signal.aborted) return;
        this.error = error.message;
      }
      if (sequence !== this.sequence) return;
      this.busy = false;
      this.render();
    }

    advance(move) {
      try {
        this.history.push(play(this.history.at(-1), move));
        this.load();
        this.root.scrollIntoView({ block: 'start', behavior: 'instant' });
      } catch (error) {
        this.error = error.message;
        this.render();
      }
    }

    render() {
      const add = (parent, tag, text, className = '') => {
        const node = document.createElement(tag);
        node.textContent = text;
        node.className = className;
        parent.append(node);
        return node;
      };
      const button = (parent, text, click) => {
        const node = add(parent, 'button', text);
        node.type = 'button';
        node.addEventListener('click', click);
        return node;
      };
      const focused = this.root.contains(document.activeElement);
      this.root.replaceChildren();
      if (focused) this.root.focus({ preventScroll: true });
      add(this.root, 'h3', 'Explore this line');
      add(this.root, 'p', 'Future draws are sampled from the remaining Crossplay tiles. This is one possible continuation. Replies are ranked by simulated win chance for the player to move. Win estimates sample hidden opponent racks and future draws independently of the displayed line; ties count as half a win.', 'analysis-note');
      const position = this.history.at(-1);
      if (!position) {
        add(this.root, 'p', this.error, 'analysis-status').setAttribute('role', 'status');
        return;
      }
      const controls = add(this.root, 'div', '', 'continuation-controls');
      const back = button(controls, '← Undo reply', () => { this.history.pop(); this.load(); });
      back.disabled = this.history.length < 2 || this.busy;
      if (!position.finished && !this.busy && position.moves?.length) {
        button(controls, 'Play best reply →', () => this.advance(position.moves[0]));
      }
      const scores = this.names.map((name, index) => name + ': ' + position.scores[index]).join(' · ');
      add(this.root, 'p', 'After ' + this.history.length + ' hypothetical ' + (this.history.length === 1 ? 'move' : 'moves') + ' · ' + scores);
      add(this.root, 'p', 'Last move: ' + position.action + ' · Bag: ' + position.bag.length +
        (position.finalTurns !== null ? ' · Final turns left: ' + position.finalTurns : ''), 'analysis-note');
      this.root.append(this.renderBoard(position.grid, position.action));
      add(this.root, 'p', this.names.map((name, index) => name + ' rack: ' + (position.racks[index] || '(empty)')).join(' · '), 'continuation-racks');
      if (position.sampledOpponent) add(this.root, 'p', 'The starting opponent rack was also sampled because it was unavailable in the review.', 'analysis-note');
      const status = add(this.root, 'p', this.error || (position.finished ? 'Line complete · ' + scores
        : this.busy ? 'Simulating reply win chances…' : this.names[position.actor] + ' to move. Select a reply to continue.'), 'analysis-status');
      status.setAttribute('role', 'status');
      if (this.error) button(this.root, 'Retry replies', () => this.load());
      if (position.finished || this.busy || this.error) return;
      add(this.root, 'p', position.analysis.forcedPass ? 'Empty rack · pass is the only reply.'
        : position.analysis.iterationsCompleted + ' simulations per reply · ' +
          (position.analysis.playToEnd ? 'Played to the Crossplay finish.' : 'Two-ply play with Quackle’s win model.') +
          ' Engine estimates can change with more samples.', 'analysis-note');
      const table = add(this.root, 'table', '', 'moves-table continuation-moves');
      const header = table.createTHead().insertRow();
      for (const label of ['Reply', 'Score', 'Leave', 'Win % (' + this.names[position.actor] + ')', 'Static equity']) add(header, 'th', label);
      const body = table.createTBody();
      for (const move of position.moves) {
        const row = body.insertRow();
        const play = move.action === 'pass' ? 'Pass' : move.action === 'exchange' ? 'Exchange ' + move.tiles : move.position + ' ' + move.tiles;
        const cell = row.insertCell();
        button(cell, play, () => this.advance(move)).className = 'move-preview-button';
        for (const value of [move.score, move.leave || '', (move.win * 100).toFixed(1) + '%',
          Number.isFinite(move.staticEquity) ? move.staticEquity.toFixed(1) : '—']) add(row, 'td', value);
      }
    }
  }

  const api = { start, play, analyze, Explorer };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.MoveContinuation = api;
})(globalThis);
