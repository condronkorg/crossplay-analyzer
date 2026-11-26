"""Saved results and known failures survive normal resume. Synthetic games only."""
import re
import unittest

from playwright.sync_api import expect

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
    open_statistics = review_browser.ReviewBrowserTest.open_statistics

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
          window.restoreMoveFailure = (review, index, {keyed=true, preview=true, removeFull=true, executionPolicy}={}) => {
            const cache = new AnalysisCache({}, POSITION_PARAMETERS), turn = review.turns[index];
            const key = cache.key(turn, 'review');
            if (preview) restorePreview(review, index);
            if (removeFull) delete review.stored_analysis.turn_analyses[key];
            const point = {...makeUnratedPoint(index, turn, 'Synthetic full analysis timeout.'),
              retryable:true, provisional:false, ...(keyed ? {analysisKey:key} : {}), ...(executionPolicy ? {executionPolicy} : {})};
            review.stored_analysis.move_quality.points = review.stored_analysis.move_quality.points
              .filter(value => value.turnIndex !== index).concat(point).sort((a,b) => a.turnIndex-b.turnIndex);
            return key;
          };
          window.restoreTerminal = (review, index) => {
            const cache = new AnalysisCache({}, POSITION_PARAMETERS), turn = review.turns[index];
            const move = structuredClone(review.stored_analysis.turn_analyses[cache.key(turn, 'review')]);
            const draw = structuredClone(review.stored_analysis.turn_analyses[cache.key(turn, 'draw')]);
            turn.bag_count = 21;
            const available=Object.entries(CrossplayLuck.TILE_COUNTS)
              .flatMap(([tile, count]) => Array(count).fill(tile));
            const boardCount=available.length-turn.rack.length-21-7;
            const refill=CrossplayReview.rackDraw({...turn,bag_count:available.length-turn.rack.length-7});
            for (const tile of turn.rack + refill.drawn) available.splice(available.indexOf(tile),1);
            const boardTiles=available.slice(0,boardCount).map(tile=>tile==='?'?'a':tile).join('');
            turn.grid=turn.grid.map((row,i)=>i<5?boardTiles.slice(i*15,(i+1)*15).padEnd(15,'.'):row);
            const samples=CrossplayBagEmptying.fullIterations(21);
            move.iterationsRequested=move.iterationsCompleted=samples;
            move.totalPlayaheads=samples*move.moves.length;
            if(draw)draw.iterationsRequested=draw.iterationsCompleted=samples;
            const point=review.stored_analysis.move_quality.points.find(value=>value.turnIndex===index);
            point.samples=samples;
            if(point.draw)point.draw.samples=samples;
            const seed = cache.seed(turn);
            const terminal = {seed, bagCount:21, plies:-1, playToEnd:true,
              winModel:'terminal', simulationMode:'play-to-end', endgamePolicy:'crossplay-endgame-v3'};
            for (const candidate of [...move.moves, move.selected, move.best]) candidate.samples=candidate.terminalSamples=samples;
            review.stored_analysis.turn_analyses[cache.key(turn, 'review')] = Object.assign(move, terminal);
            if (draw) review.stored_analysis.turn_analyses[cache.key(turn, 'draw')] = Object.assign(draw, terminal);
            review.stored_analysis.move_quality.points.find(point => point.turnIndex===index).endgamePolicy = terminal.endgamePolicy;
            return {move, draw};
          };
          window.restoreInstall = (review, shared=false) => {
            reviewQueue?.close(); reviewQueue = null;
            const queue = getReviewQueue(); queue.concurrency = 0;
            const context = makeReviewContext(review, analysisOwner(), shared);
            viewingSharedGame = shared;
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

    def test_reset_preserves_current_terminal_failure_in_another_game(self):
        self.prepare_saved_review()
        result = self.page.evaluate('''async () => {
          const review=structuredClone(restoreSaved), index=0;
          restoreTerminal(review,index);
          restoreMoveFailure(review,index,{executionPolicy:'bounded-terminal-v1'});
          const other=makeReviewContext(review,analysisOwner());
          other.review.id='mockgame02';
          reviewQueue.contexts.set(other.review.id,other);
          const queue=getReviewQueue;
          getReviewQueue=()=>{const current=queue();current.concurrency=0;return current;};
          const cleared=await restartEntireGameAnalysis(currentReview);
          const restored=reviewQueue.contexts.get(other.review.id);
          return {cleared,state:restored.states[index],quality:restored.quality.state,
            reason:restored.quality.points.find(point=>point.turnIndex===index).reason};
        }''')
        self.assertTrue(result['cleared'])
        self.assertEqual(result['state'], 'error')
        self.assertEqual(result['quality'], 'error')
        self.assertIn('Synthetic full analysis timeout', result['reason'])

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
          review.stored_analysis.turn_analyses[key]={simulated:true,endgamePolicy:'crossplay-endgame-v3',
            samplingVersion:'paired-review-v1',seed:cache.seed(turn),moves:[move],selected:move,best:move,
            staticMoves:[move],bestStaticEquity:0,candidateCount:1,totalPlayaheads:277,
            plies:-1,playToEnd:true,simulationMode:'play-to-end',winModel:'terminal',
            bagCount:2,finalTurnsRemaining:0,iterationsRequested:277,iterationsCompleted:277};
          restoreMoveFailure(review,index,{executionPolicy:'bounded-terminal-v1'});
          const current=restoreInstall(review), currentPoint=current.quality.points.find(point=>point.turnIndex===index);
          const changed=structuredClone(review);
          changed.stored_analysis.move_quality.points.find(point=>point.turnIndex===index).analysisKey=
            key.replace('crossplay-endgame-v3','old-endgame-policy');
          return {states:current.states,point:currentPoint,changed:restoreInstall(changed).states};
        }''')
        self.assertEqual(result['states'], ['done'] * 4 + ['error', 'done'])
        self.assertTrue(result['point']['retryable'])
        self.assertEqual(result['changed'], ['done'] * 4 + ['pending-preview', 'done'])

    def test_shared_terminal_preview_shows_saved_failure_and_manual_retry(self):
        self.prepare_saved_review()
        self.page.evaluate('''() => {
          const review=structuredClone(restoreSaved), index=0;
          const {move}=restoreTerminal(review,index);
          restoreMoveFailure(review,index,{executionPolicy:'bounded-terminal-v1'});
          restoreInstall(review,true);
          const request=workerRequest;
          workerRequest=(type,payload,options)=>type==='simulateReview' && payload.rack===review.turns[index].rack
            ?Promise.resolve(move):request(type,payload,options);
          reviewQueue.concurrency=1;
          prepareViewedTurn=window.automaticViewedTurn;
          currentTurnIndex=index; renderReview();
        }''')
        status = self.page.locator('#engineStatus')
        retry = self.page.get_by_role('button', name='Retry this turn', exact=True)
        expect(status).to_contain_text('Early estimate')
        expect(status).to_contain_text('Synthetic full analysis timeout')
        expect(status).not_to_contain_text('continues in the background')
        expect(retry).to_be_enabled()
        self.open_statistics()
        expect(self.page.locator('.quality-panel .trend-status')).not_to_contain_text('Your review is ready')
        expect(self.page.locator('.trend-panel .trend-status')).not_to_contain_text('Win trend complete')
        self.page.get_by_role('button', name='Game summary', exact=True).click()
        expect(self.page.locator('.overview-status')).not_to_contain_text('All moves checked')
        expect(self.page.locator('.overview-status')).to_contain_text('needs a retry')
        self.start_review()
        self.assertEqual(self.page.evaluate('restoreCalls'), [])
        retry.click()
        expect(status).to_contain_text('Full estimate')
        expect(retry).to_be_hidden()

    def test_legacy_terminal_failure_retries_once_and_new_failure_survives_save(self):
        self.prepare_saved_review()
        result = self.page.evaluate('''async () => {
          const review=structuredClone(restoreSaved), index=0;
          restoreTerminal(review,index);
          restoreMoveFailure(review,index,{keyed:false});
          await api('games/'+review.id+'/analysis',{method:'PUT',headers:{'Content-Type':'application/json'},
            body:JSON.stringify({analysis_version:ANALYSIS_VERSION,turn_analyses:review.stored_analysis.turn_analyses})});
          const context=restoreInstall(review), initial=context.states[index], request=workerRequest;
          let attempts=0;
          workerRequest=(type,payload,options)=>{
            if (type==='simulateReview' && payload.rack===review.turns[index].rack) {
              attempts++;
              return Promise.reject(new Error('Synthetic current full timeout.'));
            }
            return request(type,payload,options);
          };
          reviewQueue.concurrency=1;
          await window.automaticAnalysis(review); await restoreDrain(); await analysisSaveQueue;
          const saved=(await api('games/'+review.id+'/review')).stored_analysis;
          const restored=restoreInstall({...review, stored_analysis:saved});
          // The synthetic turn's original server record has a large bag.
          // Restore with the terminal identity used for the actual save.
          const point=saved.move_quality.points.find(value=>value.turnIndex===index);
          reviewQueue.concurrency=1;
          await window.automaticAnalysis(restored.review); await restoreDrain();
          return {initial,attempts,state:context.states[index],quality:context.quality.state,
            trend:context.trend.state,savedQuality:saved.move_quality.state,
            restored:restored.states[index],point};
        }''')
        self.assertEqual(result['initial'], 'pending-preview')
        self.assertEqual(result['attempts'], 1)
        self.assertEqual(result['state'], 'error')
        self.assertEqual(result['quality'], 'error')
        self.assertEqual(result['trend'], 'error')
        self.assertEqual(result['savedQuality'], 'error')
        self.assertEqual(result['restored'], 'error')
        self.assertEqual(result['point'].get('executionPolicy'), 'bounded-terminal-v1')

    def test_shared_saved_terminal_draw_failure_retries_only_draw(self):
        self.prepare_saved_review()
        self.page.evaluate('''() => {
          const review=structuredClone(restoreSaved), index=0;
          const {draw}=restoreTerminal(review,index);
          const key=restorePreview(review,index,'draw');
          delete review.stored_analysis.turn_analyses[key];
          const point=review.stored_analysis.move_quality.points.find(value=>value.turnIndex===index);
          point.draw={retryable:true,reason:'Synthetic saved terminal draw timeout.',analysisKey:key,
            provisional:false,executionPolicy:'bounded-terminal-v1'};
          restoreInstall(review,true);
          workerRequest=async (type,payload)=>{
            restoreCalls.push({type,iterations:payload.iterations});
            return draw;
          };
          reviewQueue.concurrency=1;
          prepareViewedTurn=window.automaticViewedTurn;
          currentTurnIndex=index; renderReview();
        }''')
        status = self.page.locator('#engineStatus')
        retry = self.page.get_by_role('button', name='Retry this turn', exact=True)
        expect(status).to_contain_text('Full estimate')
        expect(status).to_contain_text('Synthetic saved terminal draw timeout')
        self.assertEqual(self.page.evaluate('restoreCalls'), [])
        expect(retry).to_be_enabled()
        retry.click()
        expect(status).not_to_contain_text('Synthetic saved terminal draw timeout')
        expect(retry).to_be_hidden()
        self.assertEqual(self.page.evaluate('restoreCalls'), [{'type': 'simulateDraw',
                         'iterations': self.page.evaluate('CrossplayBagEmptying.fullIterations(21)')}])
        self.page.wait_for_function("contextFor(currentReview).states.every(state=>state==='done')")

    def test_terminal_draw_retry_uses_only_matching_valid_move_baseline(self):
        self.prepare_saved_review()
        result = self.page.evaluate('''async () => {
          const review=structuredClone(restoreSaved), index=0, turn=review.turns[index];
          const {move,draw}=restoreTerminal(review,index), cache=new AnalysisCache({},POSITION_PARAMETERS);
          const drawKey=restorePreview(review,index,'draw'), reviewKey=restorePreview(review,index);
          delete review.stored_analysis.turn_analyses[drawKey];
          const point=review.stored_analysis.move_quality.points.find(point=>point.turnIndex===index);
          point.draw={retryable:true,reason:'Synthetic unmarked draw timeout.',analysisKey:drawKey};
          const context=restoreInstall(review,true), initial=context.states[index];
          const payloads=[];
          let fail=true;
          workerRequest=async (type,payload)=>{
            payloads.push(payload);
            if (fail) throw new Error('Synthetic terminal draw timeout.');
            return {...draw,iterationsRequested:payload.iterations,iterationsCompleted:payload.iterations};
          };
          const failed=await analyzeReviewTurn(context,index,'review');
          const failure=context.quality.points.find(point=>point.turnIndex===index).draw;
          fail=false;
          await ensureRackDrawAnalysis(context,turn,'review');
          delete context.cache.results[drawKey];
          const previewKey=cache.key(turn,'preview');
          context.cache.results[previewKey]={...context.cache.results[previewKey],seed:move.seed+1};
          delete context.cache.results[cache.key(turn,'draw-preview')];
          await ensureRackDrawAnalysis(context,turn,'preview');
          delete context.cache.results[cache.key(turn,'draw-preview')];
          context.cache.results[previewKey]=review.stored_analysis.turn_analyses[previewKey];
          await ensureRackDrawAnalysis(context,turn,'preview');
          return {initial,failed,failure,payloads,move,
            preserved:context.cache.get(turn,'review')===review.stored_analysis.turn_analyses[reviewKey]};
        }''')
        self.assertEqual(result['initial'], 'pending-preview')
        self.assertTrue(result['failed'].get('failed'))
        self.assertEqual(result['failure'].get('executionPolicy'), 'bounded-terminal-v1')
        self.assertTrue(result['preserved'])
        self.assertEqual(len(result['payloads']), 4)
        for payload, samples in zip(result['payloads'], [result['move']['iterationsCompleted'], result['move']['iterationsCompleted'], None, 64]):
            if samples is None:
                self.assertNotIn('beforeWin', payload)
            else:
                self.assertEqual(payload['beforeWin'], {'win': .3, 'samples': samples, 'seed': result['move']['seed']})

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


    def test_queued_completion_saves_an_immutable_summary_after_turn_retry(self):
        self.prepare_saved_review()
        result = self.page.evaluate('''async () => {
          const review=structuredClone(restoreSaved), context=restoreInstall(review);
          let releaseSave;
          analysisSaveQueue=new Promise(resolve=>releaseSave=resolve);
          const writes=[], request=api;
          api=(path,options)=>{
            if(path.endsWith('/analysis')) writes.push(JSON.parse(options.body));
            return request(path,options);
          };
          const finishing=finishReviewAnalysis(context);
          const originalWord=context.quality.points[0].playedMove.word, originalMine=context.trend.points[0].mine;
          context.cache.saveError='Synthetic previous save interruption.';
          currentTurnIndex=0; renderReview();
          document.querySelector('.analysis-panel .analysis-button').click();
          const liveState=context.quality.state;
          context.quality.points[0].playedMove.word='Changed after the save was queued';
          context.trend.points[0].mine=-1;
          releaseSave();
          await finishing; await analysisSaveQueue;
          const saved=writes.find(body=>body.move_quality);
          return {liveState,quality:saved.move_quality.state,trend:saved.win_trend.state,
            savedWord:saved.move_quality.points[0].playedMove.word,originalWord,
            savedMine:saved.win_trend.points[0].mine,originalMine};
        }''')
        self.assertEqual(result['liveState'], 'running')
        self.assertEqual(result['quality'], 'done')
        self.assertEqual(result['trend'], 'done')
        self.assertEqual(result['savedWord'], result['originalWord'])
        self.assertEqual(result['savedMine'], result['originalMine'])

    def test_turn_retry_during_frequency_wait_leaves_review_pending(self):
        self.prepare_saved_review()
        result = self.page.evaluate('''async () => {
          const review=structuredClone(restoreSaved), context=restoreInstall(review), quality=context.quality;
          delete quality.points[0].opportunity;
          let releaseFrequency;
          wordFrequencyPromise=new Promise(resolve=>releaseFrequency=()=>resolve(new Map()));
          const writes=[], request=api;
          api=(path,options)=>{
            if(path.endsWith('/analysis')) writes.push(JSON.parse(options.body));
            return request(path,options);
          };
          const finishing=finishReviewAnalysis(context);
          context.cache.saveError='Synthetic previous save interruption.';
          currentTurnIndex=0; renderReview();
          document.querySelector('.analysis-panel .analysis-button').click();
          await new Promise(resolve=>setTimeout(resolve,0));
          releaseFrequency();
          await finishing; await analysisSaveQueue;
          return {sameQuality:quality===context.quality, state:quality.state, turnState:context.states[0],
            wroteSummary:writes.some(body=>Boolean(body.move_quality))};
        }''')
        self.assertEqual(result, {'sameQuality': True, 'state': 'running',
                                  'turnState': 'pending-preview', 'wroteSummary': False})

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

    def test_clear_and_rerun_discards_every_saved_estimate(self):
        self.install_review_worker()
        self.open_game()
        result = self.page.evaluate('''async () => {
          const context=contextFor(currentReview);
          context.cache.results={oldFull:{},oldPreview:{},oldDraw:{},oldOpening:{}};
          context.cache.unsaved={};context.cache.completed={};
          currentReview.stored_analysis={version:ANALYSIS_VERSION,turn_analyses:context.cache.results};
          window.resetStarts=[];
          const originalQueue=getReviewQueue;
          getReviewQueue=()=>{const queue=originalQueue();queue.concurrency=0;return queue;};
          startBackgroundAnalysis=review=>resetStarts.push(review.id);
          const before=structuredClone(currentReview.turns);
          const success=await restartEntireGameAnalysis(currentReview);
          const fresh=contextFor(currentReview);
          return {success,starts:resetStarts,keys:Object.keys(fresh.cache.results),
            opening:fresh.openingState,states:fresh.states,
            preserved:JSON.stringify(before)===JSON.stringify(currentReview.turns),
            saved:await api('games/'+currentReview.id+'/review')};
        }''')
        self.assertTrue(result['success'])
        self.assertEqual(result['starts'], ['mockgame01'])
        self.assertEqual(result['keys'], [])
        self.assertEqual(result['opening'], 'pending-preview')
        self.assertTrue(all(state == 'pending-preview' for state in result['states']))
        self.assertTrue(result['preserved'])
        self.assertIsNone(result['saved']['stored_analysis'])

    def test_reset_waits_for_save_ack_and_preserves_other_game_work(self):
        self.install_review_worker()
        self.open_game()
        result = self.page.evaluate('''async () => {
          const originalQueue=getReviewQueue;
          getReviewQueue=()=>{const queue=originalQueue();queue.concurrency=0;return queue;};
          startBackgroundAnalysis=()=>{};
          const owner=analysisOwner(), review=currentReview, oldQueue=reviewQueue;
          const otherReview=await api('games/mockgame02/review');
          const other=makeReviewContext(otherReview,owner);
          other.paused=true;
          other.cache.unsaved={pendingOther:{simulated:false,moves:[{tiles:'CAT',equity:1}]}};
          oldQueue.contexts.set(otherReview.id,other);
          const requests=[], request=api;
          api=async(path,options)=>{requests.push({path,method:options?.method||'GET'});return request(path,options);};
          let acknowledge;
          analysisSaveQueue=new Promise(resolve=>acknowledge=()=>resolve(null));
          const pending=restartEntireGameAnalysis(review);
          const duplicate=await restartEntireGameAnalysis(review);
          await new Promise(resolve=>setTimeout(resolve,20));
          const before={deletes:requests.filter(r=>r.method==='DELETE').length,aborted:oldQueue.controller.signal.aborted};
          acknowledge();
          const success=await pending;
          await analysisSaveQueue;
          const retained=reviewQueue.contexts.get(otherReview.id);
          return {before,duplicate,success,afterAborted:oldQueue.controller.signal.aborted,
            sameCache:retained.cache===other.cache,otherPaused:retained.paused,
            savedOther:(await request('games/mockgame02/review')).stored_analysis,
            deletes:requests.filter(r=>r.method==='DELETE').length};
        }''')
        self.assertEqual(result['before'], {'deletes': 0, 'aborted': False})
        self.assertFalse(result['duplicate'])
        self.assertTrue(result['success'])
        self.assertTrue(result['afterAborted'])
        self.assertTrue(result['sameCache'])
        self.assertTrue(result['otherPaused'])
        self.assertIn('pendingOther', result['savedOther']['turn_analyses'])
        self.assertEqual(result['deletes'], 1)

    def test_reset_refuses_failed_save_drain(self):
        self.install_review_worker()
        self.open_game()
        result = self.page.evaluate('''async () => {
          const review=currentReview, oldCache=positionCache;
          oldCache.results={keep:{moves:[]}};
          const originalQueue=getReviewQueue;
          getReviewQueue=()=>{const queue=originalQueue();queue.concurrency=0;return queue;};
          startBackgroundAnalysis=()=>{};
          let deletes=0;const request=api;
          api=async(path,options)=>{if(options?.method==='DELETE')deletes++;return request(path,options);};
          oldCache.saveError='Synthetic save acknowledgment failure';
          analysisSaveQueue=Promise.resolve(oldCache.saveError);
          const success=await restartEntireGameAnalysis(review);
          return {success,deletes,retained:positionCache===oldCache,
            keys:Object.keys(positionCache.results),paused:contextFor(currentReview).paused,
            message:analysisResetState.message};
        }''')
        self.assertFalse(result['success'])
        self.assertEqual(result['deletes'], 0)
        self.assertTrue(result['retained'])
        self.assertIn('keep', result['keys'])
        self.assertTrue(result['paused'])
        self.assertIn('pending save failed', result['message'])

    def test_uncertain_reset_never_restores_old_estimates_for_save(self):
        self.install_review_worker()
        self.open_game()
        result = self.page.evaluate('''async () => {
          const review=currentReview, oldCache=positionCache;
          oldCache.results={oldFull:{}};oldCache.unsaved={oldFull:{moves:[]}};
          const originalQueue=getReviewQueue;
          getReviewQueue=()=>{const queue=originalQueue();queue.concurrency=0;return queue;};
          startBackgroundAnalysis=()=>{};
          const request=api;
          api=async(path,options)=>{
            if(options?.method==='DELETE')throw new Error('Synthetic reset connection failure');
            return request(path,options);
          };
          const success=await restartEntireGameAnalysis(review);
          return {success,keys:Object.keys(positionCache.results),
            unsaved:Object.keys(positionCache.unsaved||{}),discarded:oldCache.discarded,
            paused:contextFor(currentReview).paused,message:analysisResetState.message};
        }''')
        self.assertFalse(result['success'])
        self.assertEqual(result['keys'], [])
        self.assertEqual(result['unsaved'], [])
        self.assertTrue(result['discarded'])
        self.assertTrue(result['paused'])
        self.assertIn('reset connection failure', result['message'])

    def test_reset_account_change_during_save_wait_cannot_delete(self):
        self.install_review_worker()
        self.open_game()
        result = self.page.evaluate('''async () => {
          let acknowledge,deletes=0;
          analysisSaveQueue=new Promise(resolve=>acknowledge=()=>resolve(null));
          const request=api;
          api=async(path,options)=>{if(options?.method==='DELETE')deletes++;return request(path,options);};
          const pending=restartEntireGameAnalysis(currentReview);
          resetBackgroundAnalysis();
          acknowledge();
          return {success:await pending,deletes,contexts:reviewQueue?.contexts.size||0};
        }''')
        self.assertFalse(result['success'])
        self.assertEqual(result['deletes'], 0)
        self.assertEqual(result['contexts'], 0)

    def test_refresh_waits_for_reset_delete_ack_before_replacing_queue(self):
        self.install_review_worker()
        self.open_game()
        result = self.page.evaluate('''async () => {
          const review=currentReview, oldCache=positionCache;
          oldCache.results={oldFull:{}};
          const originalQueue=getReviewQueue;
          getReviewQueue=()=>{const queue=originalQueue();queue.concurrency=0;return queue;};
          startBackgroundAnalysis=()=>{};
          let releaseDelete,deleteStarted=false,refreshRequests=0;
          const heldDelete=new Promise(resolve=>releaseDelete=resolve),request=api;
          api=async(path,options)=>{
            if(options?.method==='DELETE'){deleteStarted=true;await heldDelete;}
            if(path==='games' || path.startsWith('games?') || path==='games/refresh')refreshRequests++;
            return request(path,options);
          };
          const pending=restartEntireGameAnalysis(review);
          while(!deleteStarted)await new Promise(resolve=>setTimeout(resolve,1));
          const refreshing=refreshGames();
          await new Promise(resolve=>setTimeout(resolve,20));
          const before={refreshRequests,manual:Boolean(manualRefreshState),
            buttonEnabled:!byId('refreshButton').disabled,status:byId('refreshStatus').textContent};
          releaseDelete();
          const reset=await pending;
          await refreshing;
          const current=contextFor(currentReview);
          return {before,reset,refreshRequests,newCache:current.cache!==oldCache,
            discarded:Boolean(current.cache.discarded),keys:Object.keys(current.cache.results),
            opening:current.openingState,states:current.states};
        }''')
        self.assertEqual(result['before']['refreshRequests'], 0)
        self.assertFalse(result['before']['manual'])
        self.assertTrue(result['before']['buttonEnabled'])
        self.assertIn('analysis reset', result['before']['status'])
        self.assertTrue(result['reset'])
        self.assertGreater(result['refreshRequests'], 0)
        self.assertTrue(result['newCache'])
        self.assertFalse(result['discarded'])
        self.assertEqual(result['keys'], [])
        self.assertEqual(result['opening'], 'pending-preview')
        self.assertTrue(all(state == 'pending-preview' for state in result['states']))


    def test_reset_removes_ready_status_before_delete_ack(self):
        self.prepare_saved_review()
        self.open_statistics()
        self.page.evaluate('''() => {
          window.releaseResetDelete=null;
          const request=api;
          api=async(path,options)=>{
            if(options?.method==='DELETE') await new Promise(resolve=>releaseResetDelete=resolve);
            return request(path,options);
          };
        }''')
        self.page.get_by_role('button', name='Re-analyze moves', exact=True).click()
        self.page.wait_for_function('typeof releaseResetDelete === "function"')
        status = self.page.locator('.quality-panel .trend-status')
        self.assertNotIn('Your review is ready', status.text_content())
        self.assertIn('Clearing', status.text_content())
        self.assertTrue(self.page.get_by_role('button', name='Clearing analysis…', exact=True).first.is_disabled())
        trend_status = self.page.locator('.trend-panel .trend-status')
        self.assertIn('Clearing', trend_status.text_content())
        self.assertTrue(self.page.locator('.trend-panel .trend-controls > .analysis-button').is_disabled())
        self.page.evaluate('renderReview()')
        self.assertNotIn('Your review is ready', status.text_content())
        self.assertIn('Clearing', status.text_content())
        self.assertIn('Clearing', trend_status.text_content())
        self.assertTrue(self.page.locator('.trend-panel .trend-controls > .analysis-button').is_disabled())
        self.page.evaluate('releaseResetDelete()')
        self.page.wait_for_function('!analysisResetState.busy')

    def test_failed_reset_from_quality_control_is_visible_in_both_panels(self):
        self.prepare_saved_review()
        self.open_statistics()
        self.page.evaluate('''() => {
          positionCache.saveError='Synthetic failed save acknowledgment';
          analysisSaveQueue=Promise.resolve(positionCache.saveError);
        }''')
        self.page.get_by_role('button', name='Re-analyze moves', exact=True).click()
        self.page.wait_for_function('analysisResetState && !analysisResetState.busy')
        self.assertIn('pending save failed', self.page.locator('.quality-panel .trend-status').text_content())
        self.assertIn('pending save failed', self.page.locator('.trend-panel .trend-status').text_content())
        self.assertTrue(self.page.locator('.quality-panel .trend-controls > .analysis-button').is_enabled())
        self.assertIn('Retry', self.page.locator('.quality-panel .trend-controls > .analysis-button').text_content())
        self.assertTrue(self.page.evaluate('Boolean(moveQualityResults.points.length)'))
        self.page.evaluate('''() => {
          positionCache.saveError=null;
          analysisSaveQueue=Promise.resolve(null);
          const originalQueue=getReviewQueue;
          getReviewQueue=()=>{const queue=originalQueue();queue.concurrency=0;return queue;};
        }''')
        self.page.locator('.quality-panel .trend-controls > .analysis-button').click()
        self.page.wait_for_function('analysisResetState && !analysisResetState.busy && !analysisResetState.error')
        self.assertNotIn('pending save failed', self.page.locator('.quality-panel .trend-status').text_content())
        self.assertNotIn('pending save failed', self.page.locator('.trend-panel .trend-status').text_content())

    def test_reset_timeout_refuses_delete_and_releases_old_requests(self):
        self.install_review_worker()
        self.open_game()
        result = self.page.evaluate('''async () => {
          const oldQueue=reviewQueue;
          const originalQueue=getReviewQueue;
          getReviewQueue=()=>{const queue=originalQueue();queue.concurrency=0;return queue;};
          startBackgroundAnalysis=()=>{};
          const timeout=window.setTimeout;
          window.setTimeout=(run,delay,...args)=>timeout(run,delay===30000?5:delay,...args);
          analysisSaveQueue=new Promise(()=>{});
          let deletes=0;const request=api;
          api=async(path,options)=>{if(options?.method==='DELETE')deletes++;return request(path,options);};
          const success=await restartEntireGameAnalysis(currentReview);
          window.setTimeout=timeout;
          return {success,deletes,aborted:oldQueue.controller.signal.aborted,
            busy:analysisResetState.busy,paused:contextFor(currentReview).paused};
        }''')
        self.assertFalse(result['success'])
        self.assertEqual(result['deletes'], 0)
        self.assertTrue(result['aborted'])
        self.assertFalse(result['busy'])
        self.assertTrue(result['paused'])

    def test_unrelated_failed_save_does_not_block_target_reset(self):
        self.install_review_worker()
        self.open_game()
        result = self.page.evaluate('''async () => {
          const target=currentReview;
          const other=makeReviewContext(await api('games/mockgame02/review'),analysisOwner());
          other.paused=true;other.cache.saveError='Other game save failed';
          reviewQueue.contexts.set(other.review.id,other);
          const originalQueue=getReviewQueue;
          getReviewQueue=()=>{const queue=originalQueue();queue.concurrency=0;return queue;};
          startBackgroundAnalysis=()=>{};
          analysisSaveQueue=Promise.resolve('Other game save failed');
          const success=await restartEntireGameAnalysis(target);
          const retained=reviewQueue.contexts.get(other.review.id);
          return {success,otherError:retained.cache.saveError,sameCache:retained.cache===other.cache,
            otherPaused:retained.paused,targetKeys:Object.keys(positionCache.results)};
        }''')
        self.assertTrue(result['success'])
        self.assertEqual(result['otherError'], 'Other game save failed')
        self.assertTrue(result['sameCache'])
        self.assertTrue(result['otherPaused'])
        self.assertEqual(result['targetKeys'], [])

    def test_owner_control_reruns_opening_moves_and_draws_from_empty_cache(self):
        self.install_review_worker()
        self.open_game()
        self.page.evaluate('''async () => {
          await api('games/'+currentReview.id+'/analysis',{method:'PUT',headers:{'Content-Type':'application/json'},
            body:JSON.stringify({analysis_version:ANALYSIS_VERSION,turn_analyses:{
              oldResult:{simulated:false,moves:[{tiles:'CAT'}]}}})});
          startBackgroundAnalysis=automaticAnalysis;
        }''')
        self.page.get_by_role('button', name='Clear and rerun analysis', exact=True).click()
        self.page.wait_for_function('''() => contextFor(currentReview)?.quality?.state==='done' &&
          contextFor(currentReview)?.openingState==='done' ''')
        result = self.page.evaluate('''async () => {
          await analysisSaveQueue;
          const context=contextFor(currentReview),cache=context.cache;
          const saved=(await api('games/'+currentReview.id+'/review')).stored_analysis;
          const opening=CrossplayOpening.describe(currentReview);
          return {old:'oldResult' in saved.turn_analyses,state:saved.move_quality.state,
            moves:currentReview.turns.map(turn=>Boolean(saved.turn_analyses[cache.key(turn,'review')])),
            draws:currentReview.turns.map((turn,index)=>({complete:drawWorkComplete(context,index),
              saved:Boolean(saved.turn_analyses[cache.key(turn,'draw')])})),
            opening:Boolean(saved.turn_analyses[CrossplayOpening.key(opening,SIMULATION_ITERATIONS)])};
        }''')
        self.assertFalse(result['old'])
        self.assertEqual(result['state'], 'done')
        self.assertTrue(all(result['moves']))
        self.assertTrue(all(draw['complete'] for draw in result['draws']))
        self.assertTrue(any(draw['saved'] for draw in result['draws']))
        self.assertTrue(result['opening'])


if __name__ == '__main__':
    unittest.main()
