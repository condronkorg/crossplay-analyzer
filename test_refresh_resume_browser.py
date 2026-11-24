"""A manual refresh resumes the saved catalog and the selected review."""

import unittest

import test_review_browser as review_browser


class RefreshResumeBrowserTest(unittest.TestCase):
    setUpClass = classmethod(review_browser.ReviewBrowserTest.setUpClass.__func__)
    setUp = review_browser.ReviewBrowserTest.setUp
    tearDown = review_browser.ReviewBrowserTest.tearDown
    route_request = review_browser.ReviewBrowserTest.route_request
    connect = review_browser.ReviewBrowserTest.connect
    open_game = review_browser.ReviewBrowserTest.open_game
    start_review = review_browser.ReviewBrowserTest.start_review

    def test_refresh_keeps_known_cards_while_saved_catalog_is_pending(self):
        self.open_game()
        result = self.page.evaluate('''async () => {
          const firstPage = structuredClone(games);
          const older = {...firstPage[0], id: 'older-saved-game', status: 'completed'};
          games.push(older); renderGames();
          const queue = getReviewQueue(); queue.concurrency = 0;
          for (const context of queue.contexts.values()) context.paused = true;
          persistenceEnabled = true; signedInUser = {id: 'synthetic-catalog-owner'};
          let catalogCalls = 0;
          api = async path => {
            if (path === 'games/refresh') return {games: firstPage};
            if (path.startsWith('games?')) {catalogCalls++; return new Promise(() => {});}
            return {};
          };
          await refreshGames();
          return {olderRetained: games.some(game => game.id === older.id), catalogCalls};
        }''')
        self.assertTrue(result['olderRetained'])
        self.assertEqual(result['catalogCalls'], 1)
        self.assertEqual(self.page.locator('.game-card').count(), 3)

    def test_refresh_restarts_an_aborted_catalog_page(self):
        self.open_game()
        result = self.page.evaluate('''async () => {
          const firstPage = structuredClone(games);
          const older = {...firstPage[0], id: 'older-saved-game', status: 'completed'};
          persistenceEnabled = true; signedInUser = {id: 'synthetic-catalog-owner'};
          const queue = getReviewQueue(); queue.concurrency = 0;
          for (const context of queue.contexts.values()) context.paused = true;
          let catalogCalls = 0, firstSignal;
          api = async (path, options) => {
            if (path === 'games/refresh') return {games: firstPage};
            if (path.startsWith('games?')) {
              catalogCalls++;
              if (catalogCalls === 1) {
                firstSignal = options.signal;
                return new Promise((resolve, reject) => options.signal.addEventListener('abort',
                  () => reject(accountChanged()), {once: true}));
              }
              return {games: [...firstPage, older]};
            }
            return {};
          };
          void loadAnalysisCatalog(queue);
          await refreshGames();
          await Promise.resolve();
          return {catalogCalls, aborted: firstSignal.aborted,
            olderRetained: games.some(game => game.id === older.id)};
        }''')
        self.assertEqual(result, {'catalogCalls': 2, 'aborted': True, 'olderRetained': True})

    def test_refresh_recovers_a_selected_review_still_loading(self):
        self.connect()
        self.page.wait_for_selector('.game-card')
        self.page.evaluate('''async () => {
          const queue = getReviewQueue();
          queue.load = async () => new Promise((resolve, reject) =>
            queue.controller.signal.addEventListener('abort', () => reject(accountChanged()), {once: true}));
          const id = games.find(game => game.status === 'completed').id;
          const opening = openGame(id);
          await Promise.resolve(); await Promise.resolve();
          await refreshGames();
          await opening;
        }''')
        self.page.wait_for_function('currentReview !== null', timeout=2000)
        self.assertEqual(self.page.evaluate('currentReview.id'), self.page.evaluate('activeGameId'))
        self.assertNotIn('Account changed.', self.page.locator('#reviewPanel').inner_text())


if __name__ == '__main__':
    unittest.main()
