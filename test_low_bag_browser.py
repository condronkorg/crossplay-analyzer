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

    def test_old_exchange_rules_are_not_reused(self):
        self.open_game()
        result = self.page.evaluate("""() => makeReviewContext({...currentReview, stored_analysis: {
          version: 'quackle-equity-v1', turn_analyses: {}, move_quality: {
            evaluation: MOVE_QUALITY_EVALUATION, state:'done', points:[]}
        }}, analysisOwner()).quality""")
        self.assertIsNone(result)

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
          const context=contextFor(currentReview);
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
