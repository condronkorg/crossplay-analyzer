"""Opening estimates use the shared queue/cache without discarding completed moves."""
import unittest

import test_review_browser as review_browser


class InitialDrawProcessingBrowserTest(unittest.TestCase):
    setUpClass = classmethod(review_browser.ReviewBrowserTest.setUpClass.__func__)
    setUp = review_browser.ReviewBrowserTest.setUp
    tearDown = review_browser.ReviewBrowserTest.tearDown
    route_request = review_browser.ReviewBrowserTest.route_request
    connect = review_browser.ReviewBrowserTest.connect
    open_game = review_browser.ReviewBrowserTest.open_game
    start_review = review_browser.ReviewBrowserTest.start_review
    install_review_worker = review_browser.ReviewBrowserTest.install_review_worker

    def prepare_opening(self):
        self.install_review_worker()
        self.open_game()
        self.page.add_script_tag(url=self.base_url + '/opening-draw.js')
        self.page.evaluate('''() => {
          const grid = Array(15).fill('.'.repeat(15));
          const pass = (mine,rack,index) => ({mine,rack,rack_after:rack,index,action:'pass -- 0',
            score:0,grid:[...grid],grid_after:[...grid],mover_score:0,other_score:0,
            bag_count:86,final_turns_remaining:0});
          currentReview = {...currentReview,turns:[pass(true,'CATERS?',0),pass(false,'ADEINOR',1)]};
          const context=makeReviewContext(currentReview,analysisOwner());
          reviewQueue.contexts.set(currentReview.id,context);
          reviewQueue.concurrency=0;
          positionCache=context.cache;moveQualityResults=context.quality;trendResults=context.trend;
          window.openingCalls=[];
          window.openingResult=payload=>({simulated:true,samplingVersion:'initial-deal-v1',policy:'static-best-v1',
            firstRack:payload.firstRack,secondRack:payload.secondRack,seed:payload.seed,
            baselineWin:.55,firstOnlyWin:.65,secondOnlyWin:.45,afterWin:.6,firstWpa:12.5,secondWpa:7.5,
            iterationsRequested:payload.iterations,iterationsCompleted:payload.iterations,
            plies:2,playToEnd:false,simulationMode:'2-ply',winModel:'bogowin'});
          const request=workerRequest;
          workerRequest=(type,payload,options)=>{
            if(type!=='simulateOpening')return request(type,payload,options);
            openingCalls.push(payload.iterations);
            return Promise.resolve(openingResult(payload));
          };
        }''')

    def test_profiles_and_restore_do_not_recompute_saved_moves(self):
        self.prepare_opening()
        result=self.page.evaluate('''async()=>{
          const context=contextFor(currentReview);
          await analyzeReviewTurn(context,0,'review');
          await analyzeReviewTurn(context,1,'review');
          const legacy={version:ANALYSIS_VERSION,turn_analyses:{...context.cache.results},
            move_quality:{...context.quality,state:'done'}};
          delete legacy.move_quality.opening;
          const restored=makeReviewContext({...currentReview,stored_analysis:legacy},context.owner);
          reviewQueue.contexts.set(restored.review.id,restored);
          const states=[...restored.states];
          let moveCalls=0;const request=workerRequest;
          workerRequest=(type,...args)=>{if(type==='simulateReview')moveCalls++;return request(type,...args);};
          await analyzeOpeningDraw(restored,'preview');
          const early={...restored.quality.opening};
          await analyzeOpeningDraw(restored,'review');
          await finishReviewAnalysis(restored);
          const error=await flushAnalysisSave(restored.review,restored.cache);
          const saved={version:ANALYSIS_VERSION,turn_analyses:{...restored.cache.results},move_quality:restored.quality};
          const reloaded=makeReviewContext({...restored.review,stored_analysis:saved},restored.owner);
          return {states,moveCalls,calls:openingCalls,early,full:reloaded.quality.opening,
            openingState:reloaded.openingState,error,points:reloaded.quality.points.length};
        }''')
        self.assertEqual(result['states'],['done','done'])
        self.assertEqual(result['moveCalls'],0)
        self.assertEqual(result['calls'],[64,277])
        self.assertTrue(result['early']['provisional'])
        self.assertFalse(result['full']['provisional'])
        self.assertEqual(result['full']['firstWpa'],12.5)
        self.assertEqual(result['openingState'],'done')
        self.assertIsNone(result['error'])
        self.assertEqual(result['points'],2)

    def test_late_preview_cannot_replace_a_full_opening(self):
        self.prepare_opening()
        result=self.page.evaluate('''async()=>{
          const context=contextFor(currentReview);
          let release;const request=workerRequest;
          workerRequest=(type,payload,options)=>type==='simulateOpening'&&payload.iterations===64
            ?new Promise(resolve=>release=()=>resolve(openingResult(payload))):request(type,payload,options);
          const early=analyzeOpeningDraw(context,'preview');
          while(!release)await Promise.resolve();
          await analyzeOpeningDraw(context,'review');release();await early;
          return context.quality.opening;
        }''')
        self.assertFalse(result['provisional'])
        self.assertEqual(result['samples'],277)

    def test_missing_initial_rack_does_not_become_zero_draw_luck(self):
        self.prepare_opening()
        result=self.page.evaluate('''async()=>{
          const review={...currentReview,turns:currentReview.turns.map((turn,index)=>index===1?{...turn,rack:null}:turn)};
          const context=makeReviewContext(review,analysisOwner());
          await analyzeOpeningDraw(context,'review');
          return {state:context.openingState,opening:context.quality.opening,calls:openingCalls};
        }''')
        self.assertTrue(result['opening']['unavailable'])
        self.assertNotIn('afterWin',result['opening'])
        self.assertNotIn('firstWpa',result['opening'])
        self.assertEqual(result['calls'],[])
        self.assertEqual(result['state'],'unavailable')

    def test_full_failure_remains_visible_until_retry_and_keeps_moves_complete(self):
        self.prepare_opening()
        self.page.evaluate('''async()=>{
          const context=contextFor(currentReview);context.states=['done','done'];
          let release;const request=workerRequest;
          workerRequest=(type,payload,options)=>{
            if(type!=='simulateOpening')return request(type,payload,options);
            if(payload.iterations===64)return new Promise(resolve=>release=()=>resolve(openingResult(payload)));
            return Promise.reject(new Error('Synthetic opening failure'));
          };
          const early=analyzeOpeningDraw(context,'preview');
          while(!release)await Promise.resolve();
          try{await analyzeOpeningDraw(context,'review');}catch(_){}
          release();await early;
          window.failedOpening={...context.quality.opening};
          workerRequest=request;reviewQueue.concurrency=1;reviewQueue.retry();
        }''')
        self.page.wait_for_function("contextFor(currentReview).openingState==='done'")
        result=self.page.evaluate('''()=>({failed:failedOpening,states:contextFor(currentReview).states,
          opening:contextFor(currentReview).quality.opening})''')
        self.assertTrue(result['failed']['retryable'])
        self.assertNotIn('afterWin',result['failed'])
        self.assertEqual(result['states'],['done','done'])
        self.assertEqual(result['opening']['samples'],277)

    def test_refresh_cancels_old_opening_and_ignores_its_late_result(self):
        self.prepare_opening()
        self.page.evaluate('''async()=>{
          const context=contextFor(currentReview);context.states=['done','done'];
          const request=workerRequest;let first=true;
          workerRequest=(type,payload,options)=>{
            if(type==='simulateOpening'&&first){first=false;
              return new Promise(resolve=>window.releaseOldOpening=()=>resolve({...openingResult(payload),
                afterWin:.8,firstWpa:22.5,secondWpa:-2.5}));}
            return request(type,payload,options);
          };
          const summary=games.find(game=>game.id===currentReview.id), originalApi=api;
          api=(path,options)=>path==='games'?Promise.resolve({games:[summary]}):originalApi(path,options);
          window.oldOpeningQueue=reviewQueue;
          reviewQueue.concurrency=1;reviewQueue.select(currentReview.id);
          while(!window.releaseOldOpening)await Promise.resolve();
          await refreshGames();
        }''')
        self.page.wait_for_function("contextFor(currentReview)?.quality?.opening?.samples===277")
        self.page.evaluate('releaseOldOpening()')
        self.page.wait_for_function('oldOpeningQueue.running===0')
        result=self.page.evaluate('''()=>({aborted:oldOpeningQueue.controller.signal.aborted,
          opening:contextFor(currentReview).quality.opening,
          previews:Object.entries(positionCache.results).filter(([key])=>key.startsWith('["quackle-opening-draw-v1"'))
            .map(([,value])=>value.afterWin)})''')
        self.assertTrue(result['aborted'])
        self.assertEqual(result['opening']['afterWin'],.6)
        self.assertNotIn(.8,result['previews'])

    def test_account_change_drops_a_late_opening_result(self):
        self.prepare_opening()
        result=self.page.evaluate('''async()=>{
          const context=contextFor(currentReview);context.states=['done','done'];
          workerRequest=(type,payload)=>new Promise(resolve=>window.releaseOpening=()=>resolve(openingResult(payload)));
          const pending=analyzeOpeningDraw(context,'preview');
          while(!window.releaseOpening)await Promise.resolve();
          clearAccountUi('Signed out');releaseOpening();
          try{await pending;}catch(_){}
          return {current:currentReview,keys:Object.keys(context.cache.results),unsaved:Object.keys(context.cache.unsaved||{})};
        }''')
        self.assertIsNone(result['current'])
        self.assertEqual(result['keys'],[])
        self.assertEqual(result['unsaved'],[])

    def test_pause_does_not_start_full_opening_until_resumed(self):
        self.prepare_opening()
        self.page.evaluate('''async()=>{
          const context=contextFor(currentReview);context.states=['done','done'];
          const request=workerRequest;
          workerRequest=(type,payload,options)=>{
            if(type==='simulateOpening'&&payload.iterations===64){openingCalls.push(64);
              return new Promise(resolve=>window.releaseOpening=()=>resolve(openingResult(payload)));}
            return request(type,payload,options);
          };
          reviewQueue.concurrency=1;reviewQueue.select(currentReview.id);
          while(!window.releaseOpening)await Promise.resolve();
          pauseReviewAnalysis(currentReview);releaseOpening();
          window.originalOpeningRequest=request;
        }''')
        self.page.wait_for_function('reviewQueue.running===0')
        self.assertEqual(self.page.evaluate('openingCalls'),[64])
        self.assertEqual(self.page.evaluate('contextFor(currentReview).quality.state'),'stopped')
        self.page.evaluate('workerRequest=originalOpeningRequest;startMoveQuality(currentReview)')
        self.page.wait_for_function("contextFor(currentReview).quality.opening?.samples===277")
        self.assertEqual(self.page.evaluate('openingCalls'),[64,277])
