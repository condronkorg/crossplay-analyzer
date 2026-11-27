"""Known-state WP checks use synthetic, physically conserved positions."""
from collections import Counter
import unittest
import test_rack_draw_simulation as draw_tests
import test_endgame_simulation as endgame_tests
from crossplay_server import TILE_COUNTS


class HindsightSimulationTests(unittest.TestCase):
    setUpClass = classmethod(draw_tests.RackDrawSimulationTests.setUpClass.__func__)
    tearDownClass = classmethod(draw_tests.RackDrawSimulationTests.tearDownClass.__func__)

    def state(self, bag=0):
        grid = list(endgame_tests.EndgameSimulationTests.GRID)
        removed = 0
        for row in range(15):
            cells = list(grid[row])
            for col in range(15):
                if cells[col] != '.' and removed < bag:
                    cells[col] = '.'
                    removed += 1
            grid[row] = ''.join(cells)
        available = Counter(TILE_COUNTS)
        available.subtract('?' if c.islower() else c for r in grid for c in r if c != '.')
        available.subtract('WEAFTUO')
        other = ''.join(sorted(available.elements()))[:7]
        return dict(grid=grid, racks=['WEAFTUO', other], scores=[100, 110], toMove=0,
                    bagCount=bag, finalTurnsRemaining=0 if bag else 1)

    def evaluate(self, state, iterations=8, offset=0, count=8):
        return self.page.evaluate('''([s,n,o,c]) => {
          if(typeof testEngine.evaluateKnownStateBatch !== 'function')throw Error('Known-state API missing');
          return testEngine.evaluateKnownStateBatch(JSON.stringify(s),n,123456789,o,c);
        }''', [state, iterations, offset, count])

    def test_root_policy_plays_a_winning_move_instead_of_pass(self):
        r = self.evaluate(self.state())
        self.assertFalse(r.get('error'), r)
        self.assertEqual(r['win'], 1)
        self.assertEqual(r['terminalSamples'], 8)

    def test_split_offsets_and_rack_order_preserve_results(self):
        s = self.state(8)
        full = self.evaluate(s)
        a = self.evaluate(s, count=3)
        b = self.evaluate(s, offset=3, count=5)
        for r in [full,a,b]: self.assertFalse(r.get('error'), r)
        self.assertEqual(full['winSum'], a['winSum']+b['winSum'])
        s['racks'] = [r[::-1] for r in s['racks']]
        self.assertEqual(self.evaluate(s)['winSum'], full['winSum'])

    def test_invalid_inventory_and_pending_draw_are_rejected(self):
        for change in [dict(racks=['???????','???????']),dict(pendingDraw=dict(player=0,count=1,returnedTiles=''))]:
            r = self.evaluate({**self.state(), **change})
            self.assertTrue(r.get('error'), r)

    def test_pending_refill_is_a_chance_node_and_preserves_owner_orientation(self):
        s = self.state(4)
        # Hold one tile outside the rack for a one-tile exchange; draw first.
        returned = s['racks'][0][-1]
        s['racks'][0] = s['racks'][0][:-1]
        s['toMove'] = 1
        s['pendingDraw'] = dict(player=0,count=1,returnedTiles=returned)
        r = self.evaluate(s)
        self.assertFalse(r.get('error'), r)
        self.assertEqual(r['settledBagCount'], 4)
        self.assertEqual(r['terminalSamples'], 8)
        self.assertEqual(r['policyVersion'], 'hindsight-state-v1/crossplay-endgame-v3')

    def test_pending_last_refill_matches_settled_short_rack_state(self):
        for placed_count in (4, 7):
            s = self.state(4)
            used, retained = s['racks'][0][:placed_count], s['racks'][0][placed_count:]
            letters = iter(used)
            grid = [list(r) for r in s['grid']]
            remaining = placed_count
            for row in grid:
                for col in range(15):
                    if row[col] == '.' and remaining:
                        row[col] = next(letters)
                        remaining -= 1
            s['grid'] = [''.join(r) for r in grid]
            s['racks'][0] = retained
            s['toMove'] = 1
            s['pendingDraw'] = dict(player=0, count=4, returnedTiles='')
            available = Counter(TILE_COUNTS)
            available.subtract('?' if c.islower() else c for r in s['grid'] for c in r if c != '.')
            for rack in s['racks']: available.subtract(rack)
            settled = {**s, 'racks': [retained+''.join(sorted(available.elements())), s['racks'][1]],
                       'bagCount': 0, 'finalTurnsRemaining': 2}
            del settled['pendingDraw']
            a, b = self.evaluate(s), self.evaluate(settled)
            self.assertFalse(a.get('error'), a)
            self.assertFalse(b.get('error'), b)
            self.assertEqual(a['settledBagCount'], 0)
            self.assertEqual(a['winSum'], b['winSum'])

    def test_owner_orientation_and_empty_rack_tie(self):
        s = self.state()
        swapped = {**s, 'racks': s['racks'][::-1], 'scores': s['scores'][::-1], 'toMove': 1}
        self.assertEqual(self.evaluate(s)['win'], 1-self.evaluate(swapped)['win'])
        grid = [list(r) for r in s['grid']]
        remaining = list(s['racks'][0])
        for row in grid:
            for col in range(15):
                if row[col] == '.' and remaining: row[col] = remaining.pop()
        s.update(grid=[''.join(r) for r in grid], racks=['',s['racks'][1]],scores=[100,100])
        r = self.evaluate(s)
        self.assertFalse(r.get('error'), r)
        self.assertEqual(r['win'], .5)

    def test_known_state_does_not_change_private_review_or_draw(self):
        private = self.page.evaluate("""() => testEngine.simulateReviewDraw(JSON.stringify(Array(15).fill('.'.repeat(15))),
          'AEIRST?',30,25,1,8,86,0,{action:'place',position:'8G',tiles:'AT'},123456789,'AEEIRS?')""")
        known = self.evaluate(self.state(8))
        self.assertFalse(known.get('error'), known)
        again = self.page.evaluate("""() => testEngine.simulateReviewDraw(JSON.stringify(Array(15).fill('.'.repeat(15))),
          'AEIRST?',30,25,1,8,86,0,{action:'place',position:'8G',tiles:'AT'},123456789,'AEEIRS?')""")
        self.assertFalse(private.get('error'), private)
        self.assertEqual(private, again)

    def test_malformed_json_returns_error_without_breaking_engine(self):
        result = self.page.evaluate("() => testEngine.evaluateKnownStateBatch('{invalid',8,123,0,8)")
        self.assertTrue(result.get('error'), result)
        self.assertEqual(self.evaluate(self.state())['win'], 1)
