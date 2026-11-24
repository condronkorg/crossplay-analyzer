"""Refresh checks the saved connection before it asks for another NYT token."""

import unittest
from playwright.sync_api import expect
import test_review_browser as review_browser


class TokenRecoveryBrowserTest(unittest.TestCase):
    setUpClass = classmethod(review_browser.ReviewBrowserTest.setUpClass.__func__)
    setUp = review_browser.ReviewBrowserTest.setUp
    tearDown = review_browser.ReviewBrowserTest.tearDown
    route_request = review_browser.ReviewBrowserTest.route_request
    connect = review_browser.ReviewBrowserTest.connect

    def prepare_recovery(self, persistence=True):
        self.connect()
        expect(self.page.locator('.game-card')).to_have_count(2)
        self.page.evaluate('''persistent => {
          persistenceEnabled = persistent;
          signedInUser = {id:'synthetic-token-owner'};
          nytConfigured = false; activeNytCookie = '';
          CrossplayAuth.getSession = () => ({user:signedInUser});
          CrossplayAuth.accessToken = async () => 'synthetic-google-token';
          analyzeReviewTurn = () => new Promise(() => {});
          loadAnalysisCatalog = async () => {};
          window.recoveryGames = structuredClone(games);
          window.recoveryRequests = [];
          const originalFetch = fetch;
          window.fetch = (input, options={}) => {
            const url = new URL(input,location.origin);
            if (url.pathname === '/api/crossplay/status' || url.pathname === '/api/crossplay/games/refresh' ||
                (url.pathname === '/api/crossplay/token' && options.method === 'PUT')) {
              return new Promise(resolve => recoveryRequests.push({path:url.pathname,options,resolve}));
            }
            return originalFetch(input,options);
          };
          setConnection('Checking saved connection'); updateAuthUi();
        }''', persistence)

    def reply(self, index, body=None, status=200):
        self.page.evaluate('''({index,body,status}) => {
          recoveryRequests[index].resolve(new Response(JSON.stringify(body || {
            auth_required:true,authenticated:true,configured:true
          }),{status,headers:{'Content-Type':'application/json'}}));
        }''', {'index':index,'body':body,'status':status})

    def finish_import(self, index):
        self.page.evaluate('''index => recoveryRequests[index].resolve(new Response(JSON.stringify({
          games:recoveryGames,refresh:{status:'refreshed',checked_at:'2026-11-24T15:00:00Z',fetched:3,added:0,updated:0}
        }),{status:200}))''', index)

    def test_refresh_recovers_remembered_token_without_new_token_or_header(self):
        self.prepare_recovery()
        self.page.get_by_role('button',name='Refresh',exact=True).click()
        self.page.wait_for_function('recoveryRequests.length === 1')
        self.assertEqual(self.page.evaluate('recoveryRequests[0].path'), '/api/crossplay/status')
        self.reply(0)
        self.page.wait_for_function('recoveryRequests.length === 2')
        self.assertEqual(self.page.evaluate('recoveryRequests[1].path'), '/api/crossplay/games/refresh')
        self.assertFalse(self.page.evaluate("'X-NYT-S' in recoveryRequests[1].options.headers"))
        self.finish_import(1)
        expect(self.page.locator('#connectionState')).to_contain_text('NYT cookie connected')
        self.assertTrue(self.page.evaluate('nytConfigured'))
        self.assertFalse(self.page.evaluate("recoveryRequests.some(r => r.path.endsWith('/token'))"))
        expect(self.page.locator('.game-card')).to_have_count(2)

    def test_recovery_after_initial_status_failure_discards_old_owner_contexts(self):
        self.prepare_recovery(persistence=False)
        self.page.evaluate('''() => {
          const q=getReviewQueue();q.concurrency=0;
          const review={id:'old-local-context',turns:[]};
          q.contexts.set(review.id,makeReviewContext(review,analysisOwner()));q.ids=[review.id];
        }''')
        self.page.get_by_role('button',name='Refresh',exact=True).click()
        self.page.wait_for_function('recoveryRequests.length === 1')
        self.reply(0)
        self.page.wait_for_function('recoveryRequests.length === 2')
        expect(self.page.locator('.game-card')).to_have_count(2)
        expect(self.page.locator('#refreshStatus')).to_contain_text('Checking NYT')
        self.finish_import(1)
        expect(self.page.locator('#connectionState')).to_contain_text('NYT cookie connected')
        self.assertTrue(self.page.evaluate('persistenceEnabled'))
        self.assertFalse(self.page.evaluate("getReviewQueue().contexts.has('old-local-context')"))
        self.assertTrue(self.page.evaluate('[...getReviewQueue().contexts.values()].every(c=>c.owner===analysisOwner())'))

    def test_missing_saved_cookie_shows_help_after_check_without_token_write(self):
        self.prepare_recovery()
        self.page.get_by_role('button',name='Refresh',exact=True).click()
        self.page.wait_for_function('recoveryRequests.length === 1')
        self.reply(0,{'auth_required':True,'authenticated':True,'configured':False})
        expect(self.page.locator('#connectionState')).to_contain_text('No saved NYT cookie')
        expect(self.page.locator('#cookieInput')).to_be_focused()
        self.assertEqual(self.page.evaluate('recoveryRequests.length'),1)
        expect(self.page.locator('.game-card')).to_have_count(2)

    def test_transient_status_failure_preserves_cards_and_can_retry(self):
        self.prepare_recovery()
        self.page.get_by_role('button',name='Refresh',exact=True).click()
        self.page.wait_for_function('recoveryRequests.length === 1')
        self.reply(0,{'error':'Synthetic connection service outage'},503)
        expect(self.page.locator('#gameCount')).to_contain_text('Synthetic connection service outage')
        expect(self.page.locator('#connectionState')).not_to_contain_text('Connect your NYT cookie')
        expect(self.page.locator('.game-card')).to_have_count(2)
        self.page.get_by_role('button',name='Refresh',exact=True).click()
        self.page.wait_for_function('recoveryRequests.length === 2')
        self.reply(1)
        self.page.wait_for_function('recoveryRequests.length === 3')
        self.finish_import(2)
        expect(self.page.locator('#connectionState')).to_contain_text('NYT cookie connected')

    def test_second_recovery_wins_and_account_change_rejects_late_status(self):
        self.prepare_recovery()
        self.page.get_by_role('button',name='Refresh',exact=True).click()
        self.page.get_by_role('button',name='Refresh',exact=True).click()
        self.page.wait_for_function('recoveryRequests.length === 2')
        self.assertTrue(self.page.evaluate('recoveryRequests[0].options.signal.aborted'))
        self.reply(1)
        self.page.wait_for_function('recoveryRequests.length === 3')
        self.finish_import(2)
        expect(self.page.locator('#connectionState')).to_contain_text('NYT cookie connected')
        self.reply(0,{'error':'Old response'},503)
        self.page.evaluate('new Promise(r=>setTimeout(r,0))')
        expect(self.page.locator('#connectionState')).to_contain_text('NYT cookie connected')
        self.page.evaluate("nytConfigured=false; void refreshGames()")
        self.page.wait_for_function('recoveryRequests.length === 4')
        self.page.evaluate("signedInUser={id:'different-account'};clearAccountUi('Account changed')")
        self.reply(3)
        self.page.evaluate('new Promise(r=>setTimeout(r,0))')
        self.assertEqual(self.page.evaluate('recoveryRequests.length'),4)
        expect(self.page.locator('#connectionState')).to_have_text('Account changed')
        self.assertFalse(self.page.evaluate('nytConfigured'))

    def test_connect_supersedes_a_pending_saved_connection_check(self):
        self.prepare_recovery()
        self.page.get_by_role('button',name='Refresh',exact=True).click()
        self.page.wait_for_function('recoveryRequests.length === 1')
        self.page.get_by_role('textbox',name='NYT-S cookie').fill('synthetic-new-token-for-testing')
        self.page.locator('#saveCookieButton').click()
        self.page.wait_for_function('recoveryRequests.length === 2')
        self.assertEqual(self.page.evaluate('recoveryRequests[1].path'),'/api/crossplay/token')
        self.reply(1,{'configured':True,'remembered':True,'username':'Restored player'})
        self.page.wait_for_function('recoveryRequests.length === 3',timeout=2000)
        self.assertEqual(self.page.evaluate('recoveryRequests[2].path'),'/api/crossplay/games/refresh')
        self.assertTrue(self.page.evaluate('recoveryRequests[0].options.signal.aborted'))
        self.page.evaluate("recoveryGames.push({...recoveryGames[0],id:'new-token-game',opponent:'New token game'})")
        self.finish_import(2)
        expect(self.page.get_by_role('button',name='Review game against New token game')).to_be_visible()
        self.reply(0,{'error':'Old saved connection failed'},503)
        self.page.evaluate('new Promise(r=>setTimeout(r,0))')
        expect(self.page.locator('#connectionState')).to_contain_text('Restored player')
        expect(self.page.locator('#gameCount')).not_to_contain_text('failed')

    def test_connect_during_unknown_mode_check_preserves_unremembered_token(self):
        self.prepare_recovery(persistence=False)
        self.page.get_by_role('button',name='Refresh',exact=True).click()
        self.page.wait_for_function('recoveryRequests.length === 1')
        self.page.locator('#cookieInput').fill('synthetic-unremembered-token')
        self.page.evaluate("byId('rememberNytCookie').checked=false")
        self.page.locator('#saveCookieButton').click()
        self.page.wait_for_function('recoveryRequests.length === 2')
        self.reply(1,{'configured':True,'remembered':False,'username':'Restored player'})
        self.page.wait_for_function('recoveryRequests.length === 3',timeout=2000)
        self.assertEqual(self.page.evaluate('recoveryRequests[2].path'),'/api/crossplay/status')
        self.reply(2)
        self.page.wait_for_function('recoveryRequests.length === 4',timeout=2000)
        self.assertEqual(self.page.evaluate('recoveryRequests[3].path'),'/api/crossplay/games/refresh')
        self.assertEqual(self.page.evaluate("recoveryRequests[3].options.headers['X-NYT-S']"),'synthetic-unremembered-token')
        self.page.evaluate("recoveryGames.push({...recoveryGames[0],id:'unremembered-token-game',opponent:'Unremembered token game'})")
        self.finish_import(3)
        expect(self.page.get_by_role('button',name='Review game against Unremembered token game')).to_be_visible()
        self.assertTrue(self.page.evaluate('persistenceEnabled && nytConfigured'))
        self.assertEqual(self.page.evaluate('activeNytCookie'),'synthetic-unremembered-token')
        self.reply(0,{'error':'Old connection failed'},503)
        self.page.evaluate('new Promise(r=>setTimeout(r,0))')
        expect(self.page.locator('#gameCount')).not_to_contain_text('failed')

    def test_google_sign_in_failure_is_not_a_nyt_token_prompt(self):
        self.prepare_recovery()
        self.page.get_by_role('button',name='Refresh',exact=True).click()
        self.page.wait_for_function('recoveryRequests.length === 1')
        self.reply(0,{'auth_required':True,'authenticated':False,'configured':False})
        expect(self.page.locator('#connectionState')).to_contain_text('Sign in with Google')
        expect(self.page.get_by_role('button',name='Sign in with Google',exact=True)).to_be_visible()
        expect(self.page.locator('#cookieInput')).not_to_be_focused()
        self.assertEqual(self.page.evaluate('recoveryRequests.length'),1)
        self.page.get_by_role('button',name='Refresh',exact=True).click()
        self.page.wait_for_function('recoveryRequests.length === 2')
        self.reply(1)
        self.page.wait_for_function('recoveryRequests.length === 3')
        self.finish_import(2)
        expect(self.page.locator('#connectionState')).to_contain_text('NYT cookie connected')
        expect(self.page.get_by_role('button',name='Sign in with Google',exact=True)).to_be_hidden()

    def test_successful_refresh_clears_a_transient_saved_cookie_warning(self):
        self.prepare_recovery()
        self.page.evaluate("nytConfigured=true;setConnection('Saved NYT cookie could not be used: temporary outage','error')")
        self.page.get_by_role('button',name='Refresh',exact=True).click()
        self.page.wait_for_function('recoveryRequests.length === 1')
        self.assertEqual(self.page.evaluate('recoveryRequests[0].path'),'/api/crossplay/games/refresh')
        self.finish_import(0)
        expect(self.page.locator('#connectionState')).to_contain_text('NYT cookie connected')
        expect(self.page.locator('#cookieForm')).to_be_hidden()
        self.assertTrue(self.page.evaluate('nytConfigured'))

    def test_nyt_rejection_after_status_is_not_reported_as_google_sign_in(self):
        self.prepare_recovery()
        self.page.get_by_role('button',name='Refresh',exact=True).click()
        self.page.wait_for_function('recoveryRequests.length === 1')
        self.reply(0)
        self.page.wait_for_function('recoveryRequests.length === 2')
        self.reply(1,{'error':'NYT rejected the NYT-S cookie; paste a fresh one'},401)
        expect(self.page.locator('#connectionState')).to_contain_text('NYT rejected')
        expect(self.page.get_by_role('button',name='Sign in with Google',exact=True)).to_be_hidden()
        expect(self.page.locator('#cookieInput')).to_be_focused()
        expect(self.page.locator('.game-card')).to_have_count(2)

    def test_malformed_status_preserves_cards_without_asking_for_token(self):
        self.prepare_recovery()
        self.page.get_by_role('button',name='Refresh',exact=True).click()
        self.page.wait_for_function('recoveryRequests.length === 1')
        self.reply(0,{})
        expect(self.page.locator('#gameCount')).to_contain_text('did not return the NYT connection status')
        expect(self.page.locator('#connectionState')).not_to_contain_text('Connect your NYT cookie')
        expect(self.page.locator('#cookieInput')).not_to_be_focused()
        expect(self.page.locator('.game-card')).to_have_count(2)
        self.assertEqual(self.page.evaluate('recoveryRequests.length'),1)

    def test_local_missing_connection_still_shows_token_help(self):
        self.prepare_recovery(persistence=False)
        self.page.get_by_role('button',name='Refresh',exact=True).click()
        self.page.wait_for_function('recoveryRequests.length === 1')
        self.reply(0,{'configured':False})
        expect(self.page.locator('#connectionState')).to_contain_text('Connect your NYT cookie')
        expect(self.page.locator('#cookieInput')).to_be_focused()
        self.assertFalse(self.page.evaluate('persistenceEnabled'))
        self.assertEqual(self.page.evaluate('recoveryRequests.length'),1)

    def test_recovery_has_same_thirty_second_deadline(self):
        self.prepare_recovery()
        self.page.clock.install()
        self.page.get_by_role('button',name='Refresh',exact=True).click()
        self.page.wait_for_function('recoveryRequests.length === 1')
        self.page.clock.run_for(30001)
        expect(self.page.locator('#gameCount')).to_contain_text('timed out')
        self.assertTrue(self.page.evaluate('recoveryRequests[0].options.signal.aborted'))
        expect(self.page.get_by_role('button',name='Refresh',exact=True)).to_be_enabled()
        expect(self.page.locator('.game-card')).to_have_count(2)


if __name__=='__main__':
    unittest.main()
