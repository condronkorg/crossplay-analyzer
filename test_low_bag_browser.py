"""Low-bag exchange review, persistence, and cache migration. Synthetic data only."""
import unittest
from playwright.sync_api import expect
import test_review_browser as review_browser


class LowBagBrowserTest(unittest.TestCase):
    setUpClass = classmethod(review_browser.ReviewBrowserTest.setUpClass.__func__)
    setUp = review_browser.ReviewBrowserTest.setUp
    tearDown = review_browser.ReviewBrowserTest.tearDown
    route_request = review_browser.ReviewBrowserTest.route_request
    connect = review_browser.ReviewBrowserTest.connect
    open_game = review_browser.ReviewBrowserTest.open_game
    start_review = review_browser.ReviewBrowserTest.start_review

    def test_shortlist_coverage_reaches_the_move_explanation(self):
        self.open_game()
        text = self.page.evaluate("""() => {
          const turn=currentReview.turns[0];
          const move={action:'pass',position:'',tiles:'',score:0,leave:turn.rack,
            equity:0,staticEquity:0,win:.5};
          const point=qualityPointFromResult(0,turn,{selected:move,best:move,
            bestStaticEquity:0,moves:[move],iterationsCompleted:277,
            searchedCandidateCount:25,candidatePolicy:CrossplayBagEmptying.POLICY,
            evaluatedBagEmptyingCount:10,totalBagEmptyingCount:352});
          return makeTurnCoach(turn,point).textContent;
        }""")
        self.assertIn('Compared 25 selected moves, including 10 of 352 legal bag-emptying placements.', text)

    def test_late_full_profile_is_final_and_discloses_precision(self):
        self.open_game()
        result=self.page.evaluate("""() => {
          Object.defineProperty(navigator,'hardwareConcurrency',{value:2,configurable:true});
          const turn={...currentReview.turns[0],bag_count:21};
          const move={action:'pass',position:'',tiles:'',score:0,leave:turn.rack,
            equity:0,staticEquity:0,win:.5};
          const samples=CrossplayBagEmptying.fullIterations(21);
          const raw={selected:move,best:move,bestStaticEquity:0,moves:[move],
            iterationsCompleted:samples,bagCount:21};
          const point=qualityPointFromResult(0,turn,raw);
          const draw=drawPoint({...raw,beforeWin:.5,afterWin:.5,drawCount:0,rackAfter:turn.rack});
          return{point,draw,samples,text:makeTurnCoach(turn,point).textContent};
        }""")
        self.assertFalse(result['point']['provisional'])
        self.assertFalse(result['draw']['provisional'])
        self.assertEqual(result['point']['samples'],result['samples'])
        self.assertGreater(result['samples'],64)
        self.assertLess(result['samples'],277)
        self.assertIn('Full estimate',result['text'])
        self.assertIn(f"{result['samples']} samples per move",result['text'])
        self.assertIn('more sampling uncertainty',result['text'])

    def test_device_profile_restore_rebuilds_equal_win_summary_with_actual_samples(self):
        self.open_game()
        result=self.page.evaluate("""() => {
          Object.defineProperty(navigator,'hardwareConcurrency',{value:2,configurable:true});
          const turn={...currentReview.turns[0],index:0,rack:'WEAFTUO',rack_after:'WEAFTUO',
            action:'PASS 0',bag_count:2,final_turns_remaining:0};
          const cache=new AnalysisCache({},POSITION_PARAMETERS);
          const move={action:'pass',position:'',tiles:'',leave:turn.rack,score:0,equity:0,
            staticEquity:0,win:1,samples:192,terminalSamples:192};
          const raw={simulated:true,endgamePolicy:'crossplay-endgame-v2',
            samplingVersion:'paired-review-v1',seed:cache.seed(turn),
            moves:[move],selected:move,best:move,staticMoves:[move],bestStaticEquity:0,
            candidateCount:1,totalPlayaheads:192,plies:-1,playToEnd:true,
            simulationMode:'play-to-end',winModel:'terminal',bagCount:2,finalTurnsRemaining:0,
            iterationsRequested:192,iterationsCompleted:192,candidatePolicy:CrossplayBagEmptying.POLICY,
            equityMoves:[move],generatedCandidateCount:100,totalBagEmptyingCount:90,
            searchedCandidateCount:1,requiredBagEmptyingCount:0,evaluatedBagEmptyingCount:0};
          const point={...qualityPointFromResult(0,turn,raw),samples:277};
          const review={...currentReview,turns:[turn],stored_analysis:{version:ANALYSIS_VERSION,
            turn_analyses:{[cache.key(turn,'review')]:raw},
            move_quality:{evaluation:MOVE_QUALITY_EVALUATION,state:'done',current:1,points:[point]}}};
          const context=makeReviewContext(review,analysisOwner());
          const restored=context.quality.points[0];
          return{restored,text:makeTurnCoach(turn,restored).textContent};
        }""")
        self.assertEqual(result['restored']['playedWin'],1)
        self.assertEqual(result['restored']['bestWin'],1)
        self.assertEqual(result['restored']['samples'],192)
        self.assertFalse(result['restored']['provisional'])
        self.assertIn('192 samples per move',result['text'])
        self.assertIn('more sampling uncertainty',result['text'])
        self.assertNotIn('277 samples',result['text'])

    def test_old_exchange_rules_are_not_reused(self):
        self.open_game()
        result = self.page.evaluate("""() => makeReviewContext({...currentReview, stored_analysis: {
          version: 'quackle-equity-v1', turn_analyses: {}, move_quality: {
            evaluation: MOVE_QUALITY_EVALUATION, state:'done', points:[]}
        }}, analysisOwner()).quality""")
        self.assertIsNone(result)

    def test_legacy_endgame_summary_is_removed_before_recalculation(self):
        self.open_game()
        result = self.page.evaluate("""() => {
          const early = {...currentReview.turns[0], index:0, bag_count:86};
          const late = {...currentReview.turns[0], index:1, bag_count:2};
          const point = index => ({turnIndex:index, turn:index+1, mine:true,
            action:currentReview.turns[0].action, evaluation:MOVE_QUALITY_EVALUATION,
            playedWin:1, bestWin:1, loss:0, category:'best', samples:277,
            provisional:false, draw:{beforeWin:1, afterWin:1, drawCount:2}});
          const review = {...currentReview, turns:[early,late], stored_analysis:{
            version:ANALYSIS_VERSION, turn_analyses:{},
            move_quality:{evaluation:MOVE_QUALITY_EVALUATION, state:'done',
              current:2, points:[point(0),point(1)]},
            win_trend:{evaluation:MOVE_QUALITY_EVALUATION, state:'done',
              points:[{x:1, mine:100, theirs:0}]}}};
          const owner = makeReviewContext(review,analysisOwner());
          const guest = makeReviewContext(review,analysisOwner(),true);
          return {owner:{points:owner.quality.points, state:owner.quality.state,
              current:owner.quality.current, trend:owner.trend, states:owner.states},
            guest:{points:guest.quality.points, shared:guest.cache.shared}};
        }""")
        self.assertEqual([point['turnIndex'] for point in result['owner']['points']], [0])
        self.assertEqual(result['owner']['points'][0]['playedWin'], 1)
        self.assertEqual(result['owner']['state'], 'running')
        self.assertEqual(result['owner']['current'], 1)
        self.assertEqual(result['owner']['trend']['state'], 'running')
        self.assertEqual([point['x'] for point in result['owner']['trend']['points']], [0])
        self.assertEqual(result['owner']['states'], ['pending-preview', 'pending-preview'])
        self.assertEqual([point['turnIndex'] for point in result['guest']['points']], [0])
        self.assertTrue(result['guest']['shared'])

    def test_endgame_refresh_keeps_valid_cached_opening(self):
        self.open_game()
        result = self.page.evaluate("""() => {
          const grid = Array(15).fill('.'.repeat(15));
          const pass = (mine,rack,index,bag_count) => ({mine,rack,index,bag_count,
            rack_after:rack,action:'PASS',score:0,grid:[...grid],grid_after:[...grid],
            mover_score:0,other_score:0,final_turns_remaining:0});
          const turns = [pass(true,'CATERS?',0,86),pass(false,'ADEINOR',1,86),
            pass(true,'CATERS?',2,2)];
          const review = {...currentReview,turns};
          const input = CrossplayOpening.describe(review);
          const raw = {simulated:true,samplingVersion:'initial-deal-v1',policy:'static-best-v1',
            firstRack:input.firstRack,secondRack:input.secondRack,seed:CrossplayOpening.seed(input),
            baselineWin:.55,firstOnlyWin:.65,secondOnlyWin:.45,afterWin:.6,
            firstWpa:12.5,secondWpa:7.5,iterationsRequested:277,iterationsCompleted:277,
            plies:2,playToEnd:false,simulationMode:'2-ply',winModel:'bogowin'};
          const point = index => ({turnIndex:index,turn:index+1,mine:turns[index].mine,
            action:'PASS',evaluation:MOVE_QUALITY_EVALUATION,playedWin:1,bestWin:1,
            loss:0,category:'best',samples:277,provisional:false});
          review.stored_analysis = {version:ANALYSIS_VERSION,
            turn_analyses:{[CrossplayOpening.key(input,277)]:raw},
            move_quality:{evaluation:MOVE_QUALITY_EVALUATION,state:'done',current:3,
              points:[point(0),point(1),point(2)],opening:{afterWin:.99}}};
          const context=makeReviewContext(review,analysisOwner());
          return {opening:context.quality.opening,openingState:context.openingState,
            points:context.quality.points.map(point=>point.turnIndex),state:context.quality.state};
        }""")
        self.assertEqual(result['points'], [0, 1])
        self.assertEqual(result['state'], 'running')
        self.assertEqual(result['openingState'], 'done')
        self.assertEqual(result['opening']['afterWin'], .6)
        self.assertEqual(result['opening']['samples'], 277)
        self.assertEqual(result['opening']['firstWpa'], 12.5)

    def test_current_policy_raw_result_repairs_stale_summary_and_rejects_old_raw(self):
        self.open_game()
        result = self.page.evaluate("""() => {
          const grid = ['CD........G....','.......A.NOUN.Y','.......L...TAME',
            '......YEAH...E.','.........ab.yA.','......LOB.OWED.',
            '..PROG..I.TEN.F','....D..JOGS...I','...LASSI.O..RUN',
            '.......V.R.MI..','...C...E..HIPS.','...A.BASE.A.E..',
            '...R.O..D.V....','..RENT.LITER...','.....H..T......'];
          const turn = {...currentReview.turns[0], index:0, grid, grid_after:grid,
            rack:'WEAFTUO', rack_after:'WEAFTUO', action:'PASS', bag_count:0,
            final_turns_remaining:1};
          const cache = new AnalysisCache({},POSITION_PARAMETERS);
          const move = {action:'pass', position:'', tiles:'', leave:'WEAFTUO',
            score:0, equity:0, staticEquity:0, win:1, samples:277, terminalSamples:277};
          const raw = {simulated:true, endgamePolicy:'crossplay-endgame-v2',
            samplingVersion:'paired-review-v1', seed:cache.seed(turn),
            moves:[move], selected:move, best:move, staticMoves:[{...move,equity:0}],
            bestStaticEquity:0, candidateCount:1, totalPlayaheads:277,
            plies:-1, playToEnd:true, simulationMode:'play-to-end',winModel:'terminal',
            bagCount:0, finalTurnsRemaining:1, iterationsRequested:277,iterationsCompleted:277};
          const point = {turnIndex:0, turn:1, mine:true, action:'PASS',
            evaluation:MOVE_QUALITY_EVALUATION, playedWin:1,bestWin:1,loss:0,
            category:'best',samples:277,provisional:false,endgamePolicy:'crossplay-endgame-v2'};
          const saved = {...currentReview,turns:[turn],stored_analysis:{
            version:ANALYSIS_VERSION,turn_analyses:{[cache.key(turn,'review')]:raw},
            move_quality:{evaluation:MOVE_QUALITY_EVALUATION,state:'done',current:1,points:[point]}}};
          const restored = makeReviewContext(saved,analysisOwner());
          const stale = structuredClone(saved);
          stale.stored_analysis.move_quality.points[0].playedWin = 0;
          stale.stored_analysis.move_quality.points[0].bestWin = 0;
          const mismatched = makeReviewContext(stale,analysisOwner());
          const oldSummary = structuredClone(saved);
          delete oldSummary.stored_analysis.move_quality.points[0].endgamePolicy;
          const unmarked = makeReviewContext(oldSummary,analysisOwner());
          delete raw.endgamePolicy;
          const oldPolicy = makeReviewContext(saved,analysisOwner());
          return {restored:{quality:restored.quality,states:restored.states},
            mismatched:mismatched.quality,unmarked:unmarked.quality,oldPolicy:oldPolicy.quality};
        }""")
        self.assertEqual(len(result['restored']['quality']['points']), 1)
        self.assertEqual(result['restored']['quality']['state'], 'done')
        self.assertEqual(result['restored']['states'], ['done'])
        for repaired in (result['mismatched'], result['unmarked']):
            self.assertEqual(repaired['state'], 'done')
            self.assertEqual(len(repaired['points']), 1)
            self.assertEqual(repaired['points'][0]['playedWin'], 1)
            self.assertEqual(repaired['points'][0]['bestWin'], 1)
            self.assertEqual(repaired['points'][0]['endgamePolicy'], 'crossplay-endgame-v2')
        self.assertEqual(result['oldPolicy']['points'], [])
        self.assertEqual(result['oldPolicy']['state'], 'running')

    def test_permanent_unrated_endgame_does_not_restart_on_each_load(self):
        self.open_game()
        result = self.page.evaluate("""() => {
          const turn = {...currentReview.turns[0],bag_count:2,action:'swap -- 0',swapped_tiles:null};
          let reason;
          try { CrossplayReview.playedMove(turn); } catch(error) { reason=error.message; }
          const point = makeUnratedPoint(0,turn,reason);
          const review = {...currentReview,turns:[turn],stored_analysis:{version:ANALYSIS_VERSION,
            turn_analyses:{},move_quality:{evaluation:MOVE_QUALITY_EVALUATION,state:'done',
              current:1,points:[point]}}};
          const first = makeReviewContext(review,analysisOwner());
          const next = makeReviewContext({...review,stored_analysis:{...review.stored_analysis,
            move_quality:first.quality}},analysisOwner());
          return {first:first.quality,next:next.quality,states:next.states};
        }""")
        self.assertEqual(result['first']['state'], 'done')
        self.assertEqual(result['next']['state'], 'done')
        self.assertEqual(len(result['next']['points']), 1)
        self.assertEqual(result['next']['points'][0]['category'], 'unrated')
        self.assertEqual(result['states'], ['done'])

    def test_four_tile_swap_with_six_bag_tiles_has_saved_move_and_draw_wp(self):
        self.open_game()
        result = self.page.evaluate("""async () => {
          const grid = ['CD........G....','.......A.NOUN.Y','.......L...TAME',
            '......YEAH...E.','.........ab.yA.','......LOB.OWED.',
            '..PROG..I.TEN.F','....D..JOGS...I','...LASSI.O..RUN',
            '.......V.R.MI..','...C...E..HIPS.','...A.BASE.A.E..',
            '...R.O..D.V....','..RENT.LITER...','.....H..T......'];
          let remove=6;
          const board=grid.map(row=>[...row].map(tile=>tile!=='.' && remove>0 ? (remove--,'.') : tile).join(''));
          const rack='WEAFTUO', swapped='WEAF', retained='TUO';
          const unseen=Object.entries(CrossplayLuck.TILE_COUNTS).flatMap(([tile,count])=>Array(count).fill(tile));
          for (let tile of board.join('')+rack) {
            if (tile==='.') continue;
            if (tile===tile.toLowerCase()) tile='?';
            unseen.splice(unseen.indexOf(tile),1);
          }
          const turn={...currentReview.turns[0], action:'swap -- 0', rack,
            swapped_tiles:swapped, rack_after:retained+unseen.slice(0,4).join(''),
            grid:board, grid_after:board, bag_count:6, final_turns_remaining:0};
          currentReview.turns=[turn];
          const context=makeReviewContext(currentReview,analysisOwner());
          reviewQueue.contexts.set(currentReview.id,context);
          context.states=['pending-preview']; context.quality=null; context.trend=null;
          await analyzeReviewTurn(context,0,'preview');
          const early=structuredClone(context.quality.points[0]);
          await analyzeReviewTurn(context,0,'review');
          await finishReviewAnalysis(context);
          const full=structuredClone(context.quality.points[0]);
          const saveError=await flushAnalysisSave(currentReview,context.cache);
          const response=await api('games/'+currentReview.id+'/review');
          const restored=makeReviewContext({...currentReview, stored_analysis:response.stored_analysis},analysisOwner());
          selectTurn(0); boardView='before'; renderReview();
          return {early,full,saveError,restored:restored.quality?.points[0],states:restored.states,
            version:response.stored_analysis?.version,errors:[...context.cache.errors]};
        }""")
        self.assertIn('playedWin', result['early'], result)
        self.assertTrue(result['early']['provisional'])
        self.assertFalse(result['full']['provisional'])
        self.assertTrue(0 <= result['full']['playedWin'] <= 1)
        self.assertEqual(result['full']['playedMove']['action'], 'exchange')
        self.assertEqual(result['full']['draw']['drawCount'], 4)
        self.assertFalse(result['full']['draw']['provisional'])
        self.assertIsNone(result['saveError'])
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['restored'], result['full'])
        self.assertEqual(result['states'], ['done'])
        expect(self.page.locator('.turn-meta .turn-wp-played')).not_to_have_text('WP —')
        expect(self.page.locator('.turn-meta .turn-wp-draw')).to_contain_text('pp')
        expect(self.page.locator('.turn-row .turn-wp-loss').first).to_contain_text('pp')
        self.assertNotIn('not legal', self.page.locator('.turn-meta').inner_text())


if __name__ == '__main__':
    unittest.main()
