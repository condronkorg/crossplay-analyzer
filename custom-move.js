window.CustomMove = class CustomMove {
  constructor(container, getPosition, request) {
    this.getPosition = getPosition;
    this.request = request;
    this.controller = null;
    this.payload = null;
    this.root = document.createElement('section');
    this.root.className = 'custom-move';
    this.root.innerHTML = `
      <h3>Try a custom move</h3>
      <form class="custom-move-form">
        <label>Action<select name="action" aria-label="Custom move action"><option value="place">Play a word</option><option value="exchange">Swap tiles</option></select></label>
        <label hidden>Tiles to swap<input name="tiles" aria-label="Tiles to swap" maxlength="7" placeholder="ABC?" autocomplete="off" spellcheck="false"></label>
        <label>Word<input name="word" aria-label="Custom move word" maxlength="15" placeholder="CAT" autocomplete="off" spellcheck="false" autocapitalize="off" required></label>
        <label>Start square<input name="square" aria-label="Custom move start square" maxlength="3" placeholder="G8" autocomplete="off" spellcheck="false" required></label>
        <label>Direction<select name="direction" aria-label="Custom move direction"><option value="across">Across →</option><option value="down">Down ↓</option></select></label>
        <button type="submit">Evaluate move</button>
      </form>
      <div class="custom-move-status" role="status" aria-live="polite"></div>
      <button type="button" class="custom-move-cancel" hidden>Cancel evaluation</button>
      <div class="custom-move-result"></div>`;
    this.form = this.root.querySelector('form');
    this.status = this.root.querySelector('.custom-move-status');
    this.output = this.root.querySelector('.custom-move-result');
    this.cancelButton = this.root.querySelector('.custom-move-cancel');
    this.form.addEventListener('submit', event => {
      event.preventDefault();
      this.evaluate(false);
    });
    this.form.addEventListener('input', () => this.reset());
    this.form.elements.action.addEventListener('change', () => {
      this.reset();
      const swapping = this.form.elements.action.value === 'exchange';
      for (const name of ['word', 'square', 'direction', 'tiles']) {
        const input = this.form.elements[name];
        const visible = swapping === (name === 'tiles');
        input.closest('label').hidden = !visible;
        input.required = visible && name !== 'direction';
      }
    });
    this.cancelButton.addEventListener('click', () => {
      this.stop();
      this.status.textContent = 'Evaluation cancelled.';
    });
    container.append(this.root);
  }

  setBusy(busy) {
    for (const input of this.form.elements) input.disabled = busy;
    this.cancelButton.hidden = !busy;
    const simulate = this.output.querySelector('button');
    if (simulate) simulate.disabled = busy;
    this.root.setAttribute('aria-busy', String(busy));
  }

  stop() {
    this.controller?.abort();
    this.controller = null;
    this.setBusy(false);
  }

  reset() {
    this.stop();
    this.payload = null;
    this.output.replaceChildren();
    this.status.textContent = '';
  }

  dispose() {
    this.stop();
    this.root.remove();
  }

  async evaluate(simulateWin) {
    this.controller?.abort();
    const controller = this.controller = new AbortController();
    this.setBusy(true);
    this.status.classList.remove('custom-move-error');
    this.status.textContent = simulateWin
      ? 'Estimating win chances… You can cancel.'
      : 'Checking the move and evaluating all legal alternatives…';
    if (!simulateWin) this.output.replaceChildren();
    try {
      if (!simulateWin) {
        const position = this.getPosition();
        if (!position) throw new Error('Analyze a board first.');
        this.payload = { ...position, iterations: CrossplayBagEmptying.fullIterations(position.bagCount), customMove: {
          action: this.form.elements.action.value,
          tiles: this.form.elements.tiles.value.trim(),
          word: this.form.elements.word.value.trim(),
          square: this.form.elements.square.value.trim(),
          direction: this.form.elements.direction.value
        } };
      }
      const result = await this.request(simulateWin ? 'simulateMove' : 'evaluateMove', this.payload,
        {signal: controller.signal});
      if (controller !== this.controller) return;
      this.renderResult(result);
      this.status.textContent = simulateWin ? 'Win simulation complete.' : 'Move evaluated.';
    } catch (error) {
      if (controller !== this.controller) return;
      this.status.classList.add('custom-move-error');
      this.status.textContent = error.message;
    } finally {
      if (controller === this.controller) {
        this.controller = null;
        this.setBusy(false);
      }
    }
  }

  renderResult(result) {
    this.output.replaceChildren();
    const append = (parent, tag, text, className = '') => {
      const element = document.createElement(tag);
      element.textContent = text;
      element.className = className;
      parent.append(element);
      return element;
    };
    const table = document.createElement('table');
    const head = table.createTHead().insertRow();
    const labels = ['Move', 'Score', 'Leave', result.simulated ? 'Sim. equity' : 'Static equity'];
    if (result.simulated) labels.push('Win%');
    for (const label of labels) append(head, 'th', label);
    const body = table.createTBody();
    for (const [label, move] of [['Your move', result.selected], [result.simulated ? 'Best analyzed' : 'Best static move', result.best]]) {
      const row = body.insertRow();
      append(row, 'td', label + ': ' + (move.action === 'exchange' ? 'Swap ' + move.tiles
        : move.action === 'pass' ? 'Pass' : (move.position ? move.position + ' ' : '') + move.tiles));
      append(row, 'td', move.score);
      append(row, 'td', move.leave || '—');
      append(row, 'td', move.equity.toFixed(1));
      if (result.simulated) append(row, 'td', (move.win * 100).toFixed(1) + '%');
    }
    this.output.append(table);
    const loss = Math.max(0, result.best.equity - result.selected.equity);
    if (!result.simulated) append(this.output, 'p', 'Equity loss: ' + loss.toFixed(1), 'custom-move-comparison');
    append(this.output, 'p', 'Static rank #' + result.staticRank + ' / ' + result.legalMoveCount, 'custom-move-help');
    if (result.simulated) {
      const wpLoss = Math.max(0, result.best.win - result.selected.win) * 100;
      append(this.output, 'p', 'WP loss: ' + wpLoss.toFixed(1) + ' pp', 'custom-move-comparison');
      append(this.output, 'p', (result.searchedCandidateCount ?? result.candidateCount) + ' moves · ' +
        result.iterationsCompleted + ' samples', 'custom-move-help custom-move-counts');
    } else {
      const button = append(this.output, 'button', 'Estimate win% (slower)');
      button.type = 'button';
      button.addEventListener('click', () => this.evaluate(true));
    }
  }
};
