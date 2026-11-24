"""Manual refresh remains available and takes priority over stale work."""

import unittest

from playwright.sync_api import expect

import test_review_browser as review_browser


class RefreshBrowserTest(unittest.TestCase):
    setUpClass = classmethod(review_browser.ReviewBrowserTest.setUpClass.__func__)
    setUp = review_browser.ReviewBrowserTest.setUp
    tearDown = review_browser.ReviewBrowserTest.tearDown
    route_request = review_browser.ReviewBrowserTest.route_request
    connect = review_browser.ReviewBrowserTest.connect
    open_game = review_browser.ReviewBrowserTest.open_game
    start_review = review_browser.ReviewBrowserTest.start_review

    def intercept_game_lists(self):
        self.page.evaluate("""() => {
          persistenceEnabled = true;
          signedInUser = {id: 'synthetic-refresh-owner'};
          CrossplayAuth.getSession = () => ({user: signedInUser});
          CrossplayAuth.accessToken = async () => null;
          for (const context of reviewQueue?.contexts.values() || []) {
            context.owner = context.cache.owner = analysisOwner();
          }
          window.savedRefreshGames = structuredClone(games);
          window.gameListRequests = [];
          window.refreshSavedPayloads = [];
          const originalFetch = window.fetch;
          window.fetch = (input, options = {}) => {
            const path = new URL(typeof input === 'string' ? input : input.url, location.origin).pathname;
            if (path === '/api/crossplay/games' && new URL(input, location.origin).searchParams.has('page_size')) {
              return Promise.resolve(new Response(JSON.stringify({games:[], next_cursor:null}), {status:200}));
            }
            if (path.endsWith('/analysis') && options.method === 'PUT') {
              refreshSavedPayloads.push(JSON.parse(options.body));
              return Promise.resolve(new Response('{}', {status:200}));
            }
            if (path !== '/api/crossplay/games' && path !== '/api/crossplay/games/refresh') return originalFetch(input, options);
            // A server can reply after cancellation. Keep that boundary under
            // test control so only production request ownership rejects it.
            return new Promise((resolve, reject) => gameListRequests.push({path, options, resolve, reject}));
          };
          updateAuthUi();
        }""")

    def respond(self, index, fresh=False, status=200):
        self.page.evaluate("""({index, fresh, status}) => {
          const items = structuredClone(savedRefreshGames);
          if (fresh) items.unshift({...items[0], id: 'fresh-game', opponent: 'New Opponent', timestamp: Date.now()});
          gameListRequests[index].resolve(new Response(JSON.stringify(status === 200 ? {games: items} : {error: 'Synthetic refresh outage'}),
            {status, headers: {'Content-Type': 'application/json'}}));
        }""", {'index': index, 'fresh': fresh, 'status': status})

    def test_refresh_stays_available_and_replaces_stalled_list_request(self):
        self.connect()
        expect(self.page.locator('.game-card')).to_have_count(2)
        self.intercept_game_lists()
        self.page.evaluate('void loadGames({refresh: true, background: true})')
        self.page.wait_for_function('gameListRequests.length === 1')
        expect(self.page.get_by_role('button', name='Refresh', exact=True)).to_be_enabled()
        self.page.get_by_role('button', name='Refresh', exact=True).click()
        self.page.wait_for_function('gameListRequests.length === 2')
        self.assertTrue(self.page.evaluate('gameListRequests[0].options.signal.aborted'))
        self.assertEqual(self.page.evaluate('JSON.parse(gameListRequests[1].options.body)'), {'if_stale': False})
        expect(self.page.get_by_role('button', name='Refresh', exact=True)).to_be_enabled()
        self.respond(1, fresh=True)
        expect(self.page.get_by_role('button', name='Review game against New Opponent')).to_be_visible()
        self.respond(0)
        self.page.evaluate('new Promise(resolve => setTimeout(resolve, 0))')
        expect(self.page.get_by_role('button', name='Review game against New Opponent')).to_be_visible()

    def test_automatic_check_cannot_replace_a_pending_manual_refresh(self):
        self.connect()
        expect(self.page.locator('.game-card')).to_have_count(2)
        self.intercept_game_lists()
        self.page.get_by_role('button', name='Refresh', exact=True).click()
        self.page.wait_for_function('gameListRequests.length === 1')
        self.page.evaluate('void refreshGamesIfDue()')
        self.page.evaluate('new Promise(resolve => setTimeout(resolve, 0))')
        self.assertEqual(self.page.evaluate('gameListRequests.length'), 1)
        self.assertFalse(self.page.evaluate('gameListRequests[0].options.signal.aborted'))
        self.respond(0, fresh=True)
        expect(self.page.get_by_role('button', name='Review game against New Opponent')).to_be_visible()

    def test_second_manual_refresh_wins_even_if_first_reply_arrives_late(self):
        self.connect()
        expect(self.page.locator('.game-card')).to_have_count(2)
        self.intercept_game_lists()
        self.page.get_by_role('button', name='Refresh', exact=True).click()
        self.page.wait_for_function('gameListRequests.length === 1')
        expect(self.page.get_by_role('button', name='Refresh', exact=True)).to_be_enabled()
        self.page.get_by_role('button', name='Refresh', exact=True).click()
        self.page.wait_for_function('gameListRequests.length === 2')
        self.assertTrue(self.page.evaluate('gameListRequests[0].options.signal.aborted'))
        self.respond(1, fresh=True)
        expect(self.page.locator('.game-card')).to_have_count(3)
        self.respond(0, status=503)
        self.page.evaluate('new Promise(resolve => setTimeout(resolve, 0))')
        expect(self.page.locator('.game-card')).to_have_count(3)
        expect(self.page.locator('#gameCount')).not_to_contain_text('failed')
        expect(self.page.get_by_role('button', name='Refresh', exact=True)).to_be_enabled()

    def test_refresh_stops_stuck_engine_without_losing_finished_or_unsaved_results(self):
        self.open_game()
        self.intercept_game_lists()
        self.page.route('**/quackle-worker.js', lambda route: route.fulfill(
            content_type='application/javascript', body="self.onmessage=({data:{id,type}})=>{if(type==='init')self.postMessage({id,result:{}});};"))
        self.page.evaluate("""() => {
          window.workerTerminations = 0;
          const terminate = Worker.prototype.terminate;
          Worker.prototype.terminate = function() {workerTerminations++; return terminate.call(this);};
          const context = contextFor(currentReview), turn = currentReview.turns[0];
          const key = context.cache.key(turn, 'candidates');
          context.cache.results[key] = {moves: [{action:'pass',position:'',tiles:'',equity:1}]};
          context.cache.unsaved = {[key]: context.cache.results[key]};
          window.finishedCacheKey = key;
          window.retainedCache = context.cache;
          window.stuckAnalysisState = 'pending';
          window.stuckAnalysis = workerRequest('synthetic-stalled-analysis', {}, {signal: reviewQueue.controller.signal})
            .then(() => stuckAnalysisState = 'completed', () => stuckAnalysisState = 'cancelled');
        }""")
        self.page.wait_for_function('quacklePool?.slots.some(slot => slot.ready && slot.task?.type === "synthetic-stalled-analysis")')
        self.page.get_by_role('button', name='Refresh', exact=True).click()
        self.page.wait_for_function('gameListRequests.length === 1')
        self.assertGreater(self.page.evaluate('workerTerminations'), 0)
        self.page.wait_for_function('stuckAnalysisState === \'cancelled\'', timeout=5_000)
        self.assertEqual(self.page.evaluate('retainedCache.results[finishedCacheKey].moves[0].equity'), 1)
        self.assertTrue(self.page.evaluate('Boolean(retainedCache.unsaved?.[finishedCacheKey])'))
        self.respond(0, fresh=True)
        expect(self.page.get_by_role('button', name='Review game against New Opponent')).to_be_visible()
        self.assertEqual(self.page.evaluate('contextFor(currentReview).cache.results[finishedCacheKey].moves[0].equity'), 1)
        self.assertTrue(self.page.evaluate('Boolean(contextFor(currentReview).cache.unsaved?.[finishedCacheKey]) || refreshSavedPayloads.some(payload => payload.turn_analyses?.[finishedCacheKey]?.moves?.[0]?.equity === 1)'))
        expect(self.page.get_by_role('button', name='Refresh', exact=True)).to_be_enabled()

    def test_refresh_failure_keeps_saved_games_and_allows_immediate_retry(self):
        self.connect()
        expect(self.page.locator('.game-card')).to_have_count(2)
        self.intercept_game_lists()
        self.page.get_by_role('button', name='Refresh', exact=True).click()
        self.page.wait_for_function('gameListRequests.length === 1')
        self.respond(0, status=503)
        expect(self.page.get_by_text('Synthetic refresh outage', exact=False)).to_be_visible()
        expect(self.page.locator('.game-card')).to_have_count(2)
        self.page.get_by_role('button', name='Refresh', exact=True).click()
        self.page.wait_for_function('gameListRequests.length === 2')
        self.respond(1, fresh=True)
        expect(self.page.locator('.game-card')).to_have_count(3)

    def test_refresh_without_nyt_connection_explains_how_to_connect(self):
        expect(self.page.get_by_role('button', name='Refresh', exact=True)).to_be_enabled()
        self.page.get_by_role('button', name='Refresh', exact=True).click()
        expect(self.page.locator('#connectionState')).to_contain_text('Connect')
        expect(self.page.get_by_role('textbox', name='NYT-S cookie')).to_be_visible()
        expect(self.page.get_by_role('button', name='Refresh', exact=True)).to_be_enabled()

    def test_refresh_response_from_previous_account_cannot_replace_current_games(self):
        self.connect()
        expect(self.page.locator('.game-card')).to_have_count(2)
        self.intercept_game_lists()
        self.page.get_by_role('button', name='Refresh', exact=True).click()
        self.page.wait_for_function('gameListRequests.length === 1')
        self.page.evaluate("""() => {
          signedInUser = {id:'different-synthetic-owner'};
          clearAccountUi('Sign in again.');
        }""")
        self.respond(0, fresh=True)
        self.page.evaluate('new Promise(resolve => setTimeout(resolve, 0))')
        expect(self.page.locator('.game-card')).to_have_count(0)
        expect(self.page.get_by_role('button', name='Review game against New Opponent')).to_have_count(0)
        expect(self.page.get_by_role('button', name='Refresh', exact=True)).to_be_enabled()


    def test_stalled_refresh_times_out_and_can_be_retried(self):
        self.connect()
        expect(self.page.locator('.game-card')).to_have_count(2)
        self.intercept_game_lists()
        self.page.clock.install()
        self.page.get_by_role('button', name='Refresh', exact=True).click()
        self.page.wait_for_function('gameListRequests.length === 1')
        self.page.clock.fast_forward(30_001)
        expect(self.page.locator('#gameCount')).to_contain_text('timed out')
        expect(self.page.get_by_role('button', name='Refresh', exact=True)).to_be_enabled()
        self.page.get_by_role('button', name='Refresh', exact=True).click()
        self.page.wait_for_function('gameListRequests.length === 2')
        self.respond(1, fresh=True)
        expect(self.page.locator('.game-card')).to_have_count(3)


if __name__ == '__main__':
    unittest.main()
