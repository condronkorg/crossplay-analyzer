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
    use_saved_analysis = review_browser.ReviewBrowserTest.use_saved_analysis

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

    def test_review_layout_puts_board_before_secondary_statistics(self):
        self.use_saved_analysis()
        self.open_game()
        self.page.get_by_role('combobox', name='Select game turn').select_option('0')
        statistics = self.page.locator('.review-statistics')
        expect(statistics).to_be_visible()
        expect(statistics).not_to_have_attribute('open', '')
        expect(self.page.locator('.dashboard-stats')).to_be_hidden()
        expect(self.page.locator('.trend-settings .trend-controls')).to_be_hidden()
        for width, max_board_offset in [(1440, 400), (390, 500)]:
            with self.subTest(width=width):
                self.page.set_viewport_size({'width': width, 'height': 900})
                geometry = self.page.locator('#reviewPanel').evaluate('''root => {
                  const rect = selector => root.querySelector(selector).getBoundingClientRect();
                  const board = rect('.turn-panel'), statistics = rect('.review-statistics');
                  const chart = rect('.trend-panel');
                  return {boardOffset: board.top - root.getBoundingClientRect().top,
                    boardBottom: board.bottom, statisticsTop: statistics.top,
                    chartBottom: chart.bottom, chartHeight: chart.height,
                    fits: document.documentElement.scrollWidth <= innerWidth};
                }''')
                self.assertLessEqual(geometry['boardOffset'], max_board_offset)
                self.assertGreaterEqual(geometry['statisticsTop'], geometry['boardBottom'])
                self.assertGreaterEqual(geometry['statisticsTop'], geometry['chartBottom'])
                self.assertLessEqual(geometry['chartHeight'], 500)
                self.assertTrue(geometry['fits'], 'The page must not scroll horizontally')
                expect(self.page.locator('.review-body .turn-controls')).to_be_visible()
                expect(self.page.locator('.review-body .turn-coach')).to_be_visible()
                expect(self.page.locator('.review-body .move-comparison')).to_be_visible()
                expect(self.page.locator('.review-body .win-history')).to_be_visible()
                expect(self.page.locator('.review-body .win-bar')).to_be_visible()

    def test_disclosure_state_survives_turn_selection_and_background_refresh(self):
        self.open_game()
        statistics = self.page.locator('.review-statistics')
        settings = self.page.locator('.trend-settings')
        statistics.locator('> summary').click()
        self.page.locator('.luck-breakdown > summary').click()
        settings.locator('> summary').click()
        expect(settings.get_by_role('button', name='Start analysis', exact=True)).to_be_visible()
        self.page.get_by_role('combobox', name='Select game turn').select_option('0')
        for disclosure in [statistics, settings, self.page.locator('.luck-breakdown')]:
            expect(disclosure).to_have_attribute('open', '')
        self.page.locator('.score-point').nth(1).focus()
        self.page.keyboard.press('Enter')
        expect(self.page.get_by_role('combobox', name='Select game turn')).to_have_value('1')
        expect(self.page.locator('.score-point').nth(1)).to_be_focused()
        self.page.evaluate('refreshTrendPanel(); refreshMoveQualityPanel()')
        for disclosure in [statistics, settings, self.page.locator('.luck-breakdown')]:
            expect(disclosure).to_have_attribute('open', '')
        statistics.locator('> summary').click()
        settings.locator('> summary').click()
        self.page.evaluate('refreshTrendPanel(); refreshMoveQualityPanel()')
        self.page.get_by_role('button', name='Next →', exact=True).click()
        for disclosure in [statistics, settings]:
            expect(disclosure).not_to_have_attribute('open', '')
        expect(self.page.locator('.dashboard-stats')).to_be_hidden()
        expect(self.page.locator('.win-history')).to_be_visible()

    def test_background_refresh_preserves_review_control_focus(self):
        self.use_saved_analysis()
        self.open_game()
        self.page.get_by_role('combobox', name='Select game turn').select_option('0')
        self.page.locator('.trend-settings > summary').click()
        for selector, refresh in [
                ('.trend-settings > summary', 'refreshTrendPanel()'),
                ('.trend-settings .analysis-button', 'refreshTrendPanel()'),
                ('.coach-details > summary', 'refreshMoveQualityPanel()'),
                ('.review-lesson button', 'refreshMoveQualityPanel()')]:
            with self.subTest(control=selector):
                control = self.page.locator(selector)
                control.focus()
                expect(control).to_be_focused()
                self.page.evaluate(refresh)
                self.assertTrue(control.evaluate('node => node === document.activeElement'),
                                'Background refresh must preserve focus on ' + selector)

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

    def test_best_move_preview_uses_alternative_odds_for_both_players(self):
        self.open_game()
        points = [self.point(0, 0.75), self.point(1, 0.8)]
        points[0].update(bestWin=0.9, bestMove={
            'position': '8G', 'tiles': 'ACT', 'word': 'ACT', 'score': 5,
        })
        points[1].update(bestWin=0.95, bestMove={
            'position': 'H8', 'tiles': '.RE', 'word': 'ARE', 'score': 4,
        })
        self.set_points(points)
        panel = self.page.locator('.trend-panel')
        turns = self.page.get_by_role('combobox', name='Select game turn')
        cases = [('0', '75.0%', '90.0%', '10.0%'), ('1', '20.0%', '5.0%', '95.0%')]
        for index, recorded, best, opponent in cases:
            with self.subTest(turn=index):
                turns.select_option(index)
                expect(panel.locator('.win-you strong')).to_have_text(recorded)
                self.page.get_by_role('button', name='Preview best move', exact=True).click()
                expect(panel.locator('.win-you strong')).to_have_text(best)
                expect(panel.locator('.win-opponent strong')).to_have_text(opponent)
                expect(panel.locator('.win-turn-label')).to_contain_text('Best move preview')
                expect(panel.get_by_role('img')).to_have_attribute('aria-label',
                    f'Full estimate. Turn {int(index) + 1} of 6 · Best move preview. '
                    f'You: {best}. Demo Cedar: {opponent}.')
                expect(panel.locator(f'.win-history-point[data-turn-index="{index}"]')).to_have_attribute(
                    'aria-label', f'Turn {int(index) + 1}. You: {recorded}. Full estimate. Select to review.')
                proportion = panel.evaluate('''node => {
                  const bar = node.querySelector('.win-bar');
                  return node.querySelector('.win-bar-white').getBoundingClientRect().height / bar.clientHeight;
                }''')
                self.assertAlmostEqual(proportion, float(best.rstrip('%')) / 100, places=2)
                self.page.get_by_role('button', name='Return to played move', exact=True).click()
                expect(panel.locator('.win-you strong')).to_have_text(recorded)
                expect(panel.locator('.win-turn-label')).to_contain_text('Recorded move')

    def test_preview_refreshes_and_never_falls_back_to_recorded_odds(self):
        self.open_game()
        point = self.point(0, 0.625, provisional=True)
        point.update(bestWin=0.8, bestMove={
            'position': '8G', 'tiles': 'ACT', 'word': 'ACT', 'score': 5,
        })
        self.set_points([point])
        self.page.get_by_role('combobox', name='Select game turn').select_option('0')
        self.page.get_by_role('button', name='Preview best move', exact=True).click()
        panel = self.page.locator('.trend-panel')
        expect(panel.locator('.win-estimate-state')).to_have_text('Early estimate')
        expect(panel.locator('.win-you strong')).to_have_text('80.0%')
        self.page.evaluate('''() => {
          Object.assign(moveQualityResults.points[0], {
            bestWin: 0.9, provisional: false, samples: 277,
            bestMove: {position: '8G', tiles: 'AT', word: 'AT', score: 2}
          });
          refreshMoveQualityPanel();
          refreshTrendPanel();
        }''')
        expect(panel.locator('.win-estimate-state')).to_have_text('Full estimate')
        expect(panel.locator('.win-you strong')).to_have_text('90.0%')
        expect(self.page.locator('.board-preview-note')).to_contain_text('AT at 8G')
        expect(self.page.locator('.board .tile')).to_have_count(2)
        self.page.evaluate('''() => {
          delete moveQualityResults.points[0].bestWin;
          refreshTrendPanel();
        }''')
        expect(panel.locator('.win-estimate-state')).to_have_text('Estimate unavailable')
        expect(panel.locator('.win-turn-label')).to_contain_text('Best move preview')
        expect(panel.locator('.win-you strong')).to_have_text('—')
        expect(panel.locator('.win-opponent strong')).to_have_text('—')
        expect(panel.locator('.win-bar-white')).to_have_count(0)
        self.page.get_by_role('button', name='Return to played move', exact=True).click()
        expect(panel.locator('.win-you strong')).to_have_text('62.5%')

    def test_last_turn_preview_does_not_inherit_the_final_result(self):
        self.open_game()
        point = self.point(5, 0.8)
        point.update(bestWin=0.9, bestMove={
            'position': '8F', 'tiles': '....S', 'word': 'SCATS', 'score': 6,
        })
        self.set_points([point])
        panel = self.page.locator('.trend-panel')
        for my_score, final_chance in [(20, '100.0%'), (14, '50.0%'), (10, '0.0%')]:
            with self.subTest(my_score=my_score):
                self.page.evaluate('''score => {
                  currentReview.my_score = score;
                  currentReview.opponent_score = 14;
                  renderReview();
                }''', my_score)
                expect(panel.locator('.win-estimate-state')).to_have_text('Final result')
                expect(panel.locator('.win-you strong')).to_have_text(final_chance)
                self.page.get_by_role('button', name='Preview best move', exact=True).click()
                expect(self.page.locator('.board-preview-note')).to_be_visible()
                expect(panel.locator('.win-estimate-state')).to_have_text('Full estimate')
                expect(panel.locator('.win-you strong')).to_have_text('10.0%')
                expect(panel.locator('.win-opponent strong')).to_have_text('90.0%')
                expect(panel.locator('.win-history-point[data-turn-index="5"]')).to_have_attribute('aria-pressed', 'true')
                expect(panel.locator('.win-history-point[data-estimate="final"]')).to_have_attribute('aria-pressed', 'false')
                self.page.get_by_role('button', name='Return to played move', exact=True).click()
                expect(panel.locator('.win-estimate-state')).to_have_text('Final result')
                expect(panel.locator('.win-you strong')).to_have_text(final_chance)
                expect(panel.locator('.win-history-point[data-estimate="final"]')).to_have_attribute('aria-pressed', 'true')

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
        expect(panel.locator('.win-history')).to_be_visible()
        self.assertGreater(panel.locator('.win-history').bounding_box()['y'],
                           panel.locator('.win-evaluation').bounding_box()['y'])
        self.page.get_by_role('button', name='Next →').click()
        expect(panel.locator('.win-you strong')).to_have_text('20.0%')
        expect(self.page.locator('.turn-action')).to_contain_text('Turn 2')

    def test_history_shows_sorted_turns_perspective_and_gaps(self):
        self.open_game()
        self.set_points([self.point(3, 0.6), self.point(1, 0.8), self.point(0, 0.75)])
        chart = self.page.locator('.win-history')
        expect(chart).to_be_visible()
        for label in ['100%', '50%', '0%', 'Turn 1', 'Finish']:
            expect(chart).to_contain_text(label)
        points = chart.locator('.win-history-point')
        self.assertEqual(points.evaluate_all("nodes => nodes.map(n => n.dataset.turnIndex)"),
                         ['0', '1', '2', '3', '4', '5', '6'])
        expect(points.nth(0)).to_have_attribute('aria-label', 'Turn 1. You: 75.0%. Full estimate. Select to review.')
        expect(points.nth(1)).to_have_attribute('aria-label', 'Turn 2. You: 20.0%. Full estimate. Select to review.')
        expect(points.nth(2)).to_have_attribute('aria-label', 'Turn 3. Waiting for estimate. Select to review.')
        # Only adjacent known turns connect. No line crosses the missing turns.
        expect(chart.locator('.win-history-segment')).to_have_count(1)
        self.assertLess(float(points.nth(0).locator('.win-history-marker').get_attribute('cy')),
                        float(points.nth(1).locator('.win-history-marker').get_attribute('cy')))

    def test_history_selects_pass_exchange_and_final_with_mouse_and_keyboard(self):
        self.open_game()
        self.set_points([self.point(4, 0.4), self.point(3, 0.65), self.point(5, 0.1)])
        chart = self.page.locator('.win-history')
        chart.locator('[data-turn-index="4"]').click()
        expect(self.page.locator('.turn-subtitle')).to_contain_text('PASS')
        expect(self.page.locator('.turn-action')).to_contain_text('+0')
        board_before = self.page.locator('.turn-panel table.board').inner_text()
        score_before = self.page.locator('.turn-subtitle').inner_text()
        self.page.get_by_role('button', name='Show after move').click()
        expect(self.page.locator('.turn-panel table.board')).to_have_text(board_before, use_inner_text=True)
        expect(self.page.locator('.turn-subtitle')).to_have_text(score_before)
        expect(self.page.locator('.win-you strong')).to_have_text('40.0%')
        expect(chart.locator('[data-turn-index="4"]')).to_have_attribute('aria-pressed', 'true')
        chart.locator('[data-turn-index="3"]').focus()
        self.page.keyboard.press('Enter')
        expect(self.page.locator('.turn-subtitle')).to_contain_text('SWAP')
        expect(self.page.locator('.win-you strong')).to_have_text('35.0%')
        expect(chart.locator('[data-turn-index="3"]')).to_be_focused()
        chart.locator('[data-turn-index="5"]').click()
        expect(self.page.locator('.win-estimate-state')).to_have_text('Full estimate')
        expect(self.page.locator('.win-you strong')).to_have_text('90.0%')
        chart.locator('[data-turn-index="6"]').focus()
        self.page.keyboard.press('Space')
        expect(self.page.locator('.win-estimate-state')).to_have_text('Final result')
        expect(chart.locator('[data-turn-index="6"]')).to_have_attribute('aria-pressed', 'true')
        expect(chart.locator('[data-turn-index="6"]')).to_be_focused()

    def test_history_updates_early_points_and_uses_shared_player_name(self):
        self.open_game()
        self.set_points([self.point(0, 0.625, provisional=True), self.point(1, 0.5)])
        chart = self.page.locator('.win-history')
        expect(chart.locator('[data-turn-index="0"]')).to_have_attribute('data-estimate', 'early')
        expect(chart.locator('[data-turn-index="0"]')).to_have_attribute('aria-label',
            'Turn 1. You: 62.5%. Early estimate. Select to review.')
        expect(chart.locator('.win-history-segment')).to_have_attribute('stroke-dasharray', '5 4')
        chart.locator('[data-turn-index="0"]').focus()
        self.page.evaluate('refreshTrendPanel()')
        expect(chart.locator('[data-turn-index="0"]')).to_be_focused()
        self.page.evaluate('viewingSharedGame = true')
        self.set_points([self.point(0, 0.7), self.point(1, 0.5)])
        expect(chart.locator('[data-turn-index="0"]')).to_have_attribute('data-estimate', 'full')
        expect(chart.locator('[data-turn-index="0"]')).to_have_attribute('aria-label',
            'Turn 1. Demo Player: 70.0%. Full estimate. Select to review.')
        expect(chart.locator('.win-history-segment')).to_have_attribute('stroke-dasharray', 'none')

    def test_history_final_result_does_not_need_analysis_and_missing_scores_stay_unknown(self):
        self.open_game()
        chart = self.page.locator('.win-history')
        expect(chart.locator('[data-estimate="final"]')).to_have_count(1)
        expect(chart.locator('.win-history-segment')).to_have_count(0)
        for mine, theirs, expected in [(10, 5, '100.0%'), (5, 10, '0.0%'), (5, 5, '50.0%')]:
            self.page.evaluate('''scores => {
              currentReview.my_score = scores[0]; currentReview.opponent_score = scores[1];
              renderReview();
            }''', [mine, theirs])
            expect(chart.locator('[data-estimate="final"]')).to_have_attribute('aria-label',
                'Final result. You: ' + expected + '. Select to review.')
        self.page.evaluate('currentReview.my_score = null; renderReview()')
        expect(chart.locator('[data-estimate="final"]')).to_have_count(0)
        expect(chart.locator('.win-history-point')).to_have_count(6)

    def test_long_history_fits_phone_and_desktop(self):
        self.open_game()
        self.page.evaluate('''() => {
          const turns = currentReview.turns;
          currentReview.turns = Array.from({length: 42}, (_, index) => ({...turns[index % turns.length]}));
        }''')
        self.set_points([self.point(index, 0.1 + index % 9 / 10) for index in range(42)])
        panel = self.page.locator('.trend-panel')
        for width in [320, 390, 400, 401, 600, 760, 761, 800, 1024, 1100, 1101, 1280]:
            self.page.set_viewport_size({'width': width, 'height': 844})
            bounds = panel.evaluate('''node => ({
              right: node.getBoundingClientRect().right, left: node.getBoundingClientRect().left,
              client: node.clientWidth, scroll: node.scrollWidth,
              chartClient: node.querySelector('.win-history').clientWidth,
              chartScroll: node.querySelector('.win-history').scrollWidth
            })''')
            self.assertGreaterEqual(bounds['left'], 0)
            self.assertLessEqual(bounds['right'], width)
            self.assertEqual(bounds['scroll'], bounds['client'])
            self.assertEqual(bounds['chartScroll'], bounds['chartClient'])
            text_geometry = panel.locator('.win-history svg').evaluate('''svg => {
              const chart = svg.getBoundingClientRect();
              return [...svg.querySelectorAll('text')].map(label => {
                const rect = label.getBoundingClientRect(), matrix = label.getScreenCTM();
                return {text: label.textContent,
                  fontSize: parseFloat(getComputedStyle(label).fontSize) * Math.hypot(matrix.c, matrix.d),
                  contained: rect.left >= chart.left && rect.right <= chart.right &&
                    rect.top >= chart.top && rect.bottom <= chart.bottom};
              });
            }''')
            for label in text_geometry:
                with self.subTest(width=width, label=label['text']):
                    self.assertGreaterEqual(label['fontSize'], 11)
                    self.assertTrue(label['contained'], 'Chart label is clipped')
        self.assertGreater(panel.locator('.win-history').bounding_box()['y'],
                           panel.locator('.win-evaluation').bounding_box()['y'])
        expect(panel.locator('.win-history-point')).to_have_count(43)

    def test_candidate_table_labels_pass_and_exchange(self):
        self.open_game()
        self.page.evaluate('''() => {
          const output = document.querySelector('.position-output');
          output.replaceChildren();
          renderMoves([
            {action: 'pass', position: '', tiles: '', score: 0, win: .45},
            {action: 'exchange', position: '', tiles: 'AE?', score: 0, win: .55},
            {action: 'place', position: '8G', tiles: 'CAT', score: 5, win: .6}
          ], {simulated: false}, output, currentReview.turns[0]);
        }''')
        expect(self.page.locator('.moves-table tbody tr td:first-child')).to_have_text(['Pass', 'Exchange AE?', '8G CAT'])

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
