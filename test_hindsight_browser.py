"""Position UI uses a separate known-racks estimate, never the move grade."""
import unittest
from playwright.sync_api import expect
import test_review_browser as review_browser


class HindsightBrowserTest(unittest.TestCase):
    setUpClass = classmethod(review_browser.ReviewBrowserTest.setUpClass.__func__)
    setUp = review_browser.ReviewBrowserTest.setUp
    tearDown = review_browser.ReviewBrowserTest.tearDown
    route_request = review_browser.ReviewBrowserTest.route_request
    connect = review_browser.ReviewBrowserTest.connect
    open_game = review_browser.ReviewBrowserTest.open_game
    start_review = review_browser.ReviewBrowserTest.start_review

    def install_review(self):
        self.open_game()
        self.page.evaluate("""() => {
          const c=contextFor(currentReview), grid=Array(15).fill('.'.repeat(15));
          const first={grid,rack:'CATERS?',opponent_rack:'DEFINOU',mine:true,
            mover_score:0,other_score:0,bag_count:86,final_turns_remaining:0,
            action:'CAT 8G 10',score:10,rack_after:'ERS?ING'};
          const after=CrossplayHindsight.turnStates({turns:[first]},0).draw;
          currentReview={...currentReview,turns:[first,{...first,grid:after.grid,
            mine:false,rack:'DEFINOU',opponent_rack:'ERS?ING',mover_score:0,
            other_score:10,bag_count:83,score:0,action:'PASS 0',rack_after:'DEFINOU'}]};
          c.review=currentReview;c.cache.results={};c.cache.shared=true;c.states=['done','done'];
          c.quality={state:'done',points:[{turnIndex:0,playedWin:.6,bestWin:.9,
            loss:30,category:'blunder',samples:277,draw:{beforeWin:.6,afterWin:.8,drawCount:3}}]};
          c.timeline=undefined;c.trend=deriveWinTrend(c);window.testHindsightContext=c;
          currentTurnIndex=0;positionCache=c.cache;moveQualityResults=c.quality;trendResults=c.trend;
          window.knownStateCalls=[];
          window.workerRequest=async(type,p)=>{
            if(type!=='evaluateKnownState')throw Error('Unexpected grading request: '+type);
            knownStateCalls.push(structuredClone(p));
            return {error:'',informationModel:CrossplayHindsight.MODEL,policyVersion:CrossplayHindsight.POLICY,
              seed:p.seed,iterationsRequested:p.iterations,iterationsCompleted:p.iterations,
              offset:0,samples:p.iterations,completed:p.iterations,terminalSamples:0,
              winSum:p.iterations,win:1,bagCount:p.state.bagCount,
              settledBagCount:CrossplayHindsight.settledBag(p.state),
              finalTurnsRemaining:p.state.finalTurnsRemaining,plies:2,playToEnd:false,
              simulationMode:'2-ply',simulated:true};
          };
          renderReview();
        }""")

    def test_known_racks_chart_is_independent_of_grades_and_deduplicates_states(self):
        self.install_review()
        self.page.evaluate("""async()=>{
          window.originalPrivateQuality=JSON.stringify(testHindsightContext.quality);
          await runHindsightTimeline(testHindsightContext);
        }""")
        result=self.page.evaluate("""()=>{
          const t=trendResults;
          return {values:t.points.map(p=>p.mine),opening:t.opening.mine,
            prior:t.turns[0].draw.stateId,next:t.turns[1].before.stateId,
            calls:knownStateCalls.length,unique:new Set(knownStateCalls.map(p=>CrossplayHindsight.key(p.state,p.iterations))).size,
            privateUnchanged:JSON.stringify(testHindsightContext.quality)===originalPrivateQuality};
        }""")
        self.assertTrue(all(v==100 for v in result['values']))
        self.assertEqual(result['opening'],100)
        self.assertEqual(result['prior'],result['next'])
        self.assertEqual(result['calls'],result['unique'])
        self.assertTrue(result['privateUnchanged'])
        calls=result['calls']
        self.page.evaluate('runHindsightTimeline(testHindsightContext)')
        self.assertEqual(self.page.evaluate('knownStateCalls.length'),calls)
        for width in (1366,390):
            self.page.set_viewport_size({'width':width,'height':900})
            self.page.locator('[data-chart-control="turn"]').select_option('0')
            expect(self.page.locator('.win-history-inspector')).to_contain_text('Position WP · Both racks')
            expect(self.page.locator('.win-history-inspector')).to_contain_text('30.0 pp')
            expect(self.page.locator('.position-wp-table')).to_contain_text('100.0%')
            self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
        self.page.evaluate("boardView='after';reviewChartPins.delete(currentReview);renderReview()")
        self.page.get_by_role('button',name='Show before move',exact=True).click()
        expect(self.page.locator('.win-bar')).to_have_attribute('aria-label', __import__('re').compile('Position WP.*100.0%'))

    def test_refresh_during_known_state_work_resumes_a_new_runner(self):
        self.install_review()
        self.page.evaluate("""()=>{
          const request=workerRequest;let first=true;
          workerRequest=(...args)=>{
            if(first){first=false;return new Promise(resolve=>window.releaseOldKnown=()=>resolve(request(...args)));}
            return request(...args);
          };
          const c=testHindsightContext;c.openingState='unavailable';c.openingInput={unavailable:true};
          window.oldKnownRun=runHindsightTimeline(c);
        }""")
        self.page.wait_for_function('typeof releaseOldKnown === "function"')
        self.page.evaluate("""()=>{
          const snapshot=suspendAnalysisForRefresh();
          const copied=snapshot.contexts.get(currentReview.id);
          window.snapshotTransient={running:copied.hindsightRunning,controller:copied.hindsightController,restart:copied.hindsightRestart};
          snapshot.ids=[];snapshot.games=[];games=[];
          resumeAnalysisAfterRefresh(snapshot);
          window.resumedKnownContext=contextFor(currentReview);releaseOldKnown();
        }""")
        self.page.wait_for_function('resumedKnownContext.trend.state === "done" && !resumedKnownContext.hindsightRunning')
        self.assertEqual(self.page.evaluate('snapshotTransient'),{'running':False,'controller':None,'restart':False})
        self.assertTrue(self.page.evaluate('resumedKnownContext !== testHindsightContext'))
        self.assertTrue(self.page.evaluate('trendResults.points.every(p=>p.mine===100 && !p.provisional)'))

    def test_phone_position_table_uses_full_width_without_split_words(self):
        self.install_review()
        self.page.evaluate("currentReview.opponent='fcsadboys'")
        self.page.evaluate('runHindsightTimeline(testHindsightContext)')
        for width in (320,390):
            self.page.set_viewport_size({'width':width,'height':900})
            for summary in (False,True):
                if summary:
                    self.page.get_by_role('button',name='Game summary',exact=True).click()
                self.page.locator('[data-chart-control="turn"]').select_option('0')
                dropdown=self.page.locator('[data-chart-control="turn"]').evaluate("""node=>{
                  node.options[node.selectedIndex].textContent='Turn 24';
                  const style=getComputedStyle(node),ctx=document.createElement('canvas').getContext('2d');
                  ctx.font=style.font;
                  return {width:node.clientWidth,needed:ctx.measureText('Turn 24').width+
                    parseFloat(style.paddingLeft)+parseFloat(style.paddingRight)+24};
                }""")
                self.assertGreaterEqual(dropdown['width'],dropdown['needed'])
                for phase in ('move','draw'):
                    self.page.locator('[data-chart-control="'+phase+'"]').click()
                    result=self.page.locator('.win-history-inspector').evaluate("""node=>{
                      const table=node.querySelector('.position-wp-table'),style=getComputedStyle(node);
                      const contentWidth=node.clientWidth-parseFloat(style.paddingLeft)-parseFloat(style.paddingRight);
                      const split=[];
                      for(const cell of table.querySelectorAll('th,td'))for(const match of cell.textContent.matchAll(/\\S+/g)){
                        const range=document.createRange();range.setStart(cell.firstChild,match.index);
                        range.setEnd(cell.firstChild,match.index+match[0].length);
                        if(range.getClientRects().length!==1)split.push(match[0]);
                      }
                      return {width:table.getBoundingClientRect().width,contentWidth,split};
                    }""")
                    self.assertGreaterEqual(result['width'],result['contentWidth']-2)
                    self.assertEqual(result['split'],[])
                    self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
                if summary:
                    self.page.get_by_role('button',name='Start Review',exact=True).click()

    def test_preview_completion_stays_running_and_pause_can_resume_full_work(self):
        self.install_review()
        self.page.evaluate("""()=>{
          window.immediateKnownRequest=workerRequest;
          workerRequest=(type,p,options={})=>p.iterations===64?immediateKnownRequest(type,p):new Promise((resolve,reject)=>{
            options.signal.addEventListener('abort',()=>reject(Object.assign(Error('cancelled'),{name:'AbortError'})),{once:true});
          });
          window.heldKnownRun=runHindsightTimeline(testHindsightContext);
        }""")
        self.page.wait_for_function('trendResults.points.every(p=>p.mine===100 && p.provisional)')
        self.assertEqual(self.page.evaluate('trendResults.state'),'running')
        self.page.locator('.trend-settings > summary').click()
        expect(self.page.get_by_role('button',name='Pause analysis',exact=True)).to_be_visible()
        self.page.evaluate('pauseReviewAnalysis(currentReview)')
        self.page.evaluate('heldKnownRun')
        self.assertEqual(self.page.evaluate('trendResults.state'),'stopped')
        self.page.evaluate("""async()=>{
          window.resumedGradePumps=0;reviewQueue.pump=()=>resumedGradePumps++;
          workerRequest=immediateKnownRequest;
          await startWinTrend(currentReview);
        }""")
        self.assertEqual(self.page.evaluate('resumedGradePumps'),1)
        self.assertEqual(self.page.evaluate('trendResults.state'),'done')
        self.assertTrue(self.page.evaluate('trendResults.points.every(p=>!p.provisional)'))

    def test_phone_position_table_wraps_long_player_names_without_overflow(self):
        self.install_review()
        self.page.evaluate('runHindsightTimeline(testHindsightContext)')
        for width in (320,390):
            self.page.set_viewport_size({'width':width,'height':900})
            for summary in (False,True):
                if summary:
                    self.page.get_by_role('button',name='Game summary',exact=True).click()
                self.page.locator('[data-chart-control="turn"]').select_option('0')
                for phase in ('move','draw'):
                    self.page.locator('[data-chart-control="'+phase+'"]').click()
                    bounds=self.page.locator('.win-history-inspector').evaluate("""node=>{
                      const table=node.querySelector('.position-wp-table'),name=table.rows[2].cells[0];
                      name.textContent='ABCDEFGHIJKLMNOPQRSTUVWXYZ'.repeat(3);
                      const range=document.createRange();range.selectNodeContents(name);
                      return {inspector:node.getBoundingClientRect().right,table:table.getBoundingClientRect().right,
                        lines:range.getClientRects().length,overflow:table.scrollWidth-table.clientWidth};
                    }""")
                    self.assertLessEqual(bounds['table'],bounds['inspector'])
                    self.assertLessEqual(bounds['overflow'],1)
                    self.assertGreater(bounds['lines'],1)
                    self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
                if summary:
                    self.page.get_by_role('button',name='Start Review',exact=True).click()

    def test_real_worker_runs_known_state_batches_without_move_grading(self):
        self.open_game()
        result=self.page.evaluate("""async()=>{
          const first={grid:Array(15).fill('.'.repeat(15)),rack:'CATERS?',
            opponent_rack:'DEFINOU',mine:true,mover_score:0,other_score:0,
            bag_count:86,final_turns_remaining:0,action:'CAT 8G 10',score:10,rack_after:'ERS?ING'};
          const state=CrossplayHindsight.turnStates({turns:[first]},0).before;
          const pool=new QuackleWorkerPool({size:1,createWorker:()=>new Worker('quackle-worker.js')});
          const calls=[],send=pool.send.bind(pool);
          pool.send=(slot,task)=>{if(!task.init)calls.push({type:task.type,offset:task.payload.offset,count:task.payload.count});return send(slot,task);};
          try {
            const r=await pool.run('evaluateKnownState',{state,iterations:32,seed:CrossplayHindsight.seed(state)});
            return {valid:CrossplayHindsight.validResult(r,state,32),calls,samples:r.samples,win:r.win};
          }finally{pool.stop();}
        }""")
        self.assertTrue(result['valid'])
        self.assertEqual(result['samples'],32)
        self.assertEqual(result['calls'],[{'type':'evaluateKnownStateBatch','offset':0,'count':16},
                                          {'type':'evaluateKnownStateBatch','offset':16,'count':16}])

    def test_full_failure_keeps_preview_and_can_retry(self):
        self.install_review()
        result=self.page.evaluate("""async()=>{
          const immediate=workerRequest;
          workerRequest=(type,p)=>p.iterations===64?immediate(type,p):Promise.reject(Error('Full estimate timed out.'));
          await runHindsightTimeline(testHindsightContext);
          window.retryKnownRequest=immediate;
          return {state:trendResults.state,points:trendResults.points};
        }""")
        self.assertEqual(result['state'],'error')
        self.assertTrue(all(p['mine']==100 and p['provisional'] and p['retryable'] for p in result['points']))
        expect(self.page.locator('#trendStatus')).to_contain_text('Retry analysis')
        self.page.evaluate('workerRequest=retryKnownRequest;startWinTrend(currentReview)')
        self.page.wait_for_function('trendResults.state === "done" && !testHindsightContext.hindsightRunning')
        self.assertTrue(self.page.evaluate('trendResults.points.every(p=>p.mine===100 && !p.provisional && !p.retryable)'))

    def test_grade_completion_does_not_save_completed_position_summary_again(self):
        self.install_review()
        result=self.page.evaluate("""async()=>{
          const c=testHindsightContext;c.cache.shared=false;c.openingState='done';
          c.openingInput={unavailable:true};c.cache.unsaved={};c.cache.completed={};
          window.savedAnalysisBodies=[];const originalApi=api;
          api=(path,options)=>{
            if(options?.method==='PUT'&&path.endsWith('/analysis'))savedAnalysisBodies.push(JSON.parse(options.body));
            return originalApi(path,options);
          };
          await runHindsightTimeline(c);await flushAnalysisSave(c.review,c.cache);
          await finishReviewAnalysis(c);
          return {error:c.cache.saveError,positionSaves:savedAnalysisBodies.filter(b=>b.win_trend).length,
            gradeSaves:savedAnalysisBodies.filter(b=>b.move_quality).length};
        }""")
        self.assertIsNone(result['error'])
        self.assertEqual(result['positionSaves'],1)
        self.assertEqual(result['gradeSaves'],1)

    def test_finished_grades_save_while_position_estimates_are_pending(self):
        self.install_review()
        result=self.page.evaluate("""async()=>{
          const c=testHindsightContext;c.cache.shared=false;c.openingState='done';
          c.openingInput={unavailable:true};c.cache.unsaved={};c.cache.completed={};
          window.savedAnalysisBodies=[];const originalApi=api;
          api=(path,options)=>{
            if(options?.method==='PUT'&&path.endsWith('/analysis'))savedAnalysisBodies.push(JSON.parse(options.body));
            return originalApi(path,options);
          };
          await finishReviewAnalysis(c);
          return {error:c.cache.saveError,bodies:savedAnalysisBodies,trend:c.trend.state,quality:c.quality.state};
        }""")
        self.assertIsNone(result['error'])
        self.assertEqual(result['quality'],'done')
        self.assertEqual(result['trend'],'running')
        self.assertEqual(len(result['bodies']),1)
        self.assertEqual(result['bodies'][0]['move_quality']['state'],'done')
        self.assertNotIn('win_trend',result['bodies'][0])

    def test_cached_position_summary_saves_once_and_missing_summary_is_repaired(self):
        self.install_review()
        result=self.page.evaluate("""async()=>{
          const c=testHindsightContext;await runHindsightTimeline(c);
          window.summarySaveCount=0;const originalApi=api;
          let releaseSave,saveStarted;
          const held=new Promise(resolve=>releaseSave=resolve),started=new Promise(resolve=>saveStarted=resolve);
          api=(path,options)=>{
            if(options?.method==='PUT'&&JSON.parse(options.body).win_trend){
              summarySaveCount++;saveStarted();return held.then(()=>originalApi(path,options));
            }
            return originalApi(path,options);
          };
          c.cache.shared=false;
          await runHindsightTimeline(c);await started;
          await runHindsightTimeline(c);releaseSave();await flushAnalysisSave(c.review,c.cache);
          await runHindsightTimeline(c);await flushAnalysisSave(c.review,c.cache);
          const afterRepeated=summarySaveCount;
          const reverse=value=>Array.isArray(value)?value.map(reverse):value&&typeof value==='object'?
            Object.fromEntries(Object.keys(value).reverse().map(k=>[k,reverse(value[k])])):value;
          const stored={version:ANALYSIS_VERSION,turn_analyses:structuredClone(c.cache.results),win_trend:reverse(c.trend)};
          const restored=makeReviewContext({...c.review,stored_analysis:stored},c.owner);
          await runHindsightTimeline(restored);await flushAnalysisSave(restored.review,restored.cache);
          const afterRestored=summarySaveCount;
          delete stored.win_trend;
          const repaired=makeReviewContext({...c.review,stored_analysis:stored},c.owner);
          await runHindsightTimeline(repaired);await flushAnalysisSave(repaired.review,repaired.cache);
          await runHindsightTimeline(repaired);await flushAnalysisSave(repaired.review,repaired.cache);
          return {afterRepeated,afterRestored,afterRepaired:summarySaveCount,calls:knownStateCalls.length};
        }""")
        self.assertEqual(result['afterRepeated'],1)
        self.assertEqual(result['afterRestored'],1)
        self.assertEqual(result['afterRepaired'],2)

    def test_failed_position_summary_save_is_retried_without_recomputing(self):
        self.install_review()
        result=self.page.evaluate("""async()=>{
          const c=testHindsightContext;await runHindsightTimeline(c);c.cache.shared=false;
          let fail=true,attempts=0;const originalApi=api;
          api=(path,options)=>{
            if(options?.method==='PUT'&&JSON.parse(options.body).win_trend){
              attempts++;if(fail)return Promise.reject(Error('Save offline.'));
            }
            return originalApi(path,options);
          };
          const calls=knownStateCalls.length;
          await runHindsightTimeline(c);await analysisSaveQueue;
          const pending=Boolean(c.cache.completed?.win_trend),firstError=c.cache.saveError;
          fail=false;await runHindsightTimeline(c);await flushAnalysisSave(c.review,c.cache);
          await runHindsightTimeline(c);await flushAnalysisSave(c.review,c.cache);
          return {pending,firstError,error:c.cache.saveError,attempts,recomputed:knownStateCalls.length!==calls};
        }""")
        self.assertTrue(result['pending'])
        self.assertEqual(result['firstError'],'Save offline.')
        self.assertIsNone(result['error'])
        self.assertEqual(result['attempts'],2)
        self.assertFalse(result['recomputed'])

    def test_missing_known_state_stays_a_gap_despite_full_private_estimates(self):
        self.install_review()
        expect(self.page.locator('.win-history-opening')).to_have_attribute('data-estimate','missing')
        self.assertEqual(self.page.locator('.win-history-segment').count(),0)
        expect(self.page.locator('.win-bar')).to_have_attribute('aria-label', __import__('re').compile('No estimate available'))


if __name__=='__main__':
    unittest.main()
