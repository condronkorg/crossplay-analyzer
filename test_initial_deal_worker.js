const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');
const context = vm.createContext({self: {addEventListener() {}}});
context.importScripts = (...files) => files.filter(file => file === 'opening-draw.js')
  .forEach(file => vm.runInContext(fs.readFileSync(file, 'utf8'), context));
vm.runInContext(fs.readFileSync('quackle-worker.js', 'utf8'), context);
const payload = {firstRack: 'AEIRST?', secondRack: 'ADEINRT', iterations: 64, seed: 123};
const complete = () => ({simulated: true, samplingVersion: 'initial-deal-v1', policy: 'static-best-v1',
  firstRack: '?AEIRST', secondRack: 'ADEINRT', seed: 123, baselineWin: .6, firstOnlyWin: .7,
  secondOnlyWin: .5, afterWin: .65, firstWpa: 12.5, secondWpa: 7.5,
  iterationsRequested: 64, iterationsCompleted: 64, plies: 2, playToEnd: false,
  simulationMode: '2-ply', winModel: 'bogowin'});

test('opening worker validates and forwards only initial racks, samples, and seed', () => {
  const module = {simulateInitialDeal(...args) {
    assert.deepEqual(args, ['AEIRST?', 'ADEINRT', 64, 123]);
    return complete();
  }};
  assert.deepEqual(context.simulateOpeningDeal(module, {...payload, played: {tiles: 'AT'}}), complete());
});

test('opening worker rejects malformed inputs before calling the engine', () => {
  for (const change of [{firstRack: 'AEIRST'}, {firstRack: 'aeirst?'}, {secondRack: 'QQAEIRS'},
    {firstRack: 'AEIRS??', secondRack: 'ADEIN??'}, {iterations: 0}, {iterations: 8}, {iterations: 278},
    {seed: -1}, {seed: 1.5}, {seed: 4294967296}]) {
    let calls = 0;
    assert.throws(() => context.simulateOpeningDeal({simulateInitialDeal() {calls++;}}, {...payload, ...change}));
    assert.equal(calls, 0);
  }
});

test('opening worker rejects incomplete, mismatched, and inconsistent native results', () => {
  for (const change of [{simulated: false}, {seed: 124}, {samplingVersion: 'bad'}, {policy: 'bad'},
    {firstRack: 'AEIRST?'}, {secondRack: 'EEEEIII'}, {baselineWin: NaN}, {afterWin: 2},
    {firstWpa: 9}, {secondWpa: 9}, {iterationsCompleted: 63}, {iterationsRequested: 65},
    {plies: -1}, {playToEnd: true}, {simulationMode: 'play-to-end'}, {winModel: 'terminal'}]) {
    assert.throws(() => context.simulateOpeningDeal({simulateInitialDeal() {
      return {...complete(), ...change};
    }}, payload), /incomplete opening/);
  }
  assert.throws(() => context.simulateOpeningDeal({simulateInitialDeal() {
    return {error: 'Native deal failed'};
  }}, payload), /Native deal failed/);
});
