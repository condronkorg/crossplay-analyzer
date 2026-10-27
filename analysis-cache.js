/* Position results are shared by grading, turn details, and the win trend. */
(function(root) {
  'use strict';
  class AnalysisCache {
    constructor(saved = {}, parameters = {}) {
      this.results = { ...saved };
      this.parameters = { moveCount: 15, iterations: 277, plies: 2, terminalBagLimit: 21, ...parameters };
      this.pending = new Map();
      this.errors = new Map();
    }

    key(turn, kind) {
      return JSON.stringify(['quackle-position-v1', kind, turn.grid, turn.rack,
        Number(turn.mover_score) || 0, Number(turn.other_score) || 0,
        Number(turn.bag_count), Number(turn.bag_count) === 0 ? Number(turn.final_turns_remaining) : 0,
        this.parameters.moveCount, kind === 'simulation' ? this.parameters.iterations : 0,
        kind === 'simulation' ? this.parameters.plies : 0,
        kind === 'simulation' ? this.parameters.terminalBagLimit : 0]);
    }

    get(turn, kind) {
      return this.results[this.key(turn, kind)] || null;
    }

    async request(turn, kind, compute) {
      const key = this.key(turn, kind);
      if (this.results[key]) return this.results[key];
      if (this.pending.has(key)) return this.pending.get(key);
      this.errors.delete(key);
      const promise = Promise.resolve().then(compute).then(result => {
        this.results[key] = result;
        return result;
      }).catch(error => {
        this.errors.set(key, error.message);
        throw error;
      }).finally(() => this.pending.delete(key));
      this.pending.set(key, promise);
      return promise;
    }
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = AnalysisCache;
  else root.AnalysisCache = AnalysisCache;
})(typeof window !== 'undefined' ? window : globalThis);
