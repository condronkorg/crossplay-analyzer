"""Opening passes use NYT position markers and repair earlier parser failures."""

import unittest

from playwright.sync_api import expect

import test_review_browser as review_browser


class OpeningPassBrowserTest(unittest.TestCase):
    setUpClass = classmethod(review_browser.ReviewBrowserTest.setUpClass.__func__)
    setUp = review_browser.ReviewBrowserTest.setUp
    tearDown = review_browser.ReviewBrowserTest.tearDown
    route_request = review_browser.ReviewBrowserTest.route_request
    connect = review_browser.ReviewBrowserTest.connect
    open_game = review_browser.ReviewBrowserTest.open_game
    start_review = review_browser.ReviewBrowserTest.start_review

    def test_opening_pass_repairs_saved_parser_failure_and_keeps_zero_draw(self):
        self.open_game()
        result = self.page.evaluate("""async () => {
          const grid=Array(15).fill('.'.repeat(15));
          const turn={...currentReview.turns[0],action:'pass -- 0',score:0,
            rack:'CATERS?',rack_after:'CATERS?',grid,grid_after:grid,
            bag_count:86,final_turns_remaining:0,mover_score:0,other_score:0};
          const oldPoint={turnIndex:0,turn:1,mine:true,action:turn.action,category:'unrated',
            loss:null,reason:'The recorded move could not be read.',retryable:true};
          const review={...currentReview,turns:[turn],turn_count:1,stored_analysis:{
            version:ANALYSIS_VERSION,turn_analyses:{},move_quality:{state:'done',
              evaluation:MOVE_QUALITY_EVALUATION,points:[oldPoint]}}};
          const context=makeReviewContext(review,analysisOwner());
          const pending=[...context.states];
          currentReview=review;
          reviewQueue.contexts.set(review.id,context);
          positionCache=context.cache;moveQualityResults=context.quality;trendResults=context.trend;
          await analyzeReviewTurn(context,0,'review');
          await finishReviewAnalysis(context);
          const full=structuredClone(context.quality.points[0]);
          const saveError=await flushAnalysisSave(review,context.cache);
          const response=await api('games/'+review.id+'/review');
          const restored=makeReviewContext({...review,stored_analysis:response.stored_analysis},analysisOwner());
          selectTurn(0);boardView='before';renderReview();
          return {pending,full,saveError,restored:restored.quality?.points[0],states:restored.states,
            errors:[...context.cache.errors]};
        }""")
        self.assertEqual(result['pending'], ['pending-preview'])
        self.assertIn('playedWin', result['full'], result)
        self.assertEqual(result['full']['playedMove']['action'], 'pass')
        self.assertFalse(result['full']['provisional'])
        self.assertEqual(result['full']['samples'], 277)
        self.assertTrue(0 <= result['full']['playedWin'] <= 1)
        self.assertEqual(result['full']['draw']['drawCount'], 0)
        self.assertEqual(result['full']['draw']['beforeWin'], result['full']['draw']['afterWin'])
        self.assertEqual(result['full']['draw']['luck'], 0)
        self.assertIsNone(result['saveError'])
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['restored'], result['full'])
        self.assertEqual(result['states'], ['done'])
        expect(self.page.locator('.turn-meta .turn-wp-played')).not_to_have_text('WP —')
        self.assertNotIn('could not be read', self.page.locator('.turn-meta').inner_text())


if __name__ == '__main__':
    unittest.main()
