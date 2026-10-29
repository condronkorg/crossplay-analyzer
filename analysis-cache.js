/* Position results are shared by grading, turn details, and the win trend. */
(function(root) {
  'use strict';
  class AnalysisCache {
    constructor(saved = {}, parameters = {}) {
      this.results = { ...saved };
      this.parameters = { moveCount: 15, iterations: 277, plies: 2, terminalBagLimit: 21,
        trendMoveCount: 1, trendIterations: 80, previewMoveCount: 5, previewIterations: 64, ...parameters };
      this.pending = new Map();
      this.errors = new Map();
    }

    // Review results include the actual action and use paired sampling. Keep
    // legacy keys intact for custom move analysis and existing saved results.
    key(turn, kind) {
      const p = this.parameters;
      const review = kind === 'review' || kind === 'preview';
      const sampled = review || kind === 'simulation' || kind === 'trend';
      const values = [review ? 'quackle-review-v1' : 'quackle-position-v1', kind, turn.grid, turn.rack,
        Number(turn.mover_score) || 0, Number(turn.other_score) || 0,
        Number(turn.bag_count), Number(turn.bag_count) === 0 ? Number(turn.final_turns_remaining) : 0,
        kind === 'preview' ? p.previewMoveCount : kind === 'trend' ? p.trendMoveCount : p.moveCount,
        kind === 'preview' ? p.previewIterations : kind === 'simulation' || kind === 'review' ? p.iterations : kind === 'trend' ? p.trendIterations : 0,
        sampled ? p.plies : 0, sampled ? p.terminalBagLimit : 0];
      if (review) values.push(String(turn.action || ''), String(turn.swapped_tiles || ''), 'paired-review-v1');
      return JSON.stringify(values);
    }

    // Both profiles use the full review seed, so preview samples match its prefix.
    seed(turn) {
      let hash = 2166136261;
      for (const character of this.key(turn, 'review')) {
        hash ^= character.charCodeAt(0);
        hash = Math.imul(hash, 16777619);
      }
      return hash >>> 0;
    }

    get(turn, kind) {
      return this.results[this.key(turn, kind)] || null;
    }

    // compute receives a callback to report that the engine has started the
    // work; a cancelled request that already started is still shared.
    async request(turn, kind, compute, signal, { priority = 0, onPromote } = {}) {
      const key = this.key(turn, kind);
      if (this.results[key]) return this.results[key];
      const existing = this.pending.get(key);
      if (existing && (!existing.signal?.aborted || existing.started)) {
        if (!existing.started && priority > existing.priority) {
          existing.priority = priority;
          // The initial compute callback can still be waiting for its microtask.
          queueMicrotask(() => existing.onPromote?.(existing.priority));
        }
        return existing.promise;
      }
      this.errors.delete(key);
      const entry = { signal, started: false, priority, onPromote };
      entry.promise = Promise.resolve().then(() => compute(() => { entry.started = true; })).then(result => {
        this.results[key] = result;
        return result;
      }).catch(error => {
        // Cancelled work is not a failure; the position is requested again when needed.
        if (error.name !== 'AbortError') this.errors.set(key, error.message);
        throw error;
      }).finally(() => {
        if (this.pending.get(key) === entry) this.pending.delete(key);
      });
      this.pending.set(key, entry);
      return entry.promise;
    }
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = AnalysisCache;
  else root.AnalysisCache = AnalysisCache;
})(typeof window !== 'undefined' ? window : globalThis);
