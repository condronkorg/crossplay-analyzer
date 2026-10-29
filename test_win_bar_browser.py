#!/usr/bin/env python3
"""Synthetic browser checks for the selected-turn win chance bar."""

import unittest

from playwright.sync_api import expect

import test_review_browser as review_browser


class WinBarBrowserTest(unittest.TestCase):
    setUpClass = classmethod(review_browser.ReviewBrowserTest.setUpClass.__func__)
    setUp = review_browser.ReviewBrowserTest.setUp
    tearDown = review_browser.ReviewBrowserTest.tearDown
    route_request = review_browser.ReviewBrowserTest.route_request
    connect = review_browser.ReviewBrowserTest.connect
    open_game = review_browser.ReviewBrowserTest.open_game

    def set_points(self, points):
        self.page.evaluate('''points => {
          moveQualityResults = {state: 'running', points};
          trendResults = deriveWinTrend({review: currentReview, quality: moveQualityResults});
          renderReview();
        }''', points)

    def point(self, index, probability, provisional=False):
        return {
            'turnIndex': index, 'turn': index + 1, 'mine': index % 2 == 0,
            'playedWin': probability, 'bestWin': probability + 0.01,
            'loss': 1, 'category': 'best', 'provisional': provisional,
            'samples': 64 if provisional else 277,
        }

    def test_bar_is_visible_by_default_and_follows_both_players(self):
        self.open_game()
        panel = self.page.locator('.trend-panel')
        expect(panel).to_be_visible()
        self.assertFalse(panel.evaluate("node => Boolean(node.closest('details'))"))
        expect(panel.locator('.win-estimate-state')).to_have_text('Final result')
        self.set_points([self.point(0, 0.75), self.point(1, 0.8)])
        turns = self.page.get_by_role('combobox', name='Select game turn')
        turns.select_option('0')
        expect(panel.locator('.win-you strong')).to_have_text('75.0%')
        expect(panel.locator('.win-opponent strong')).to_have_text('25.0%')
        expect(panel.locator('.win-turn-label')).to_contain_text('Turn 1 of 6')
        self.page.get_by_role('button', name='Next →').click()
        expect(panel.locator('.win-you strong')).to_have_text('20.0%')
        expect(panel.locator('.win-opponent strong')).to_have_text('80.0%')
        expect(panel.locator('.win-turn-label')).to_contain_text('Turn 2 of 6')
        proportions = panel.evaluate('''node => {
          const bar = node.querySelector('.win-bar');
          return node.querySelector('.win-bar-white').getBoundingClientRect().height / bar.clientHeight;
        }''')
        self.assertAlmostEqual(proportions, 0.2, places=2)
        expect(panel.get_by_role('img')).to_have_attribute('aria-label',
            'Full estimate. Turn 2 of 6 · Recorded move. You: 20.0%. Demo Cedar: 80.0%.')

    def test_missing_turn_has_no_fabricated_probability(self):
        self.open_game()
        self.set_points([self.point(0, 0.75)])
        self.page.get_by_role('combobox', name='Select game turn').select_option('2')
        panel = self.page.locator('.trend-panel')
        expect(panel.locator('.win-estimate-state')).to_have_text('Waiting for estimate')
        expect(panel.locator('.win-bar-white')).to_have_count(0)
        expect(panel.locator('.win-you strong')).to_have_text('—')
        expect(panel.locator('.win-opponent strong')).to_have_text('—')
        self.page.get_by_role('button', name='Last ⇥').click()
        expect(panel.locator('.win-estimate-state')).to_have_text('Final result')
        self.page.get_by_role('button', name='Show before move').click()
        expect(panel.locator('.win-estimate-state')).to_have_text('Waiting for estimate')
        self.set_points([{'turnIndex': 5, 'category': 'unrated', 'reason': 'Missing rack'}])
        expect(panel.locator('.win-estimate-state')).to_have_text('Estimate unavailable')
        expect(panel.locator('.win-bar-white')).to_have_count(0)

    def test_bar_updates_when_early_and_full_results_arrive(self):
        self.open_game()
        self.page.get_by_role('combobox', name='Select game turn').select_option('0')
        self.set_points([self.point(0, 0.625, provisional=True)])
        panel = self.page.locator('.trend-panel')
        expect(panel.locator('.win-estimate-state')).to_have_text('Early estimate')
        expect(panel.locator('.win-you strong')).to_have_text('62.5%')
        expect(self.page.locator('.turn-coach')).to_contain_text('64 samples per move')
        expect(self.page.locator('.quality-panel .estimate-level')).to_contain_text('Includes early estimates')
        self.page.evaluate('''() => {
          moveQualityResults.points[0] = {...moveQualityResults.points[0], playedWin: 0.7, provisional: false, samples: 277};
          trendResults = deriveWinTrend({review: currentReview, quality: moveQualityResults});
          refreshTrendPanel();
          refreshMoveQualityPanel();
        }''')
        expect(panel.locator('.win-estimate-state')).to_have_text('Full estimate')
        expect(panel.locator('.win-you strong')).to_have_text('70.0%')
        expect(self.page.locator('.turn-coach')).to_contain_text('277 samples per move')
        expect(self.page.locator('.quality-panel .estimate-level')).to_have_count(0)

    def test_mobile_bar_fits_and_preserves_turn_navigation(self):
        self.page.set_viewport_size({'width': 390, 'height': 844})
        self.open_game()
        self.set_points([self.point(0, 0.75), self.point(1, 0.8)])
        self.page.get_by_role('combobox', name='Select game turn').select_option('0')
        panel = self.page.locator('.trend-panel')
        dimensions = panel.evaluate('''node => {
          const rect = node.getBoundingClientRect();
          return {left: rect.left, right: rect.right, width: window.innerWidth,
            scroll: node.scrollWidth, client: node.clientWidth};
        }''')
        self.assertGreaterEqual(dimensions['left'], 0)
        self.assertLessEqual(dimensions['right'], dimensions['width'])
        self.assertEqual(dimensions['scroll'], dimensions['client'])
        self.page.get_by_role('button', name='Next →').click()
        expect(panel.locator('.win-you strong')).to_have_text('20.0%')
        expect(self.page.locator('.turn-action')).to_contain_text('Turn 2')

    def test_position_details_use_early_results_then_prefer_full_results(self):
        self.open_game()
        self.page.get_by_role('combobox', name='Select game turn').select_option('0')
        self.page.evaluate('''() => {
          const turn = currentReview.turns[0];
          positionCache.results[positionCache.key(turn, 'preview')] = {
            simulated: true, provisional: true, iterationsCompleted: 64,
            moves: [{position: '8G', tiles: 'CAT', score: 5, leave: 'AEIL', win: .625, equity: 3.3}]
          };
          refreshPositionPanel();
        }''')
        expect(self.page.locator('#engineStatus')).to_contain_text('Early estimate · 64 simulations per move')
        expect(self.page.locator('.position-output')).to_contain_text('64 samples per move')
        expect(self.page.locator('.moves-table')).to_contain_text('62.5%')
        self.page.evaluate('''() => {
          positionCache.errors.set(positionCache.key(currentReview.turns[0], 'review'), 'Engine unavailable');
          refreshPositionPanel();
        }''')
        expect(self.page.locator('#engineStatus')).to_contain_text('Full estimate failed: Engine unavailable')
        expect(self.page.get_by_role('button', name='Retry this turn')).to_be_visible()
        self.page.evaluate('''() => {
          const turn = currentReview.turns[0];
          const early = positionCache.get(turn, 'preview');
          positionCache.results[positionCache.key(turn, 'review')] = {
            ...early, provisional: false, iterationsCompleted: 277,
            moves: [{...early.moves[0], win: .7}]
          };
          refreshPositionPanel();
        }''')
        expect(self.page.locator('#engineStatus')).to_have_text('Full estimate · 277 simulations per move.')
        expect(self.page.locator('.position-output')).to_contain_text('277 samples per move')
        expect(self.page.locator('.moves-table')).to_contain_text('70.0%')
        expect(self.page.get_by_role('button', name='Retry this turn')).to_be_hidden()


if __name__ == '__main__':
    unittest.main()
