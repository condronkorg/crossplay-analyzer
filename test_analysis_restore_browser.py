"""Saved results and known failures survive normal resume. Synthetic games only."""
import re
import unittest

import test_review_browser as review_browser


class AnalysisRestoreBrowserTest(unittest.TestCase):
    setUpClass = classmethod(review_browser.ReviewBrowserTest.setUpClass.__func__)
    setUp = review_browser.ReviewBrowserTest.setUp
    tearDown = review_browser.ReviewBrowserTest.tearDown
    route_request = review_browser.ReviewBrowserTest.route_request
    connect = review_browser.ReviewBrowserTest.connect
    open_game = review_browser.ReviewBrowserTest.open_game
    start_review = review_browser.ReviewBrowserTest.start_review
    install_review_worker = review_browser.ReviewBrowserTest.install_review_worker
    use_saved_analysis = review_browser.ReviewBrowserTest.use_saved_analysis

    def prepare_saved_review(self):
        self.install_review_worker()
        self.open_game()
        self.page.evaluate('''async () => {
          const context = contextFor(currentReview);
          reviewQueue.concurrency = 0;
          await analyzeOpeningDraw(context, 'review');
          for (let index = 0; index < currentReview.turns.length; index++) {
            await analyzeReviewTurn(context, index, 'review');
          }
          await finishReviewAnalysis(context);
          await flushAnalysisSave(currentReview, context.cache);
          window.restoreSaved = structuredClone({...currentReview, stored_analysis:{
            version:ANALYSIS_VERSION, turn_analyses:context.cache.results,
            move_quality:{...context.quality, state:'done'}, win_trend:context.trend}});
          window.restoreCalls = [];
          const request = workerRequest;
          workerRequest = (type, payload, options) => {
            const index = currentReview.turns.findIndex(turn => turn.rack === payload.rack &&
              JSON.stringify(turn.grid) === payload.gridJson &&
              (Number(turn.mover_score) || 0) === payload.playerScore);
            restoreCalls.push({type, index, iterations:payload.iterations});
            return request(type, payload, options);
          };
          window.restorePreview = (review, index, kind='review') => {
            const cache = new AnalysisCache({}, POSITION_PARAMETERS), turn = review.turns[index];
            const full = cache.key(turn, kind), preview = cache.key(turn, kind === 'draw' ? 'draw-preview' : 'preview');
            const result = structuredClone(review.stored_analysis.turn_analyses[full]);
            result.iterationsRequested = result.iterationsCompleted = 64;
            if (kind === 'review') {
              result.totalPlayaheads = 64 * result.moves.length;
              for (const move of [...result.moves, result.selected, result.best]) {
                move.samples = 64;
                if (result.playToEnd) move.terminalSamples = 64;
              }
            }
            review.stored_analysis.turn_analyses[preview] = result;
            return full;
          };
          window.restoreMoveFailure = (review, index, {keyed=true, preview=true, removeFull=true}={}) => {
            const cache = new AnalysisCache({}, POSITION_PARAMETERS), turn = review.turns[index];
            const key = cache.key(turn, 'review');
            if (preview) restorePreview(review, index);
            if (removeFull) delete review.stored_analysis.turn_analyses[key];
            const point = {...makeUnratedPoint(index, turn, 'Synthetic full analysis timeout.'),
              retryable:true, provisional:false, ...(keyed ? {analysisKey:key} : {})};
            review.stored_analysis.move_quality.points = review.stored_analysis.move_quality.points
              .filter(value => value.turnIndex !== index).concat(point).sort((a,b) => a.turnIndex-b.turnIndex);
            return key;
          };
          window.restoreInstall = review => {
            reviewQueue?.close(); reviewQueue = null;
            const queue = getReviewQueue(); queue.concurrency = 0;
            const context = makeReviewContext(review, analysisOwner());
            queue.contexts.set(review.id, context); queue.ids=[review.id]; queue.activeId=review.id;
            currentReview=review; activeGameId=review.id; positionCache=context.cache;
            moveQualityResults=context.quality; trendResults=context.trend;
            restoreCalls=[];
            return context;
          };
          window.restoreDrain = async () => {
            while (reviewQueue.running || reviewQueue.loading.size) await new Promise(resolve => setTimeout(resolve, 0));
            await Promise.resolve();
          };
        }''')

    def test_partial_saved_review_restores_completed_and_known_failed_turn_states(self):
        self.prepare_saved_review()
        result = self.page.evaluate('''() => {
          const review=structuredClone(restoreSaved);
          restoreMoveFailure(review,4); restoreMoveFailure(review,5);
          const context=restoreInstall(review);
          return {states:context.states, opening:context.openingState,
            failed:context.quality.points.filter(point=>point.retryable).map(point=>point.turnIndex)};
        }''')
        self.assertEqual(result['states'], ['done'] * 4 + ['error', 'error'])
        self.assertEqual(result['opening'], 'done')
        self.assertEqual(result['failed'], [4, 5])

    def test_resume_does_not_restart_completed_or_known_failed_work(self):
        self.prepare_saved_review()
        result = self.page.evaluate('''async () => {
          const review=structuredClone(restoreSaved);
          restoreMoveFailure(review,4); restoreMoveFailure(review,5);
          const context=restoreInstall(review);
          reviewQueue.concurrency=1;
          await window.automaticAnalysis(review); await restoreDrain();
          return {calls:restoreCalls, states:context.states,
            failed:context.quality.points.filter(point=>point.retryable).map(point=>point.turnIndex)};
        }''')
        self.assertEqual(result['calls'], [])
        self.assertEqual(result['states'], ['done'] * 4 + ['error', 'error'])
        self.assertEqual(result['failed'], [4, 5])

    def test_current_raw_full_result_replaces_stale_failure_without_engine_work(self):
        self.prepare_saved_review()
        result = self.page.evaluate('''async () => {
          const review=structuredClone(restoreSaved);
          restoreMoveFailure(review,4,{removeFull:false});
          const context=restoreInstall(review);
          const initialStates=[...context.states];
          const initialPoint=context.quality.points.find(value=>value.turnIndex===4);
          reviewQueue.concurrency=1;
          await window.automaticAnalysis(review); await restoreDrain();
          const point=context.quality.points.find(value=>value.turnIndex===4);
          return {calls:restoreCalls,states:context.states,point,initialStates,initialPoint};
        }''')
        self.assertEqual(result['calls'], [])
        self.assertEqual(result['initialStates'], ['done'] * 6)
        self.assertIsInstance(result['initialPoint'].get('playedWin'), (int, float))
        self.assertEqual(result['states'], ['done'] * 6)
        self.assertIsInstance(result['point'].get('playedWin'), (int, float))
        self.assertFalse(result['point'].get('retryable', False))

    def test_explicit_retry_only_runs_failed_moves(self):
        self.prepare_saved_review()
        result = self.page.evaluate('''async () => {
          const review=structuredClone(restoreSaved);
          restoreMoveFailure(review,4); restoreMoveFailure(review,5);
          const context=restoreInstall(review);
          const before=[...context.states];
          reviewQueue.concurrency=1; reviewQueue.retry(); await restoreDrain();
          return {before,calls:restoreCalls,states:context.states};
        }''')
        self.assertEqual(result['before'], ['done'] * 4 + ['error', 'error'])
        self.assertEqual(result['states'], ['done'] * 6)
        self.assertEqual(sorted(result['calls'], key=lambda call: call['index']), [
            {'type': 'simulateReview', 'index': 4, 'iterations': 277},
            {'type': 'simulateReview', 'index': 5, 'iterations': 277},
        ])

    def test_explicit_rebuild_retries_failures_and_reuses_successful_engine_results(self):
        self.prepare_saved_review()
        result = self.page.evaluate('''async () => {
          const review=structuredClone(restoreSaved);
          restoreMoveFailure(review,4); restoreMoveFailure(review,5);
          const context=restoreInstall(review);
          reviewQueue.concurrency=1;
          await startMoveQuality(review); await restoreDrain();
          return {calls:restoreCalls,states:context.states};
        }''')
        self.assertEqual(result['states'], ['done'] * 6)
        self.assertEqual(sorted(result['calls'], key=lambda call: call['index']), [
            {'type': 'simulateReview', 'index': 4, 'iterations': 277},
            {'type': 'simulateReview', 'index': 5, 'iterations': 277},
        ])

    def test_failure_identity_and_valid_preview_gate_legacy_failures(self):
        self.prepare_saved_review()
        result = self.page.evaluate('''() => {
          const states=[];
          for (const options of [
            {keyed:false,preview:true}, {keyed:false,preview:false}, {keyed:true,preview:false}
          ]) {
            const review=structuredClone(restoreSaved); restoreMoveFailure(review,4,options);
            states.push(restoreInstall(review).states[4]);
          }
          const stale=structuredClone(restoreSaved); restoreMoveFailure(stale,4);
          stale.turns[4].mover_score+=1;
          states.push(restoreInstall(stale).states[4]);
          return states;
        }''')
        self.assertEqual(result, ['error', 'pending-preview', 'error', 'pending-preview'])

    def test_endgame_failure_requires_current_policy_identity(self):
        self.prepare_saved_review()
        result = self.page.evaluate('''() => {
          const review=structuredClone(restoreSaved), index=4;
          const grid=['..........G....','.......A.NOUN.Y','.......L...TAME',
            '......YEAH...E.','.........ab.yA.','......LOB.OWED.',
            '..PROG..I.TEN.F','....D..JOGS...I','...LASSI.O..RUN',
            '.......V.R.MI..','...C...E..HIPS.','...A.BASE.A.E..',
            '...R.O..D.V....','..RENT.LITER...','.....H..T......'];
          const turn={...review.turns[index],grid,grid_after:[...grid],rack:'WEAFTUO',
            rack_after:'WEAFTUO',action:'PASS 0',score:0,mover_score:110,other_score:100,
            bag_count:2,final_turns_remaining:0};
          review.turns[index]=turn;
          const cache=new AnalysisCache({},POSITION_PARAMETERS), key=cache.key(turn,'review');
          const move={action:'pass',position:'',tiles:'',score:0,leave:turn.rack,
            equity:0,staticEquity:0,win:.5,samples:277,terminalSamples:277};
          review.stored_analysis.turn_analyses[key]={simulated:true,endgamePolicy:'crossplay-endgame-v1',
            samplingVersion:'paired-review-v1',seed:cache.seed(turn),moves:[move],selected:move,best:move,
            staticMoves:[move],bestStaticEquity:0,candidateCount:1,totalPlayaheads:277,
            plies:-1,playToEnd:true,simulationMode:'play-to-end',winModel:'terminal',
            bagCount:2,finalTurnsRemaining:0,iterationsRequested:277,iterationsCompleted:277};
          restoreMoveFailure(review,index);
          const current=restoreInstall(review), currentPoint=current.quality.points.find(point=>point.turnIndex===index);
          const changed=structuredClone(review);
          changed.stored_analysis.move_quality.points.find(point=>point.turnIndex===index).analysisKey=
            key.replace('crossplay-endgame-v1','old-endgame-policy');
          return {states:current.states,point:currentPoint,changed:restoreInstall(changed).states};
        }''')
        self.assertEqual(result['states'], ['done'] * 4 + ['error', 'done'])
        self.assertTrue(result['point']['retryable'])
        self.assertEqual(result['changed'], ['done'] * 4 + ['pending-preview', 'done'])

    def test_new_full_move_failure_records_the_current_analysis_identity(self):
        self.prepare_saved_review()
        result = self.page.evaluate('''async () => {
          const review=structuredClone(restoreSaved), index=4;
          const fullKey=restorePreview(review,index);
          delete review.stored_analysis.turn_analyses[fullKey];
          review.stored_analysis.move_quality.points=review.stored_analysis.move_quality.points.filter(point=>point.turnIndex!==index);
          const context=restoreInstall(review), request=workerRequest;
          workerRequest=(type,payload,options)=>type==='simulateReview'
            ?Promise.reject(new Error('Synthetic new full timeout.')):request(type,payload,options);
          await analyzeReviewTurn(context,index,'review');
          const point=context.quality.points.find(value=>value.turnIndex===index);
          return {key:fullKey,point};
        }''')
        self.assertTrue(result['point']['retryable'])
        self.assertFalse(result['point']['provisional'])
        self.assertEqual(result['point'].get('analysisKey'), result['key'])

    def test_new_draw_and_opening_failures_record_their_full_analysis_keys(self):
        self.prepare_saved_review()
        result = self.page.evaluate('''async () => {
          const review=structuredClone(restoreSaved), cache=new AnalysisCache({},POSITION_PARAMETERS);
          const drawKey=cache.key(review.turns[0],'draw');
          delete review.stored_analysis.turn_analyses[drawKey];
          let context=restoreInstall(review); const request=workerRequest;
          workerRequest=(type,payload,options)=>['simulateDraw','simulateOpening'].includes(type)
            ?Promise.reject(new Error('Synthetic full draw timeout.')):request(type,payload,options);
          await analyzeReviewTurn(context,0,'review');
          const draw=context.quality.points.find(point=>point.turnIndex===0).draw;
          const openingReview=structuredClone(restoreSaved), input=CrossplayOpening.describe(openingReview);
          const openingKey=CrossplayOpening.key(input,277);
          delete openingReview.stored_analysis.turn_analyses[openingKey];
          context=restoreInstall(openingReview);
          try { await analyzeOpeningDraw(context,'review'); } catch (_) {}
          return {drawKey,draw,openingKey,opening:context.quality.opening};
        }''')
        self.assertTrue(result['draw']['retryable'])
        self.assertEqual(result['draw'].get('analysisKey'), result['drawKey'])
        self.assertTrue(result['opening']['retryable'])
        self.assertEqual(result['opening'].get('analysisKey'), result['openingKey'])

    def test_only_a_new_turn_is_queued_when_older_turns_are_saved(self):
        self.prepare_saved_review()
        result = self.page.evaluate('''async () => {
          const review=structuredClone(restoreSaved), last=review.turns.at(-1);
          review.turns.push({...last,index:6,action:'PASS 0',score:0,rack_after:last.rack,
            mover_score:last.mover_score+100,grid_after:[...last.grid]});
          review.turn_count=review.turns.length;
          const context=restoreInstall(review), before=[...context.states];
          reviewQueue.concurrency=1; await window.automaticAnalysis(review); await restoreDrain();
          return {before,states:context.states,calls:restoreCalls};
        }''')
        self.assertEqual(result['before'], ['done'] * 6 + ['pending-preview'])
        self.assertEqual(result['states'], ['done'] * 7)
        self.assertEqual({call['index'] for call in result['calls']}, {6})
        self.assertTrue(any(call['iterations'] == 277 for call in result['calls']))

    def test_draw_failure_stays_blocked_until_retry_and_only_redraw_is_computed(self):
        self.prepare_saved_review()
        result = self.page.evaluate('''async () => {
          const review=structuredClone(restoreSaved), turn=review.turns[0];
          const key=restorePreview(review,0,'draw');
          delete review.stored_analysis.turn_analyses[key];
          const point=review.stored_analysis.move_quality.points.find(value=>value.turnIndex===0);
          point.draw={retryable:true,provisional:false,reason:'Synthetic full draw timeout.',analysisKey:key};
          const context=restoreInstall(review);
          const before={states:[...context.states],draw:{...context.quality.points[0].draw}};
          reviewQueue.concurrency=1; await window.automaticAnalysis(review); await restoreDrain();
          const automatic=[...restoreCalls];
          reviewQueue.retry(); await restoreDrain();
          return {before,automatic,calls:restoreCalls,states:context.states,
            draw:context.quality.points[0].draw};
        }''')
        self.assertEqual(result['before']['states'], ['error'] + ['done'] * 5)
        self.assertTrue(result['before']['draw']['retryable'])
        self.assertIn('Synthetic full draw timeout', result['before']['draw']['reason'])
        self.assertEqual(result['automatic'], [])
        self.assertEqual(result['calls'], [{'type': 'simulateDraw', 'index': 0, 'iterations': 277}])
        self.assertEqual(result['states'], ['done'] * 6)
        self.assertFalse(result['draw'].get('retryable', False))
        self.assertEqual(result['draw']['samples'], 277)

    def test_opening_failure_keeps_saved_moves_done_until_explicit_opening_retry(self):
        self.prepare_saved_review()
        result = self.page.evaluate('''async () => {
          const review=structuredClone(restoreSaved), input=CrossplayOpening.describe(review);
          const fullKey=CrossplayOpening.key(input,277), previewKey=CrossplayOpening.key(input,64);
          const early=structuredClone(review.stored_analysis.turn_analyses[fullKey]);
          early.iterationsRequested=early.iterationsCompleted=64;
          review.stored_analysis.turn_analyses[previewKey]=early;
          delete review.stored_analysis.turn_analyses[fullKey];
          review.stored_analysis.move_quality.opening={unavailable:true,retryable:true,
            reason:'Synthetic full opening timeout.',analysisKey:fullKey};
          const context=restoreInstall(review);
          const before={states:[...context.states],opening:context.openingState,
            point:{...context.quality.opening}};
          reviewQueue.concurrency=1; await window.automaticAnalysis(review); await restoreDrain();
          const automatic=[...restoreCalls];
          reviewQueue.retry(); await restoreDrain();
          return {before,automatic,calls:restoreCalls,states:context.states,
            opening:context.openingState,point:context.quality.opening};
        }''')
        self.assertEqual(result['before']['states'], ['done'] * 6)
        self.assertEqual(result['before']['opening'], 'error')
        self.assertTrue(result['before']['point']['retryable'])
        self.assertEqual(result['automatic'], [])
        self.assertEqual(result['calls'], [{'type': 'simulateOpening', 'index': -1, 'iterations': 277}])
        self.assertEqual(result['states'], ['done'] * 6)
        self.assertEqual(result['opening'], 'done')
        self.assertEqual(result['point']['samples'], 277)

    def test_fully_saved_review_reloads_through_startup_without_worker_calls(self):
        self.prepare_saved_review()
        saved = self.page.evaluate('restoreSaved.stored_analysis')
        summary = self.page.evaluate('games.find(game=>game.id===currentReview.id)')
        self.use_saved_analysis(saved)
        self.page.route(re.compile(r'/api/crossplay/games(?:\?.*)?$'),
                        lambda route: route.fulfill(json={'games': [summary], 'next_cursor': None}))
        self.context.add_init_script('''window.restoreEngineCalls=[];
          const post=Worker.prototype.postMessage;
          Worker.prototype.postMessage=function(data,...rest){
            restoreEngineCalls.push(data.type);return post.call(this,data,...rest);};''')
        for _ in range(2):
            self.page.reload()
            self.page.wait_for_function("currentReview?.id==='mockgame01' && reviewQueue?.running===0 && "
                                        "contextFor(currentReview)?.states.every(state=>state==='done')")
            self.assertEqual(self.page.evaluate('restoreEngineCalls'), [])
            self.assertEqual(self.page.evaluate('contextFor(currentReview).openingState'), 'done')


    def test_old_completion_cannot_finish_a_rebuilt_review_after_frequency_wait(self):
        self.prepare_saved_review()
        result = self.page.evaluate("""async () => {
          const review = structuredClone(restoreSaved);
          const context = restoreInstall(review);
          delete context.quality.points[0].opportunity;
          const finishingQuality = context.quality;
          let releaseFrequency;
          wordFrequencyPromise = new Promise(resolve => {
            releaseFrequency = () => resolve(new Map());
          });
          const summaries = [], request = api;
          api = async (path, options) => {
            if (path.endsWith('/analysis')) {
              const body = JSON.parse(options.body);
              if (body.move_quality) summaries.push(structuredClone(body.move_quality));
            }
            return request(path, options);
          };
          const pending = finishReviewAnalysis(context);
          await Promise.resolve();
          await startMoveQuality(review);
          const rebuiltQuality = context.quality;
          releaseFrequency();
          await pending;
          return {replaced:finishingQuality !== rebuiltQuality,
            retained:context.quality === rebuiltQuality, state:context.quality.state,
            points:context.quality.points.length, states:context.states, summaries};
        }""")
        self.assertTrue(result['replaced'])
        self.assertTrue(result['retained'])
        self.assertEqual(result['points'], 0)
        self.assertEqual(result['state'], 'running')
        self.assertEqual(result['states'], ['pending-preview'] * 6)
        self.assertEqual(result['summaries'], [])


if __name__ == '__main__':
    unittest.main()
