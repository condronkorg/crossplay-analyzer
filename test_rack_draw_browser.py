"""Draw estimates through the production cache, queue, and persistence paths."""
import unittest

import test_review_browser as review_browser


class RackDrawBrowserTest(unittest.TestCase):
    setUpClass = classmethod(review_browser.ReviewBrowserTest.setUpClass.__func__)
    setUp = review_browser.ReviewBrowserTest.setUp
    tearDown = review_browser.ReviewBrowserTest.tearDown
    route_request = review_browser.ReviewBrowserTest.route_request
    connect = review_browser.ReviewBrowserTest.connect
    open_game = review_browser.ReviewBrowserTest.open_game
    start_review = review_browser.ReviewBrowserTest.start_review
    install_review_worker = review_browser.ReviewBrowserTest.install_review_worker

    def prepare_draw(self):
        self.install_review_worker()
        self.open_game()
        self.page.evaluate('''() => {
          const turn = currentReview.turns[0];
          Object.assign(turn, {rack: 'CATERS?',rack_after: 'ERS?AEI',action: 'CAT 8G 10',
            bag_count: 86,final_turns_remaining: 0,grid: Array(15).fill('.'.repeat(15))});
          window.drawCalls = [];
          const request = workerRequest;
          workerRequest = (type,payload,options) => {
            if (type !== 'simulateDraw') return request(type,payload,options);
            drawCalls.push(payload.iterations);
            return Promise.resolve({simulated:true,samplingVersion:'rack-draw-v1',seed:payload.seed,
              rackAfter:payload.rackAfter,beforeWin:.4,afterWin:.55,drawCount:3,
              iterationsRequested:payload.iterations,iterationsCompleted:payload.iterations,
              bagCount:86,finalTurnsRemaining:0,playToEnd:false,simulationMode:'2-ply',winModel:'bogowin',
              plies:2,selected:payload.played});
          };
        }''')

    def test_draw_profiles_save_resume_and_add_timeline_points(self):
        self.prepare_draw()
        result = self.page.evaluate('''async () => {
          const context = contextFor(currentReview);
          await analyzeReviewTurn(context,0,'preview');
          const early = structuredClone(context.quality.points[0]);
          await analyzeReviewTurn(context,0,'review');
          const full = structuredClone(context.quality.points[0]);
          const saved = {version:ANALYSIS_VERSION,turn_analyses:context.cache.results,
            move_quality:{...context.quality,state:'done'}};
          const restored = makeReviewContext({...currentReview,stored_analysis:saved},context.owner);
          return {early,full,calls:drawCalls,phases:context.trend.points.filter(p=>p.x===0).map(p=>p.phase),
            restored:restored.quality.points[0].draw,
            keys:Object.keys(context.cache.results).map(key=>JSON.parse(key)[1])};
        }''')
        self.assertTrue(result['early']['draw']['provisional'])
        self.assertFalse(result['full']['draw']['provisional'])
        self.assertAlmostEqual(result['full']['draw']['luck'], 15)
        self.assertEqual(result['calls'], [64, 277])
        self.assertEqual(result['phases'], ['before-draw', 'after-draw'])
        self.assertEqual(result['restored'], result['full']['draw'])
        self.assertCountEqual(result['keys'], ['preview', 'review', 'draw-preview', 'draw'])

    def test_legacy_full_moves_get_draws_without_repeating_move_simulation(self):
        self.prepare_draw()
        result = self.page.evaluate('''async () => {
          const context = contextFor(currentReview);
          await analyzeReviewTurn(context,0,'review');
          const saved = {version:ANALYSIS_VERSION,turn_analyses:{...context.cache.results},
            move_quality:{...context.quality,state:'done',points:context.quality.points.map(p=>{const q={...p};delete q.draw;return q;})}};
          delete saved.turn_analyses[context.cache.key(currentReview.turns[0],'draw')];
          const restored=makeReviewContext({...currentReview,stored_analysis:saved},context.owner);
          reviewQueue.contexts.set(restored.review.id,restored);
          const request=workerRequest;window.moveCalls=[];drawCalls.length=0;
          workerRequest=(type,payload,options)=>{
            if(type==='simulateReview')moveCalls.push(type);
            return request(type,payload,options);
          };
          await analyzeReviewTurn(restored,0,'preview');
          await analyzeReviewTurn(restored,0,'review');
          return {moves:moveCalls,draws:drawCalls,draw:restored.quality.points[0].draw};
        }''')
        self.assertEqual(result['moves'], [])
        self.assertEqual(result['draws'], [64, 277])
        self.assertFalse(result['draw']['provisional'])

    def test_draw_failure_preserves_move_grade_and_is_retryable(self):
        self.prepare_draw()
        result = self.page.evaluate('''async () => {
          const context=contextFor(currentReview),request=workerRequest;
          workerRequest=(type,payload,options)=>type==='simulateDraw'?Promise.reject(new Error('Draw failed')):request(type,payload,options);
          await analyzeReviewTurn(context,0,'review');
          const failed=structuredClone(context.quality.points[0]);
          workerRequest=request;
          await analyzeReviewTurn(context,0,'review');
          return {failed,recovered:context.quality.points[0]};
        }''')
        self.assertNotEqual(result['failed']['category'], 'unrated')
        self.assertTrue(result['failed']['draw']['retryable'])
        self.assertFalse(result['recovered']['draw'].get('retryable', False))
        self.assertAlmostEqual(result['recovered']['draw']['luck'], 15)

    def test_late_draw_preview_does_not_replace_full_draw(self):
        self.prepare_draw()
        result=self.page.evaluate('''async () => {
          const context=contextFor(currentReview),request=workerRequest;let release;
          workerRequest=(type,payload,options)=>type==='simulateDraw'&&payload.iterations===64
            ?new Promise(resolve=>{release=()=>resolve(request(type,payload,options));})
            :request(type,payload,options);
          const pending=analyzeReviewTurn(context,0,'preview');
          while(!release)await new Promise(resolve=>setTimeout(resolve,0));
          await analyzeReviewTurn(context,0,'review');
          release();await pending;
          return context.quality.points[0];
        }''')
        self.assertEqual(result['samples'], 277)
        self.assertEqual(result['draw']['samples'], 277)
        self.assertFalse(result['draw']['provisional'])

    def test_pausing_before_draw_does_not_start_another_worker_request(self):
        self.prepare_draw()
        result=self.page.evaluate('''async () => {
          const context=contextFor(currentReview),request=workerRequest;
          workerRequest=async(type,payload,options)=>{
            const result=await request(type,payload,options);
            if(type==='simulateReview')pauseReviewAnalysis(currentReview);
            return result;
          };
          await analyzeReviewTurn(context,0,'preview');
          return {calls:drawCalls,state:context.quality.state};
        }''')
        self.assertEqual(result, {'calls': [], 'state': 'stopped'})

    def test_real_worker_draw_matches_recorded_move_and_validates_output(self):
        self.open_game()
        result=self.page.evaluate("""async () => {
          const payload={gridJson:JSON.stringify(Array(15).fill('.'.repeat(15))),rack:'AEIRST?',
            rackAfter:'AEEIRS?',playerScore:30,opponentScore:25,bagCount:86,finalTurnsRemaining:0,
            numMoves:1,iterations:3,seed:123456789,played:{action:'place',position:'8G',tiles:'AT'}};
          const draw=await workerRequest('simulateDraw',payload);
          const move=await workerRequest('simulateReview',payload);
          return {draw,move:move.selected.win};
        }""")
        self.assertEqual(result['draw']['beforeWin'],result['move'])
        self.assertEqual(result['draw']['drawCount'],2)
        self.assertEqual(result['draw']['iterationsCompleted'],3)

    def test_late_draw_preview_preserves_failed_full_draw_for_cached_move(self):
        self.prepare_draw()
        result=self.page.evaluate('''async () => {
          const context=contextFor(currentReview),request=workerRequest;
          await ensurePositionAnalysis(currentReview,currentReview.turns[0],'review');
          let release;
          workerRequest=(type,payload,options)=>type!=='simulateDraw'?request(type,payload,options)
            :payload.iterations===64?new Promise(resolve=>{release=()=>resolve(request(type,payload,options));})
            :Promise.reject(new Error('Full draw failed'));
          const pending=analyzeReviewTurn(context,0,'preview');
          while(!release)await new Promise(resolve=>setTimeout(resolve,0));
          await analyzeReviewTurn(context,0,'review');
          await finishReviewAnalysis(context);
          release();await pending;
          const failed=structuredClone(context.quality.points[0]);
          const state=context.quality.state;
          workerRequest=request;
          await analyzeReviewTurn(context,0,'review');
          return {failed,state,recovered:context.quality.points[0]};
        }''')
        self.assertEqual(result['state'],'done')
        self.assertTrue(result['failed']['draw'].get('retryable'))
        self.assertFalse(result['failed']['draw'].get('provisional',False))
        self.assertEqual(result['recovered']['draw']['samples'],277)
        self.assertFalse(result['recovered']['draw'].get('retryable',False))
