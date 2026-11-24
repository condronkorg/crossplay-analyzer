"""Analysis interruption checks through the real review page and queue."""

import unittest

from playwright.sync_api import expect

import test_review_browser as review_browser


class RefreshAnalysisBrowserTest(unittest.TestCase):
    setUpClass = classmethod(review_browser.ReviewBrowserTest.setUpClass.__func__)
    setUp = review_browser.ReviewBrowserTest.setUp
    tearDown = review_browser.ReviewBrowserTest.tearDown
    route_request = review_browser.ReviewBrowserTest.route_request
    connect = review_browser.ReviewBrowserTest.connect
    open_game = review_browser.ReviewBrowserTest.open_game
    start_review = review_browser.ReviewBrowserTest.start_review

    def test_refresh_replaces_queue_without_losing_results_or_late_state_writes(self):
        self.open_game()
        result = self.page.evaluate("""async () => {
          const oldQueue = getReviewQueue(), old = contextFor(currentReview);
          const owner = analysisOwner(), cache = old.cache;
          cache.results.keep = {value: 7};
          cache.unsaved = {keep: {value: 7}};
          cache.completed = {move_quality: {state: 'running'}};
          old.quality = {state: 'running', points: [{turnIndex: 0, category: 'best', loss: 0, playedWin: .6, bestWin: .6}]};
          old.trend = {state: 'running', points: [{x: 0, mine: 60}]};
          let savePayload;
          const originalApi = api;
          api = (path, options) => {
            if (!path.endsWith('/analysis')) return originalApi(path, options);
            savePayload = JSON.parse(options.body);
            return Promise.resolve({});
          };
          // Opening work is already complete; this regression interrupts a move.
          old.openingState = 'done';
          old.states = old.states.map(() => 'done');
          old.states[1] = 'pending-preview';
          oldQueue.run = () => new Promise(resolve => {window.finishOld = resolve;});
          oldQueue.concurrency = 1;
          oldQueue.pump();
          await Promise.resolve();
          window.resumeCalls = 0;
          analyzeReviewTurn = () => {resumeCalls++; return new Promise(resolve => {window.finishNew = resolve;});};
          const snapshot = suspendAnalysisForRefresh();
          resumeAnalysisAfterRefresh(snapshot);
          const next = contextFor(currentReview);
          finishOld({complete: true});
          await new Promise(resolve => setTimeout(resolve, 0));
          return {sameOwner: analysisOwner() === owner, closed: oldQueue.closed,
            newQueue: getReviewQueue() !== oldQueue, cloned: next !== old,
            sameCache: next.cache === cache, keep: next.cache.results.keep.value,
            unsaved: savePayload.turn_analyses.keep.value, completed: Boolean(savePayload.move_quality),
            distinctStates: next.states !== old.states, state: next.states[1],
            oldState: old.states[1], resumed: resumeCalls,
            distinctQuality: next.quality !== old.quality && next.quality.points !== old.quality.points,
            distinctTrend: next.trend !== old.trend && next.trend.points !== old.trend.points};
        }""")
        self.assertEqual(result, {'sameOwner': True, 'closed': True, 'newQueue': True,
                                 'cloned': True, 'sameCache': True, 'keep': 7,
                                 'unsaved': 7, 'completed': True, 'distinctStates': True,
                                 'state': 'running-preview', 'oldState': 'done', 'resumed': 1,
                                 'distinctQuality': True, 'distinctTrend': True})

    def test_refresh_discards_analysis_that_finishes_a_late_frequency_lookup(self):
        self.open_game()
        self.page.evaluate("""async () => {
          const context = contextFor(currentReview);
          context.paused = true;
          context.quality = {state: 'running', points: []};
          const selected = {action: 'place', position: '8G', tiles: 'CAT', score: 5,
            equity: 10, staticEquity: 10, win: .6};
          ensurePositionAnalysis = async () => ({selected, best: selected,
            bestStaticEquity: 10, moves: [selected], staticMoves: [selected], iterationsCompleted: 277});
          loadWordFrequency = () => new Promise(resolve => {window.finishFrequency = resolve;});
          window.oldAnalysisResult = null;
          analyzeReviewTurn(context, 0).then(() => {oldAnalysisResult = 'applied';},
            error => {oldAnalysisResult = error.name;});
          await Promise.resolve(); await Promise.resolve();
          const snapshot = suspendAnalysisForRefresh();
          resumeAnalysisAfterRefresh(snapshot);
          finishFrequency(null);
        }""")
        self.page.wait_for_function('oldAnalysisResult !== null')
        self.assertEqual(self.page.evaluate('oldAnalysisResult'), 'AbortError')
        self.assertEqual(self.page.evaluate('contextFor(currentReview).quality.points'), [])
        self.assertTrue(self.page.evaluate('contextFor(currentReview).paused'))

    def test_refresh_retains_a_save_that_fails_after_queue_is_replaced(self):
        self.open_game()
        self.page.evaluate("""async () => {
          const context = contextFor(currentReview);
          context.paused = true;
          context.cache.unsaved = {saved: {value: 12}};
          const original = api;
          api = (path, options) => path.endsWith('/analysis')
            ? new Promise((resolve, reject) => {window.failSave = reject;}) : original(path, options);
          window.saveResult = undefined;
          flushAnalysisSave(currentReview, context.cache).then(value => {saveResult = value;});
          await Promise.resolve(); await Promise.resolve();
          const snapshot = suspendAnalysisForRefresh();
          resumeAnalysisAfterRefresh(snapshot);
          failSave(new Error('Synthetic save failure'));
        }""")
        self.page.wait_for_function('saveResult !== undefined')
        self.assertEqual(self.page.evaluate('contextFor(currentReview).cache.unsaved.saved.value'), 12)
        self.assertEqual(self.page.evaluate('contextFor(currentReview).cache.saveError'), 'Synthetic save failure')


if __name__ == '__main__':
    unittest.main()
