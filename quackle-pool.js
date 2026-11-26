/* Runs Quackle requests on a few workers so independent positions are analyzed in parallel. */
(function (root) {
  'use strict';
  const Bag =
    typeof module !== 'undefined' && module.exports
      ? require('./bag-emptying-analysis.js')
      : root.CrossplayBagEmptying;
  function abortError() {
    const error = new Error('Quackle request cancelled.');
    error.name = 'AbortError';
    return error;
  }

  class QuackleWorkerPool {
    static browserSize() {
      return Bag.browserWorkerBudget();
    }

    static forBrowser() {
      return new QuackleWorkerPool({
        createWorker: () => new Worker('quackle-worker.js'), size: this.browserSize()
      });
    }

    constructor({ createWorker, size = 1, timeout = 120000 } = {}) {
      this.createWorker = createWorker;
      this.maxSize = Math.max(1, size);
      this.size = this.maxSize;
      this.timeout = timeout;
      this.slots = [];
      this.queue = [];
      this.nextId = 0;
      this.sequence = 0;
      this.partial = new Map();
      this.batchJobs = new Map();
      this.batchOptions = new Map();
      this.generation = 0;
    }

    // Higher priorities run first; equal priorities run in request order.
    // Aborting a signal only cancels work that has not reached a worker yet.
    run(type, payload = {}, options = {}) {
      const terminal = Number.isInteger(payload.bagCount) &&
        payload.bagCount >= 0 && payload.bagCount <= 21;
      if (terminal && ['simulateReview', 'simulateKibitz', 'simulateMove', 'simulateDraw'].includes(type))
        return this.runBatched(type, payload, options);
      return this.runSingle(type, payload, options);
    }

    runBatched(type, payload, options) {
      const key = JSON.stringify([type, payload]);
      if (options.signal?.aborted) return Promise.reject(abortError());
      if (!Number.isInteger(payload.iterations) || payload.iterations < 1 || payload.iterations > 277)
        return Promise.reject(new Error('Simulation iterations must be between 1 and 277.'));
      const generation = this.generation;
      const existing = this.batchJobs.get(key);
      if (existing) {
        if (options.priority !== undefined && existing.options.taskKey)
          this.promote(existing.options.taskKey, options.priority);
        return existing.promise.catch((error) => {
          if (
            error.name === 'AbortError' &&
            !options.signal?.aborted &&
            generation === this.generation
          )
            return this.runBatched(type, payload, options);
          throw error;
        });
      }
      const onStart = options.onStart;
      let started = false;
      options = { ...options, onStart: onStart && (() => {
        if (started) return;
        started = true;
        onStart();
      }) };
      if (options.taskKey) this.batchOptions.set(options.taskKey, options);
      const active = () => {
        if (options.signal?.aborted || generation !== this.generation) throw abortError();
      };
      const compute = type === 'simulateDraw'
        ? this.computeDraw(key, payload, options, generation, active)
        : this.computeReview(key, type, payload, options, generation, active);
      const promise = compute.finally(() => {
        if (this.batchOptions.get(options.taskKey) === options)
          this.batchOptions.delete(options.taskKey);
        if (this.batchJobs.get(key)?.promise === promise) this.batchJobs.delete(key);
      });
      this.batchJobs.set(key, {promise, options});
      return promise;
    }

    saveCheckpoint(key, state) {
      this.partial.set(key, state);
      while (this.partial.size > 16) {
        const old = [...this.partial.keys()].find(id => id !== key && !this.batchJobs.has(id));
        if (!old) break;
        this.partial.delete(old);
      }
    }

    async computeReview(key, type, payload, options, generation, active) {
      const {bagCount, iterations} = payload;
      const expanded = bagCount >= 1 && bagCount <= 7 && [192, 277].includes(iterations);
      let state = this.partial.get(key);
      active();
      if (!state) {
        const plan = await this.runSingle(
          expanded ? 'planBagEmptying' : 'planReviewSimulation',
          { ...payload, requestType: type },
          options,
        );
        active();
        if (plan.error) throw new Error(plan.error);
        state = Bag.create(plan);
        this.saveCheckpoint(key, state);
      }
      const lanes = Math.min(this.size, expanded ? Math.ceil(state.sums.length / 8) : state.sums.length);
      const groupSize = expanded ? 8 : Math.min(8, Math.ceil(state.sums.length / lanes));
      // Groups use disjoint candidates and keep the next sample offset per candidate.
      const reserved = new Set();
      let failure = null;
      const claimGroup = () => {
        active();
        const start = state.sums.findIndex((sum, index) =>
          sum.samples < iterations && !reserved.has(index));
        if (start < 0) return null;
        const offset = state.sums[start].samples;
        let end = start + 1;
        while (end < state.sums.length && end - start < groupSize &&
            state.sums[end].samples === offset && !reserved.has(end)) end++;
        for (let index = start; index < end; index++) reserved.add(index);
        return {start, end};
      };
      const runLane = async () => {
        while (!failure) {
          let group;
          try {
            group = claimGroup();
            if (!group) return;
            const {start, end} = group;
            while (!failure && state.sums[start].samples < iterations) {
              active();
              const offset = state.sums[start].samples;
              const count = Math.min(16, iterations - offset);
              const batch = await this.runSingle(
                'simulateReviewBatch',
                {
                  ...payload,
                  finalTurnsRemaining: state.plan.finalTurnsRemaining,
                  candidates: state.plan.moves.slice(start, end),
                  seed: state.plan.seed,
                  offset,
                  count,
                },
                options,
              );
              if (generation !== this.generation) throw abortError();
              if (batch.offset !== offset || batch.completed > count || batch.moves?.length !== end - start)
                throw new Error('Invalid candidate batch coverage.');
              Bag.incorporate(state, { ...batch, start });
            }
          } catch (error) {
            failure ||= error;
          } finally {
            if (group) {
              for (let index = group.start; index < group.end; index++) reserved.delete(index);
            }
          }
        }
      };
      // A failed lane must settle its siblings before a retry can reuse state.
      await Promise.all(Array.from({length: lanes}, () => runLane()));
      if (generation !== this.generation) throw abortError();
      if (failure) throw failure;
      active();
      const result = Bag.finish(state, iterations, expanded);
      this.partial.delete(key);
      return result;
    }

    async computeDraw(key, payload, options, generation, active) {
      const {iterations, seed, beforeWin} = payload;
      const knownBeforeWin = [64, 96, 128, 192, 277].includes(iterations) && beforeWin?.samples === iterations &&
        beforeWin.seed === seed && Number.isFinite(beforeWin.win) && beforeWin.win >= 0 && beforeWin.win <= 1
        ? beforeWin.win : -1;
      const state = this.partial.get(key) || {samples: 0, beforeWinSum: 0, afterWinSum: 0};
      const review = typeof module !== 'undefined' && module.exports
        ? require('./review-analysis.js') : root.CrossplayReview;
      const turn = Bag.drawTurn(payload, payload.played || {});
      while (state.samples < iterations) {
        active();
        const offset = state.samples;
        const count = Math.min(16, iterations - offset);
        const batch = await this.runSingle('simulateDrawBatch', { ...payload, offset, count, knownBeforeWin }, options);
        if (generation !== this.generation) throw abortError();
        if (!Bag.validDrawBatch(batch, offset, count, knownBeforeWin))
          throw new Error('Invalid rack draw batch coverage.');
        const {completed, offset: _, beforeWinSum, afterWinSum, ...metadata} = batch;
        if (!review.validDrawResult({...metadata, iterationsRequested: completed}, turn, seed, completed))
          throw new Error('Quackle returned incomplete rack draw batch results.');
        state.samples += completed;
        state.beforeWinSum += beforeWinSum;
        state.afterWinSum += afterWinSum;
        state.metadata = metadata;
        this.saveCheckpoint(key, state);
      }
      active();
      const result = { ...state.metadata, iterationsRequested: iterations, iterationsCompleted: state.samples,
        beforeWin: knownBeforeWin === -1 ? state.beforeWinSum / iterations : knownBeforeWin,
        afterWin: knownBeforeWin !== -1 && state.metadata.drawCount === 0
          ? knownBeforeWin : state.afterWinSum / iterations };
      if (!review.validDrawResult(result, turn, seed, iterations))
        throw new Error('Quackle returned incomplete rack draw results.');
      this.partial.delete(key);
      return result;
    }

    runSingle(type, payload = {}, { priority = 0, signal, onStart, taskKey } = {}) {
      return new Promise((resolve, reject) => {
        if (signal?.aborted) return reject(abortError());
        const task = {
          type,
          payload,
          priority,
          order: ++this.sequence,
          resolve,
          reject,
          signal,
          onStart,
          taskKey,
        };
        if (signal) {
          task.onAbort = () => {
            const index = this.queue.indexOf(task);
            if (index < 0) return;
            this.queue.splice(index, 1);
            reject(abortError());
          };
          signal.addEventListener('abort', task.onAbort, { once: true });
        }
        this.queue.push(task);
        this.schedule();
      });
    }

    // A viewed turn can join and promote an existing background request.
    promote(taskKey, priority) {
      const options = this.batchOptions.get(taskKey);
      if (options) options.priority = Math.max(options.priority || 0, priority);
      const tasks = taskKey ? this.queue.filter((task) => task.taskKey === taskKey) : [];
      if (!tasks.length) return Boolean(options);
      for (const task of tasks) task.priority = Math.max(task.priority, priority);
      this.schedule();
      return true;
    }

    schedule() {
      this.queue.sort((a, b) => b.priority - a.priority || a.order - b.order);
      for (const slot of this.slots) {
        if (!this.queue.length) return;
        if (slot.ready && !slot.task) this.send(slot, this.queue.shift());
      }
      // Each worker loads its own engine and dictionary, so start them only when work is waiting.
      let starting = this.slots.filter((slot) => !slot.ready).length;
      while (this.slots.length < this.size && this.queue.length > starting) {
        this.spawn();
        starting++;
      }
    }

    spawn() {
      const slot = { worker: this.createWorker(), ready: false, task: null };
      this.slots.push(slot);
      slot.worker.addEventListener('message', (event) => this.receive(slot, event.data || {}));
      slot.worker.addEventListener('error', (event) =>
        this.fail(slot, new Error(event.message || 'Quackle worker failed.')),
      );
      slot.worker.addEventListener('messageerror', () =>
        this.fail(slot, new Error('Quackle worker returned an unreadable response.')),
      );
      this.send(slot, { type: 'init', payload: {}, init: true });
    }

    send(slot, task) {
      if (task.signal) task.signal.removeEventListener('abort', task.onAbort);
      task.id = ++this.nextId;
      slot.task = task;
      if (task.onStart) task.onStart();
      task.timer = setTimeout(
        () =>
          this.fail(slot, new Error('Quackle request timed out. Try again to restart the engine.')),
        this.timeout,
      );
      try {
        slot.worker.postMessage({ id: task.id, type: task.type, payload: task.payload });
      } catch (error) {
        this.fail(slot, error);
      }
    }

    receive(slot, data) {
      const task = slot.task;
      if (!task || data.id !== task.id) return;
      clearTimeout(task.timer);
      slot.task = null;
      if (task.init) {
        if (data.error) return this.fail(slot, new Error(data.error));
        slot.ready = true;
      } else if (data.error) task.reject(new Error(data.error));
      else task.resolve(data.result);
      this.schedule();
    }

    fail(slot, error) {
      const index = this.slots.indexOf(slot);
      if (index < 0) return;
      this.slots.splice(index, 1);
      slot.worker.terminate();
      const task = slot.task;
      slot.task = null;
      if (task) clearTimeout(task.timer);
      if (task && !task.init) task.reject(error);
      if (!slot.ready) {
        // A worker that cannot start is not retried; stop growing past the ones that work.
        this.size = Math.max(1, this.slots.length);
        if (!this.slots.length) this.rejectQueue(error);
      }
      this.schedule();
    }

    rejectQueue(error) {
      const queued = this.queue;
      this.queue = [];
      for (const task of queued) {
        if (task.signal) task.signal.removeEventListener('abort', task.onAbort);
        task.reject(error);
      }
    }

    stop(error = new Error('Quackle worker stopped')) {
      this.generation++;
      this.batchJobs.clear();
      this.batchOptions.clear();
      for (const slot of this.slots) {
        slot.worker.terminate();
        if (slot.task) {
          clearTimeout(slot.task.timer);
          if (!slot.task.init) slot.task.reject(error);
        }
      }
      this.slots = [];
      this.partial.clear();
      this.size = this.maxSize;
      this.rejectQueue(error);
    }
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = QuackleWorkerPool;
  else root.QuackleWorkerPool = QuackleWorkerPool;
})(typeof window !== 'undefined' ? window : globalThis);
