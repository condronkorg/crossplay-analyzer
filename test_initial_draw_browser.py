"""Initial rack UI checks with synthetic games and deterministic estimates."""

import unittest

from playwright.sync_api import expect

import test_game_overview as overview_browser


class InitialDrawBrowserTest(unittest.TestCase):
    setUpClass = classmethod(overview_browser.GameOverviewTest.setUpClass.__func__)
    setUp = overview_browser.GameOverviewTest.setUp
    tearDown = overview_browser.GameOverviewTest.tearDown
    route_request = overview_browser.GameOverviewTest.route_request
    connect = overview_browser.GameOverviewTest.connect
    open_summary = overview_browser.GameOverviewTest.open_summary
    set_draw_points = overview_browser.GameOverviewTest.set_draw_points

    def set_opening(self, **changes):
        opening = dict(firstMine=True, firstRack='ACEIRST', secondRack='AEINORT',
                       beforeWin=.54, firstOnlyWin=.60, secondOnlyWin=.50,
                       afterWin=.58, firstWpa=7, secondWpa=3,
                       samples=277, provisional=False)
        opening.update(changes)
        self.page.evaluate('''opening => {
          moveQualityResults.opening = opening;
          renderReview();
        }''', opening)

    def test_opening_contributes_once_per_player_and_keeps_first_refill(self):
        self.open_summary()
        self.set_draw_points()
        self.set_opening()
        expect(self.page.locator('[data-summary-row="draw-luck"] td')).to_have_text(
            ['+9.5 (+19.0)', '−2.0 (−4.0)'])
        expect(self.page.locator('[data-summary-row="draw-coverage"] td')).to_have_text(['2 / 2', '2 / 2'])
        expect(self.page.locator('[data-summary-row="average"] td')).to_have_text(['−6.0 (−6.0)', '0.0 (0.0)'])
        marker = self.page.locator('.win-history-opening')
        expect(marker).to_have_attribute('data-estimate', 'full')
        expect(marker).to_have_attribute('aria-label', 'Initial rack draw. You: 58.0%. Full estimate. Select to inspect.')
        segment = self.page.locator('.win-history-segment').first
        self.assertLess(float(segment.get_attribute('x1')), float(segment.get_attribute('x2')))
        marker.focus()
        inspector = self.page.locator('.win-history-inspector')
        expect(inspector).to_have_attribute('data-inspected-phase', 'opening')
        expect(inspector).to_contain_text('ACEIRST')
        expect(inspector).to_contain_text('AEINORT')
        expect(inspector).to_contain_text('+7.0 pp')
        expect(inspector).to_contain_text('+3.0 pp')
        expect(inspector.locator('[data-move="after-opening"] strong')).to_have_text('58.0%')

    def test_opponent_opening_uses_your_view_and_each_own_wpa(self):
        self.open_summary()
        self.set_draw_points()
        self.set_opening(firstMine=False)
        self.page.locator('.win-history-opening').focus()
        expect(self.page.locator('.win-history-opening')).to_contain_text('You: 42.0%')
        expect(self.page.locator('[data-summary-row="draw-luck"] td')).to_have_text(
            ['+7.5 (+15.0)', '0.0 (0.0)'])
        expect(self.page.locator('[data-move="after-opening"] strong')).to_have_text('42.0%')

    def test_zero_and_missing_opening_never_invent_a_measurement(self):
        self.open_summary()
        self.set_draw_points()
        self.set_opening(firstWpa=0, secondWpa=0)
        expect(self.page.locator('[data-summary-row="draw-luck"] td')).to_have_text(
            ['+6.0 (+12.0)', '−3.5 (−7.0)'])
        self.set_opening(unavailable=True, reason='Opening rack is missing.')
        expect(self.page.locator('[data-summary-row="draw-coverage"] td')).to_have_text(['1 / 2', '1 / 2'])
        expect(self.page.locator('[data-summary-row="draw-luck"] td')).to_have_text(
            ['+12.0 (+12.0)', '−7.0 (−7.0)'])
        marker = self.page.locator('.win-history-opening')
        expect(marker).to_have_attribute('data-estimate', 'missing')
        marker.focus()
        expect(self.page.locator('.win-history-inspector')).to_contain_text('Opening rack is missing.')
        expect(self.page.locator('[data-move="after-opening"] strong')).to_have_text('—')

    def test_early_and_retryable_states(self):
        self.open_summary()
        self.set_draw_points()
        self.set_opening(samples=64, provisional=True)
        marker = self.page.locator('.win-history-opening')
        expect(marker).to_have_attribute('data-estimate', 'early')
        marker.focus()
        expect(self.page.locator('.win-history-inspector')).to_contain_text('Early estimate')
        expect(self.page.locator('.overview-status')).to_contain_text('3 early draw estimates')
        self.set_opening(retryable=True, reason='Opening estimate needs a retry.')
        expect(marker).to_have_attribute('data-estimate', 'missing')
        marker.focus()
        expect(self.page.locator('[data-move="after-opening"] strong')).to_have_text('—')
        expect(self.page.locator('.win-history-inspector')).to_contain_text('needs a retry')

    def test_opening_keyboard_selection_and_refresh_keep_details_and_focus(self):
        self.open_summary()
        self.set_draw_points()
        self.set_opening()
        marker = self.page.locator('.win-history-opening')
        marker.focus()
        self.page.keyboard.press('Enter')
        expect(self.page.locator('.game-overview')).to_be_visible()
        expect(marker).to_be_focused()
        self.page.evaluate('refreshGameOverview(); refreshMoveQualityPanel(); refreshTrendPanel()')
        expect(marker).to_be_focused()
        expect(self.page.locator('.win-history-inspector')).to_have_attribute('data-inspected-phase', 'opening')
        self.page.set_viewport_size({'width': 390, 'height': 900})
        expect(self.page.locator('.win-history-inspector')).to_have_attribute('data-inspected-phase', 'opening')
        self.page.locator('.win-history-point[data-turn-index="0"][data-phase="before-draw"]').focus()
        self.page.keyboard.press('Enter')
        expect(self.page.get_by_role('combobox', name='Select game turn')).to_have_value('0')
        expect(self.page.locator('.win-history-inspector')).to_have_attribute('data-inspected-phase', 'before-draw')

    def test_opening_is_available_before_move_estimates(self):
        self.open_summary()
        self.page.evaluate("() => { moveQualityResults = {state: 'running', points: []}; trendResults = {state: 'running', points: []}; }")
        self.set_opening()
        expect(self.page.locator('.win-history-opening')).to_have_attribute('data-estimate', 'full')
        expect(self.page.locator('[data-summary-row="draw-luck"] td')).to_have_text(['+7.0 (+7.0)', '+3.0 (+3.0)'])
        expect(self.page.locator('[data-summary-row="average"] td')).to_have_text(['—', '—'])
        self.page.locator('.win-history-opening').focus()
        self.page.keyboard.press('Space')
        expect(self.page.locator('.win-history-inspector')).to_have_attribute('data-inspected-phase', 'opening')

    def test_dense_mobile_opening_has_stable_separate_target(self):
        self.open_summary()
        self.set_draw_points()
        self.set_opening()
        self.page.evaluate('''() => {
          currentReview.turns = Array.from({length: 80}, (_, i) => ({...currentReview.turns[i % 6], mine: i % 2 === 0}));
          renderReview();
        }''')
        self.page.set_viewport_size({'width': 320, 'height': 900})
        self.page.wait_for_function("() => Math.abs(document.querySelector('.win-history svg').viewBox.baseVal.width - document.querySelector('.win-history-scroll').clientWidth) <= 1")
        geometry = self.page.evaluate('''() => {
          const rect = selector => document.querySelector(selector).getBoundingClientRect();
          const opening = rect('.win-history-opening .win-history-marker');
          const hit = rect('[data-hit-phase="opening"]');
          const turn = rect('[data-hit-turn="0"][data-hit-phase="turn"]');
          return {marker: opening.width, hit: hit.width, clear: hit.right <= turn.left,
            fits: document.documentElement.scrollWidth <= innerWidth};
        }''')
        self.assertGreaterEqual(geometry['marker'], 14)
        self.assertGreaterEqual(geometry['hit'], 28)
        self.assertTrue(geometry['clear'])
        self.assertTrue(geometry['fits'])
        self.page.locator('[data-hit-phase="opening"]').click()
        expect(self.page.locator('.win-history-inspector')).to_have_attribute('data-inspected-phase', 'opening')
        # Visible curve markers keep their own targets. Use the clear upper
        # part of the turn column to test its separation from the opening.
        target = self.page.locator('[data-hit-turn="0"][data-hit-phase="turn"]')
        bounds = target.bounding_box()
        target.click(position={'x': bounds['width'] / 2, 'y': 2})
        expect(self.page.get_by_role('combobox', name='Select game turn')).to_have_value('0')
        expect(self.page.locator('.win-history-inspector')).to_have_attribute('data-inspected-phase', 'before-draw')


    def test_selected_opening_summary_fits_desktop_height(self):
        self.page.set_viewport_size({'width':1366,'height':768})
        self.open_summary()
        self.set_draw_points()
        self.set_opening()
        self.page.locator('.win-history-opening').click()
        self.page.evaluate('scrollTo(0,0)')
        bounds=self.page.locator('.game-overview').bounding_box()
        self.assertLessEqual(bounds['y']+bounds['height'],768)


if __name__ == '__main__':
    unittest.main()
