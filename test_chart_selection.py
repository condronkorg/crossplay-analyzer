"""Chart navigation uses explicit, stable targets. Synthetic games only."""
import unittest
from playwright.sync_api import expect
import test_game_overview as overview


class ChartSelectionTest(unittest.TestCase):
    setUpClass = classmethod(overview.GameOverviewTest.setUpClass.__func__)
    setUp = overview.GameOverviewTest.setUp
    tearDown = overview.GameOverviewTest.tearDown
    route_request = overview.GameOverviewTest.route_request
    connect = overview.GameOverviewTest.connect
    open_summary = overview.GameOverviewTest.open_summary
    set_draw_points = overview.GameOverviewTest.set_draw_points
    set_long_draw_history = overview.GameOverviewTest.set_long_draw_history
    wait_for_timeline_layout = overview.GameOverviewTest.wait_for_timeline_layout

    def prepare_dense(self):
        self.page.set_viewport_size({'width': 320, 'height': 900})
        self.open_summary()
        self.set_long_draw_history()
        self.page.evaluate('trendResults.points.forEach(point=>point.mine=50);renderReview()')
        self.wait_for_timeline_layout()

    def assert_inspected(self, turn, phase):
        inspector = self.page.locator('.win-history-inspector')
        expect(inspector).to_have_attribute('data-inspected-turn', str(turn))
        expect(inspector).to_have_attribute('data-inspected-phase', phase)

    def test_dense_controls_choose_exact_turn_and_pin_against_pointer_movement(self):
        self.prepare_dense()
        self.page.get_by_role('combobox', name='Chart turn', exact=True).select_option('40')
        self.assert_inspected(40, 'before-draw')
        draw = self.page.get_by_role('button', name='Inspect rack draw', exact=True)
        bounds = draw.bounding_box()
        self.page.mouse.click(bounds['x']+3,bounds['y']+bounds['height']/2)
        self.assert_inspected(40, 'after-draw')
        expect(self.page.locator('.win-history-point[data-turn-index="40"][data-phase="after-draw"]')).to_have_attribute('aria-pressed','true')
        expect(self.page.locator('.win-history-point[data-turn-index="40"][data-phase="before-draw"]')).to_have_attribute('aria-pressed','false')
        expect(self.page.locator('.win-history-point[data-phase="after-draw"] .win-history-selected-ring')).to_have_count(1)
        for control in self.page.locator('.win-history-controls button, .win-history-controls select').all():
            self.assertGreaterEqual(control.bounding_box()['height'], 44)
            self.assertGreaterEqual(control.bounding_box()['width'], 44)
        self.page.locator('.win-history-point[data-turn-index="12"][data-phase="before-draw"] .win-history-marker').hover(force=True)
        self.page.mouse.move(bounds['x']+bounds['width']/2,bounds['y']+bounds['height']/2,steps=8)
        self.assert_inspected(40, 'after-draw')
        self.page.get_by_role('button',name='Inspect recorded move',exact=True).click()
        self.assert_inspected(40, 'before-draw')
        self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth<=innerWidth'))

    def test_touchscreen_controls_keep_exact_turn_and_phase(self):
        self.context.close()
        self.context = self.browser.new_context(has_touch=True, is_mobile=True,
            viewport={'width':320,'height':900}, service_workers='block')
        self.addCleanup(self.context.close)
        self.context.route('**/*', self.route_request)
        self.page=self.context.new_page()
        self.page.on('pageerror',lambda error:self.page_errors.append(str(error)))
        self.page.goto(self.base_url,wait_until='domcontentloaded')
        self.page.evaluate('window.automaticAnalysis=startBackgroundAnalysis;startBackgroundAnalysis=()=>{};'
            'window.automaticViewedTurn=prepareViewedTurn;prepareViewedTurn=()=>{}')
        self.open_summary();self.set_long_draw_history()
        self.page.get_by_role('combobox',name='Chart turn',exact=True).select_option('40')
        draw=self.page.get_by_role('button',name='Inspect rack draw',exact=True)
        draw.scroll_into_view_if_needed()
        bounds=draw.bounding_box()
        self.page.touchscreen.tap(bounds['x']+3,bounds['y']+bounds['height']/2)
        self.assert_inspected(40,'after-draw')
        next_button=self.page.get_by_role('button',name='Next chart turn',exact=True)
        bounds=next_button.bounding_box()
        self.page.touchscreen.tap(bounds['x']+bounds['width']/2,bounds['y']+bounds['height']/2)
        self.assert_inspected(41,'after-draw')

    def test_keyboard_phase_and_turn_persist_after_refresh_and_resize(self):
        self.prepare_dense()
        self.page.get_by_role('combobox',name='Chart turn',exact=True).select_option('40')
        draw=self.page.get_by_role('button',name='Inspect rack draw',exact=True)
        draw.focus();self.page.keyboard.press('Enter')
        self.assert_inspected(40,'after-draw')
        self.page.evaluate('refreshMoveQualityPanel();refreshTrendPanel()')
        expect(draw).to_be_focused()
        self.assert_inspected(40,'after-draw')
        self.page.set_viewport_size({'width':1366,'height':768})
        self.wait_for_timeline_layout()
        self.assert_inspected(40,'after-draw')
        self.page.get_by_role('button',name='Next chart turn',exact=True).focus()
        self.page.keyboard.press('Enter')
        self.assert_inspected(41,'after-draw')
        self.page.get_by_role('button',name='Previous chart turn',exact=True).click()
        self.assert_inspected(40,'after-draw')

    def test_missing_draw_and_opening_final_have_explicit_states(self):
        self.open_summary();self.set_draw_points()
        selector=self.page.get_by_role('combobox',name='Chart turn',exact=True)
        selector.select_option('2')
        draw=self.page.get_by_role('button',name='Inspect rack draw',exact=True)
        expect(draw).to_be_disabled();expect(draw).to_have_text('No draw')
        selector.select_option('opening')
        expect(self.page.locator('.win-history-inspector')).to_have_attribute('data-inspected-phase','opening')
        expect(self.page.get_by_role('button',name='Previous chart turn',exact=True)).to_be_disabled()
        selector.select_option('final')
        expect(self.page.locator('.win-history-inspector')).to_have_attribute('data-inspected-phase','final')
        expect(self.page.get_by_role('button',name='Next chart turn',exact=True)).to_be_disabled()
        expect(draw).to_be_disabled()

    def test_keyboard_navigation_boundaries_keep_focus_on_turn_selector(self):
        self.open_summary();self.set_draw_points()
        selector=self.page.get_by_role('combobox',name='Chart turn',exact=True)
        selector.select_option('0')
        self.page.get_by_role('button',name='Previous chart turn',exact=True).focus()
        self.page.keyboard.press('Enter')
        expect(self.page.locator('.win-history-inspector')).to_have_attribute('data-inspected-phase','opening')
        expect(selector).to_be_focused()
        selector.select_option('5')
        self.page.get_by_role('button',name='Next chart turn',exact=True).focus()
        self.page.keyboard.press('Enter')
        expect(self.page.locator('.win-history-inspector')).to_have_attribute('data-inspected-phase','final')
        expect(selector).to_be_focused()

    def test_board_phase_button_releases_a_final_chart_selection(self):
        self.open_summary();self.set_draw_points()
        self.page.get_by_role('combobox',name='Chart turn',exact=True).select_option('final')
        expect(self.page.locator('.win-history-inspector')).to_have_attribute('data-inspected-phase','final')
        self.page.get_by_role('button',name='Show before move',exact=True).click()
        self.assert_inspected(5,'before-draw')
        self.page.locator('.win-history-point[data-turn-index="0"][data-phase="before-draw"] .win-history-marker').hover()
        self.assert_inspected(0,'before-draw')

    def test_pending_or_failed_refill_is_not_labelled_no_draw(self):
        self.open_summary();self.set_draw_points()
        self.page.evaluate("""() => {
          trendResults.points=trendResults.points.filter(point=>!(point.x===0&&point.phase==='after-draw'));
          delete moveQualityResults.points[0].draw;
          renderReview();
        }""")
        self.page.get_by_role('combobox',name='Chart turn',exact=True).select_option('0')
        draw=self.page.get_by_role('button',name='Inspect rack draw',exact=True)
        expect(draw).to_be_disabled();expect(draw).to_have_text('Draw pending')
        self.page.evaluate("moveQualityResults.points[0].draw={unavailable:true,reason:'Synthetic missing estimate'};renderReview()")
        expect(draw).to_be_disabled();expect(draw).to_have_text('Draw unavailable')

    def test_plot_band_uses_chosen_phase_in_both_vertical_halves(self):
        self.open_summary();self.set_draw_points();self.wait_for_timeline_layout()
        self.page.get_by_role('combobox',name='Chart turn',exact=True).select_option('1')
        self.page.get_by_role('button',name='Inspect rack draw',exact=True).click()
        for position in [.15,.85]:
            hit=self.page.locator('[data-hit-turn="0"][data-hit-phase="turn"]')
            bounds=hit.bounding_box()
            self.page.mouse.click(bounds['x']+bounds['width']/2,bounds['y']+bounds['height']*position)
            self.assert_inspected(0,'after-draw')


if __name__ == '__main__':
    unittest.main()
