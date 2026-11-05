"""Game overview checks with synthetic games and deterministic estimates."""

import unittest

from playwright.sync_api import expect

import test_review_browser as review_browser


class GameOverviewTest(unittest.TestCase):
    setUpClass = classmethod(review_browser.ReviewBrowserTest.setUpClass.__func__)
    setUp = review_browser.ReviewBrowserTest.setUp
    tearDown = review_browser.ReviewBrowserTest.tearDown
    route_request = review_browser.ReviewBrowserTest.route_request
    connect = review_browser.ReviewBrowserTest.connect

    def open_summary(self):
        self.connect()
        self.page.get_by_role('button', name='Review game against Demo Cedar').click()
        expect(self.page.locator('.game-overview')).to_be_visible()

    def set_points(self):
        self.page.evaluate('''() => {
          moveQualityResults = {state: 'running', points: [
            {turnIndex: 0, category: 'best', loss: 0.5, playedWin: .70, bestWin: .705},
            {turnIndex: 1, category: 'blunder', loss: 12, playedWin: .35, bestWin: .47},
            {turnIndex: 2, category: 'unrated', reason: 'Missing rack'},
            {turnIndex: 3, category: 'mistake', loss: 7, playedWin: .40, bestWin: .47, provisional: true}
          ]};
          trendResults = deriveWinTrend({review: currentReview, quality: moveQualityResults});
          refreshMoveQualityPanel(); refreshTrendPanel();
        }''')

    def test_summary_enters_first_turn_and_chart_enters_selected_turn(self):
        self.open_summary()
        expect(self.page.locator('.board')).to_have_count(0)
        self.page.get_by_role('button', name='Start Review', exact=True).click()
        expect(self.page.get_by_role('combobox', name='Select game turn')).to_have_value('0')
        expect(self.page.locator('.board')).to_be_visible()
        self.page.get_by_role('button', name='Game summary', exact=True).click()
        expect(self.page.locator('.game-overview')).to_be_visible()
        expect(self.page.get_by_role('button', name='Start Review', exact=True)).to_be_focused()
        self.page.locator('.win-history-point[data-turn-index="3"]').click()
        expect(self.page.get_by_role('combobox', name='Select game turn')).to_have_value('3')
        expect(self.page.locator('.board')).to_be_visible()
        self.page.get_by_role('button', name='Game summary', exact=True).click()
        self.page.get_by_role('button', name='Review game against Demo River').click()
        expect(self.page.locator('.game-overview')).to_contain_text('Demo River')
        expect(self.page.locator('.board')).to_have_count(0)

    def test_both_player_values_keep_pending_unrated_and_early_distinct(self):
        self.open_summary()
        expect(self.page.locator('[data-summary-row="average"] td')).to_have_text(['—', '—'])
        expect(self.page.locator('[data-summary-row="pending"] td')).to_have_text(['3', '3'])
        self.set_points()
        expect(self.page.locator('[data-summary-row="average"] td')).to_have_text(['0.5 pp', '9.5 pp'])
        expect(self.page.locator('[data-summary-row="coverage"] td')).to_have_text(['1 / 3', '2 / 3'])
        for category, values in [('best', ['1', '0']), ('blunder', ['0', '1']),
                                 ('mistake', ['0', '1']), ('unrated', ['1', '0']),
                                 ('pending', ['1', '1'])]:
            expect(self.page.locator(f'[data-summary-row="{category}"] td')).to_have_text(values)
        expect(self.page.locator('.overview-status')).to_contain_text('early')
        expect(self.page.locator('.win-history-point[data-turn-index="2"]')).to_have_attribute('data-estimate', 'missing')
        self.assertNotIn('Accuracy', self.page.locator('.game-overview').inner_text())
        self.page.evaluate('''() => {
          moveQualityResults.points[3].provisional = false;
          refreshMoveQualityPanel();
        }''')
        expect(self.page.locator('.overview-status')).not_to_contain_text('early')

    def test_summary_chart_uses_the_card_width_and_labels_errors(self):
        self.page.set_viewport_size({'width': 1440, 'height': 1000})
        self.open_summary()
        self.set_points()
        chart = self.page.locator('.overview-chart .win-history-scroll')
        geometry = chart.evaluate('''node => ({
          width: node.getBoundingClientRect().width,
          height: node.getBoundingClientRect().height,
          svgWidth: node.querySelector('svg').getBoundingClientRect().width
        })''')
        self.assertGreater(geometry['width'], 700)
        self.assertGreaterEqual(geometry['height'], 320)
        self.assertAlmostEqual(geometry['svgWidth'], geometry['width'], delta=1)
        expect(chart.locator('.win-history-grade')).to_have_text(['??', '?'])
        expect(chart.locator('[data-turn-index="3"]')).to_have_attribute('data-quality', 'mistake')
        self.page.set_viewport_size({'width': 320, 'height': 844})
        self.assertGreaterEqual(chart.bounding_box()['height'], 320)
        self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))

    def test_background_updates_keep_view_and_keyboard_focus(self):
        self.open_summary()
        self.set_points()
        start = self.page.get_by_role('button', name='Start Review', exact=True)
        start.focus()
        self.page.evaluate('refreshMoveQualityPanel(); refreshTrendPanel()')
        expect(start).to_be_focused()
        key = self.page.locator('.win-history-help > summary')
        key.focus()
        self.page.keyboard.press('Enter')
        self.page.evaluate('refreshMoveQualityPanel(); refreshTrendPanel()')
        expect(key).to_be_focused()
        expect(self.page.locator('.win-history-help')).to_have_attribute('open', '')
        point = self.page.locator('.win-history-point[data-turn-index="1"]')
        point.focus()
        self.page.evaluate('refreshMoveQualityPanel(); refreshTrendPanel()')
        expect(point).to_be_focused()
        self.page.keyboard.press('Enter')
        expect(self.page.get_by_role('combobox', name='Select game turn')).to_have_value('1')
        self.page.evaluate('refreshMoveQualityPanel(); refreshTrendPanel()')
        expect(self.page.locator('.game-overview')).to_have_count(0)
        expect(self.page.locator('.board')).to_be_visible()

    def test_failed_early_estimates_stay_pending_and_paused_status_is_visible(self):
        self.open_summary()
        self.page.evaluate('''() => {
          moveQualityResults = {state: 'running', points: currentReview.turns.map((turn, turnIndex) => ({
            turnIndex, category: 'unrated', loss: null, retryable: true, provisional: true, reason: 'Worker failed'
          }))};
          refreshMoveQualityPanel();
        }''')
        expect(self.page.locator('[data-summary-row="pending"] td')).to_have_text(['3', '3'])
        expect(self.page.locator('.overview-status')).not_to_contain_text('All moves checked')
        expect(self.page.locator('.overview-status')).to_contain_text('retry')
        self.page.evaluate('pauseReviewAnalysis(currentReview)')
        expect(self.page.locator('.overview-status')).to_contain_text('paused')

    def test_direct_and_shared_links_open_summary(self):
        self.open_summary()
        owner_url = self.page.url
        shared_url = self.page.evaluate('gameShareUrl(currentReview.share_token).href')
        self.page.goto(owner_url)
        expect(self.page.locator('.game-overview')).to_be_visible()
        expect(self.page.locator('.board')).to_have_count(0)
        self.page.goto(shared_url)
        expect(self.page.locator('.game-overview')).to_be_visible()
        expect(self.page.locator('.overview-player-name')).to_have_text(['Demo Player', 'Demo Cedar'])
        self.page.get_by_role('button', name='Start Review', exact=True).click()
        expect(self.page.locator('.board')).to_be_visible()

    def test_phone_layout_handles_long_names_and_many_turns(self):
        self.open_summary()
        self.page.evaluate('''() => {
          currentReview.my_name = 'LongPlayerNameWithoutAnySpacesAtAll';
          currentReview.opponent = 'AnOpponentWithAnEquallyLongPlayerName';
          currentReview.turns = Array.from({length: 80}, (_, i) => ({...currentReview.turns[i % 6]}));
          moveQualityResults = {state: 'complete', points: currentReview.turns.map((turn, turnIndex) => ({
            turnIndex, category: turnIndex % 2 ? 'mistake' : 'blunder', loss: turnIndex % 2 ? 7 : 12,
            playedWin: .5, bestWin: .62
          }))};
          trendResults = deriveWinTrend({review: currentReview, quality: moveQualityResults});
          renderReview();
        }''')
        for width in [320, 390, 768, 1440]:
            with self.subTest(width=width):
                self.page.set_viewport_size({'width': width, 'height': 900})
                self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
                start = self.page.get_by_role('button', name='Start Review', exact=True)
                expect(start).to_be_visible()
                geometry = self.page.locator('.game-overview').evaluate('''node => {
                  const button = node.querySelector('.start-review').getBoundingClientRect();
                  const card = node.getBoundingClientRect();
                  return {buttonHeight: button.height, inside: button.left >= card.left && button.right <= card.right};
                }''')
                self.assertGreaterEqual(geometry['buttonHeight'], 44)
                self.assertTrue(geometry['inside'])
                labels = self.page.locator('.overview-chart svg').evaluate('''svg => {
                  const chart = svg.getBoundingClientRect();
                  return [...svg.querySelectorAll('text')].map(label => {
                    const r = label.getBoundingClientRect(), m = label.getScreenCTM();
                    return {text: label.textContent, size: parseFloat(getComputedStyle(label).fontSize) * Math.hypot(m.c, m.d),
                      inside: r.left >= chart.left && r.right <= chart.right && r.top >= chart.top && r.bottom <= chart.bottom};
                  });
                }''')
                for label in labels:
                    self.assertTrue(label['inside'], label)
                    self.assertGreaterEqual(label['size'], 11, label)
                badges = self.page.locator('.overview-chart .win-history-grade').evaluate_all('''nodes => nodes.map(node => {
                  const r = node.getBoundingClientRect(); return {left: r.left, right: r.right};
                })''')
                for previous, following in zip(badges, badges[1:]):
                    self.assertLess(previous['right'], following['left'])
