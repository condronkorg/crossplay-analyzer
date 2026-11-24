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
      this.expandedJobs = new Map();
      this.expandedOptions = new Map();
      this.generation = 0;
    }

    // Higher priorities run first; equal priorities run in request order.
    // Aborting a signal only cancels work that has not reached a worker yet.
    run(type, payload = {}, options = {}) {
      if (
        payload.bagCount >= 1 &&
        payload.bagCount <= 7 &&
        payload.iterations === 277 &&
        ['simulateReview', 'simulateKibitz', 'simulateMove'].includes(type)
      ) {
        return this.runExpanded(type, payload, options);
      }
      return this.runSingle(type, payload, options);
    }

    runExpanded(type, payload, options) {
      const key = JSON.stringify([type, payload]);
      if (options.signal?.aborted) return Promise.reject(abortError());
      const generation = this.generation;
      const existing = this.expandedJobs.get(key);
      if (existing) {
        return existing.catch((error) => {
          if (
            error.name === 'AbortError' &&
            !options.signal?.aborted &&
            generation === this.generation
          )
            return this.runExpanded(type, payload, options);
          throw error;
        });
      }
      options = { ...options };
      if (options.taskKey) this.expandedOptions.set(options.taskKey, options);
      const promise = this.computeExpanded(key, type, payload, options, generation).finally(() => {
        if (this.expandedOptions.get(options.taskKey) === options)
          this.expandedOptions.delete(options.taskKey);
        if (this.expandedJobs.get(key) === promise) this.expandedJobs.delete(key);
      });
      this.expandedJobs.set(key, promise);
      return promise;
    }

    async computeExpanded(key, type, payload, options, generation) {
      let state = this.partial.get(key);
      const active = () => {
        if (options.signal?.aborted || generation !== this.generation) throw abortError();
      };
      active();
      if (!state) {
        const plan = await this.runSingle(
          'planBagEmptying',
          { ...payload, requestType: type },
          options,
        );
        active();
        if (plan.error) throw new Error(plan.error);
        state = Bag.create(plan);
        this.partial.set(key, state);
        while (this.partial.size > 16) this.partial.delete(this.partial.keys().next().value);
      }
      while (state.sums.some((sum) => sum.samples < 277)) {
        active();
        const start = state.sums.findIndex((sum) => sum.samples < 277);
        const offset = state.sums[start].samples;
        let end = start + 1;
        while (end < state.sums.length && end - start < 8 && state.sums[end].samples === offset)
          end++;
        const batch = await this.runSingle(
          'simulateCandidateBatch',
          {
            ...payload,
            candidates: state.plan.moves.slice(start, end),
            seed: state.plan.seed,
            offset,
            count: Math.min(16, 277 - offset),
          },
          { ...options, onStart: undefined },
        );
        if (generation !== this.generation) throw abortError();
        Bag.incorporate(state, { ...batch, start });
      }
      const result = Bag.finish(state, 277);
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
      const expanded = this.expandedOptions.get(taskKey);
      if (expanded) expanded.priority = Math.max(expanded.priority || 0, priority);
      const task = this.queue.find((task) => task.taskKey === taskKey);
      if (!task || !taskKey) return Boolean(expanded);
      task.priority = Math.max(task.priority, priority);
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
      this.expandedJobs.clear();
      this.expandedOptions.clear();
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
