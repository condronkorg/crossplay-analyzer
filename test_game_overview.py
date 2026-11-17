"""Game overview checks with synthetic games and deterministic estimates."""

import re
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

    def set_draw_points(self):
        self.page.evaluate("""() => {
          // One recorded placement and one exchange, followed by pass-only turns.
          currentReview.turns = currentReview.turns.map((turn, index) => ({...turn,
            action: index === 0 ? 'CAT 8G 10' : index === 1 ? 'swap -- 0' : 'PASS 0',
            rack: index === 0 ? 'CATERS?' : 'AEIRST?',
            rack_after: index === 0 ? 'ERS?ING' : index === 1 ? 'AEIRSTZ' : 'AEIRST?',
            swapped_tiles: index === 1 ? '?' : null,
            bag_count: 86, grid: Array(15).fill('.'.repeat(15))
          }));
          moveQualityResults = {state: 'running', points: [
            {turnIndex: 0, category: 'mistake', loss: 6, playedWin: .50, bestWin: .56,
             draw: {beforeWin: .50, afterWin: .62, luck: 12, drawCount: 3,
               rackAfter: 'ERS?ING', samples: 277, provisional: false}},
            {turnIndex: 1, category: 'best', loss: 0, playedWin: .60, bestWin: .60,
             draw: {beforeWin: .60, afterWin: .53, luck: -7, drawCount: 1,
               rackAfter: 'AEIRSTZ', samples: 64, provisional: true}}
          ]};
          trendResults = {state: 'running', points: [
            {x: 0, phase: 'before-draw', mine: 50},
            {x: 0, phase: 'after-draw', mine: 62, draw: moveQualityResults.points[0].draw},
            {x: 1, phase: 'before-draw', mine: 40},
            {x: 1, phase: 'after-draw', mine: 47, provisional: true, draw: moveQualityResults.points[1].draw}
          ]};
          renderReview();
        }""")

    def test_draw_luck_summary_has_signed_player_totals_and_separate_coverage(self):
        self.open_summary()
        self.set_draw_points()
        expect(self.page.locator('[data-summary-row="draw-luck"] td')).to_have_text(['+12.0 (+12.0)', '−7.0 (−7.0)'])
        expect(self.page.locator('[data-summary-row="draw-coverage"] td')).to_have_text(['1 / 1', '1 / 1'])
        expect(self.page.locator('.overview-status')).to_contain_text('1 early draw estimate')
        self.page.evaluate("""() => {
          delete moveQualityResults.points[1].draw;
          refreshMoveQualityPanel();
        }""")
        expect(self.page.locator('[data-summary-row="draw-luck"] td')).to_have_text(['+12.0 (+12.0)', '—'])
        expect(self.page.locator('[data-summary-row="draw-coverage"] td')).to_have_text(['1 / 1', '0 / 1'])
        expect(self.page.locator('.overview-status')).to_contain_text('1 draw waiting')
        self.page.evaluate("""() => {
          currentReview.turns[1].rack_after = null;
          refreshMoveQualityPanel();
        }""")
        expect(self.page.locator('.overview-status')).to_contain_text('1 draw record unavailable')

    def test_wpa_averages_and_totals_use_only_measured_events(self):
        self.open_summary()
        self.set_draw_points()
        self.page.evaluate("""() => {
          currentReview.turns[2] = {...currentReview.turns[0]};
          moveQualityResults.points.push({turnIndex: 2, category: 'blunder', loss: 13.4,
            draw: {...moveQualityResults.points[0].draw, beforeWin: .5, afterWin: .44}});
          refreshMoveQualityPanel();
        }""")
        expect(self.page.locator('[data-summary-row="average"] td')).to_have_text(['−9.7 (−19.4)', '0.0 (0.0)'])
        expect(self.page.locator('[data-summary-row="draw-luck"] td')).to_have_text(['+3.0 (+6.0)', '−7.0 (−7.0)'])
        expect(self.page.locator('[data-summary-row="draw-coverage"] td')).to_have_text(['2 / 2', '1 / 1'])
        self.page.evaluate("""() => {
          moveQualityResults.points[0].loss = 0;
          moveQualityResults.points[2].loss = 0;
          refreshMoveQualityPanel();
        }""")
        expect(self.page.locator('[data-summary-row="average"] td')).to_have_text(['0.0 (0.0)', '0.0 (0.0)'])

    def test_draw_letters_show_repeats_blanks_and_unavailable_records(self):
        self.open_summary()
        self.set_draw_points()
        self.page.evaluate("""() => {
          currentReview.turns[0].rack = 'CATERSA';
          currentReview.turns[0].rack_after = 'ERSA??N';
          selectTurn(0);
        }""")
        tiles = self.page.locator('.turn-meta .rack-draw-tiles')
        expect(tiles.locator('.draw-new .rack-tile')).to_have_text(['?', '?', 'N'])
        expect(tiles.locator('.draw-new [aria-label="Blank"]')).to_have_count(2)
        expect(tiles.locator('.draw-kept .rack-tile')).to_have_text(list('ERSA'))
        self.page.evaluate("""() => {
          currentReview.turns[0].rack_after = 'ERSAEEN';
          renderReview();
        }""")
        expect(tiles.locator('.draw-new .rack-tile')).to_have_text(['E', 'E', 'N'])
        self.page.evaluate("""() => {
          currentReview.turns[0].rack_after = null;
          renderReview();
        }""")
        expect(tiles).to_contain_text('New tiles unavailable')
        expect(tiles.locator('.rack-tile')).to_have_count(0)
        self.page.evaluate('selectTurn(2)')
        expect(tiles).to_have_text('No tiles drawn.')
        self.page.evaluate("""() => {
          currentReview.turns[1].swapped_tiles = null;
          selectTurn(1);
        }""")
        expect(tiles).to_contain_text('New tiles unavailable')

    def test_chart_draw_points_keep_move_annotations_and_draw_details_separate(self):
        self.open_summary()
        self.set_draw_points()
        chart = self.page.locator('.win-history')
        before = chart.locator('.win-history-point[data-turn-index="0"][data-phase="before-draw"]')
        after = chart.locator('.win-history-point[data-turn-index="0"][data-phase="after-draw"]')
        expect(before).to_have_count(1)
        expect(after).to_have_count(1)
        expect(before.locator('.win-history-grade')).to_have_text('?')
        expect(after.locator('.win-history-grade, .win-history-sweep')).to_have_count(0)
        expect(after).to_have_attribute('aria-label', re.compile('After rack draw'))
        self.assertGreater(float(after.locator('.win-history-hit').get_attribute('x')),
                           float(before.locator('.win-history-hit').get_attribute('x')))
        after.focus()
        inspector = chart.locator('.win-history-inspector')
        expect(inspector).to_have_attribute('data-inspected-phase', 'after-draw')
        expect(inspector).to_contain_text('+12.0')
        expect(inspector.locator('.draw-new .rack-tile')).to_have_text(['I', 'N', 'G'])
        expect(inspector.locator('.draw-kept .rack-tile')).to_have_text(['E', 'R', 'S', '?'])
        expect(inspector.locator('.draw-kept [aria-label="Blank"]')).to_have_count(1)
        expect(inspector.locator('h4')).to_have_text('New tiles drawn')
        expect(inspector.locator('[data-move="before-draw"] strong')).to_have_text('50.0%')
        expect(inspector.locator('[data-move="after-draw"] strong')).to_have_text('62.0%')
        opponent = chart.locator('.win-history-point[data-turn-index="1"][data-phase="after-draw"]')
        opponent.focus()
        expect(inspector).to_contain_text('−7.0')
        expect(inspector.locator('.draw-new .rack-tile')).to_have_text(['Z'])
        expect(inspector.locator('.draw-kept .rack-tile')).to_have_text(list('AEIRST'))
        expect(inspector.locator('[data-move="before-draw"] strong')).to_have_text('60.0%')
        expect(inspector.locator('[data-move="after-draw"] strong')).to_have_text('53.0%')
        expect(opponent).to_have_attribute('aria-label', re.compile('You: 47.0%'))
        self.page.keyboard.press('Enter')
        expect(self.page.get_by_role('combobox', name='Select game turn')).to_have_value('1')
        expect(self.page.locator('.win-history-point[data-turn-index="1"][data-phase="after-draw"]')).to_be_focused()

    def test_background_refresh_preserves_draw_inspection_and_keyboard_stage(self):
        self.open_summary()
        self.set_draw_points()
        before = self.page.locator('.win-history-point[data-turn-index="0"][data-phase="before-draw"]')
        after = self.page.locator('.win-history-point[data-turn-index="1"][data-phase="after-draw"]')
        before.focus()
        after.hover()
        for refresh in ['refreshMoveQualityPanel()', 'refreshTrendPanel()']:
            state = self.page.evaluate("""refresh => {
              eval(refresh);
              const inspector = document.querySelector('.win-history-inspector');
              return {turn: inspector.dataset.inspectedTurn, phase: inspector.dataset.inspectedPhase,
                focusedTurn: document.activeElement.dataset.turnIndex,
                focusedPhase: document.activeElement.dataset.phase};
            }""", refresh)
            self.assertEqual(state, {'turn': '1', 'phase': 'after-draw',
                                     'focusedTurn': '0', 'focusedPhase': 'before-draw'})
        after.focus()
        self.page.evaluate('refreshMoveQualityPanel(); refreshTrendPanel()')
        expect(after).to_be_focused()
        for width in [320, 390, 1440]:
            self.page.set_viewport_size({'width': width, 'height': 900})
            self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))

    def set_long_draw_history(self):
        self.set_draw_points()
        self.page.evaluate("""() => {
          const turn = currentReview.turns[0], point = moveQualityResults.points[0];
          currentReview.turns = Array.from({length: 80}, (_, index) => ({...turn, mine: index % 2 === 0}));
          moveQualityResults = {state: 'done', points: currentReview.turns.map((turn, turnIndex) => ({
            ...point, turnIndex, loss: 7, category: 'mistake', playedWin: .5, bestWin: .57,
            draw: {...point.draw, beforeWin: .5, afterWin: .55}
          }))};
          trendResults = {state: 'done', points: currentReview.turns.flatMap((turn, x) => [
            {x, phase: 'before-draw', mine: 50},
            {x, phase: 'after-draw', mine: turn.mine ? 55 : 45, draw: moveQualityResults.points[x].draw}
          ])};
          renderReview();
        }""")

    def wait_for_timeline_layout(self):
        self.page.wait_for_function("""() => {
          const viewport = document.querySelector('.win-history-scroll');
          return Math.abs(viewport.querySelector('svg').viewBox.baseVal.width - viewport.clientWidth) <= 1;
        }""")

    def test_desktop_summary_and_all_draw_points_fit_without_scrolling(self):
        self.page.set_viewport_size({'width': 1366, 'height': 768})
        self.open_summary()
        self.set_long_draw_history()
        self.wait_for_timeline_layout()
        summary = self.page.locator('.game-overview')
        self.assertLessEqual(summary.bounding_box()['height'], 560)
        chart = self.page.locator('.win-history-scroll')
        self.assertEqual(chart.evaluate('node => node.scrollWidth'), chart.evaluate('node => node.clientWidth'))
        expect(chart.locator('.win-history-point')).to_have_count(161)
        finish = chart.locator('[data-estimate="final"]').bounding_box()
        viewport = chart.bounding_box()
        self.assertLessEqual(finish['x'] + finish['width'], viewport['x'] + viewport['width'])
        expect(summary.get_by_role('button', name='Start Review', exact=True)).to_be_visible()
        before = chart.locator('[data-turn-index="0"][data-phase="before-draw"]')
        after = chart.locator('[data-turn-index="1"][data-phase="after-draw"]')
        before.focus()
        after.hover()
        self.assertLessEqual(summary.bounding_box()['height'], 560)
        chart.locator('[data-turn-index="1"][data-phase="before-draw"]').hover()
        self.assertGreater(chart.locator('.win-history-grade:visible').count(), 1)
        expect(chart.locator('[data-turn-index="1"][data-phase="before-draw"] .win-history-grade')).to_be_visible()
        after.hover()
        self.page.evaluate("""() => {
          moveQualityResults.points[1].bestMove = {action: 'place', position: '8D', tiles: 'RETAINS'};
          refreshGameOverview();
        }""")
        missed = chart.locator('[data-turn-index="1"][data-phase="before-draw"]')
        expect(missed).to_have_attribute('data-sweep', 'missed')
        expect(missed.locator('polygon')).to_be_visible()
        expect(missed.locator('line')).to_be_visible()
        missed.focus()
        expect(missed.locator('polygon')).to_be_visible()
        expect(missed.locator('line')).to_be_visible()
        before.focus()
        after.hover()
        for width in [390, 1280, 1440]:
            self.page.set_viewport_size({'width': width, 'height': 900})
            self.wait_for_timeline_layout()
            expect(before).to_be_focused()
            expect(self.page.locator('.win-history-inspector')).to_have_attribute('data-inspected-turn', '1')
            expect(self.page.locator('.win-history-inspector')).to_have_attribute('data-inspected-phase', 'after-draw')
            self.assertEqual(chart.evaluate('node => node.scrollWidth'), chart.evaluate('node => node.clientWidth'))
        self.page.keyboard.press('Enter')
        expect(self.page.get_by_role('combobox', name='Select game turn')).to_have_value('0')
        expect(before).to_be_focused()

    def set_timeline_events(self):
        self.set_long_draw_history()
        self.page.evaluate("""() => {
          currentReview.turns = currentReview.turns.map((turn, i) => ({...turn,
            grid: Array(15).fill('.'.repeat(15)),
            action: i % 8 === 2 ? 'RETAINS 8D 64' : 'CAT 8G 10'}));
          moveQualityResults.points.forEach((point, i) => {
            point.category = i % 5 ? 'mistake' : 'blunder';
            point.loss = i % 5 ? 7 : 12;
            point.provisional = i % 3 === 0;
            point.bestMove = i % 8 === 6 ? {action: 'place', position: '8D', tiles: 'RETAINS'} : null;
          });
          renderReview();
        }""")
        self.wait_for_timeline_layout()

    def assert_timeline_icons_fit(self):
        icons = self.page.locator('.win-history-annotation:visible').evaluate_all("""nodes => nodes.map(node => {
          const r = node.getBoundingClientRect(), svg = node.ownerSVGElement.getBoundingClientRect();
          return {lane: node.dataset.lane, left: r.left, right: r.right, top: r.top, bottom: r.bottom,
            inside: r.left >= svg.left && r.right <= svg.right && r.top >= svg.top && r.bottom <= svg.bottom};
        })""")
        self.assertGreater(len(icons), 0)
        for icon in icons:
            self.assertTrue(icon['inside'], icon)
        for lane in ['grade', 'sweep']:
            row = [icon for icon in icons if icon['lane'] == lane]
            for previous, following in zip(row, row[1:]):
                self.assertGreaterEqual(following['left'] - previous['right'], 3, row)
        grades = [icon for icon in icons if icon['lane'] == 'grade']
        sweeps = [icon for icon in icons if icon['lane'] == 'sweep']
        if grades and sweeps:
            self.assertGreater(min(icon['top'] for icon in sweeps), max(icon['bottom'] for icon in grades))
        tick_top = self.page.locator('.win-history svg > text').last.bounding_box()['y']
        self.assertLess(max(icon['bottom'] for icon in icons), tick_top)

    def test_timeline_events_stay_visible_without_overlap_and_inspect_crowded_turns(self):
        self.open_summary()
        self.set_timeline_events()
        for width in [1366, 390, 320]:
            with self.subTest(width=width):
                self.page.set_viewport_size({'width': width, 'height': 768})
                self.wait_for_timeline_layout()
                chart = self.page.locator('.win-history')
                for selector in ['[data-quality="blunder"] .win-history-grade:visible',
                                 '[data-quality="mistake"] .win-history-grade:visible',
                                 '[data-sweep="played"] .win-history-sweep:visible',
                                 '[data-sweep="missed"] .win-history-sweep:visible']:
                    self.assertGreater(chart.locator(selector).count(), 0, selector)
                self.assert_timeline_icons_fit()
                # A hidden badge becomes readable on focus; adjacent badges yield room.
                hidden = chart.locator('.win-history-point:has(.win-history-grade[data-collapsed="true"])').first
                turn_index = hidden.get_attribute('data-turn-index')
                hidden = chart.locator(f'[data-turn-index="{turn_index}"][data-phase="before-draw"]')
                hidden.focus()
                expect(hidden.locator('.win-history-grade')).to_be_visible()
                self.assert_timeline_icons_fit()
                self.page.evaluate('refreshTrendPanel(); refreshMoveQualityPanel()')
                expect(hidden).to_be_focused()
                expect(hidden.locator('.win-history-grade')).to_be_visible()
                self.assert_timeline_icons_fit()
                # Both independent facts fit on the same turn, even at100% or with no estimate.
                mixed = chart.locator('[data-sweep="played"]').first
                mixed.focus()
                expect(mixed.locator('.win-history-grade')).to_be_visible()
                expect(mixed.locator('.win-history-sweep')).to_be_visible()
                self.assert_timeline_icons_fit()
                expect(chart.locator('[data-phase="after-draw"] .win-history-annotation')).to_have_count(0)
                expect(chart.locator('[data-phase="final"] .win-history-annotation')).to_have_count(0)
                self.assertEqual(chart.locator('.win-history-scroll').evaluate('node => node.scrollWidth - node.clientWidth'), 0)
                if width == 1366:
                    self.assertLessEqual(self.page.locator('.game-overview').bounding_box()['height'], 560)
                hidden.focus()
                self.page.keyboard.press('Enter')
                expect(self.page.get_by_role('combobox', name='Select game turn')).to_have_value(turn_index)
                expect(hidden).to_be_focused()
                self.page.get_by_role('button', name='Game summary', exact=True).click()

    def test_missing_selected_point_stays_clear_of_its_event_badge(self):
        self.open_summary()
        self.set_points()
        self.page.evaluate("""() => {
          trendResults.points = trendResults.points.filter(point => point.x !== 3);
          renderReview();
        }""")
        self.wait_for_timeline_layout()
        point = self.page.locator('.win-history-point[data-turn-index="3"]')
        point.focus()
        self.page.keyboard.press('Enter')
        expect(point).to_have_attribute('data-estimate', 'missing')
        ring = point.locator(':scope > circle').first.bounding_box()
        badge = point.locator('.win-history-grade').bounding_box()
        self.assertLess(ring['y'] + ring['height'] + 1, badge['y'] - .5)

    def test_icon_edges_select_their_own_turn_in_a_dense_chart(self):
        self.page.set_viewport_size({'width': 1366, 'height': 768})
        self.open_summary()
        self.set_timeline_events()
        for selector in ['[data-quality="blunder"] .win-history-grade:visible',
                         '[data-quality="mistake"] .win-history-grade:visible',
                         '[data-sweep="played"] .win-history-annotation[data-lane="sweep"]:visible',
                         '[data-sweep="missed"] .win-history-annotation[data-lane="sweep"]:visible']:
            with self.subTest(selector=selector):
                icon = self.page.locator('.win-history ' + selector).first
                turn_index = icon.locator('..').get_attribute('data-turn-index')
                # This edge is several turns from the center in the dense fixture.
                icon.click(position={'x': 4, 'y': 8}, timeout=3000)
                expect(self.page.get_by_role('combobox', name='Select game turn')).to_have_value(turn_index)
                self.page.get_by_role('button', name='Game summary', exact=True).click()

    def test_dense_icons_keep_a_rare_event_type_when_both_types_can_fit(self):
        self.page.set_viewport_size({'width': 1366, 'height': 768})
        self.open_summary()
        self.set_long_draw_history()
        for blunders in [[1], [0, 20]]:
            with self.subTest(blunders=blunders):
                self.page.evaluate("""blunders => {
                  moveQualityResults.points.forEach((point, i) => {
                    point.category = blunders.includes(i) ? 'blunder' : 'mistake';
                    point.loss = blunders.includes(i) ? 12 : 7;
                  });
                  if (blunders.length > 1) moveQualityResults.points = moveQualityResults.points.slice(0,4).concat(moveQualityResults.points[20]);
                  refreshMoveQualityPanel(); refreshTrendPanel();
                }""", blunders)
                self.wait_for_timeline_layout()
                self.page.locator('.win-history-point[data-phase="final"]').focus()
                for kind in ['mistake', 'blunder']:
                    self.assertGreater(self.page.locator(f'.win-history [data-quality="{kind}"] .win-history-grade:visible').count(), 0)
                self.assert_timeline_icons_fit()

    def test_probability_halves_have_pale_tints_and_player_labels(self):
        self.open_summary()
        self.set_points()
        upper = self.page.locator('.win-history-band-upper')
        lower = self.page.locator('.win-history-band-lower')
        expect(upper).to_have_attribute('fill', '#eaf3ff')
        expect(lower).to_have_attribute('fill', '#fff0ee')
        self.assertEqual(upper.get_attribute('height'), lower.get_attribute('height'))
        self.assertAlmostEqual(float(lower.get_attribute('y')),
                               float(upper.get_attribute('y')) + float(upper.get_attribute('height')))
        expect(self.page.locator('.win-history-direction')).to_contain_text('Upper half favors You')
        expect(self.page.locator('.win-history-direction')).to_contain_text('lower half favors Demo Cedar')

    def test_summary_enters_first_turn_and_chart_enters_selected_turn(self):
        self.open_summary()
        expect(self.page.locator('.board')).to_have_count(0)
        self.page.get_by_role('button', name='Start Review', exact=True).click()
        expect(self.page.get_by_role('combobox', name='Select game turn')).to_have_value('0')
        expect(self.page.locator('.board')).to_be_visible()
        expect(self.page.locator('.game-overview')).to_be_visible()
        expect(self.page.locator('.win-history')).to_have_count(1)
        self.assertLess(self.page.locator('.game-overview').bounding_box()['y'],
                        self.page.locator('.turn-controls').bounding_box()['y'])
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
        expect(self.page.locator('[data-summary-row="average"] td')).to_have_text(['−0.5 (−0.5)', '−9.5 (−19.0)'])
        expect(self.page.locator('[data-summary-row="coverage"] td')).to_have_text(['1 / 3', '2 / 3'])
        expect(self.page.locator('[data-summary-row="average"] th')).to_contain_text('Gameplay')
        expect(self.page.locator('[data-summary-row="best"] .overview-grade-mark')).to_have_text('!')
        sizes = self.page.locator('[data-summary-row="average"] td').first.evaluate('''node => ({
          average: parseFloat(getComputedStyle(node.querySelector('.overview-wpa-average')).fontSize),
          total: parseFloat(getComputedStyle(node.querySelector('.overview-wpa-total')).fontSize)
        })''')
        self.assertLess(sizes['total'], sizes['average'])
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
        self.wait_for_timeline_layout()
        chart = self.page.locator('.overview-chart .win-history-scroll')
        geometry = chart.evaluate('''node => ({
          width: node.getBoundingClientRect().width,
          height: node.getBoundingClientRect().height,
          svgWidth: node.querySelector('svg').getBoundingClientRect().width
        })''')
        self.assertGreater(geometry['width'], 300)
        self.assertLessEqual(geometry['height'], 240)
        self.assertAlmostEqual(geometry['svgWidth'], geometry['width'], delta=1)
        inspector = self.page.locator('.overview-chart .win-history-inspector')
        visual = self.page.locator('.overview-chart .win-history-visual')
        self.assertGreaterEqual(inspector.bounding_box()['y'], visual.bounding_box()['y'] + visual.bounding_box()['height'])
        expect(inspector).to_have_attribute('data-inspected-turn', '0')
        expect(chart.locator('svg > polygon')).to_have_count(0)
        expect(chart.locator('.win-history-segment').first).to_have_attribute('stroke', '#256b4a')
        expect(chart.locator('.win-history-grade')).to_have_text(['??', '?'])
        expect(chart.locator('[data-turn-index="3"]')).to_have_attribute('data-quality', 'mistake')
        self.page.set_viewport_size({'width': 320, 'height': 844})
        self.wait_for_timeline_layout()
        self.assertLessEqual(chart.bounding_box()['height'], 240)
        self.assertGreater(inspector.bounding_box()['y'], visual.bounding_box()['y'] + visual.bounding_box()['height'])
        heading = inspector.locator('.win-history-inspector-head').bounding_box()
        comparison = inspector.locator('.win-history-comparison').bounding_box()
        self.assertGreater(comparison['x'], heading['x'] + heading['width'])
        self.assertAlmostEqual(comparison['y'], heading['y'], delta=1)
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
        bingo = self.page.locator('.win-history-inspector-event > summary')
        bingo.focus()
        self.page.keyboard.press('Enter')
        self.page.evaluate('refreshMoveQualityPanel(); refreshTrendPanel()')
        expect(bingo).to_be_focused()
        expect(self.page.locator('.win-history-inspector-event')).to_have_attribute('open', '')
        self.page.keyboard.press('Enter')
        point = self.page.locator('.win-history-point[data-turn-index="1"]')
        point.focus()
        self.page.evaluate('refreshMoveQualityPanel(); refreshTrendPanel()')
        expect(point).to_be_focused()
        self.page.keyboard.press('Enter')
        expect(self.page.get_by_role('combobox', name='Select game turn')).to_have_value('1')
        self.page.evaluate('refreshMoveQualityPanel(); refreshTrendPanel()')
        expect(self.page.locator('.game-overview')).to_be_visible()
        expect(self.page.locator('.win-history')).to_have_count(1)
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

    def test_summary_preserves_hover_inspection_during_background_refresh(self):
        self.open_summary()
        self.set_points()
        inspector = self.page.locator('.win-history-inspector')
        selection = self.page.locator('.win-history-selection')
        self.page.mouse.move(0, 0)
        self.page.locator('.win-history-point[data-turn-index="0"]').focus()
        self.page.locator('.win-history-point[data-turn-index="1"]').hover()
        expect(inspector).to_have_attribute('data-inspected-turn', '1')
        highlight_x = selection.get_attribute('x')
        for refresh in ['quality', 'trend']:
            state = self.page.evaluate('''refresh => {
              (refresh === 'quality' ? refreshMoveQualityPanel : refreshTrendPanel)();
              return {turn: document.querySelector('.win-history-inspector').dataset.inspectedTurn,
                x: document.querySelector('.win-history-selection').getAttribute('x'),
                focused: document.activeElement.dataset.turnIndex};
            }''', refresh)
            self.assertEqual(state, {'turn': '1', 'x': highlight_x, 'focused': '0'})
            expect(self.page.locator('.game-overview')).to_be_visible()
        self.page.keyboard.press('Tab')
        self.page.keyboard.press('Tab')
        expect(inspector).to_have_attribute('data-inspected-turn', '2')
        self.page.locator('.win-history-point[data-turn-index="3"]').click()
        expect(self.page.get_by_role('combobox', name='Select game turn')).to_have_value('3')
        expect(inspector).to_have_attribute('data-inspected-turn', '3')

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
        self.page.locator('.win-history-point[data-turn-index="1"]').focus()
        for width in [320, 390, 768, 1440]:
            with self.subTest(width=width):
                self.page.set_viewport_size({'width': width, 'height': 900})
                self.wait_for_timeline_layout()
                self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
                for cell in self.page.locator('[data-summary-row="average"] td').all():
                    average = cell.locator('.overview-wpa-average').bounding_box()
                    total = cell.locator('.overview-wpa-total').bounding_box()
                    self.assertGreaterEqual(total['x'], average['x'] + average['width'])
                    self.assertLess(total['y'], average['y'] + average['height'])
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
                  return [...svg.querySelectorAll('text')].filter(label => label.getClientRects().length).map(label => {
                    const r = label.getBoundingClientRect(), m = label.getScreenCTM();
                    return {text: label.textContent, size: parseFloat(getComputedStyle(label).fontSize) * Math.hypot(m.c, m.d),
                      inside: r.left >= chart.left && r.right <= chart.right && r.top >= chart.top && r.bottom <= chart.bottom};
                  });
                }''')
                for label in labels:
                    self.assertTrue(label['inside'], label)
                    self.assertGreaterEqual(label['size'], 11, label)
                badges = self.page.locator('.overview-chart .win-history-grade:visible').evaluate_all('''nodes => nodes.map(node => {
                  const r = node.getBoundingClientRect(); return {left: r.left, right: r.right};
                })''')
                for previous, following in zip(badges, badges[1:]):
                    self.assertLess(previous['right'], following['left'])
