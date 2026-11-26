#!/usr/bin/env python3
"""Playwright checks through the real frontend, local API and NYT fixtures.

Starts an isolated server by default. Set CROSSPLAY_TEST_BASE_URL to exercise
an already-running `npm run frontend` server instead. External requests fail.
"""

import json
import os
import re
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from playwright.sync_api import Error, expect, sync_playwright

from tools.mock_nyt import EMPTY_COOKIE, EXPIRED_COOKIE, MOCK_COOKIE, OUTAGE_COOKIE
from tools.test_support import mock_server, trace_quackle_workers


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

    def start_review(self, page=None):
        page = page or self.page
        expect(page.locator('.game-overview')).to_be_visible()
        page.get_by_role('button', name='Start Review', exact=True).click()

    def open_game(self):
        self.connect()
        self.page.get_by_role('button', name='Review game against Demo Cedar').click()
        self.start_review()
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
        expect(turns).to_have_value('0')
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
        expect(self.page.locator('.game-overview')).to_be_visible()
        expect(self.page.locator('.board')).to_have_count(0)
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
        self.start_review(guest)
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
        self.start_review()
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
        expect(self.page.get_by_role('button', name='Refresh', exact=True)).to_be_enabled()
        expect(self.page.get_by_role('button', name='Forget cookie')).to_be_hidden()

    def test_upstream_failure(self):
        self.connect(OUTAGE_COOKIE)
        expect(self.page.locator('#gameCount')).to_contain_text('NYT returned HTTP 503')
        expect(self.page.get_by_role('button', name='Refresh', exact=True)).to_be_enabled()

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

    def open_statistics(self):
        statistics = self.page.locator('.review-statistics')
        if not statistics.evaluate('node => node.open'):
            statistics.locator('> summary').click()

    def stat(self, label):
        return self.page.locator('.dashboard-stat').filter(
            has=self.page.get_by_text(label, exact=True))

    def test_personal_summary_and_score_replay_without_analysis(self):
        self.open_game()
        self.open_statistics()
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
        lesson = self.page.locator('.review-lesson')
        expect(lesson).to_contain_text('Start with turn 3 · 5.6 percentage points')
        lesson.get_by_role('button').click()
        expect(self.page.get_by_role('combobox', name='Select game turn')).to_have_value('2')
        self.open_statistics()
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
        self.start_review()
        expect(self.stat('Avg. win chance lost').locator('.dashboard-stat-value')).to_have_text('—')
        expect(self.page.locator('.insight')).to_have_count(0)
        expect(self.page.locator('.review-heading')).to_contain_text('Lost by 6 points')

    def test_luck_panel_without_analysis_and_legacy_saved_analysis(self):
        self.use_saved_analysis()
        self.open_game()
        self.open_statistics()
        panel = self.page.locator('.luck-panel')
        cards = panel.locator('.luck-stat')
        expect(cards.nth(0)).to_contain_text('3 of your 3 racks estimated')
        expect(cards.nth(1)).to_contain_text('2 of your 3 turns rated')
        expect(cards.nth(2).locator('strong')).to_have_text('—')
        # The known swap in this legacy result must resume instead of staying unrated.
        expect(panel).to_contain_text('Performance analysis running…')
        self.assertEqual(self.page.evaluate('moveQualityResults.state'), 'running')
        panel.locator('.luck-breakdown summary').click()
        expect(panel.locator('tbody tr')).to_have_count(3)
        panel.get_by_role('button', name='Replay turn 3 from luck breakdown').click()
        expect(self.page.get_by_role('combobox', name='Select game turn')).to_have_value('2')
        self.page.get_by_role('button', name='Review game against Demo River').click()
        self.start_review()
        expect(self.page.locator('.luck-panel')).to_contain_text('Performance unavailable')
        expect(self.page.locator('.luck-stat').nth(1).locator('strong')).to_have_text('—')

    def test_saved_word_opportunities_weight_only_eligible_personal_turns(self):
        analysis = {'version': 'quackle-crossplay-v2', 'move_quality': {
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
        self.open_statistics()
        panel = self.page.locator('.luck-panel')
        expect(panel.locator('.luck-stat').nth(1).locator('strong')).to_have_text('50 / 100')
        expect(panel.locator('.luck-stat').nth(2).locator('strong')).to_have_text('63 / 100')
        expect(panel).to_contain_text('1 of your 3 turns weighted · partial analysis')
        expect(panel.locator('.frequency-source')).to_have_text('wordfreq · CC BY-SA 4.0')
        panel.locator('.luck-breakdown summary').click()
        expect(panel.locator('tbody tr').first).to_contain_text('QAT · Less common')
        expect(panel.locator('tbody tr').first).to_contain_text('6.0 eq')
        expect(panel.locator('tbody tr').nth(1)).to_contain_text('Unrated')

    def test_incomplete_and_unrated_analysis_never_implies_perfect_play(self):
        analysis = {'version': 'quackle-crossplay-v2', 'move_quality': {
            'state': 'stopped', 'points': [
                {'turnIndex': 0, 'category': 'unrated', 'loss': None, 'reason': 'Outside candidates'},
                {'turnIndex': 1, 'category': 'best', 'loss': 0.0}]}}
        self.use_saved_analysis(analysis)
        self.open_game()
        expect(self.stat('Avg. win chance lost').locator('.dashboard-stat-value')).to_have_text('—')
        expect(self.page.locator('.insights-panel .panel-subtitle')).to_have_text('No rated turns')
        expect(self.page.locator('.insights-panel .analysis-note')).to_have_text('0 / 3 turns rated')
        expect(self.page.locator('.insights-panel .insight')).to_have_count(0)

    def test_draw_missing_scores_and_final_adjustments(self):
        self.use_saved_analysis(my_score=14, opponent_score=14)
        self.open_game()
        expect(self.page.locator('.review-heading')).to_contain_text('Draw')
        expect(self.page.locator('.score-point')).to_have_count(7)
        expect(self.page.locator('.score-point').last).to_have_attribute('aria-label', 'Final score: 14 to 14. Replay turn 6')
        self.page.unroute('**/api/crossplay/games/mockgame01/review')
        self.use_saved_analysis(my_score=None, opponent_score=None)
        self.page.get_by_role('button', name='Review game against Demo Cedar').click()
        self.start_review()
        expect(self.page.locator('.review-heading')).to_contain_text('Final scores unavailable')
        expect(self.page.locator('.review-heading')).not_to_contain_text('Draw')
        expect(self.page.locator('.score-value').first).to_have_text('—')

    def test_mobile_layout_and_board_fit(self):
        self.use_saved_analysis()
        self.open_game()
        self.open_statistics()
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
                expect(self.page.get_by_role('button', name='Stop move analysis', exact=True)).to_be_visible()
        self.page.set_viewport_size({'width': 390, 'height': 844})
        self.page.locator('.insight').click()
        expect(self.page.get_by_role('combobox', name='Select game turn')).to_have_value('2')

    def test_walkthrough_navigation_and_personal_flag_filter(self):
        self.use_saved_analysis()
        self.open_game()
        self.open_statistics()
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
        expect(self.page.locator('.turn-coach')).to_contain_text('Waiting for analysis…')
        expect(self.page.locator('.win-estimate-state')).to_have_text('Waiting for estimate')
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
        self.open_statistics()
        self.page.locator('.insight').click()
        comparison = self.page.locator('.move-comparison')
        expect(comparison).to_contain_text('SCAT · 8F')
        expect(comparison).to_contain_text('CATS · 8G')
        expect(comparison).to_contain_text('6 points · tiles left: ?EINRU')
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
        self.start_review()
        expect(self.page.locator('.move-comparison')).to_have_count(0)
        expect(self.page.get_by_role('checkbox', name='Only your flagged moves')).not_to_be_checked()

    def test_blank_alternative_and_older_saved_review(self):
        analysis = json.loads((Path(__file__).parent / 'tools/fixtures/review-analysis.json').read_text())
        analysis['move_quality']['points'][2]['bestMove']['tiles'] = '...s'
        self.use_saved_analysis(analysis)
        self.open_game()
        self.open_statistics()
        self.page.locator('.insight').click()
        self.page.get_by_role('button', name='Preview best move', exact=True).click()
        expect(self.page.locator('.board .blank-tile.last-play')).to_have_text('S')
        expect(self.page.locator('.board .last-play')).to_have_count(1)
        # Old saved analyses and sparse, out-of-order results map by turnIndex.
        self.page.unroute('**/api/crossplay/games/mockgame01/review')
        self.use_saved_analysis({'version': 'quackle-crossplay-v2', 'move_quality': {
            'state': 'stopped', 'points': [
                {'turnIndex': 2, 'category': 'blunder', 'loss': 10.5},
                {'turnIndex': 0, 'category': 'inaccuracy', 'loss': 2.0}]}})
        self.page.get_by_role('button', name='Review game against Demo Cedar').click()
        self.start_review()
        self.open_statistics()
        self.page.get_by_role('button', name='Start walkthrough', exact=True).click()
        expect(self.page.locator('.turn-coach')).to_contain_text('Inaccuracy · 2.0 percentage points lost')
        self.page.get_by_role('button', name='Next flagged move →').click()
        expect(self.page.locator('.turn-coach')).to_contain_text('Blunder · 10.5 percentage points lost')
        expect(self.page.locator('.turn-coach')).to_contain_text('Comparison unavailable · Re-analyze moves')
        expect(self.page.get_by_role('button', name='Preview best move', exact=True)).to_have_count(0)
        self.page.get_by_role('checkbox', name='Only your flagged moves').check()
        expect(self.page.locator('.turn-row')).to_have_count(2)
        expect(self.page.locator('.turn-row .quality-chip').last).to_have_text('Blunder')

    def test_only_played_candidate_is_highlighted_as_turn_changes(self):
        self.install_review_worker()
        self.open_game()
        turns = self.page.get_by_role('combobox', name='Select game turn')
        for turn_index in (0, 2, 4, 0):
            with self.subTest(turn=turn_index):
                turns.select_option(str(turn_index))
                self.page.evaluate("analyzeReviewTurn(contextFor(currentReview), currentTurnIndex, 'preview')")
                rows = self.page.locator('.position-output .moves-table tbody tr')
                played = self.page.locator('.position-output .played-move')
                expect(played).to_have_count(1)
                expect(played.locator('.played-move-label')).to_have_text('Played')
                expect(played.locator('td').first).to_have_css('font-weight', '700')
                expect(played.locator('td').first).to_have_css('background-color', 'rgb(255, 244, 212)')
                # The best alternative must not look like the played move.
                if turn_index != 4:
                    expect(rows.first).not_to_have_class(re.compile(r'(^|\s)played-move(\s|$)'))
                    expect(rows.first.locator('td').first).to_have_css('font-weight', '400')
                    expect(rows.first.locator('td').first).to_have_css('color', 'rgb(32, 51, 45)')
                    expect(rows.nth(1)).to_have_class(re.compile(r'(^|\s)played-move(\s|$)'))
                else:
                    expect(rows.first).to_have_class(re.compile(r'(^|\s)played-move(\s|$)'))

    def test_table_previews_blank_alternatives_and_restores_the_recorded_board(self):
        self.use_saved_analysis()
        self.open_game()
        self.page.get_by_role('combobox', name='Select game turn').select_option('2')
        original = self.page.locator('.turn-panel > .board-scroll .board').inner_text()
        self.page.evaluate('''() => {
          const turn = currentReview.turns[currentTurnIndex];
          positionCache.results[positionCache.key(turn, 'preview')] = {
            simulated: true, iterationsCompleted: 64, moves: [
              {action: 'place', position: '8G', tiles: '...s', score: 6, win: .62},
              {action: 'place', position: '8F', tiles: 's...', score: 5, win: .43},
              {action: 'pass', position: '', tiles: '', score: 0, win: .1},
              {action: 'exchange', position: '', tiles: 'E', score: 0, win: .2},
              {action: 'place', position: '99Z', tiles: 'X', score: 0}
            ]
          };
          renderReview();
        }''')
        rows = self.page.locator('.position-output .moves-table tbody tr')
        expect(rows.last.locator('button')).to_have_count(0)
        # Clicking a score cell must select the whole move, including a blank
        # after existing letters. The existing tiles must remain untouched.
        rows.first.locator('td').nth(1).click()
        expect(self.page.locator('.board-preview-note')).to_contain_text('CATS at 8G')
        expect(self.page.locator('.board .blank-tile.last-play')).to_have_text('S')
        expect(self.page.locator('.board .last-play')).to_have_count(1)
        expect(rows.first.locator('button')).to_have_attribute('aria-pressed', 'true')
        expect(self.page.locator('.win-turn-label')).to_contain_text('Selected move preview')
        expect(self.page.locator('.win-you strong')).to_have_text('62.0%')
        rows.first.locator('button').focus()
        self.page.evaluate('''() => {
          const turn = currentReview.turns[currentTurnIndex];
          const early = positionCache.get(turn, 'preview');
          positionCache.results[positionCache.key(turn, 'review')] = {
            ...early, iterationsCompleted: 277,
            moves: early.moves.map(move => ({...move, win: .7}))
          };
          refreshMoveQualityPanel(); refreshTrendPanel(); refreshPositionPanel();
        }''')
        expect(rows.first.locator('button')).to_be_focused()
        expect(self.page.locator('.win-you strong')).to_have_text('70.0%')
        expect(self.page.locator('.win-estimate-state')).to_have_text('Full estimate')
        # Native buttons also support keyboard activation for passes/exchanges.
        for index, label in [(2, 'Pass'), (3, 'Exchange E')]:
            rows.nth(index).locator('button').focus()
            self.page.keyboard.press('Enter')
            expect(self.page.locator('.board-preview-note')).to_contain_text(label)
            expect(self.page.locator('.board .last-play')).to_have_count(0)
            expect(self.page.locator('.board .tile')).to_have_count(5)
        self.page.get_by_role('button', name='Return to played move', exact=True).click()
        self.assertEqual(self.page.locator('.board').inner_text(), original)
        rows.first.locator('button').click()
        self.page.get_by_role('button', name='Next →', exact=True).click()
        expect(self.page.locator('.board-preview-note')).to_have_count(0)

    def test_continuation_recomputes_replies_and_can_undo_without_changing_review(self):
        self.open_game()
        self.page.evaluate('''() => {
          window.continuationRequests = [];
          const request = workerRequest;
          workerRequest = (type, payload, options) => {
            continuationRequests.push({type, iterations: payload.iterations, grid: payload.gridJson});
            return request(type, payload, options);
          };
          const turn = currentReview.turns[0];
          positionCache.results[positionCache.key(turn, 'candidates')] = {
            moves: [{action: 'place', position: '8G', tiles: 'ACT', score: 5}]
          };
          renderReview();
        }''')
        original = self.page.evaluate('JSON.stringify(currentReview)')
        self.page.locator('.move-preview-button').click()
        expect(self.page.locator('.win-you strong')).to_have_text('—')
        self.page.get_by_role('button', name='Explore continuations', exact=True).click()
        panel = self.page.get_by_role('region', name='Hypothetical continuation')
        replies = panel.locator('.continuation-moves tbody tr')
        expect(replies.first).to_be_visible(timeout=120_000)
        expect(panel).not_to_contain_text('Future draws are sampled')
        expect(panel).to_contain_text('Demo Cedar to move')
        expect(panel).to_contain_text('277 samples')
        expect(panel.get_by_role('columnheader', name='Win % (Demo Cedar)', exact=True)).to_be_visible()
        wins = replies.locator('td:nth-child(4)').all_text_contents()
        self.assertTrue(all(value.endswith('%') for value in wins))
        self.assertEqual(wins, sorted(wins, key=lambda value: float(value[:-1]), reverse=True))
        first_estimates = replies.all_text_contents()
        expect(panel.locator('.board .tile')).to_have_count(3)
        board = panel.locator('.board').inner_text()
        first_reply = replies.first.locator('button').inner_text()
        # Real Quackle must see the changed ACT board, not the recorded CAT.
        self.assertTrue(self.page.evaluate('''() => {
          const state = continuationExplorer.history.at(-1);
          return state.grid[7] === '......ACT......' && state.racks[1] === currentReview.turns[1].rack;
        }'''))
        replies.first.locator('button').click()
        expect(panel).to_contain_text('After 2 hypothetical moves')
        expect(replies.first).to_be_visible(timeout=120_000)
        expect(panel).to_contain_text('You to move')
        expect(panel.get_by_role('columnheader', name='Win % (You)', exact=True)).to_be_visible()
        best_action = self.page.evaluate('''() => {
          const state = continuationExplorer.history.at(-1);
          return MoveContinuation.play(state, state.moves[0]).action;
        }''')
        panel.get_by_role('button', name='Play best reply →', exact=True).click()
        expect(panel).to_contain_text('After 3 hypothetical moves')
        self.assertEqual(self.page.evaluate('continuationExplorer.history.at(-1).action'), best_action)
        expect(replies.first).to_be_visible(timeout=120_000)
        panel.get_by_role('button', name='← Undo reply', exact=True).click()
        panel.get_by_role('button', name='← Undo reply', exact=True).click()
        expect(panel).to_contain_text('After 1 hypothetical move')
        self.assertEqual(panel.locator('.board').inner_text(), board)
        self.assertEqual(replies.first.locator('button').inner_text(), first_reply)
        self.assertEqual(replies.all_text_contents(), first_estimates)
        # Three real reply lists prove recomputation. Undo must use the saved
        # lists, without simulating an entire game at 277 samples.
        requests = self.page.evaluate('continuationRequests')
        self.assertEqual([(item['type'], item['iterations']) for item in requests],
                         [('simulateReview', 277)] * 3)
        self.assertEqual(json.loads(requests[0]['grid'])[7], '......ACT......')
        self.assertIsNone(self.page.evaluate('continuationExplorer.error'))
        self.assertEqual(self.page.evaluate('JSON.stringify(currentReview)'), original)
        self.page.get_by_role('button', name='Next →', exact=True).click()
        expect(panel).to_have_count(0)
        expect(self.page.locator('.board-preview-note')).to_have_count(0)

    def test_continuation_final_empty_bag_turn_finishes_without_a_worker(self):
        self.open_game()
        result = self.page.evaluate("""() => {
          // The model tests cover draining the bag and both final turns. This
          // small observed final-turn fixture checks the terminal UI directly.
          // Reuse the physical empty-bag position from the WASM regressions.
          const grid = [
            'CD........G....', '.......A.NOUN.Y', '.......L...TAME',
            '......YEAH...E.', '.........ab.yA.', '......LOB.OWED.',
            '..PROG..I.TEN.F', '....D..JOGS...I', '...LASSI.O..RUN',
            '.......V.R.MI..', '...C...E..HIPS.', '...A.BASE.A.E..',
            '...R.O..D.V....', '..RENT.LITER...', '.....H..T......'
          ];
          const rack = 'WEAFTUO';
          const pool = Object.entries(CrossplayLuck.TILE_COUNTS)
            .flatMap(([tile, count]) => Array(count).fill(tile));
          const boardTiles = [...grid.join('')].filter(tile => tile !== '.')
            .map(tile => tile === tile.toLowerCase() ? '?' : tile);
          for (const tile of [...boardTiles, ...rack]) pool.splice(pool.indexOf(tile), 1);
          let requests = 0;
          continuationExplorer = new MoveContinuation.Explorer({
            turn: {grid, rack, bag_count: 0, final_turns_remaining: 1,
              mover_score: 20, other_score: 10},
            opponentRack: pool.join(''), seed: 1, move: {action: 'pass', tiles: '', score: 0},
            names: ['You', 'Demo Cedar'], renderBoard,
            request: () => { requests++; throw new Error('The completed line must not simulate replies.'); }
          });
          document.querySelector('.turn-panel').append(continuationExplorer.root);
          const state = continuationExplorer.history.at(-1);
          return {requests, bag: state.bag.length, finalTurns: state.finalTurns, finished: state.finished,
            error: continuationExplorer.error};
        }""")
        panel = self.page.get_by_role('region', name='Hypothetical continuation')
        expect(panel).to_contain_text('Line complete · You: 20 · Demo Cedar: 10')
        expect(panel.get_by_role('button', name='Play best reply →', exact=True)).to_have_count(0)
        expect(panel.get_by_role('button', name='← Undo reply', exact=True)).to_be_disabled()
        self.assertEqual(result, {'requests': 0, 'bag': 0, 'finalTurns': 0, 'finished': True, 'error': None})
        self.page.get_by_role('button', name='Next →', exact=True).click()
        expect(panel).to_have_count(0)

    def install_review_worker(self, delay=0):
        worker = r"""
          self.onmessage = ({data: {id, type, payload: p}}) => {
            if (type === 'init') return self.postMessage({id, result: {}});
            if (type === 'simulateOpening') {
              const result = {simulated: true, samplingVersion: 'initial-deal-v1', policy: 'static-best-v1',
                firstRack: p.firstRack, secondRack: p.secondRack, seed: p.seed,
                baselineWin: .55, firstOnlyWin: .65, secondOnlyWin: .45, afterWin: .6,
                firstWpa: 12.5, secondWpa: 7.5, iterationsRequested: p.iterations,
                iterationsCompleted: p.iterations, plies: 2, playToEnd: false,
                simulationMode: '2-ply', winModel: 'bogowin'};
              return setTimeout(() => self.postMessage({id, result}), DELAY);
            }
            const win = p.rack === 'ACERSTU' ? .3 : p.rack === '?EINRSU' ? .43 : .5;
            if (type === 'simulateDraw') {
              const move = p.played, grid = JSON.parse(p.gridJson);
              let used = move.action === 'exchange' ? move.tiles.length : 0;
              if (move.action === 'place') {
                const across = move.position.match(/^(\d+)([A-O])$/);
                const down = move.position.match(/^([A-O])(\d+)$/);
                let row = Number(across ? across[1] : down[2]) - 1;
                let col = 'ABCDEFGHIJKLMNO'.indexOf(across ? across[2] : down[1]);
                for (const tile of move.tiles) {
                  if (grid[row][col] === '.' && tile !== '.') used++;
                  if (across) col++; else row++;
                }
              }
              const drawCount = move.action === 'pass' ? 0 : Math.min(p.bagCount, 7 - (p.rack.length - used));
              const end = p.bagCount <= 21;
              const result = {simulated: true, samplingVersion: 'rack-draw-v1', seed: p.seed,
                selected: {...move}, rackAfter: p.rackAfter, drawCount, beforeWin: win,
                afterWin: drawCount ? Math.min(1, win + .05) : win,
                plies: end ? -1 : 2, playToEnd: end, simulationMode: end ? 'play-to-end' : '2-ply',
                winModel: end ? 'terminal' : 'bogowin', bagCount: p.bagCount,
                finalTurnsRemaining: p.finalTurnsRemaining,
                iterationsRequested: p.iterations, iterationsCompleted: p.iterations};
              return setTimeout(() => self.postMessage({id, result}), DELAY);
            }
            if (type !== 'simulateReview') return self.postMessage({id, error: 'Unexpected request: ' + type});
            const selected = {...p.played, score: 5, leave: 'ERSU', equity: 2, staticEquity: 2,
              win, samples: p.iterations};
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

    def test_preview_updates_visible_estimate_then_full_result_replaces_it(self):
        self.install_review_worker()
        self.open_game()
        result = self.page.evaluate("""async () => {
          currentTurnIndex = 0; boardView = 'before'; renderReview();
          const context = contextFor(currentReview);
          const calls = [], request = workerRequest;
          workerRequest = (type, payload, options) => {
            calls.push({type, iterations: payload.iterations, count: payload.numMoves, seed: payload.seed});
            return request(type, payload, options);
          };
          await analyzeReviewTurn(context, 0, 'preview');
          const early = structuredClone(context.quality.points[0]);
          const earlyLabel = document.querySelector('.win-estimate-state').textContent;
          const savedPreview = {version: ANALYSIS_VERSION, turn_analyses: structuredClone(context.cache.results)};
          const resumed = makeReviewContext({...currentReview, stored_analysis: savedPreview}, analysisOwner());
          await analyzeReviewTurn(context, 0, 'review');
          const complete = await analyzeReviewTurn(context, 0, 'preview');
          return {early, earlyLabel, full: context.quality.points[0], calls, complete,
            resumed: Boolean(resumed.cache.get(currentReview.turns[0], 'preview')),
            keys: Object.keys(context.cache.results).map(key => JSON.parse(key)[1])};
        }""")
        self.assertTrue(result['early']['provisional'])
        self.assertEqual(result['early']['samples'], 64)
        self.assertEqual(result['earlyLabel'], 'Early estimate')
        self.assertFalse(result['full']['provisional'])
        self.assertEqual(result['full']['samples'], 277)
        self.assertEqual([(call['iterations'], call['count']) for call in result['calls']
                          if call['type'] == 'simulateReview'], [(64, 5), (277, 15)])
        self.assertEqual([(call['iterations'], call['type']) for call in result['calls']],
                         [(64, 'simulateReview'), (64, 'simulateDraw'),
                          (277, 'simulateReview'), (277, 'simulateDraw')])
        self.assertEqual(result['early']['draw']['samples'], 64)
        self.assertEqual(result['full']['draw']['samples'], 277)
        self.assertAlmostEqual(result['full']['draw']['luck'], 5)
        self.assertEqual(result['calls'][0]['seed'], result['calls'][1]['seed'])
        self.assertTrue(result['complete']['complete'])
        self.assertTrue(result['resumed'])
        self.assertCountEqual(result['keys'], ['preview', 'review', 'draw-preview', 'draw'])
        expect(self.page.locator('.win-estimate-state')).to_have_text('Full estimate')

    def test_late_preview_cannot_replace_a_full_result(self):
        self.install_review_worker(delay='p.iterations === 64 ? 250 : 0')
        self.open_game()
        result = self.page.evaluate("""async () => {
          const context = contextFor(currentReview);
          await Promise.all([analyzeReviewTurn(context, 0, 'preview'), analyzeReviewTurn(context, 0, 'review')]);
          return {points: context.quality.points, trend: context.trend.points};
        }""")
        self.assertEqual(len(result['points']), 1)
        self.assertEqual(result['points'][0]['samples'], 277)
        self.assertFalse(result['points'][0]['provisional'])
        self.assertFalse(result['trend'][0]['provisional'])

    def test_saved_previews_resume_full_analysis_without_repeating_the_early_pass(self):
        self.install_review_worker()
        self.open_game()
        stored = self.page.evaluate("""async () => {
          const context = contextFor(currentReview);
          for (let index = 0; index < currentReview.turns.length; index++) await analyzeReviewTurn(context, index, 'preview');
          await flushAnalysisSave();
          return {version: ANALYSIS_VERSION, turn_analyses: context.cache.results};
        }""")
        self.use_saved_analysis(stored)
        self.page.evaluate("""window.resumeCalls = []; const request = workerRequest;
          workerRequest = (type, payload, options) => {
            if (options.taskKey.includes(':mockgame01:')) resumeCalls.push({type, iterations: payload.iterations});
            return request(type, payload, options);
          }; startBackgroundAnalysis = window.automaticAnalysis; undefined""")
        self.page.get_by_role('button', name='Review game against Demo Cedar').click()
        self.start_review()
        expect(self.page.locator('.quality-panel .trend-status')).to_contain_text('Your review is ready')
        self.assertEqual(self.page.evaluate("resumeCalls.filter(call => call.type !== 'simulateOpening').map(call => call.iterations)"), [277] * 10)
        self.assertEqual(self.page.evaluate("resumeCalls.filter(call => call.type === 'simulateOpening').map(call => call.iterations)"), [64, 277])
        self.assertTrue(self.page.evaluate('moveQualityResults.points.every(point => !point.provisional && point.samples === 277)'))

    def test_late_visible_preview_preserves_completed_review_and_final_result(self):
        self.install_review_worker()
        self.open_game()
        result = self.page.evaluate("""async () => {
          const context = contextFor(currentReview), request = workerRequest;
          await analyzeOpeningDraw(context, 'review');
          let release;
          workerRequest = (type, payload, options) => type === 'simulateReview' && payload.iterations === 64
            ? new Promise(resolve => { release = () => resolve(request(type, payload, options)); })
            : request(type, payload, options);
          const pending = analyzeReviewTurn(context, 0, 'preview');
          while (!release) await new Promise(resolve => setTimeout(resolve, 0));
          for (let index = 0; index < currentReview.turns.length; index++) await analyzeReviewTurn(context, index, 'review');
          await finishReviewAnalysis(context);
          const message = context.quality.message;
          release(); await pending;
          await analyzeReviewTurn(context, 0, 'preview');
          return {quality: context.quality.state, trend: context.trend.state,
            final: context.trend.points.at(-1).actual, sameMessage: context.quality.message === message};
        }""")
        self.assertEqual(result, {'quality': 'done', 'trend': 'done', 'final': True, 'sameMessage': True})

    def test_visible_turn_preview_updates_grade_without_waiting_for_background(self):
        self.install_review_worker()
        self.open_game()
        self.page.evaluate("prepareViewedTurn = window.automaticViewedTurn; currentTurnIndex = 0; boardView = 'before'; renderReview()")
        expect(self.page.locator('.win-estimate-state')).to_have_text('Early estimate')
        self.assertEqual(self.page.evaluate('moveQualityResults.points[0].samples'), 64)
        self.assertEqual(self.page.evaluate('reviewQueue.running'), 0)

    def test_late_preview_after_full_failure_keeps_review_incomplete_and_retryable(self):
        self.install_review_worker()
        self.open_game()
        result = self.page.evaluate("""async () => {
          const context = contextFor(currentReview), request = workerRequest;
          await analyzeOpeningDraw(context, 'review');
          let release;
          workerRequest = (type, payload, options) => type === 'simulateReview' && payload.iterations === 64
            ? new Promise(resolve => { release = () => resolve(request(type, payload, options)); })
            : payload.rack === 'ACERSTU' ? Promise.reject(new Error('Full analysis failed'))
            : request(type, payload, options);
          const pending = analyzeReviewTurn(context, 0, 'preview');
          while (!release) await new Promise(resolve => setTimeout(resolve, 0));
          for (let index = 0; index < currentReview.turns.length; index++) await analyzeReviewTurn(context, index, 'review');
          await finishReviewAnalysis(context);
          release(); await pending;
          const retry = await analyzeReviewTurn(context, 0, 'preview');
          return {quality: context.quality.state, trend: context.trend.state,
            point: context.quality.points[0], retry};
        }""")
        self.assertEqual(result['quality'], 'error')
        self.assertEqual(result['trend'], 'error')
        self.assertEqual(result['point']['category'], 'unrated')
        self.assertTrue(result['point']['retryable'])
        self.assertFalse(result['point']['provisional'])
        self.assertFalse(result['retry']['complete'])
        self.assertTrue(result['retry']['failed'])

    def test_paused_review_does_not_start_new_preview_when_turn_changes(self):
        self.install_review_worker()
        self.open_game()
        result = self.page.evaluate("""async () => {
          const context = contextFor(currentReview);
          await analyzeReviewTurn(context, 0, 'preview');
          pauseReviewAnalysis(currentReview);
          const calls = [], request = workerRequest;
          workerRequest = (...args) => { calls.push(args[1].iterations); return request(...args); };
          prepareViewedTurn = window.automaticViewedTurn;
          currentTurnIndex = 1; boardView = 'before'; renderReview();
          await new Promise(resolve => setTimeout(resolve, 100));
          return {calls, points: context.quality.points.length, state: context.quality.state};
        }""")
        self.assertEqual(result, {'calls': [], 'points': 1, 'state': 'stopped'})

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
        self.open_statistics()
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
        self.start_review()
        self.page.get_by_role('combobox', name='Select game turn').select_option('2')
        expect(self.page.locator('.turn-coach')).to_contain_text('Mistake · 7.0 percentage points lost')
        expect(self.page.get_by_role('button', name='Preview best move', exact=True)).to_be_enabled()

    def test_older_saved_grades_are_regraded_and_unevaluable_plays_stay_unrated(self):
        worker = "self.onmessage = ({data: {id, type}}) => self.postMessage(type === 'init' ? {id, result: {}} : {id, error: 'This move is not legal in Quackle’s dictionary at this position.'});"
        self.page.route('**/quackle-worker.js', lambda route: route.fulfill(
            content_type='application/javascript', body=worker))
        self.use_saved_analysis({'version': 'quackle-crossplay-v2', 'move_quality': {
            'state': 'done', 'evaluation': 'static-equity-v1', 'points': [{
                'turnIndex': 0, 'turn': 1, 'mine': True, 'action': 'CAT 8G 5', 'category': 'unrated', 'loss': None,
                'reason': 'Played move was outside Quackle’s returned top 15 candidates.'}]}})
        self.page.evaluate('startBackgroundAnalysis = window.automaticAnalysis; undefined')
        self.open_game()
        expect(self.page.locator('.quality-panel .trend-status')).to_contain_text('Some analysis failed')
        self.assertEqual(self.page.evaluate('moveQualityResults.evaluation'), 'win-probability-v1')
        point = self.page.evaluate('moveQualityResults.points[0]')
        self.assertEqual(point['category'], 'unrated')
        self.assertTrue(point['retryable'])
        self.assertIn('Quackle could not evaluate the played move: This move is not legal', point['reason'])

    def use_saved_unrated_exchange(self, swapped_tiles):
        grid = ['.' * 15 for _ in range(15)]
        review_changes = {
            'turns': [{'index': 0, 'mine': True, 'action': 'SWAP 0', 'score': 0,
                       'rack': 'AEIRST?', 'swapped_tiles': swapped_tiles,
                       'grid': grid, 'grid_after': grid, 'bag_count': 86,
                       'final_turns_remaining': 0, 'mover_score': 12, 'other_score': 10}],
            'my_score': 12, 'opponent_score': 10, 'turn_count': 1,
        }
        self.use_saved_analysis({
            'version': 'quackle-crossplay-v2',
            'move_quality': {
                'state': 'done', 'evaluation': 'win-probability-v1',
                'points': [{'turnIndex': 0, 'turn': 1, 'mine': True, 'action': 'SWAP 0',
                            'category': 'unrated', 'loss': None, 'provisional': False,
                            'retryable': False, 'reason': 'The recorded exchanged tiles are missing.'}],
            },
        }, **review_changes)
        return review_changes

    def test_saved_unrated_exchange_recovers_when_tile_details_are_available(self):
        # This saved grade predates recovery of the exact physical exchanged tiles.
        self.install_review_worker()
        review_changes = self.use_saved_unrated_exchange('AE?')
        saved = []
        self.page.route('**/api/crossplay/games/mockgame01/analysis',
                        lambda route: (saved.append(route.request.post_data_json),
                                       route.fulfill(json={'saved': True})))
        self.page.evaluate("""window.exchangeCalls = []; const request = workerRequest;
          workerRequest = (type, payload, options) => {
            if (options.taskKey.includes(':mockgame01:')) exchangeCalls.push({type, played: payload.played});
            return request(type, payload, options);
          }; startBackgroundAnalysis = window.automaticAnalysis; undefined""")
        self.open_game()
        # Wait for either a repaired result or the old, incorrectly complete state.
        self.page.wait_for_function("""() => {
          const context = currentReview && contextFor(currentReview);
          return context && reviewQueue.running === 0 && context.states.every(state => ['done', 'error'].includes(state));
        }""")
        result = self.page.evaluate("""() => {
          const context = contextFor(currentReview), turn = currentReview.turns[0];
          return {point: context.quality.points[0], states: context.states, calls: exchangeCalls,
            trend: context.trend.points,
            validFullResult: CrossplayReview.validResult(context.cache.get(turn, 'review'), turn, context.cache.seed(turn))};
        }""")
        self.assertIsInstance(result['point'].get('playedWin'), (int, float), result)
        self.assertEqual(result['point']['playedMove']['action'], 'exchange')
        self.assertEqual(result['point']['playedMove']['tiles'], 'AE?')
        self.assertFalse(result['point']['provisional'])
        self.assertEqual(result['point']['samples'], 277)
        self.assertTrue(result['validFullResult'])
        self.assertTrue(result['calls'])
        self.assertTrue(all(call == {'type': 'simulateReview', 'played': {
            'action': 'exchange', 'position': '', 'tiles': 'AE?'}} for call in result['calls']))
        turn_point = next(point for point in result['trend'] if not point.get('actual'))
        self.assertEqual(turn_point['x'], 0)
        self.assertEqual(turn_point['mine'], result['point']['playedWin'] * 100)
        self.page.get_by_role('button', name='Show before move', exact=True).click()
        expect(self.page.locator('.win-estimate-state')).to_have_text('Full estimate')
        expect(self.page.locator('.win-you strong')).to_have_text('50.0%')
        self.page.evaluate('flushAnalysisSave()')
        completed = [snapshot for snapshot in saved if 'move_quality' in snapshot]
        self.assertTrue(completed, 'The repaired grade must be saved')
        self.assertEqual(completed[-1]['move_quality']['points'][0], result['point'])
        stored = {'version': completed[-1]['analysis_version'],
                  'move_quality': completed[-1]['move_quality'],
                  'win_trend': completed[-1]['win_trend'],
                  'turn_analyses': {key: value for snapshot in saved
                                    for key, value in snapshot['turn_analyses'].items()}}
        self.page.unroute('**/api/crossplay/games/mockgame01/review')
        self.use_saved_analysis(stored, **review_changes)
        self.page.evaluate('Promise.all(Array.from(reviewQueue.contexts.values(), context => flushAnalysisSave(context.review, context.cache)))')
        self.context.add_init_script("window.engineCalls=[]; const post=Worker.prototype.postMessage; Worker.prototype.postMessage=function(data,...rest){engineCalls.push(data.type); return post.call(this,data,...rest);};")
        self.page.reload()
        expect(self.page.locator('.game-overview')).to_be_visible()
        self.page.wait_for_function("currentReview?.id === 'mockgame01' && reviewQueue?.running === 0 && contextFor(currentReview)?.states.every(state => ['done', 'error'].includes(state))")
        self.assertEqual(self.page.evaluate('engineCalls'), [])
        self.assertEqual(self.page.evaluate('moveQualityResults.points[0]'), result['point'])
        self.start_review()
        self.page.get_by_role('button', name='Show before move', exact=True).click()
        expect(self.page.locator('.win-estimate-state')).to_have_text('Full estimate')

    def test_swap_placeholder_retries_saves_and_reuses_win_probability(self):
        self.install_review_worker()
        grid = ['.' * 15 for _ in range(15)]
        review_changes = {
            'turns': [{'index': 0, 'mine': True, 'action': 'swap -- 0', 'score': 0,
                       'rack': 'AEIRST?', 'swapped_tiles': 'AE?', 'grid': grid, 'grid_after': grid,
                       'bag_count': 86, 'final_turns_remaining': 0, 'mover_score': 12, 'other_score': 10}],
            'my_score': 12, 'opponent_score': 10, 'turn_count': 1,
        }
        self.use_saved_analysis({
            'version': 'quackle-crossplay-v2',
            'move_quality': {'state': 'done', 'evaluation': 'win-probability-v1', 'points': [{
                'turnIndex': 0, 'turn': 1, 'mine': True, 'action': 'swap -- 0',
                'category': 'unrated', 'loss': None, 'retryable': True, 'provisional': False,
                'reason': 'Quackle could not evaluate the played move: The recorded move could not be read.',
            }]},
        }, **review_changes)
        saved = []
        self.page.route('**/api/crossplay/games/mockgame01/analysis',
                        lambda route: (saved.append(route.request.post_data_json),
                                       route.fulfill(json={'saved': True})))
        self.open_game()
        self.page.evaluate("""async () => {
          window.exchangeCalls = []; let failFirst = true; const request = workerRequest;
          workerRequest = (type, payload, options) => {
            if (options.taskKey.includes(':mockgame01:')) {
              exchangeCalls.push({type, played: payload.played, iterations: payload.iterations});
              if (failFirst) { failFirst = false; return Promise.reject(new Error('Synthetic temporary exchange failure')); }
            }
            return request(type, payload, options);
          };
          await analyzeReviewTurn(contextFor(currentReview), 0, 'preview');
          refreshPositionPanel();
        }""")
        expect(self.page.locator('#engineStatus')).to_contain_text('Synthetic temporary exchange failure')
        self.page.get_by_role('button', name='Retry this turn', exact=True).click()
        self.page.wait_for_function("currentReview?.id === 'mockgame01' && reviewQueue?.running === 0 && contextFor(currentReview)?.states.every(state => ['done', 'error'].includes(state))")
        result = self.page.evaluate("""() => {
          const context = contextFor(currentReview), turn = currentReview.turns[0];
          return {point: context.quality.points[0], calls: exchangeCalls,
            validFullResult: CrossplayReview.validResult(context.cache.get(turn, 'review'), turn, context.cache.seed(turn))};
        }""")
        self.assertTrue(result['validFullResult'], result)
        self.assertEqual(result['point']['playedWin'], 0.5)
        self.assertFalse(result['point']['provisional'])
        self.assertEqual(result['point']['samples'], 277)
        self.assertEqual(result['calls'], [
            {'type': 'simulateReview', 'played': {'action': 'exchange', 'position': '', 'tiles': 'AE?'},
             'iterations': count} for count in (64, 64, 277)])
        self.page.get_by_role('button', name='Show before move', exact=True).click()
        expect(self.page.locator('.win-estimate-state')).to_have_text('Full estimate')
        expect(self.page.locator('.win-you strong')).to_have_text('50.0%')
        expect(self.page.get_by_role('button', name='Retry this turn', exact=True)).to_be_hidden()
        self.page.evaluate('flushAnalysisSave()')
        completed = [snapshot for snapshot in saved if 'move_quality' in snapshot]
        self.assertTrue(completed)
        self.assertEqual(completed[-1]['move_quality']['points'][0]['playedMove']['tiles'], 'AE?')
        self.assertEqual(completed[-1]['move_quality']['points'][0]['playedWin'], 0.5)
        self.assertEqual(completed[-1]['win_trend']['points'][0]['mine'], 50)
        stored = {'version': completed[-1]['analysis_version'],
                  'move_quality': completed[-1]['move_quality'], 'win_trend': completed[-1]['win_trend'],
                  'turn_analyses': {key: value for snapshot in saved for key, value in snapshot['turn_analyses'].items()}}
        # Complete the other synthetic game so a full reload needs no engine calls.
        self.page.evaluate('window.automaticAnalysis()')
        self.page.wait_for_function('reviewQueue.running === 0')
        self.page.evaluate('Promise.all(Array.from(reviewQueue.contexts.values(), context => flushAnalysisSave(context.review, context.cache)))')
        self.page.unroute('**/api/crossplay/games/mockgame01/review')
        self.use_saved_analysis(stored, **review_changes)
        self.context.add_init_script("window.engineCalls=[]; const post=Worker.prototype.postMessage; Worker.prototype.postMessage=function(data,...rest){engineCalls.push(data.type); return post.call(this,data,...rest);};")
        self.page.reload()
        self.page.wait_for_function("currentReview?.id === 'mockgame01' && reviewQueue?.running === 0 && contextFor(currentReview)?.states.every(state => ['done', 'error'].includes(state))")
        self.assertEqual(self.page.evaluate('engineCalls'), [])
        self.assertEqual(self.page.evaluate('moveQualityResults.points[0]'), result['point'])
        self.start_review()
        self.page.get_by_role('button', name='Show before move', exact=True).click()
        expect(self.page.locator('.win-you strong')).to_have_text('50.0%')

    def test_loaded_unrated_exchange_recovers_when_tile_details_arrive(self):
        self.install_review_worker()
        self.use_saved_unrated_exchange(None)
        self.open_game()
        result = self.page.evaluate("""async () => {
          const context = contextFor(currentReview), turn = currentReview.turns[0];
          const before = structuredClone(context.quality.points[0]);
          turn.swapped_tiles = 'AE?';
          const calls = [], request = workerRequest;
          workerRequest = (type, payload, options) => {
            calls.push({type, played: payload.played, iterations: payload.iterations});
            return request(type, payload, options);
          };
          const preview = await analyzeReviewTurn(context, 0, 'preview');
          const validPreview = CrossplayReview.validResult(context.cache.get(turn, 'preview'), turn,
            context.cache.seed(turn), PREVIEW_ITERATIONS, PREVIEW_MOVE_COUNT);
          await analyzeReviewTurn(context, 0, 'review');
          return {before, preview, validPreview, calls, point: context.quality.points[0]};
        }""")
        self.assertEqual(result['before']['category'], 'unrated')
        self.assertEqual(result['preview'], {'complete': False})
        self.assertTrue(result['validPreview'])
        self.assertEqual(result['calls'], [
            {'type': 'simulateReview', 'played': {'action': 'exchange', 'position': '', 'tiles': 'AE?'},
             'iterations': iterations} for iterations in (64, 277)])
        self.assertEqual(result['point']['playedWin'], 0.5)
        self.assertEqual(result['point']['playedMove']['tiles'], 'AE?')
        self.assertFalse(result['point']['provisional'])

    def test_legacy_swap_parse_error_without_tiles_recovers_to_explicit_unrated_state(self):
        grid = ['.' * 15 for _ in range(15)]
        self.use_saved_analysis({
            'version': 'quackle-crossplay-v2',
            'move_quality': {'state': 'done', 'evaluation': 'win-probability-v1', 'points': [{
                'turnIndex': 0, 'turn': 1, 'mine': True, 'action': 'swap -- 0',
                'category': 'unrated', 'loss': None, 'provisional': False, 'retryable': False,
                'reason': 'Quackle could not evaluate the played move: The recorded move could not be read.',
            }]},
        }, turns=[{'index': 0, 'mine': True, 'action': 'swap -- 0', 'score': 0,
                   'rack': 'AEIRST?', 'swapped_tiles': None, 'grid': grid, 'grid_after': grid,
                   'bag_count': 86, 'final_turns_remaining': 0, 'mover_score': 12, 'other_score': 10}],
            my_score=12, opponent_score=10, turn_count=1)
        self.open_game()
        self.page.evaluate("""async () => {
          const context = contextFor(currentReview);
          await analyzeReviewTurn(context, 0, 'preview');
          await finishReviewAnalysis(context);
        }""")
        result = self.page.evaluate("""() => {
          const context = contextFor(currentReview), point = context.quality.points[0];
          const restored = makeReviewContext({...currentReview, stored_analysis: {
            version: ANALYSIS_VERSION, move_quality: context.quality,
            turn_analyses: context.cache.results
          }}, context.owner);
          return {point, states: context.states, error: context.error, errors: [...context.cache.errors],
            hasCandidates: Boolean(context.cache.get(currentReview.turns[0], 'candidates')),
            restoredStates: restored.states};
        }""")
        self.assertIsNotNone(result.get('point'), result)
        self.assertEqual(result['point']['reason'], 'The recorded exchanged tiles are missing.')
        self.assertFalse(result['point']['retryable'])
        self.assertNotIn('playedWin', result['point'])
        self.assertEqual(result['errors'], [])
        self.assertTrue(result['hasCandidates'])
        self.assertEqual(result['restoredStates'], ['done'])
        expect(self.page.locator('#engineStatus')).to_contain_text('recorded exchanged tiles are missing')
        expect(self.page.locator('#engineStatus')).not_to_contain_text('Analysis failed')
        expect(self.page.locator('.analysis-panel .analysis-button')).to_be_hidden()

    def test_saved_exchange_without_tile_details_does_not_guess_a_probability(self):
        self.install_review_worker()
        self.use_saved_unrated_exchange(None)
        self.page.evaluate("""window.exchangeCalls = []; const request = workerRequest;
          workerRequest = (type, payload, options) => {
            if (options.taskKey.includes(':mockgame01:')) exchangeCalls.push({type, played: payload.played});
            return request(type, payload, options);
          }; startBackgroundAnalysis = window.automaticAnalysis; undefined""")
        self.open_game()
        self.page.wait_for_function("currentReview?.id === 'mockgame01' && reviewQueue?.running === 0 && contextFor(currentReview)?.states.every(state => ['done', 'error'].includes(state))")
        result = self.page.evaluate("""({point: moveQualityResults.points[0],
          recordedTrend: trendResults.points.filter(point => !point.actual), calls: exchangeCalls})""")
        self.assertEqual(result['point']['category'], 'unrated')
        self.assertNotIn('playedWin', result['point'])
        self.assertFalse(result['point']['retryable'])
        self.assertEqual(result['recordedTrend'], [])
        self.assertFalse(any(call['type'] == 'simulateReview' for call in result['calls']))
        self.page.get_by_role('button', name='Show before move', exact=True).click()
        expect(self.page.locator('.win-estimate-state')).to_have_text('Estimate unavailable')
        expect(self.page.locator('.win-you strong')).to_have_text('—')
        expect(self.page.locator('.turn-coach')).to_contain_text('recorded exchanged tiles are missing')

    def test_exchange_without_tile_details_still_analyzes_the_position(self):
        self.use_saved_analysis({})
        self.open_game()
        result = self.page.evaluate("""async () => {
          const context = contextFor(currentReview), turn = currentReview.turns[3];
          turn.swapped_tiles = null;
          currentTurnIndex = 3; renderReview();
          const calls = [], request = workerRequest;
          workerRequest = (type, payload, options) => {
            calls.push({type, played: payload.played});
            return request(type, payload, options);
          };
          const outcome = await analyzeReviewTurn(context, 3, 'preview');
          const point = context.quality.points.find(point => point.turnIndex === 3);
          const candidates = context.cache.get(turn, 'candidates');
          const callsAfterSwap = structuredClone(calls);
          await analyzeReviewTurn(context, 4, 'preview');
          const nextPoint = context.quality.points.find(point => point.turnIndex === 4);
          context.quality.state = 'done';
          const callCount = calls.length;
          const reopened = await analyzeReviewTurn(context, 3, 'preview');
          const stored_analysis = {version: ANALYSIS_VERSION, turn_analyses: context.cache.results};
          const restored = makeReviewContext({...currentReview, stored_analysis}, context.owner);
          const invalidSaved = {version: ANALYSIS_VERSION, turn_analyses: {
            [context.cache.key(turn, 'candidates')]: {moves: [null]}
          }};
          const invalid = makeReviewContext({...currentReview, stored_analysis: invalidSaved}, context.owner);
          return {outcome, point, callsAfterSwap, moves: candidates.moves.length,
            errors: [...context.cache.errors.values()], nextWin: nextPoint.playedWin,
            reused: reopened.complete && calls.length === callCount,
            ignoredInvalid: invalid.cache.get(turn, 'candidates') === null,
            restored: JSON.stringify(restored.cache.get(turn, 'candidates')) === JSON.stringify(candidates)};
        }""")
        self.assertEqual(result['outcome'], {'complete': True})
        self.assertEqual(result['point']['category'], 'unrated')
        self.assertIsNone(result['point']['loss'])
        self.assertFalse(result['point']['retryable'])
        self.assertFalse(result['point']['provisional'])
        self.assertNotIn('playedWin', result['point'])
        self.assertEqual([call['type'] for call in result['callsAfterSwap']], ['kibitz'])
        self.assertTrue(all(call.get('played') is None for call in result['callsAfterSwap']))
        self.assertGreater(result['moves'], 0)
        self.assertEqual(result['errors'], [])
        self.assertIsInstance(result['nextWin'], (int, float))
        self.assertTrue(result['reused'])
        self.assertTrue(result['ignoredInvalid'])
        self.assertTrue(result['restored'])
        expect(self.page.locator('.position-output .moves-table tbody tr').first).to_be_visible()
        expect(self.page.locator('.position-output .moves-table tbody tr').first.locator('td').nth(3)).to_have_text('—')
        expect(self.page.locator('#engineStatus')).to_contain_text('Move candidates ready. This turn is unrated')
        expect(self.page.locator('.analysis-panel .analysis-button')).to_be_hidden()
        expect(self.page.locator('.turn-coach')).to_contain_text('recorded exchanged tiles are missing')

    def test_missing_exchange_position_failure_can_be_retried(self):
        self.use_saved_analysis({})
        self.open_game()
        self.page.evaluate("""async () => {
          const context = contextFor(currentReview), request = workerRequest;
          currentReview.turns[3].swapped_tiles = null;
          currentTurnIndex = 3; renderReview();
          workerRequest = () => Promise.reject(new Error('Synthetic position failure'));
          try { await analyzeReviewTurn(context, 3, 'preview'); } catch (_) {}
          workerRequest = request;
          refreshPositionPanel();
        }""")
        expect(self.page.locator('#engineStatus')).to_contain_text('Analysis failed')
        self.page.get_by_role('button', name='Retry this turn', exact=True).click()
        expect(self.page.locator('#engineStatus')).to_contain_text('Move candidates ready. This turn is unrated')
        expect(self.page.locator('.analysis-panel .analysis-button')).to_be_hidden()

    def test_move_quality_refreshes_summary_with_real_engine(self):
        self.open_game()
        self.page.get_by_role('button', name='Last ⇥', exact=True).click()
        self.open_statistics()
        self.page.get_by_role('button', name='Analyze move quality', exact=True).click()
        expect(self.page.locator('.quality-panel .trend-status')).to_contain_text(
            'Your review is ready', timeout=120_000)
        expect(self.page.locator('.quality-point')).to_have_count(3)
        self.assertTrue(self.page.evaluate('moveQualityResults.points.every(point => Number.isFinite(point.playedWin))'))
        expect(self.page.locator('.insights-panel .analysis-note')).to_have_text('3 / 3 turns rated')
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
        self.assertTrue(all(state['keys'] == 22 and state['points'] == 6 for state in states), states)
        self.page.evaluate('Promise.all(Array.from(reviewQueue.contexts.values(), c => flushAnalysisSave(c.review, c.cache)))')
        self.page.get_by_role('button', name='Review game against Demo Cedar').click()
        self.start_review()
        self.page.get_by_role('button', name='Review game against Demo River').click()
        self.start_review()
        expect(self.page.locator('#engineStatus')).to_contain_text('277 simulations per move')
        self.context.add_init_script("window.engineCalls=[]; const post=Worker.prototype.postMessage; Worker.prototype.postMessage=function(data,...rest){engineCalls.push(data.type); return post.call(this,data,...rest);};")
        self.page.goto(self.base_url)
        expect(self.page.locator('#backgroundStatus')).to_contain_text('2/2 games', timeout=15_000)
        self.assertEqual(self.page.evaluate('engineCalls'), [])

    def test_three_worker_budget_runs_three_independent_turns(self):
        self.assert_turns_run_with_worker_budget(cores=8, memory=8, budget=3)

    def test_low_memory_worker_budget_runs_two_independent_turns(self):
        self.assert_turns_run_with_worker_budget(cores=8, memory=4, budget=2)

    def test_two_cores_run_one_turn_at_a_time(self):
        self.assert_turns_run_with_worker_budget(cores=2, memory=8, budget=1)

    def assert_turns_run_with_worker_budget(self, *, cores, memory, budget):
        self.install_review_worker()
        self.page.evaluate('''({cores,memory}) => {
          Object.defineProperty(navigator,'hardwareConcurrency',{value:cores});
          Object.defineProperty(navigator,'deviceMemory',{value:memory});
        }''', {'cores': cores, 'memory': memory})
        self.open_game()
        result = self.page.evaluate('''async budget => {
          const context=contextFor(currentReview);
          const grid=Array(15).fill('.'.repeat(15));
          currentReview.turns=Array.from({length:4},(_,index)=>({
            index, mine:index%2===0, grid, grid_after:grid, rack:'CATERS?', rack_after:'CATERS?',
            action:'PASS 0', score:0, bag_count:86, final_turns_remaining:0,
            mover_score:index, other_score:0
          }));
          context.states=currentReview.turns.map(()=> 'pending');
          context.openingState='unavailable';
          const pending=new Map(), requests=[], workers=new Set(), post=Worker.prototype.postMessage;
          Worker.prototype.postMessage=function(message,...options) {
            if(message.type!=='simulateReview') return post.call(this,message,...options);
            const {playerScore,iterations,numMoves}=message.payload;
            requests.push({index:playerScore,iterations,numMoves});
            workers.add(this);
            pending.set(playerScore,()=>post.call(this,message,...options));
          };
          const release=index=>{
            const finish=pending.get(index);
            pending.delete(index);
            finish();
          };
          const waitFor=async check=>{
            const deadline=performance.now()+2500;
            while(!check() && performance.now()<deadline) await new Promise(resolve=>setTimeout(resolve,5));
            return check();
          };
          reviewQueue.select(currentReview.id);
          const started=await waitFor(()=>pending.size>=budget);
          const initial={active:[...pending.keys()].sort(),workers:workers.size,queue:reviewQueue.running};
          if(!started) return {initial};
          const firstDone=budget-1;
          release(firstDone);
          if(!await waitFor(()=>context.states[firstDone]==='done' && requests.length===budget+1)) {
            throw new Error('A finished turn did not publish or free a worker.');
          }
          const published={
            states:[...context.states],
            points:context.quality.points.map(point=>({index:point.turnIndex,samples:point.samples}))
          };
          if(!await waitFor(()=>{
            for(const index of [...pending.keys()]) release(index);
            return reviewQueue.running===0;
          })) throw new Error('The remaining turns did not finish.');
          await analysisSaveQueue;
          return {initial,published,states:context.states,workers:workers.size,requests,
            summary:context.quality.state};
        }''', budget)
        self.assertEqual(result['initial'], {'active': list(range(budget)), 'workers': budget, 'queue': budget})
        expected_states = ['running'] * (budget - 1) + ['done', 'running'] + ['pending'] * (3 - budget)
        self.assertEqual(result['published'], {
            'states': expected_states, 'points': [{'index': budget - 1, 'samples': 277}],
        })
        self.assertEqual(result['states'], ['done'] * 4)
        self.assertEqual(result['workers'], budget)
        self.assertEqual(sorted(result['requests'], key=lambda request: request['index']),
                         [{'index': index, 'iterations': 277, 'numMoves': 15} for index in range(4)])
        self.assertEqual(result['summary'], 'done')

    def test_retry_turn_updates_the_grade_trend_and_saved_summary(self):
        self.install_review_worker()
        saved = []
        self.page.route('**/api/crossplay/games/mockgame01/analysis',
                        lambda route: (saved.append(route.request.post_data_json), route.fulfill(json={'saved': True})))
        self.open_game()
        self.open_statistics()
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
        expect(self.page.locator('.quality-panel .trend-status')).to_contain_text('Some analysis failed')
        self.page.evaluate('flushAnalysisSave()')
        self.page.wait_for_function('reviewQueue.running === 0')
        self.page.get_by_role('combobox', name='Select game turn').select_option('0')
        expect(self.page.locator('.turn-coach')).to_contain_text('This turn is unrated')
        self.page.evaluate('failRecordedMove = false')
        self.page.get_by_role('button', name='Retry this turn', exact=True).click()
        expect(self.page.locator('.turn-coach')).to_contain_text('Blunder · 20.0 percentage points lost')
        self.page.evaluate('flushAnalysisSave()')
        self.page.wait_for_function('reviewQueue.running === 0')
        self.assertEqual(self.page.evaluate('retryCalls'), 4)
        completed = [value for value in saved if 'move_quality' in value]
        self.assertEqual(completed[-1]['move_quality']['points'][0]['category'], 'blunder')
        self.assertEqual(len(completed[-1]['win_trend']['points']), 11)

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
        self.open_statistics()
        self.page.evaluate("""window.failRetry = true; const request = workerRequest;
          workerRequest = (type,payload,options) => {
            if (type === 'simulateReview' && options.taskKey.includes(':mockgame01:') && payload.rack === 'ACERSTU') {
              if (failRetry) return Promise.reject(new Error('Temporary engine failure.'));
              if (payload.iterations === 64) return new Promise(resolve => { window.releaseTurnRetry = () => resolve(request(type,payload,options)); });
            }
            return request(type,payload,options);
          }; undefined""")
        self.page.get_by_role('button', name='Analyze move quality', exact=True).click()
        expect(self.page.locator('.quality-panel .trend-status')).to_contain_text('Some analysis failed')
        self.page.evaluate('flushAnalysisSave()')
        self.page.wait_for_function('reviewQueue.running === 0')
        self.page.get_by_role('combobox', name='Select game turn').select_option('0')
        self.page.evaluate('failRetry = false')
        self.page.get_by_role('button', name='Retry this turn', exact=True).click()
        self.page.get_by_role('button', name='Review game against Demo River').click()
        self.start_review()
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
        self.open_statistics()
        self.page.get_by_role('button', name='Analyze move quality', exact=True).click()
        expect(self.page.locator('.quality-panel .trend-status')).to_contain_text('Your review is ready')
        self.page.evaluate('flushAnalysisSave()')
        self.page.wait_for_function('reviewQueue.running === 0')
        stored = self.page.evaluate("({version: ANALYSIS_VERSION, turn_analyses: positionCache.results, move_quality: moveQualityResults})")
        stored['turn_analyses'].pop(next(key for key in stored['turn_analyses']
                                         if json.loads(key)[:2] == ['quackle-review-v1', 'review']))
        self.use_saved_analysis(stored)
        self.page.evaluate("window.resumeCalls=[]; const request=workerRequest; workerRequest=(type,payload,options)=>{resumeCalls.push({type,key:options.taskKey}); return request(type,payload,options);}; startBackgroundAnalysis=window.automaticAnalysis; undefined")
        self.page.get_by_role('button', name='Review game against Demo Cedar').click()
        self.start_review()
        expect(self.page.locator('.quality-panel .trend-status')).to_contain_text('Your review is ready')
        self.assertEqual(self.page.evaluate("resumeCalls.filter(call => call.key.includes(':mockgame01:')).map(call => call.type)"), ['simulateReview'])
        self.assertEqual(self.page.evaluate('Object.keys(positionCache.results).length'), 22)

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
        identities = self.page.evaluate('Object.keys(positionCache.results).map(key => JSON.parse(key))')
        profiles = [(identity[0], identity[3] if identity[0] == 'quackle-opening-draw-v1' else identity[1])
                    for identity in identities]
        self.assertEqual({profile: profiles.count(profile) for profile in set(profiles)}, {
            ('quackle-review-v1', 'preview'): 6, ('quackle-review-v1', 'review'): 6,
            ('quackle-rack-draw-v1', 'draw-preview'): 4, ('quackle-rack-draw-v1', 'draw'): 4,
            ('quackle-opening-draw-v1', 64): 1, ('quackle-opening-draw-v1', 277): 1,
        })
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
        self.start_review()
        expect(self.page.locator('#engineStatus')).to_contain_text('277 simulations per move')
        self.assertEqual(self.page.evaluate('positionCache.results'), results)
        self.page.evaluate('flushAnalysisSave()')
        self.assertEqual(len(saved), save_count)
        self.page.reload()
        self.start_review()
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
        self.enter_custom_move('CAT', 'G8')
        expect(self.page.locator('.custom-move-status')).to_have_text('Move evaluated.', timeout=30_000)
        rank = self.page.locator('.custom-move-result .custom-move-help').first.inner_text()
        self.assertGreater(int(rank.split('#')[1].split()[0]), 100)
        self.page.get_by_role('button', name='Estimate win% (slower)', exact=True).click()
        expect(self.page.get_by_role('button', name='Evaluate move', exact=True)).to_be_disabled()
        self.page.get_by_role('button', name='Cancel evaluation', exact=True).click()
        expect(self.page.locator('.custom-move-status')).to_have_text('Evaluation cancelled.')
        expect(self.page.get_by_role('button', name='Evaluate move', exact=True)).to_be_enabled()
        # Retrying starts a fresh worker and includes a move far outside the shortlist.
        self.page.get_by_role('button', name='Estimate win% (slower)', exact=True).click()
        expect(self.page.locator('.custom-move-status')).to_have_text('Win simulation complete.', timeout=120_000)
        expect(self.page.locator('.custom-move-result')).to_contain_text('277 samples')
        expect(self.page.locator('.custom-move-result')).to_contain_text('Sim. equity')
        expect(self.page.locator('.custom-move-result tbody tr').first).to_contain_text('8G CAT')
        self.assertRegex(self.page.locator('.custom-move-result tbody tr').first.locator('td').last.inner_text(), r'^\d+\.\d%$')
        compared = re.search(r'(\d+) moves ·', self.page.locator('.custom-move-result').inner_text())
        self.assertIsNotNone(compared)
        self.assertLessEqual(int(compared.group(1)), 16)
        self.page.get_by_role('combobox', name='Select game turn').select_option('1')
        expect(self.page.locator('.custom-move-result tbody tr')).to_have_count(0)

    def test_custom_swap_static_validation_and_action_reset(self):
        self.open_game()
        action = self.page.get_by_role('combobox', name='Custom move action', exact=True)
        action.select_option('exchange')
        tiles = self.page.get_by_role('textbox', name='Tiles to swap', exact=True)
        expect(self.page.get_by_role('textbox', name='Custom move word', exact=True)).to_be_hidden()
        for letters, message in [('ZZ', 'rack does not contain'), ('CC', 'rack does not contain'), ('C!', '1 to 7 letters')]:
            with self.subTest(letters=letters):
                tiles.fill(letters)
                self.page.get_by_role('button', name='Evaluate move', exact=True).click()
                expect(self.page.locator('.custom-move-status')).to_contain_text(message, timeout=30_000)
                expect(self.page.locator('.custom-move-result tbody tr')).to_have_count(0)
        tiles.fill('TC')
        self.page.get_by_role('button', name='Evaluate move', exact=True).click()
        expect(self.page.locator('.custom-move-status')).to_have_text('Move evaluated.', timeout=30_000)
        row = self.page.locator('.custom-move-result tbody tr').first
        expect(row).to_contain_text('Your move: Swap CT')
        expect(row.locator('td').nth(1)).to_have_text('0')
        expect(row.locator('td').nth(2)).to_have_text('AERSU')
        expect(self.page.locator('.board .tile')).to_have_count(3)
        action.select_option('place')
        expect(tiles).to_be_hidden()
        expect(self.page.locator('.custom-move-result tbody tr')).to_have_count(0)
        self.enter_custom_move('CAT', 'G8')
        expect(self.page.locator('.custom-move-status')).to_have_text('Move evaluated.', timeout=30_000)
        expect(self.page.locator('.custom-move-result tbody tr').first).to_contain_text('Your move: 8G CAT')
        self.page.get_by_role('combobox', name='Select game turn').select_option('2')
        self.page.get_by_role('combobox', name='Custom move action', exact=True).select_option('exchange')
        self.page.get_by_role('textbox', name='Tiles to swap', exact=True).fill('?')
        self.page.get_by_role('button', name='Evaluate move', exact=True).click()
        expect(self.page.locator('.custom-move-status')).to_have_text('Move evaluated.', timeout=30_000)
        expect(self.page.locator('.custom-move-result tbody tr').first).to_contain_text('Your move: Swap ?')

    def test_custom_swap_win_simulation_and_cancellation(self):
        self.open_game()
        self.page.get_by_role('combobox', name='Custom move action', exact=True).select_option('exchange')
        self.page.get_by_role('textbox', name='Tiles to swap', exact=True).fill('TC')
        self.page.get_by_role('button', name='Evaluate move', exact=True).click()
        expect(self.page.locator('.custom-move-status')).to_have_text('Move evaluated.', timeout=30_000)
        self.page.get_by_role('button', name='Estimate win% (slower)', exact=True).click()
        self.page.get_by_role('button', name='Cancel evaluation', exact=True).click()
        expect(self.page.locator('.custom-move-status')).to_have_text('Evaluation cancelled.')
        self.page.get_by_role('button', name='Estimate win% (slower)', exact=True).click()
        expect(self.page.locator('.custom-move-status')).to_have_text('Win simulation complete.', timeout=120_000)
        row = self.page.locator('.custom-move-result tbody tr').first
        expect(row).to_contain_text('Your move: Swap CT')
        self.assertRegex(row.locator('td').last.inner_text(), r'^\d+\.\d%$')
        expect(self.page.locator('.custom-move-result')).to_contain_text('277 samples')
        expect(self.page.locator('.custom-move-result')).to_contain_text('WP loss')
        compared = re.search(r'(\d+) moves ·', self.page.locator('.custom-move-result').inner_text())
        self.assertIsNotNone(compared)
        self.assertLessEqual(int(compared.group(1)), 16)
        self.assertLessEqual(self.page.locator('.custom-move-result').evaluate('el => el.scrollWidth'),
                             self.page.locator('.custom-move-result').evaluate('el => el.clientWidth') + 1)

    def test_custom_simulation_best_row_matches_wp_loss(self):
        self.open_game()
        result = self.page.evaluate("""async () => {
          const result = await customMoveController.request('simulateMove', {
            gridJson: JSON.stringify(Array(15).fill('.'.repeat(15))), rack: 'AEQRSTU',
            playerScore: 100, opponentScore: 52, bagCount: 86, finalTurnsRemaining: 0,
            iterations: 10, customMove: {action: 'place', word: 'TEA', square: 'G8', direction: 'across'}
          });
          customMoveController.renderResult(result);
          return result;
        }""")
        self.assertEqual(result['best']['win'], max(move['win'] for move in result['moves']))
        rows = self.page.locator('.custom-move-result tbody tr')
        expect(rows.nth(1)).to_contain_text('Best analyzed:')
        expect(rows.nth(1).locator('td').last).to_have_text(f"{100 * result['best']['win']:.1f}%")
        loss = max(0, result['best']['win'] - result['selected']['win']) * 100
        expect(self.page.locator('.custom-move-result')).to_contain_text(
            f'WP loss: {loss:.1f} pp')

    def set_terminal_custom_turn(self):
        return self.page.evaluate("""() => {
          const rack = 'WEAFTUO';
          // Reuse the existing conserved engine regression board, with six
          // board tiles removed so the bag has six tiles.
          const grid = [
            '...............', '...........UN.Y', '.......L...TAME',
            '......YEAH...E.', '.........ab.yA.', '......LOB.OWED.',
            '..PROG..I.TEN.F', '....D..JOGS...I', '...LASSI.O..RUN',
            '.......V.R.MI..', '...C...E..HIPS.', '...A.BASE.A.E..',
            '...R.O..D.V....', '..RENT.LITER...', '.....H..T......'];
          const turn = {...currentReview.turns[0], grid, grid_after: grid, rack, rack_after: rack,
            action: 'PASS', mover_score: 30, other_score: 25, bag_count: 6, final_turns_remaining: 0};
          currentReview.turns[0] = turn;
          selectTurn(0);
        }""")

    def enter_terminal_custom_swap(self):
        self.page.get_by_role('combobox', name='Custom move action', exact=True).select_option('exchange')
        self.page.get_by_role('textbox', name='Tiles to swap', exact=True).fill('WEAF')
        self.page.get_by_role('button', name='Evaluate move', exact=True).click()
        expect(self.page.locator('.custom-move-status')).to_have_text('Move evaluated.', timeout=30_000)

    def test_custom_swap_with_six_bag_tiles_has_static_and_simulated_results(self):
        self.open_game()
        self.set_terminal_custom_turn()
        trace_quackle_workers(self.page)
        self.page.evaluate("""async () => {
          Object.defineProperty(navigator, 'hardwareConcurrency', {value: 2});
          await workerRequest('init');
        }""")
        self.enter_terminal_custom_swap()
        row = self.page.locator('.custom-move-result tbody tr').first
        expect(row).to_contain_text('Your move: Swap AEFW')
        expect(row.locator('td').nth(2)).to_have_text('TUO')
        self.page.get_by_role('button', name='Estimate win% (slower)', exact=True).click()
        self.page.wait_for_function("workerTrace.requests.some(r => ['planBagEmptying', 'simulateMove'].includes(r.type))")
        self.assertEqual(self.page.evaluate("workerTrace.requests.find(r => ['planBagEmptying', 'simulateMove'].includes(r.type)).type"),
                         'planBagEmptying')
        self.page.wait_for_function("""() => {
          const status = document.querySelector('.custom-move-status');
          return status.textContent === 'Win simulation complete.' || status.classList.contains('custom-move-error');
        }""", timeout=120_000)
        expect(self.page.locator('.custom-move-status')).to_have_text('Win simulation complete.')
        expect(row).to_contain_text('Your move: Swap AEFW')
        self.assertRegex(row.locator('td').last.inner_text(), r'^\d+\.\d%$')
        samples = self.page.evaluate('CrossplayBagEmptying.fullIterations(6)')
        expect(self.page.locator('.custom-move-result')).to_contain_text(f'{samples} samples')
        trace = self.page.evaluate('workerTrace')
        self.assertEqual(trace['count'], 1)
        plan = next(request for request in trace['requests'] if request['type'] == 'planBagEmptying')
        self.assertEqual(plan['payload']['iterations'], samples)
        self.assertTrue(any(r['type'] == 'simulateReviewBatch' for r in trace['requests']))
        self.assertNotIn('simulateMove', [r['type'] for r in trace['requests']])

    def check_custom_terminal_cancellation(self, hold_custom):
        self.open_game()
        self.set_terminal_custom_turn()
        trace_quackle_workers(self.page)
        self.page.evaluate("""holdCustom => {
          Object.defineProperty(navigator, 'hardwareConcurrency', {value: 2, configurable: true});
          workerTrace.hold = ({type, payload}) => holdCustom
            ? (type === 'simulateMove' || (type === 'simulateReviewBatch' && payload.customMove))
            : (type === 'simulateReviewBatch' && !payload.customMove);
          window.startPendingReview = () => {
            const turn = currentReview.turns[0];
            window.pendingReview = ensurePositionAnalysis(currentReview, turn, 'preview').then(
              result => ({samples: result.selected.terminalSamples}), error => ({error: error.message}));
          };
        }""", hold_custom)
        self.enter_terminal_custom_swap()
        estimate = self.page.get_by_role('button', name='Estimate win% (slower)', exact=True)
        if hold_custom:
            estimate.click()
            self.page.wait_for_function('Boolean(workerTrace.release)')
            self.page.evaluate('startPendingReview()')
        else:
            self.page.evaluate('startPendingReview()')
            self.page.wait_for_function('Boolean(workerTrace.release)')
            estimate.click()
        self.page.get_by_role('button', name='Cancel evaluation').click()
        expect(self.page.locator('.custom-move-status')).to_have_text('Evaluation cancelled.')
        self.page.evaluate('workerTrace.release()')
        self.assertEqual(self.page.evaluate('pendingReview'), {'samples': 64})
        expect(self.page.locator('.custom-move-result')).to_contain_text('Static equity')
        trace = self.page.evaluate('workerTrace')
        self.assertEqual(trace['count'], 1)
        self.assertEqual(trace['terminations'], 0)
        self.assertNotIn('simulateMove', [r['type'] for r in trace['requests']])
        custom_batches = [r for r in trace['requests'] if r['type'] == 'simulateReviewBatch'
                          and r['payload'].get('customMove')]
        self.assertEqual(len(custom_batches), int(hold_custom))

    def test_cancel_running_custom_batch_keeps_pending_review(self):
        self.check_custom_terminal_cancellation(hold_custom=True)

    def test_cancel_queued_custom_plan_keeps_running_review(self):
        self.check_custom_terminal_cancellation(hold_custom=False)

    def test_custom_swap_rejects_empty_bag_before_engine_evaluation(self):
        self.open_game()
        response = self.page.evaluate("""async () => {
          const grid = [
            'CD........G....', '.......A.NOUN.Y', '.......L...TAME',
            '......YEAH...E.', '.........ab.yA.', '......LOB.OWED.',
            '..PROG..I.TEN.F', '....D..JOGS...I', '...LASSI.O..RUN',
            '.......V.R.MI..', '...C...E..HIPS.', '...A.BASE.A.E..',
            '...R.O..D.V....', '..RENT.LITER...', '.....H..T......'];
          const worker = new Worker('quackle-worker.js');
          return await new Promise(resolve => {
            worker.onmessage = event => { worker.terminate(); resolve(event.data); };
            worker.postMessage({ id: 1, type: 'evaluateMove', payload: {
              gridJson: JSON.stringify(grid), rack: 'WEAFTUO', playerScore: 0,
              opponentScore: 0, bagCount: 0, finalTurnsRemaining: 1,
              customMove: { action: 'exchange', tiles: 'W' }
            }});
          });
        }""")
        self.assertIn('bag', response.get('error', ''))

    def test_frequency_failure_preserves_move_quality_and_can_be_retried(self):
        self.page.route('**/wordfreq-en-25000-log.json',
                        lambda route: route.fulfill(status=503, body='Unavailable'))
        self.open_game()
        self.open_statistics()
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
        self.open_statistics()
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
