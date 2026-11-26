const test = require('node:test');
const assert = require('node:assert/strict');
const AnalysisCache = require('./analysis-cache.js');
const turn = { grid: Array(15).fill('.'.repeat(15)), rack: 'ACERSTU', mover_score: 0, other_score: 0, bag_count: 86 };

test('concurrent views share one engine request and reload reuses the saved result', async () => {
  const cache = new AnalysisCache();
  let calls = 0;
  const compute = async () => { calls++; return { simulated: false, moves: [{ tiles: 'CAT', equity: 5 }] }; };
  const [grade, details] = await Promise.all([
    cache.request(turn, 'candidates', compute), cache.request(turn, 'candidates', compute)
  ]);
  assert.equal(calls, 1);
  assert.equal(grade, details);
  const restored = new AnalysisCache(JSON.parse(JSON.stringify(cache.results)));
  assert.deepEqual(await restored.request(turn, 'candidates', compute), grade);
  assert.equal(calls, 1);
});

test('position, scores, final turns and analysis mode are part of the cache identity', async () => {
  const cache = new AnalysisCache();
  await cache.request(turn, 'candidates', async () => ({ moves: [] }));
  for (const change of [{ rack: 'ABCDEFG' }, { mover_score: 1 }, { other_score: 2 },
    { bag_count: 0, final_turns_remaining: 1 }, { grid: ['A..............', ...turn.grid.slice(1)] }]) {
    assert.equal(cache.get({ ...turn, ...change }, 'candidates'), null);
  }
  assert.equal(cache.get(turn, 'simulation'), null);
  assert.equal(new AnalysisCache(cache.results, { moveCount: 10 }).get(turn, 'candidates'), null);
  assert.notEqual(cache.key({ ...turn, bag_count: 0, final_turns_remaining: 1 }, 'simulation'),
    cache.key({ ...turn, bag_count: 0, final_turns_remaining: 2 }, 'simulation'));
});

test('failed requests are retryable and never become saved results', async () => {
  const cache = new AnalysisCache();
  await assert.rejects(cache.request(turn, 'simulation', async () => { throw new Error('engine failure'); }));
  assert.equal(cache.get(turn, 'simulation'), null);
  assert.equal(cache.pending.size, 0);
  const result = await cache.request(turn, 'simulation', async () => ({ moves: [{ win: 0.5 }] }));
  assert.equal(result.moves[0].win, 0.5);
  assert.equal(cache.errors.size, 0);
});

test('trend estimates have their own identity and keep full simulation keys unchanged', () => {
  const cache = new AnalysisCache();
  assert.notEqual(cache.key(turn, 'trend'), cache.key(turn, 'simulation'));
  assert.notEqual(cache.key(turn, 'trend'), new AnalysisCache({}, { trendIterations: 40 }).key(turn, 'trend'));
  // Results saved before trend estimates existed must still be found.
  assert.equal(cache.key(turn, 'simulation'), JSON.stringify(['quackle-position-v1', 'simulation', turn.grid,
    turn.rack, 0, 0, 86, 0, 15, 277, 2, 21]));
});

test('cancelled requests are not errors, and only unstarted ones are replaced', async () => {
  const cache = new AnalysisCache();
  const queued = new AbortController();
  const cancelled = cache.request(turn, 'simulation', () => new Promise((_, reject) => {
    queued.signal.addEventListener('abort', () => reject(Object.assign(new Error('cancelled'), { name: 'AbortError' })));
  }), queued.signal);
  await Promise.resolve();
  queued.abort();
  // A new request right after cancelling must not join the cancelled one.
  const replacement = cache.request(turn, 'simulation', async () => ({ moves: [{ win: 0.4 }] }));
  await assert.rejects(cancelled, { name: 'AbortError' });
  assert.equal((await replacement).moves[0].win, 0.4);
  assert.equal(cache.errors.size, 0);

  const other = { ...turn, rack: 'EINORST' };
  const running = new AbortController();
  let finish;
  let calls = 0;
  const first = cache.request(other, 'simulation', onStart => {
    calls++;
    onStart();
    return new Promise(resolve => { finish = resolve; });
  }, running.signal);
  await Promise.resolve();
  running.abort();
  const second = cache.request(other, 'simulation', async () => { calls++; return { moves: [] }; });
  finish({ moves: [{ win: 0.6 }] });
  assert.equal(await second, await first);
  assert.equal(calls, 1);
});

test('review results include the recorded action and paired sampling version in their identity', () => {
  const cache = new AnalysisCache();
  const played = { ...turn, action: 'CAT 8G 10', swapped_tiles: null };
  const key = cache.key(played, 'review');
  assert.deepEqual(JSON.parse(key), ['quackle-review-v1', 'review', played.grid, played.rack,
    0, 0, 86, 0, 15, 277, 2, 21, 'CAT 8G 10', '', 'paired-review-v1']);
  assert.notEqual(key, cache.key({ ...played, action: 'CAT 8H 10' }, 'review'));
  assert.notEqual(key, cache.key({ ...played, action: 'cAT 8G 10' }, 'review'));
  assert.notEqual(cache.key({ ...played, action: 'SWAP 0', swapped_tiles: 'AB' }, 'review'),
    cache.key({ ...played, action: 'SWAP 0', swapped_tiles: 'AE' }, 'review'));
  assert.notEqual(key, cache.key(played, 'simulation'));
  assert.equal(cache.seed(played), new AnalysisCache().seed(played));
  assert.notEqual(cache.seed(played), cache.seed({ ...played, other_score: 50 }));
  assert.ok(Number.isInteger(cache.seed(played)) && cache.seed(played) >= 0 && cache.seed(played) <= 0xffffffff);
});

test('preview results use a separate profile without changing full review keys or paired seeds', async () => {
  const cache = new AnalysisCache();
  const played = { ...turn, action: 'CAT 8G 10' };
  const fullKey = JSON.stringify(['quackle-review-v1', 'review', played.grid, played.rack,
    0, 0, 86, 0, 15, 277, 2, 21, 'CAT 8G 10', '', 'paired-review-v1']);
  assert.equal(cache.key(played, 'review'), fullKey);
  assert.deepEqual(JSON.parse(cache.key(played, 'preview')), ['quackle-review-v1', 'preview',
    played.grid, played.rack, 0, 0, 86, 0, 5, 64, 2, 21, 'CAT 8G 10', '', 'paired-review-v1']);
  let fullSeed = 2166136261;
  for (const character of fullKey) fullSeed = Math.imul(fullSeed ^ character.charCodeAt(0), 16777619);
  assert.equal(cache.seed(played), fullSeed >>> 0);
  const custom = new AnalysisCache({}, { previewMoveCount: 3, previewIterations: 32 });
  assert.notEqual(custom.key(played, 'preview'), cache.key(played, 'preview'));
  assert.equal(custom.key(played, 'review'), fullKey);
  assert.equal(custom.seed(played), cache.seed(played));
  const preview = { moves: [{ win: .4 }], iterationsCompleted: 64 };
  const full = { moves: [{ win: .45 }], iterationsCompleted: 277 };
  await cache.request(played, 'preview', async () => preview);
  assert.equal(cache.get(played, 'review'), null);
  await cache.request(played, 'review', async () => full);
  const restored = new AnalysisCache(JSON.parse(JSON.stringify(cache.results)));
  assert.deepEqual(restored.get(played, 'preview'), preview);
  assert.deepEqual(restored.get(played, 'review'), full);
});

test('a viewed turn promotes pending background work without a duplicate computation', async () => {
  const cache = new AnalysisCache();
  let finish;
  let calls = 0;
  const promotions = [];
  const first = cache.request(turn, 'review', () => {
    calls++;
    return new Promise(resolve => { finish = resolve; });
  }, undefined, { priority: 1, onPromote: priority => promotions.push(priority) });
  await Promise.resolve();
  const second = cache.request(turn, 'review', () => { calls++; }, undefined, { priority: 100 });
  const third = cache.request(turn, 'review', () => { calls++; }, undefined, { priority: 2 });
  finish({ moves: [] });
  assert.equal(await first, await second);
  assert.equal(await first, await third);
  assert.equal(calls, 1);
  assert.deepEqual(promotions, [100]);
});


test('rack draws have separate early and full identities without invalidating move estimates', () => {
  const cache = new AnalysisCache();
  const played = { ...turn, action: 'CAT 8G 10', rack_after: 'ERSTUAE' };
  const full = cache.key(played, 'draw');
  assert.deepEqual(JSON.parse(full), ['quackle-rack-draw-v1', 'draw', played.grid, played.rack,
    0, 0, 86, 0, 277, 2, 21, played.action, '', played.rack_after, 'rack-draw-v1']);
  assert.notEqual(full, cache.key(played, 'draw-preview'));
  assert.notEqual(full, cache.key({ ...played, rack_after: 'ERSTU??' }, 'draw'));
  assert.notEqual(full, cache.key({ ...played, swapped_tiles: 'AE' }, 'draw'));
  assert.equal(cache.key(played, 'review'), cache.key({ ...played, rack_after: 'ERSTU??' }, 'review'));
  assert.equal(cache.seed(played), cache.seed({ ...played, rack_after: 'ERSTU??' }));
});

test('endgame policy invalidates only sampled low-bag caches and keeps paired seeds', () => {
  const cache = new AnalysisCache();
  for (const bag_count of [0, 2, 21, 22, 86]) {
    const played = {...turn, bag_count, final_turns_remaining: bag_count === 0 ? 2 : 0,
      action: 'CAT 8G 10', rack_after: 'ERSTUAE'};
    for (const kind of ['review', 'preview', 'draw', 'draw-preview', 'simulation', 'trend']) {
      const identity = JSON.parse(cache.key(played, kind));
      if (identity.at(-1)==='crossplay-low-bag-equity-v2') identity.pop();
      assert.equal(identity.at(-1) === 'crossplay-endgame-v3', bag_count <= 21, kind + ':' + bag_count);
      if (bag_count <= 21) {
        const legacyKey = JSON.stringify(identity.slice(0, -1));
        const saved = new AnalysisCache({[legacyKey]: {legacy: true}});
        assert.equal(saved.get(played, kind), null);
        assert.deepEqual(saved.results[legacyKey], {legacy: true});
      }
    }
    assert.equal(JSON.parse(cache.key(played, 'candidates')).includes('crossplay-endgame-v3'), false);
    const legacyReview = JSON.parse(cache.key(played, 'review'));
    if (legacyReview.at(-1) === 'crossplay-low-bag-equity-v2') legacyReview.pop();
    if (legacyReview.at(-1) === 'crossplay-endgame-v3') legacyReview.pop();
    if (bag_count >= 1 && bag_count <= 21) legacyReview[9] = 277;
    let expectedSeed = 2166136261;
    for (const character of JSON.stringify(legacyReview)) expectedSeed = Math.imul(expectedSeed ^ character.charCodeAt(0), 16777619);
    assert.equal(cache.seed(played), expectedSeed >>> 0);
  }
});

test('non-move opening identities share work and restore without a turn key', async () => {
  const cache = new AnalysisCache();
  const key = JSON.stringify(['quackle-opening-draw-v1', 'ACERSTU', 'AEINORS', 277]);
  let calls = 0;
  const compute = async () => { calls++; return {afterWin: .57}; };
  const [one, two] = await Promise.all([
    cache.requestKey(key, compute), cache.requestKey(key, compute)
  ]);
  assert.equal(calls, 1);
  assert.equal(one, two);
  const restored = new AnalysisCache(cache.results);
  assert.equal(await restored.requestKey(key, compute), one);
  assert.equal(calls, 1);
  assert.equal(cache.get(turn, 'review'), null);
});

test('a viewed turn promotes remaining batches after expanded analysis has started', async () => {
  const cache = new AnalysisCache();
  let finish;
  const priorities = [];
  let calls = 0;
  const first = cache.request(turn, 'review', onStart => {
    calls++;
    onStart();
    return new Promise(resolve => { finish = resolve; });
  }, undefined, {priority: 1, onPromote: priority => priorities.push(priority)});
  await Promise.resolve();
  const viewed = cache.request(turn, 'review', () => {
    calls++;
    throw new Error('The viewed turn must share the existing job.');
  }, undefined, {priority: 100});
  await Promise.resolve();
  finish({moves: []});
  assert.equal(await viewed, await first);
  assert.equal(calls, 1);
  assert.deepEqual(priorities, [100]);
});


test('v3 terminal policy rejects v2 cached results without changing paired worlds', () => {
  const turn = {grid: Array(15).fill('.'.repeat(15)),rack:'AEIRST?',mover_score:110,
    other_score:100,bag_count:5,final_turns_remaining:0,action:'AT 8G 4'};
  const current = new AnalysisCache();
  const key = current.key(turn,'review');
  const oldKey = key.replace('crossplay-endgame-v3','crossplay-endgame-v2')
    .replace('crossplay-low-bag-equity-v2','crossplay-low-bag-equity-v1');
  const restored = new AnalysisCache({[oldKey]:{simulated:true}});
  assert.equal(restored.get(turn,'review'),null);
  assert.deepEqual(restored.results[oldKey],{simulated:true});
  assert.equal(restored.seed(turn),current.seed(turn));
});


test('full late-game profiles select paired sample counts without changing legacy seeds', () => {
  const Bag=require('./bag-emptying-analysis.js');
  for(const bag of [0,7,8,21,22,86]){
    const t={...turn,bag_count:bag,final_turns_remaining:bag===0?2:0};
    const c=new AnalysisCache(), n=bag>=1&&bag<=21?192:277;
    assert.equal(Bag.fullIterations(bag),n);
    assert.equal(JSON.parse(c.key(t,'review'))[9],n);
    assert.equal(JSON.parse(c.key(t,'draw'))[8],n);
    assert.equal(JSON.parse(c.key(t,'preview'))[9],64);
    const identity=JSON.parse(c.key(t,'review'));
    if(identity.at(-1)===Bag.POLICY)identity.pop();
    if(identity.at(-1)==='crossplay-endgame-v3')identity.pop();
    identity[9]=277;
    let seed=2166136261;for(const ch of JSON.stringify(identity))seed=Math.imul(seed^ch.charCodeAt(0),16777619);
    assert.equal(c.seed(t),seed>>>0);
    assert.equal(JSON.parse(new AnalysisCache({}, {iterations:32}).key(t,'review'))[9],32);
  }
});


test('device worker budgets select matching full profiles and preserve paired worlds', () => {
  const fs=require('fs'),vm=require('vm');
  for(const workers of [1,2,3]){
    const context={navigator:{hardwareConcurrency:workers+1,deviceMemory:8},matchMedia:()=>({matches:false})};
    vm.createContext(context);
    for(const name of ['bag-emptying-analysis.js','quackle-pool.js','analysis-cache.js'])
      vm.runInContext(fs.readFileSync(name,'utf8'),context);
    assert.equal(vm.runInContext('QuackleWorkerPool.browserSize()',context),workers);
    for(const bag of [0,1,7,8,21,22]){
      context.t={...turn,bag_count:bag,final_turns_remaining:bag===0?2:0};
      const n=bag>=1&&bag<=7?192:bag>=8&&bag<=21?(workers===1?96:192):277;
      const identity=JSON.parse(vm.runInContext('new AnalysisCache().key(t,"review")',context));
      assert.equal(identity[9],n,workers+':'+bag);
      assert.equal(JSON.parse(vm.runInContext('new AnalysisCache().key(t,"draw")',context))[8],n);
      assert.equal(vm.runInContext('new AnalysisCache().seed(t)',context),new AnalysisCache().seed(context.t));
      assert.equal(vm.runInContext('CrossplayBagEmptying.fullIterations(t.bag_count,32)',context),32);
    }
  }
});


test('authoritative lowbag equity invalidates ordinary static caches only in its phase', () => {
 const cache=new AnalysisCache();
 for(const bag of [0,1,7,8,21,22]){
   const t={...turn,bag_count:bag,final_turns_remaining:bag===0?2:0};
   const identity=JSON.parse(cache.key(t,'candidates'));
   const changed=bag>=1&&bag<=7;
   assert.equal(identity.at(-1)==='crossplay-equity-v1',changed);
   if(changed){
     const old=JSON.stringify(identity.slice(0,-1));
     assert.equal(new AnalysisCache({[old]:{moves:[{equity:99}]}}).get(t,'candidates'),null);
   }else assert.equal(identity.length,12);
 }
});
