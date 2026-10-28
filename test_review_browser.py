#!/usr/bin/env python3
"""Playwright checks through the real frontend, local API and NYT fixtures.

Starts an isolated server by default. Set CROSSPLAY_TEST_BASE_URL to exercise
an already-running `npm run frontend` server instead. External requests fail.
"""

import json
import os
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from playwright.sync_api import Error, expect, sync_playwright

from tools.mock_nyt import EMPTY_COOKIE, EXPIRED_COOKIE, MOCK_COOKIE, OUTAGE_COOKIE
from tools.test_support import mock_server


class ReviewBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stack = ExitStack()
        cls.addClassCleanup(cls.stack.close)
        cls.stack.enter_context(patch.dict(os.environ, {'VERCEL': ''}))
        cls.stack.enter_context(patch('crossplay_server.urllib.request.urlopen',
                                     side_effect=AssertionError('Unexpected upstream network request')))
        cls.base_url = os.environ.get('CROSSPLAY_TEST_BASE_URL', '').rstrip('/')
        if not cls.base_url:
            cls.base_url = cls.stack.enter_context(mock_server())
        parsed = urlsplit(cls.base_url)
        if parsed.scheme != 'http' or parsed.hostname not in ('localhost', '127.0.0.1', '::1'):
            raise ValueError('CROSSPLAY_TEST_BASE_URL must be a loopback HTTP mock server')
        cls.playwright = cls.stack.enter_context(sync_playwright())
        try:
            cls.browser = cls.playwright.chromium.launch(headless=True)
        except Error:
            # Bits Code provides system Chrome; CI installs Playwright Chromium.
            cls.browser = cls.playwright.chromium.launch(channel='chrome', headless=True)
        cls.stack.callback(cls.browser.close)
        request = cls.playwright.request.new_context()
        try:
            health = request.get(cls.base_url + '/health')
            if not health.ok or health.json().get('mock_nyt') is not True:
                raise ValueError('Browser tests require a server started with --mock-nyt')
        finally:
            request.dispose()

    def setUp(self):
        self.context = self.browser.new_context(service_workers='block')
        self.addCleanup(self.context.close)
        self.external_requests = []
        self.page_errors = []
        self.context.route('**/*', self.route_request)
        self.page = self.context.new_page()
        self.page.on('pageerror', lambda error: self.page_errors.append(str(error)))
        self.page.goto(self.base_url, wait_until='domcontentloaded')
        expect(self.page.locator('#connectionState')).to_contain_text('Not connected')
        # Display/custom-move tests control computation explicitly. Automation
        # tests below restore the production entry point.
        self.page.evaluate('window.automaticAnalysis = startBackgroundAnalysis; startBackgroundAnalysis = () => {};'
                           'window.automaticViewedTurn = prepareViewedTurn; prepareViewedTurn = () => {}')

    def route_request(self, route):
        if urlsplit(route.request.url).netloc != urlsplit(self.base_url).netloc:
            self.external_requests.append(route.request.url)
            route.abort()
        else:
            route.continue_()

    def tearDown(self):
        self.assertEqual(self.external_requests, [], 'The review page must work offline')
        self.assertEqual(self.page_errors, [])

    def connect(self, cookie=MOCK_COOKIE):
        self.page.get_by_role('textbox', name='NYT-S cookie').fill(cookie)
        self.page.get_by_role('button', name='Connect', exact=True).click()

    def open_game(self):
        self.connect()
        self.page.get_by_role('button', name='Review game against Demo Cedar').click()
        expect(self.page.get_by_role('heading', name='vs Demo Cedar', exact=True)).to_be_visible()

    def test_connect_replay_refresh_reload_and_forget(self):
        self.open_game()
        expect(self.page.locator('#connectionState')).to_have_text('NYT connected as Demo Player')
        expect(self.page.locator('#cookieInput')).to_have_value('')
        expect(self.page.locator('#gameCount')).to_have_text('2 finished · 1 in progress hidden')
        expect(self.page.locator('.game-card')).to_have_count(2)
        expect(self.page.get_by_role('button', name='Review game against Demo Active')).to_have_count(0)
        expect(self.page.locator('.turn-row')).to_have_count(6)
        turns = self.page.get_by_role('combobox', name='Select game turn')
        expect(turns).to_have_value('5')
        turns.select_option('0')
        expect(self.page.locator('.turn-action')).to_have_text('Turn 1 · Demo Player · +5')
        expect(self.page.locator('.board .tile')).to_have_count(3)
        expect(self.page.get_by_role('button', name='← Previous', exact=True)).to_be_disabled()
        self.page.get_by_role('button', name='Show before move').click()
        expect(self.page.locator('.board .tile')).to_have_count(0)
        self.page.get_by_role('button', name='Show after move').click()
        self.page.get_by_role('button', name='Next →').click()
        expect(turns).to_have_value('1')
        expect(self.page.locator('.board .tile')).to_have_count(5)
        turns.select_option('2')
        expect(self.page.locator('.board .blank-tile')).to_have_count(1)
        expect(self.page.locator('.board .blank-tile')).to_have_text('S')
        self.page.get_by_role('button', name='Refresh', exact=True).click()
        expect(self.page.locator('.game-card')).to_have_count(2)
        self.page.reload()
        expect(self.page.get_by_role('heading', name='vs Demo Cedar', exact=True)).to_be_visible()
        expect(self.page.locator('#sharedBanner')).to_be_hidden()
        self.assertIn('?review=mockgame01', self.page.url)
        expect(self.page.locator('#connectionState')).to_have_text('NYT connected as Demo Player')
        expect(self.page.locator('.game-card')).to_have_count(2)
        cookies = self.context.cookies()
        session = next(cookie for cookie in cookies if cookie['name'] == 'crossplay_session')
        self.assertTrue(session['httpOnly'])
        storage = self.page.evaluate('JSON.stringify({local: {...localStorage}, session: {...sessionStorage}})')
        self.assertNotIn(MOCK_COOKIE, storage)
        self.assertNotIn(MOCK_COOKIE, json.dumps(cookies))
        self.page.get_by_role('button', name='Forget cookie').click()
        expect(self.page.locator('#connectionState')).to_contain_text('Cookie forgotten')
        expect(self.page.locator('.game-card')).to_have_count(0)
        self.page.reload()
        expect(self.page.locator('#connectionState')).to_contain_text('Not connected')

    def test_guest_can_replay_shared_game_until_owner_forgets_cookie(self):
        self.open_game()
        self.page.locator('.share-details summary').click()
        share_url = self.page.get_by_role('textbox', name='Shareable game link').input_value()
        guest_context = self.browser.new_context(service_workers='block')
        self.addCleanup(guest_context.close)
        guest_context.route('**/*', self.route_request)
        guest = guest_context.new_page()
        guest.on('pageerror', lambda error: self.page_errors.append(str(error)))
        shared_requests = []
        guest.on('request', lambda request: shared_requests.append(request)
                 if '/api/crossplay/shared/' in request.url else None)
        guest.goto(share_url)
        expect(guest.get_by_role('heading', name='Demo Player vs Demo Cedar', exact=True)).to_be_visible()
        expect(guest.locator('.account')).to_be_hidden()
        expect(guest.locator('.turn-row')).to_have_count(6)
        guest.get_by_role('combobox', name='Select game turn').select_option('0')
        expect(guest.locator('.board .tile')).to_have_count(3)
        self.assertEqual(len(shared_requests), 1)
        for header in ('authorization', 'x-nyt-s', 'cookie'):
            self.assertNotIn(header, shared_requests[0].all_headers())
        self.page.get_by_role('button', name='Forget cookie', exact=True).click()
        expect(self.page.locator('#connectionState')).to_contain_text('Cookie forgotten')
        guest.reload()
        expect(guest.locator('#reviewPanel')).to_contain_text('Shared game not found')

    def test_review_detail_fallback(self):
        self.connect()
        self.page.get_by_role('button', name='Review game against Demo River').click()
        expect(self.page.get_by_role('heading', name='vs Demo River', exact=True)).to_be_visible()
        self.page.get_by_role('combobox', name='Select game turn').select_option('0')
        expect(self.page.locator('.turn-action')).to_have_text('Turn 1 · Demo River · +5')

    def test_empty_account(self):
        self.connect(EMPTY_COOKIE)
        expect(self.page.locator('#connectionState')).to_contain_text('NYT connected as Demo Player')
        expect(self.page.locator('#gameCount')).to_have_text('0 finished')
        expect(self.page.locator('#gameList')).to_contain_text('No recent Crossplay games')

    def test_rejected_cookie(self):
        self.connect(EXPIRED_COOKIE)
        expect(self.page.locator('#connectionState')).to_contain_text('NYT rejected')
        expect(self.page.get_by_role('button', name='Refresh', exact=True)).to_be_disabled()
        expect(self.page.get_by_role('button', name='Forget cookie')).to_be_hidden()

    def test_upstream_failure(self):
        self.connect(OUTAGE_COOKIE)
        expect(self.page.locator('#gameList')).to_contain_text('NYT returned HTTP 503')
        expect(self.page.locator('#gameCount')).to_have_text('Could not load games')

    def use_saved_analysis(self, analysis=None, **review_changes):
        """Exercise the saved-analysis API contract with explicitly synthetic data."""
        if analysis is None:
            analysis = json.loads((Path(__file__).parent / 'tools/fixtures/review-analysis.json').read_text())

        quality = analysis.get('move_quality', {})
        if quality and 'evaluation' not in quality:
            quality['evaluation'] = 'win-probability-v1'
            for point in quality.get('points', []):
                if point.get('loss') is not None:
                    loss = point['loss']
                    point.update(playedWin=(50 - loss) / 100, bestWin=.5, equityLoss=loss,
                                 evaluation='win-probability-v1',
                                 category='best' if loss < 2 else 'inaccuracy' if loss < 5 else 'mistake' if loss < 10 else 'blunder')
        self.page.evaluate("reviewQueue?.contexts.delete('mockgame01')")

        def review_route(route):
            response = route.fetch()
            review = response.json()
            review.update(review_changes)
            review['stored_analysis'] = analysis
            route.fulfill(response=response, json=review)

        self.page.route('**/api/crossplay/games/mockgame01/review', review_route)

    def stat(self, label):
        return self.page.locator('.dashboard-stat').filter(
            has=self.page.get_by_text(label, exact=True))

    def test_personal_summary_and_score_replay_without_analysis(self):
        self.open_game()
        expect(self.page.locator('.review-heading')).to_contain_text('Won by 6 points')
        expect(self.page.locator('.score-person').first).to_have_text('You')
        expect(self.stat('Points per turn')).to_contain_text('3.3')
        expect(self.stat('Highest-scoring play')).to_contain_text('5 pts')
        expect(self.stat('Highest-scoring play')).to_contain_text('CAT')
        expect(self.stat('Best moves').locator('.dashboard-stat-value')).to_have_text('—')
        expect(self.stat('Avg. win chance lost').locator('.dashboard-stat-value')).to_have_text('—')
        expect(self.page.locator('.quality-empty')).to_be_visible()
        expect(self.page.locator('.score-point')).to_have_count(6)
        self.page.locator('.score-point').first.focus()
        self.page.keyboard.press('Enter')
        expect(self.page.get_by_role('combobox', name='Select game turn')).to_have_value('0')
        expect(self.page.locator('.score-point').first).to_be_focused()
        expect(self.page.locator('.board .tile')).to_have_count(3)
        expect(self.page.locator('#cookieForm')).to_be_hidden()
        self.page.get_by_role('button', name='Manage connection', exact=True).click()
        expect(self.page.locator('#cookieForm')).to_be_visible()
        expect(self.page.get_by_role('button', name='Manage connection')).to_have_attribute('aria-expanded', 'true')

    def test_saved_quality_excludes_opponent_and_unrated_moves(self):
        self.use_saved_analysis()
        self.open_game()
        expect(self.stat('Best moves').locator('.dashboard-stat-value')).to_have_text('1 / 2')
        expect(self.stat('Avg. win chance lost').locator('.dashboard-stat-value')).to_have_text('3.0 pp')
        expect(self.stat('Avg. win chance lost')).to_contain_text('2 of your 3 turns rated')
        expect(self.page.locator('.quality-summary')).to_contain_text('1 Best')
        expect(self.page.locator('.quality-summary')).not_to_contain_text('Blunder')
        expect(self.page.locator('.quality-summary')).to_contain_text('1 unrated')
        expect(self.page.locator('.quality-point')).to_have_count(3)
        expect(self.page.locator('.insight')).to_have_count(1)
        expect(self.page.locator('.insight')).to_contain_text('Turn 3 · SCAT')
        expect(self.page.locator('.insight')).to_contain_text('5.6 pp')
        self.page.locator('.insight').click()
        expect(self.page.get_by_role('combobox', name='Select game turn')).to_have_value('2')
        expect(self.page.locator('.turn-meta')).to_contain_text('Mistake')
        expect(self.page.locator('.board .last-play')).to_have_count(1)
        self.page.locator('.quality-point').first.focus()
        self.page.keyboard.press('Space')
        expect(self.page.get_by_role('combobox', name='Select game turn')).to_have_value('0')
        expect(self.page.locator('.quality-point').first).to_be_focused()
        self.page.get_by_role('button', name='Review game against Demo River').click()
        expect(self.stat('Avg. win chance lost').locator('.dashboard-stat-value')).to_have_text('—')
        expect(self.page.locator('.insight')).to_have_count(0)
        expect(self.page.locator('.review-heading')).to_contain_text('Lost by 6 points')

    def test_luck_panel_without_analysis_and_legacy_saved_analysis(self):
        self.use_saved_analysis()
        self.open_game()
        panel = self.page.locator('.luck-panel')
        cards = panel.locator('.luck-stat')
        expect(cards.nth(0)).to_contain_text('3 of your 3 racks estimated')
        expect(cards.nth(1)).to_contain_text('2 of your 3 turns rated')
        expect(cards.nth(2).locator('strong')).to_have_text('—')
        expect(panel).to_contain_text('Re-analyze moves')
        panel.locator('.luck-breakdown summary').click()
        expect(panel.locator('tbody tr')).to_have_count(3)
        panel.get_by_role('button', name='Replay turn 3 from luck breakdown').click()
        expect(self.page.get_by_role('combobox', name='Select game turn')).to_have_value('2')
        self.page.get_by_role('button', name='Review game against Demo River').click()
        expect(self.page.locator('.luck-panel')).to_contain_text('Use Analyze move quality above')
        expect(self.page.locator('.luck-stat').nth(1).locator('strong')).to_have_text('—')

    def test_saved_word_opportunities_weight_only_eligible_personal_turns(self):
        analysis = {'version': 'quackle-equity-v1', 'move_quality': {
            'state': 'stopped', 'points': [
                {'turnIndex': 0, 'category': 'mistake', 'loss': 10, 'playedEquity': 20,
                 'opportunity': {'version': 1, 'accessibleEquity': 26, 'bestWord': 'QAT',
                                 'bestWordZipf': 2, 'knownCandidates': 2, 'candidateCount': 3}},
                {'turnIndex': 1, 'category': 'best', 'loss': 0, 'playedEquity': 30,
                 'opportunity': {'version': 1, 'accessibleEquity': 30, 'bestWord': 'CAT',
                                 'bestWordZipf': 5, 'knownCandidates': 1, 'candidateCount': 1}},
                {'turnIndex': 2, 'category': 'unrated', 'loss': None}]}}
        self.use_saved_analysis(analysis)
        self.open_game()
        panel = self.page.locator('.luck-panel')
        expect(panel.locator('.luck-stat').nth(1).locator('strong')).to_have_text('50 / 100')
        expect(panel.locator('.luck-stat').nth(2).locator('strong')).to_have_text('63 / 100')
        expect(panel).to_contain_text('1 of your 3 turns weighted · partial analysis')
        expect(panel).to_contain_text('Word frequency available for 2 of 3 candidate entries')
        panel.locator('.luck-breakdown summary').click()
        expect(panel.locator('tbody tr').first).to_contain_text('QAT · Less common')
        expect(panel.locator('tbody tr').first).to_contain_text('6.0 eq')
        expect(panel.locator('tbody tr').nth(1)).to_contain_text('Unrated')

    def test_incomplete_and_unrated_analysis_never_implies_perfect_play(self):
        analysis = {'version': 'quackle-equity-v1', 'move_quality': {
            'state': 'stopped', 'points': [
                {'turnIndex': 0, 'category': 'unrated', 'loss': None, 'reason': 'Outside candidates'},
                {'turnIndex': 1, 'category': 'best', 'loss': 0.0}]}}
        self.use_saved_analysis(analysis)
        self.open_game()
        expect(self.stat('Avg. win chance lost').locator('.dashboard-stat-value')).to_have_text('—')
        expect(self.page.locator('.insights-panel')).to_contain_text('No rated turns yet')
        expect(self.page.locator('.insights-panel')).to_contain_text('Analysis is incomplete')
        expect(self.page.locator('.insights-panel')).not_to_contain_text('Every rated move')

    def test_draw_missing_scores_and_final_adjustments(self):
        self.use_saved_analysis(my_score=14, opponent_score=14)
        self.open_game()
        expect(self.page.locator('.review-heading')).to_contain_text('Draw')
        expect(self.page.locator('.score-point')).to_have_count(7)
        expect(self.page.locator('.score-point').last).to_have_attribute('aria-label', 'Final score: 14 to 14. Replay turn 6')
        self.page.unroute('**/api/crossplay/games/mockgame01/review')
        self.use_saved_analysis(my_score=None, opponent_score=None)
        self.page.get_by_role('button', name='Review game against Demo Cedar').click()
        expect(self.page.locator('.review-heading')).to_contain_text('Final scores unavailable')
        expect(self.page.locator('.review-heading')).not_to_contain_text('Draw')
        expect(self.page.locator('.score-value').first).to_have_text('—')

    def test_mobile_layout_and_board_fit(self):
        self.use_saved_analysis()
        self.open_game()
        self.page.locator('.luck-breakdown summary').click()
        for width in (320, 390, 768, 1024, 1440):
            with self.subTest(width=width):
                self.page.set_viewport_size({'width': width, 'height': 900})
                self.assertTrue(self.page.evaluate(
                    'document.documentElement.scrollWidth <= window.innerWidth'),
                    'The page must not scroll horizontally')
                board = self.page.locator('.board-scroll')
                self.assertTrue(board.evaluate('(el) => el.scrollWidth <= el.clientWidth'),
                                'The complete board must fit the viewport')
                expect(self.page.get_by_role('button', name='Re-analyze moves')).to_be_visible()
        self.page.set_viewport_size({'width': 390, 'height': 844})
        self.page.locator('.insight').click()
        expect(self.page.get_by_role('combobox', name='Select game turn')).to_have_value('2')

    def test_walkthrough_navigation_and_personal_flag_filter(self):
        self.use_saved_analysis()
        self.open_game()
        self.page.get_by_role('button', name='Start walkthrough', exact=True).click()
        turns = self.page.get_by_role('combobox', name='Select game turn')
        expect(turns).to_have_value('0')
        expect(self.page.locator('.turn-coach')).to_contain_text('Best · 0.4 percentage points lost')
        expect(self.page.get_by_role('button', name='← Previous flagged move')).to_be_disabled()
        self.page.get_by_role('button', name='Next flagged move →').click()
        expect(turns).to_have_value('2')
        expect(self.page.locator('.turn-coach')).to_contain_text('Mistake · 5.6 percentage points lost')
        expect(self.page.get_by_role('button', name='Next flagged move →')).to_be_disabled()
        self.page.get_by_role('checkbox', name='Only your flagged moves').check()
        expect(self.page.locator('.turn-row')).to_have_count(1)
        expect(self.page.locator('.turn-row')).to_contain_text('s(CAT)')
        expect(self.page.locator('.turn-row .quality-chip')).to_have_text('Mistake')
        self.page.locator('.turn-row').click()
        self.page.keyboard.press('ArrowRight')
        expect(turns).to_have_value('3')
        expect(self.page.locator('.turn-coach')).to_contain_text('This turn is unrated')
        self.page.keyboard.press('End')
        expect(turns).to_have_value('5')
        self.page.get_by_role('button', name='← Previous flagged move').click()
        expect(turns).to_have_value('2')
        self.page.locator('.turn-controls').focus()
        self.page.keyboard.press('Home')
        expect(turns).to_have_value('0')
        self.page.get_by_role('checkbox', name='Only your flagged moves').uncheck()
        expect(self.page.locator('.turn-row')).to_have_count(6)
        self.page.get_by_role('button', name='Last ⇥', exact=True).click()
        expect(turns).to_have_value('5')
        expect(self.page.get_by_role('button', name='Last ⇥', exact=True)).to_be_disabled()

    def test_best_move_preview_restores_board_and_preserves_existing_tiles(self):
        self.use_saved_analysis()
        self.open_game()
        self.page.locator('.insight').click()
        comparison = self.page.locator('.move-comparison')
        expect(comparison).to_contain_text('SCAT · 8F')
        expect(comparison).to_contain_text('CATS · 8G')
        expect(comparison).to_contain_text('6 points · leave ?EINRU')
        original = self.page.locator('.board').inner_text()
        self.page.get_by_role('button', name='Preview best move', exact=True).click()
        expect(self.page.locator('.board-preview-note')).to_contain_text('CATS at 8G')
        expect(self.page.locator('.board .last-play')).to_have_count(1)
        expect(self.page.locator('.board .last-play')).to_have_text('S')
        expect(self.page.locator('.board .blank-tile')).to_have_count(0)
        expect(self.page.locator('.board .tile')).to_have_count(6)
        # Exploring another move must use the original before-turn position,
        # even while the walkthrough displays Quackle's best-move preview.
        self.enter_custom_move('sCAT', 'F8')
        expect(self.page.locator('.custom-move-status')).to_have_text('Move evaluated.', timeout=30_000)
        expect(self.page.locator('.custom-move-result tbody tr').first.locator('td').nth(1)).to_have_text('5')
        expect(self.page.locator('.board-preview-note')).to_contain_text('CATS at 8G')
        expect(self.page.locator('.board .blank-tile')).to_have_count(0)
        self.page.get_by_role('button', name='Return to played move', exact=True).click()
        expect(self.page.locator('.custom-move-result tbody tr')).to_have_count(0)
        self.assertEqual(self.page.locator('.board').inner_text(), original)
        expect(self.page.locator('.board .blank-tile')).to_have_count(1)
        self.page.get_by_role('button', name='Preview best move', exact=True).click()
        self.page.get_by_role('button', name='Show before move', exact=True).click()
        expect(self.page.locator('.board-preview-note')).to_have_count(0)
        expect(self.page.locator('.board .tile')).to_have_count(5)
        self.page.get_by_role('button', name='Preview best move', exact=True).click()
        self.page.get_by_role('button', name='Next →', exact=True).click()
        expect(self.page.locator('.board-preview-note')).to_have_count(0)
        expect(self.page.locator('.board .tile')).to_have_count(6)
        self.page.get_by_role('button', name='Review game against Demo River').click()
        expect(self.page.locator('.move-comparison')).to_have_count(0)
        expect(self.page.get_by_role('checkbox', name='Only your flagged moves')).not_to_be_checked()

    def test_blank_alternative_and_older_saved_review(self):
        analysis = json.loads((Path(__file__).parent / 'tools/fixtures/review-analysis.json').read_text())
        analysis['move_quality']['points'][2]['bestMove']['tiles'] = '...s'
        self.use_saved_analysis(analysis)
        self.open_game()
        self.page.locator('.insight').click()
        self.page.get_by_role('button', name='Preview best move', exact=True).click()
        expect(self.page.locator('.board .blank-tile.last-play')).to_have_text('S')
        expect(self.page.locator('.board .last-play')).to_have_count(1)
        # Old saved analyses and sparse, out-of-order results map by turnIndex.
        self.page.unroute('**/api/crossplay/games/mockgame01/review')
        self.use_saved_analysis({'version': 'quackle-equity-v1', 'move_quality': {
            'state': 'stopped', 'points': [
                {'turnIndex': 2, 'category': 'blunder', 'loss': 10.5},
                {'turnIndex': 0, 'category': 'inaccuracy', 'loss': 2.0}]}})
        self.page.get_by_role('button', name='Review game against Demo Cedar').click()
        self.page.get_by_role('button', name='Start walkthrough', exact=True).click()
        expect(self.page.locator('.turn-coach')).to_contain_text('Inaccuracy · 2.0 percentage points lost')
        self.page.get_by_role('button', name='Next flagged move →').click()
        expect(self.page.locator('.turn-coach')).to_contain_text('Blunder · 10.5 percentage points lost')
        expect(self.page.locator('.turn-coach')).to_contain_text('Re-analyze moves to add')
        expect(self.page.get_by_role('button', name='Preview best move', exact=True)).to_have_count(0)
        self.page.get_by_role('checkbox', name='Only your flagged moves').check()
        expect(self.page.locator('.turn-row')).to_have_count(2)
        expect(self.page.locator('.turn-row .quality-chip').last).to_have_text('Blunder')

    def install_review_worker(self, delay=0):
        worker = r"""
          self.onmessage = ({data: {id, type, payload: p}}) => {
            if (type === 'init') return self.postMessage({id, result: {}});
            if (type !== 'simulateReview') return self.postMessage({id, error: 'Unexpected request: ' + type});
            const selected = {...p.played, score: 5, leave: 'ERSU', equity: 2, staticEquity: 2,
              win: p.rack === 'ACERSTU' ? .3 : p.rack === '?EINRSU' ? .43 : .5, samples: p.iterations};
            const best = p.rack === 'ACERSTU' || p.rack === '?EINRSU'
              ? {...selected, position: p.rack === 'ACERSTU' ? '8G' : '8F',
                 tiles: p.rack === 'ACERSTU' ? 'ACT' : 'S...', win: .5, equity: 12, staticEquity: 12} : selected;
            const end = p.bagCount <= 21;
            if (end) { selected.terminalSamples = p.iterations; best.terminalSamples = p.iterations; }
            const moves = selected === best ? [selected] : [best, selected];
            const result = {simulated: true, moves, selected, best, seed: p.seed, samplingVersion: 'paired-review-v1',
              staticMoves: moves.map(move => ({...move, equity: move.staticEquity})), bestStaticEquity: best.staticEquity,
              plies: end ? -1 : 2, playToEnd: end, simulationMode: end ? 'play-to-end' : '2-ply', winModel: end ? 'terminal' : 'bogowin',
              bagCount: p.bagCount, finalTurnsRemaining: p.finalTurnsRemaining,
              iterationsRequested: p.iterations, iterationsCompleted: p.iterations, candidateCount: moves.length,
              totalPlayaheads: moves.length * p.iterations};
            setTimeout(() => self.postMessage({id, result}), DELAY);
          };
        """.replace('DELAY', str(delay))
        self.page.route('**/quackle-worker.js', lambda route: route.fulfill(
            content_type='application/javascript', body=worker))

    def test_grading_saves_comparisons_and_does_not_match_wrong_blank(self):
        # Deterministic worker replies exercise grading and persistence, separately
        # from the real WASM tests. All boards and candidates are synthetic.
        self.install_review_worker()
        saved = []

        def save(route):
            saved.append(route.request.post_data_json)
            route.fulfill(json={'saved': True})

        self.page.route('**/api/crossplay/games/mockgame01/analysis', save)
        self.page.evaluate('window.evaluatedMoves = []; const request = workerRequest; workerRequest = (type, payload, options) => {'
                           ' if (type === "simulateReview") evaluatedMoves.push(payload.played);'
                           ' return request(type, payload, options); }; undefined')
        self.open_game()
        self.page.get_by_role('button', name='Analyze move quality', exact=True).click()
        expect(self.page.locator('.quality-panel .trend-status')).to_contain_text('Your review is ready')
        completed = [snapshot for snapshot in saved if 'move_quality' in snapshot]
        self.assertTrue(completed)
        points = completed[-1]['move_quality']['points']
        self.assertEqual(points[0]['category'], 'blunder')
        self.assertAlmostEqual(points[0]['loss'], 20)
        self.assertEqual(points[0]['bestMove']['word'], 'ACT')
        self.assertEqual(points[0]['playedMove']['leave'], 'ERSU')
        # An S tile must not match the assigned blank s used in the actual game,
        # so the played move is evaluated directly instead.
        self.assertIn({'action': 'place', 'position': '8F', 'tiles': 's...'}, self.page.evaluate('evaluatedMoves'))
        self.assertEqual(points[2]['category'], 'mistake')
        self.assertAlmostEqual(points[2]['loss'], 7)
        self.assertEqual(points[2]['playedMove']['tiles'], 's...')
        self.assertEqual(points[2]['candidateCount'], 2)
        self.assertEqual(points[2]['bestMove']['tiles'], 'S...')
        for point in points[3:]:
            self.assertEqual(point['category'], 'best')
            self.assertEqual(point['playedWin'], .5)
            self.assertIsNone(point['equityLoss'])
        self.page.get_by_role('button', name='Start walkthrough', exact=True).click()
        expect(self.page.locator('.turn-coach')).to_contain_text('Blunder · 20.0 percentage points lost')
        expect(self.page.locator('.move-comparison')).to_contain_text('ACT · 8G')
        # Reloading the saved API representation must restore its comparison.
        self.use_saved_analysis({'version': completed[-1]['analysis_version'],
                                 'move_quality': completed[-1]['move_quality']})
        self.page.get_by_role('button', name='Review game against Demo Cedar').click()
        self.page.get_by_role('combobox', name='Select game turn').select_option('2')
        expect(self.page.locator('.turn-coach')).to_contain_text('Mistake · 7.0 percentage points lost')
        expect(self.page.get_by_role('button', name='Preview best move', exact=True)).to_be_enabled()

    def test_older_saved_grades_are_regraded_and_unevaluable_plays_stay_unrated(self):
        worker = "self.onmessage = ({data: {id, type}}) => self.postMessage(type === 'init' ? {id, result: {}} : {id, error: 'This move is not legal in Quackle’s dictionary at this position.'});"
        self.page.route('**/quackle-worker.js', lambda route: route.fulfill(
            content_type='application/javascript', body=worker))
        self.use_saved_analysis({'version': 'quackle-equity-v1', 'move_quality': {
            'state': 'done', 'evaluation': 'static-equity-v1', 'points': [{
                'turnIndex': 0, 'turn': 1, 'mine': True, 'action': 'CAT 8G 5', 'category': 'unrated', 'loss': None,
                'reason': 'Played move was outside Quackle’s returned top 15 candidates.'}]}})
        self.page.evaluate('startBackgroundAnalysis = window.automaticAnalysis; undefined')
        self.open_game()
        expect(self.page.locator('.quality-panel .trend-status')).to_contain_text('Your review is ready')
        self.assertEqual(self.page.evaluate('moveQualityResults.evaluation'), 'win-probability-v1')
        reason = self.page.evaluate('moveQualityResults.points[0].reason')
        self.assertTrue(reason.startswith('Quackle could not evaluate the played move: This move is not legal'), reason)

    def test_move_quality_refreshes_summary_with_real_engine(self):
        self.open_game()
        self.page.get_by_role('button', name='Analyze move quality', exact=True).click()
        expect(self.page.locator('.quality-panel .trend-status')).to_contain_text(
            'Your review is ready', timeout=120_000)
        expect(self.page.locator('.quality-point')).to_have_count(3)
        self.assertTrue(self.page.evaluate('moveQualityResults.points.every(point => Number.isFinite(point.playedWin))'))
        expect(self.page.locator('.insights-panel')).to_contain_text('of your 3 turns rated')
        expect(self.page.get_by_role('button', name='Re-analyze moves', exact=True)).to_be_enabled()
        # The selected turn is the opponent's final pass; its badge updates too.
        expect(self.page.locator('.turn-meta .quality-chip')).not_to_have_text('Unrated')
        self.page.get_by_role('button', name='Start walkthrough', exact=True).click()
        expect(self.page.locator('.move-comparison')).to_be_visible()
        self.assertTrue(self.page.evaluate('Boolean(moveQualityResults.points[0].bestMove.tiles)'))
        points = self.page.evaluate('moveQualityResults.points')
        word_points = [point for point in points if point['turnIndex'] in (0, 1, 2)]
        self.assertTrue(all(point['opportunity']['version'] == 1 for point in word_points))
        self.assertTrue(all(point['playedMove']['action'] in ('pass', 'exchange')
                            for point in points if point['turnIndex'] in (3, 4, 5)))
        expect(self.page.locator('.luck-panel')).to_contain_text('3 of your 3 racks estimated')

    def test_background_checks_other_games_and_reuses_saved_turns_after_reload(self):
        self.install_review_worker(delay=15)
        self.page.evaluate('startBackgroundAnalysis = window.automaticAnalysis; undefined')
        self.connect()
        expect(self.page.locator('#backgroundStatus')).to_contain_text('2/2 games', timeout=15_000)
        states = self.page.evaluate('Array.from(reviewQueue.contexts.values(), c => ({id:c.review.id, keys:Object.keys(c.cache.results).length, points:c.quality.points.length}))')
        self.assertEqual({state['id'] for state in states}, {'mockgame01', 'mockgame02'})
        self.assertTrue(all(state['keys'] == state['points'] == 6 for state in states))
        self.page.evaluate('Promise.all(Array.from(reviewQueue.contexts.values(), c => flushAnalysisSave(c.review, c.cache)))')
        self.page.get_by_role('button', name='Review game against Demo Cedar').click()
        self.page.get_by_role('button', name='Review game against Demo River').click()
        expect(self.page.locator('#engineStatus')).to_contain_text('277 simulations per move')
        self.context.add_init_script("window.engineCalls=[]; const post=Worker.prototype.postMessage; Worker.prototype.postMessage=function(data,...rest){engineCalls.push(data.type); return post.call(this,data,...rest);};")
        self.page.goto(self.base_url)
        expect(self.page.locator('#backgroundStatus')).to_contain_text('2/2 games', timeout=15_000)
        self.assertEqual(self.page.evaluate('engineCalls'), [])

    def test_retry_turn_updates_the_grade_trend_and_saved_summary(self):
        self.install_review_worker()
        saved = []
        self.page.route('**/api/crossplay/games/mockgame01/analysis',
                        lambda route: (saved.append(route.request.post_data_json), route.fulfill(json={'saved': True})))
        self.open_game()
        self.page.evaluate("""window.retryCalls = 0; window.failRecordedMove = true;
          const request = workerRequest;
          workerRequest = (type, payload, options) => {
            if (type === 'simulateReview' && payload.rack === 'ACERSTU') {
              retryCalls++;
              if (failRecordedMove) return Promise.reject(new Error('Temporary engine failure.'));
            }
            return request(type, payload, options);
          }; undefined""")
        self.page.get_by_role('button', name='Analyze move quality', exact=True).click()
        expect(self.page.locator('.quality-panel .trend-status')).to_contain_text('Your review is ready')
        self.page.evaluate('flushAnalysisSave()')
        self.page.wait_for_function('reviewQueue.running === 0')
        self.page.get_by_role('combobox', name='Select game turn').select_option('0')
        expect(self.page.locator('.turn-coach')).to_contain_text('This turn is unrated')
        self.page.evaluate('failRecordedMove = false')
        self.page.get_by_role('button', name='Retry this turn', exact=True).click()
        expect(self.page.locator('.turn-coach')).to_contain_text('Blunder · 20.0 percentage points lost')
        self.page.evaluate('flushAnalysisSave()')
        self.page.wait_for_function('reviewQueue.running === 0')
        self.assertEqual(self.page.evaluate('retryCalls'), 2)
        completed = [value for value in saved if 'move_quality' in value]
        self.assertEqual(completed[-1]['move_quality']['points'][0]['category'], 'blunder')
        self.assertEqual(len(completed[-1]['win_trend']['points']), 7)

    def test_navigation_during_turn_retry_saves_each_game_to_its_own_id(self):
        self.install_review_worker()
        saved = {'mockgame01': [], 'mockgame02': []}
        for game_id in saved:
            def save(route):
                key = route.request.url.split('/')[-2]
                saved[key].append(route.request.post_data_json)
                route.fulfill(json={'saved': True})
            self.page.route('**/api/crossplay/games/' + game_id + '/analysis', save)
        self.open_game()
        self.page.evaluate("""window.failRetry = true; const request = workerRequest;
          workerRequest = (type,payload,options) => {
            if (type === 'simulateReview' && options.taskKey.includes(':mockgame01:') && payload.rack === 'ACERSTU') {
              if (failRetry) return Promise.reject(new Error('Temporary engine failure.'));
              return new Promise(resolve => { window.releaseTurnRetry = () => resolve(request(type,payload,options)); });
            }
            return request(type,payload,options);
          }; undefined""")
        self.page.get_by_role('button', name='Analyze move quality', exact=True).click()
        expect(self.page.locator('.quality-panel .trend-status')).to_contain_text('Your review is ready')
        self.page.evaluate('flushAnalysisSave()')
        self.page.wait_for_function('reviewQueue.running === 0')
        self.page.get_by_role('combobox', name='Select game turn').select_option('0')
        self.page.evaluate('failRetry = false')
        self.page.get_by_role('button', name='Retry this turn', exact=True).click()
        self.page.get_by_role('button', name='Review game against Demo River').click()
        expect(self.page.get_by_role('heading', name='vs Demo River', exact=True)).to_be_visible()
        self.page.evaluate("positionCache.unsaved = {navigationSentinel: {moves: []}}; releaseTurnRetry(); undefined")
        self.page.wait_for_function("reviewQueue.contexts.get('mockgame01').quality.points[0].category === 'blunder' && reviewQueue.running === 0")
        self.page.evaluate('Promise.all(Array.from(reviewQueue.contexts.values(), context => flushAnalysisSave(context.review, context.cache)))')
        self.assertFalse(any('navigationSentinel' in value['turn_analyses'] for value in saved['mockgame01']))
        self.assertTrue(any('navigationSentinel' in value['turn_analyses'] for value in saved['mockgame02']))
        completed = [value for value in saved['mockgame01'] if 'move_quality' in value]
        self.assertEqual(completed[-1]['move_quality']['points'][0]['category'], 'blunder')

    def test_partial_saved_results_resume_only_the_missing_move(self):
        self.install_review_worker()
        self.open_game()
        self.page.get_by_role('button', name='Analyze move quality', exact=True).click()
        expect(self.page.locator('.quality-panel .trend-status')).to_contain_text('Your review is ready')
        self.page.evaluate('flushAnalysisSave()')
        self.page.wait_for_function('reviewQueue.running === 0')
        stored = self.page.evaluate("({version: ANALYSIS_VERSION, turn_analyses: positionCache.results, move_quality: moveQualityResults})")
        stored['turn_analyses'].pop(next(iter(stored['turn_analyses'])))
        self.use_saved_analysis(stored)
        self.page.evaluate("window.resumeCalls=[]; const request=workerRequest; workerRequest=(type,payload,options)=>{resumeCalls.push({type,key:options.taskKey}); return request(type,payload,options);}; startBackgroundAnalysis=window.automaticAnalysis; undefined")
        self.page.get_by_role('button', name='Review game against Demo Cedar').click()
        expect(self.page.locator('.quality-panel .trend-status')).to_contain_text('Your review is ready')
        self.assertEqual(self.page.evaluate("resumeCalls.filter(call => call.key.includes(':mockgame01:')).map(call => call.type)"), ['simulateReview'])
        self.assertEqual(self.page.evaluate('Object.keys(positionCache.results).length'), 6)

    def test_auth_failure_keeps_unsaved_results_and_account_reset_discards_old_work(self):
        self.install_review_worker(delay=100)
        self.open_game()
        result = self.page.evaluate("""async () => {
          const cache = positionCache;
          cache.unsaved = {sentinel: {moves: []}};
          persistenceEnabled = true; signedInUser = {id:'first',email:'first@example.test'};
          cache.owner = analysisOwner();
          CrossplayAuth.accessToken = async () => null;
          CrossplayAuth.getSession = () => null;
          await flushAnalysisSave(currentReview, cache);
          const retained = Boolean(cache.unsaved.sentinel) && Boolean(cache.saveError);
          signedInUser = {id:'second',email:'second@example.test'};
          const oldOwner = cache.owner;
          await flushAnalysisSave(currentReview, cache);
          clearAccountUi('Changed account');
          return {retained,changed:oldOwner !== analysisOwner(),queue:reviewQueue};
        }""")
        self.assertEqual(result, {'retained': True, 'changed': True, 'queue': None})

    def test_late_game_list_cannot_cross_a_local_session_reset(self):
        self.open_game()
        result = self.page.evaluate("""async () => {
          const request = api;
          let release;
          api = async path => path === 'games' ? await new Promise(resolve => { release = resolve; }) : request(path);
          const pending = loadGames();
          clearAccountUi('New local account');
          updateAccountUi('New player');
          games = [{id:'new-game',status:'completed',opponent:'New opponent'}];
          renderGames();
          release({games:[{id:'old-game',status:'completed',opponent:'Old opponent'}]});
          await pending;
          api = request;
          return games.map(game => game.id);
        }""")
        self.assertEqual(result, ['new-game'])
        expect(self.page.get_by_role('button', name='Review game against New opponent')).to_be_visible()
        expect(self.page.get_by_role('button', name='Refresh', exact=True)).to_be_enabled()

    def test_replacing_local_cookie_clears_old_background_contexts(self):
        self.install_review_worker(delay=100)
        self.open_game()
        owner = self.page.evaluate('analysisOwner()')
        self.page.get_by_role('button', name='Manage connection', exact=True).click()
        self.page.get_by_role('textbox', name='NYT-S cookie').fill(EMPTY_COOKIE)
        self.page.get_by_role('button', name='Replace cookie', exact=True).click()
        expect(self.page.locator('#gameCount')).to_have_text('0 finished')
        self.assertNotEqual(owner, self.page.evaluate('analysisOwner()'))
        self.assertEqual(self.page.evaluate('reviewQueue ? reviewQueue.contexts.size : 0'), 0)
        expect(self.page.locator('.turn-row')).to_have_count(0)

    def test_automatic_analysis_with_bundled_quackle_is_saved_and_reused(self):
        self.page.evaluate('startBackgroundAnalysis = window.automaticAnalysis; prepareViewedTurn = window.automaticViewedTurn; undefined')
        saved = []

        def save(route):
            saved.append(route.request.post_data_json)
            route.continue_()

        self.page.route('**/api/crossplay/games/mockgame01/analysis', save)
        self.open_game()
        expect(self.page.locator('.quality-panel .trend-status')).to_contain_text('Your review is ready', timeout=120_000)
        expect(self.page.locator('#trendStatus')).to_contain_text('Win trend complete', timeout=120_000)
        # Every recorded move has a full simulation, reused by details and trend.
        expect(self.page.locator('#engineStatus')).to_contain_text('277 simulations per move', timeout=120_000)
        self.page.get_by_role('combobox', name='Select game turn').select_option('0')
        expect(self.page.locator('#engineStatus')).to_contain_text('277 simulations per move', timeout=120_000)
        expect(self.page.locator('.moves-table tbody tr').first).to_be_visible()
        expect(self.page.get_by_role('button', name='Retry this turn', exact=True)).to_be_hidden()
        kinds = self.page.evaluate('Object.keys(positionCache.results).map(key => JSON.parse(key)[1])')
        self.assertEqual({kind: kinds.count(kind) for kind in set(kinds)},
                         {'review': 6})
        self.page.evaluate('flushAnalysisSave()')
        results = self.page.evaluate('positionCache.results')
        saved_keys = [key for snapshot in saved for key in snapshot['turn_analyses']]
        self.assertEqual(sorted(saved_keys), sorted(results), 'Each engine result is saved exactly once')
        self.assertLess(len(saved), len(saved_keys), 'Engine results are batched into fewer saves')
        self.assertEqual(sum('move_quality' in snapshot for snapshot in saved), 1)
        self.assertEqual(sum('win_trend' in snapshot for snapshot in saved), 1)
        # Owners and shared guests load results without starting the engine or saving again.
        save_count = len(saved)
        self.page.evaluate('workerRequest = () => { throw new Error("Repeated engine work"); }; undefined')
        self.page.get_by_role('button', name='Review game against Demo Cedar').click()
        expect(self.page.locator('#engineStatus')).to_contain_text('277 simulations per move')
        self.assertEqual(self.page.evaluate('positionCache.results'), results)
        self.page.evaluate('flushAnalysisSave()')
        self.assertEqual(len(saved), save_count)
        self.page.reload()
        expect(self.page.locator('#sharedBanner')).to_be_hidden()
        expect(self.page.locator('#engineStatus')).to_contain_text('277 simulations per move')
        self.assertEqual(self.page.evaluate('positionCache.results'), results)

    def enter_custom_move(self, word, square, direction='across'):
        self.page.get_by_role('textbox', name='Custom move word', exact=True).fill(word)
        self.page.get_by_role('textbox', name='Custom move start square', exact=True).fill(square)
        self.page.get_by_role('combobox', name='Custom move direction').select_option(direction)
        self.page.get_by_role('button', name='Evaluate move', exact=True).click()

    def test_custom_move_outside_shortlist_and_invalid_moves(self):
        self.open_game()
        self.page.get_by_role('combobox', name='Select game turn').select_option('0')
        self.enter_custom_move('CAT', 'G8')
        expect(self.page.locator('.custom-move-status')).to_have_text('Move evaluated.', timeout=30_000)
        rows = self.page.locator('.custom-move-result tbody tr')
        expect(rows.first).to_contain_text('Your move: 8G CAT')
        expect(rows.first.locator('td').nth(1)).to_have_text('5')
        expect(rows.first.locator('td').nth(2)).to_have_text('ERSU')
        rank = self.page.locator('.custom-move-result .custom-move-help').first.inner_text()
        self.assertGreater(int(rank.split('#')[1].split()[0]), 20)
        expect(self.page.locator('.custom-move-result')).to_contain_text('Static equity')
        # The recorded move and after-board remain intact during exploration.
        expect(self.page.locator('.board .tile')).to_have_count(3)
        expect(self.page.locator('.turn-action')).to_have_text('Turn 1 · Demo Player · +5')
        for word, square, message in [
            ('CAT', 'A1', 'not legal'),
            ('ZZZ', 'G8', 'rack does not contain'),
            ('CAT', 'O15', 'runs off the board'),
            ('CA.', 'G8', 'only on a square'),
            ('CAT', 'P8', 'start square'),
            ('C<script>', 'G8', 'Enter a word'),
        ]:
            with self.subTest(word=word, square=square):
                self.enter_custom_move(word, square)
                expect(self.page.locator('.custom-move-status')).to_contain_text(message)
                expect(self.page.locator('.custom-move-result tbody tr')).to_have_count(0)
                expect(self.page.get_by_role('button', name='Evaluate move', exact=True)).to_be_enabled()

    def test_custom_move_existing_tiles_blanks_and_turn_reset(self):
        self.open_game()
        turns = self.page.get_by_role('combobox', name='Select game turn')
        turns.select_option('1')
        self.enter_custom_move('ARE', 'H8', 'down')
        expect(self.page.locator('.custom-move-status')).to_have_text('Move evaluated.', timeout=30_000)
        expect(self.page.locator('.custom-move-result tbody tr').first.locator('td').nth(1)).to_have_text('4')
        self.enter_custom_move('.RE', 'H8', 'down')
        expect(self.page.locator('.custom-move-status')).to_have_text('Move evaluated.')
        self.enter_custom_move('ORE', 'H8', 'down')
        expect(self.page.locator('.custom-move-status')).to_contain_text('conflicts with an existing tile')
        turns.select_option('2')
        expect(self.page.locator('.custom-move-result tbody tr')).to_have_count(0)
        self.enter_custom_move('sCAT', 'F8')
        expect(self.page.locator('.custom-move-status')).to_have_text('Move evaluated.', timeout=30_000)
        expect(self.page.locator('.custom-move-result tbody tr').first.locator('td').nth(1)).to_have_text('5')
        expect(self.page.locator('.custom-move-result tbody tr').first).to_contain_text('s')
        self.enter_custom_move('SCAT', 'F8')
        expect(self.page.locator('.custom-move-status')).to_have_text('Move evaluated.')
        expect(self.page.locator('.custom-move-result tbody tr').first.locator('td').nth(1)).to_have_text('7')
        expect(self.page.locator('.custom-move-result tbody tr').first.locator('td').nth(2)).to_contain_text('?')
        # A single new tile may be written in either direction, even when the
        # engine describes the resulting word in the perpendicular direction.
        self.enter_custom_move('s', 'F8', 'down')
        expect(self.page.locator('.custom-move-status')).to_have_text('Move evaluated.')
        expect(self.page.locator('.custom-move-result tbody tr').first.locator('td').nth(1)).to_have_text('5')

    def test_custom_move_win_simulation_and_cancellation(self):
        self.open_game()
        self.page.get_by_role('combobox', name='Select game turn').select_option('0')
        self.enter_custom_move('RECUT', 'H8')
        expect(self.page.locator('.custom-move-status')).to_have_text('Move evaluated.', timeout=30_000)
        expect(self.page.locator('.custom-move-result')).to_contain_text('Static rank #22')
        self.page.get_by_role('button', name='Estimate win% (slower)', exact=True).click()
        expect(self.page.get_by_role('button', name='Evaluate move', exact=True)).to_be_disabled()
        self.page.get_by_role('button', name='Cancel evaluation', exact=True).click()
        expect(self.page.locator('.custom-move-status')).to_have_text('Evaluation cancelled.')
        expect(self.page.get_by_role('button', name='Evaluate move', exact=True)).to_be_enabled()
        # Retrying starts a fresh worker and includes a move outside the top 20.
        self.page.get_by_role('button', name='Estimate win% (slower)', exact=True).click()
        expect(self.page.locator('.custom-move-status')).to_have_text('Win simulation complete.', timeout=120_000)
        expect(self.page.locator('.custom-move-result')).to_contain_text('277 samples each')
        expect(self.page.locator('.custom-move-result')).to_contain_text('Sim. equity')
        expect(self.page.locator('.custom-move-result tbody tr').first).to_contain_text('8H RECUT')
        self.assertRegex(self.page.locator('.custom-move-result tbody tr').first.locator('td').last.inner_text(), r'^\d+\.\d%$')
        self.page.get_by_role('combobox', name='Select game turn').select_option('1')
        expect(self.page.locator('.custom-move-result tbody tr')).to_have_count(0)

    def test_frequency_failure_preserves_move_quality_and_can_be_retried(self):
        self.page.route('**/wordfreq-en-25000-log.json',
                        lambda route: route.fulfill(status=503, body='Unavailable'))
        self.open_game()
        self.page.get_by_role('button', name='Analyze move quality', exact=True).click()
        expect(self.page.locator('.quality-panel .trend-status')).to_contain_text(
            'Your review is ready', timeout=120_000)
        expect(self.page.locator('.luck-panel')).to_contain_text('Word commonness could not load')
        self.assertTrue(self.page.evaluate('moveQualityResults.points.length > 0'))
        self.assertFalse(self.page.evaluate('moveQualityResults.points.some(point => point.opportunity)'))
        self.page.unroute('**/wordfreq-en-25000-log.json')
        self.page.get_by_role('button', name='Re-analyze moves', exact=True).click()
        expect(self.page.locator('.quality-panel .trend-status')).to_contain_text(
            'Your review is ready', timeout=120_000)
        expect(self.page.locator('.luck-panel')).not_to_contain_text('Word commonness could not load')
        self.assertTrue(self.page.evaluate('moveQualityResults.points.some(point => point.opportunity)'))

    def test_real_engine_produces_weighted_performance_for_a_rated_move(self):
        # A synthetic opening with a short rack and a top static candidate.
        grid = ['.' * 15 for _ in range(15)]
        after = grid.copy()
        after[7] = '.......ACT.....'
        self.use_saved_analysis({}, turns=[{
            'index': 0, 'mine': True, 'action': 'ACT 8H 6', 'score': 6,
            'rack': 'CAT', 'grid': grid, 'grid_after': after,
            'bag_count': 90, 'final_turns_remaining': 0,
            'mover_score': 0, 'other_score': 0,
        }], my_score=6, opponent_score=0, turn_count=1)
        self.open_game()
        self.page.get_by_role('button', name='Analyze move quality', exact=True).click()
        expect(self.page.locator('.quality-panel .trend-status')).to_contain_text(
            'Your review is ready', timeout=120_000)
        expect(self.page.locator('.luck-panel')).to_contain_text('1 of your 1 turns weighted')
        expect(self.page.locator('.luck-stat').nth(1).locator('strong')).to_have_text('100 / 100')
        expect(self.page.locator('.luck-stat').nth(2).locator('strong')).to_have_text('100 / 100')
        self.page.locator('.luck-breakdown summary').click()
        expect(self.page.locator('.luck-table')).to_contain_text('ACT · Common')
        first_points = self.page.evaluate('moveQualityResults.points')
        self.assertEqual(self.page.evaluate('moveQualityResults.evaluation'), 'win-probability-v1')
        self.page.get_by_role('button', name='Re-analyze moves', exact=True).click()
        expect(self.page.locator('.quality-panel .trend-status')).to_contain_text(
            'Your review is ready', timeout=120_000)
        self.assertEqual(self.page.evaluate('moveQualityResults.points'), first_points,
                         'Seeded win results and separate static performance must be reused')


if __name__ == '__main__':
    unittest.main(verbosity=2)
