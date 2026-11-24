"""Endgame policy regressions. All game positions are synthetic."""
import json
import unittest

import test_rack_draw_simulation as draw_tests


class EndgameSimulationTests(unittest.TestCase):
    setUpClass = classmethod(draw_tests.RackDrawSimulationTests.setUpClass.__func__)
    tearDownClass = classmethod(draw_tests.RackDrawSimulationTests.tearDownClass.__func__)
    payload = draw_tests.RackDrawSimulationTests.payload
    request = draw_tests.RackDrawSimulationTests.request

    GRID = [
        'CD........G....', '.......A.NOUN.Y', '.......L...TAME',
        '......YEAH...E.', '.........ab.yA.', '......LOB.OWED.',
        '..PROG..I.TEN.F', '....D..JOGS...I', '...LASSI.O..RUN',
        '.......V.R.MI..', '...C...E..HIPS.', '...A.BASE.A.E..',
        '...R.O..D.V....', '..RENT.LITER...', '.....H..T......',
    ]

    def test_last_draw_accounts_for_opponents_winning_block(self):
        grid = list(self.GRID)
        grid[0] = 'CD.............'
        result = self.request(self.payload(
            gridJson=json.dumps(grid), rack='EIIKQXG', playerScore=95,
            opponentScore=110, bagCount=1, finalTurnsRemaining=0,
            played={'action': 'place', 'position': 'K1', 'tiles': 'GO'},
            rackAfter='EIIKQXZ', iterations=8))
        self.assertFalse(result.get('error'), result)
        self.assertTrue(result['simulated'])
        # The opponent can play WET for44, blocking the89-point reply.
        # Greedy OUTWEAR scores51 but loses. Before the fix this returns1.
        self.assertEqual(result['afterWin'], 0)
        self.assertEqual(result.get('endgamePolicy'), 'crossplay-endgame-v1')

    def test_low_bag_policy_keeps_paired_samples_stable_across_cache_hits(self):
        grid = list(self.GRID)
        grid[0] = '..........G....'
        payload = self.payload(
            gridJson=json.dumps(grid), rack='WEAFTUO', playerScore=110,
            opponentScore=100, bagCount=2, finalTurnsRemaining=0,
            played={'action': 'pass', 'position': '', 'tiles': ''},
            iterations=2, numMoves=1)
        first = self.request(payload, 'simulateReviewMove')
        self.assertFalse(first.get('error'), first)
        self.assertTrue(first['simulated'])
        payload['numMoves'] = 2
        expanded = self.request(payload, 'simulateReviewMove')
        self.assertEqual(first['selected'], expanded['selected'])
        payload['seed'] = 0
        self.request(payload, 'simulateReviewMove')
        payload['seed'] = 123456789
        payload['numMoves'] = 1
        self.assertEqual(first, self.request(payload, 'simulateReviewMove'))

    def test_empty_bag_root_includes_winner_outside_static_shortlist(self):
        result = self.request(self.payload(
            gridJson=json.dumps(self.GRID), rack='WEAFTUO', playerScore=110,
            opponentScore=100, bagCount=0, finalTurnsRemaining=2,
            played={'action': 'pass', 'position': '', 'tiles': ''},
            iterations=1, numMoves=1), 'simulateReviewMove')
        self.assertFalse(result.get('error'), result)
        self.assertEqual(result['best']['win'], 1)
        self.assertEqual(result['best']['tiles'], 'WET')
        self.assertEqual(result['best']['position'], '15B')
        self.assertLessEqual(len(result['moves']), 2)

    def test_final_cache_preserves_physical_board_blanks(self):
        payload = self.payload(
            gridJson=json.dumps(self.GRID), rack='WEAFTUO', playerScore=87,
            opponentScore=0, bagCount=0, finalTurnsRemaining=2,
            played={'action': 'pass', 'position': '', 'tiles': ''},
            iterations=1, numMoves=1)
        ordinary = self.request(payload, 'simulateReviewMove')
        self.assertEqual(ordinary['selected']['equity'], -89)
        self.assertEqual(ordinary['selected']['win'], 0)
        # Preserve every visible letter and the physical tile distribution.
        # Move the blank A from (4,9) to (11,3). The best reply falls89->86.
        grid = [list(row) for row in self.GRID]
        grid[4][9] = 'A'
        grid[11][3] = 'a'
        payload['gridJson'] = json.dumps([''.join(row) for row in grid])
        reassigned = self.request(payload, 'simulateReviewMove')
        self.assertEqual(reassigned['selected']['equity'], -86)
        self.assertEqual(reassigned['selected']['win'], 1)

    def test_known_two_final_turns_prefers_wet_to_outwear(self):
        result = self.request(self.payload(
            gridJson=json.dumps(self.GRID), rack='WEAFTUO', playerScore=110,
            opponentScore=100, bagCount=0, finalTurnsRemaining=2,
            played={'action': 'place', 'position': '15B', 'tiles': 'WET'},
            iterations=8, numMoves=15), 'simulateReviewMove')
        self.assertFalse(result.get('error'), result)
        self.assertEqual(result['selected']['win'], 1)
        greedy = next(move for move in result['moves']
                      if move['position'] == 'D1' and move['tiles'] == 'OUTWEAR')
        self.assertEqual(greedy['win'], 0)
        self.assertEqual(result.get('endgamePolicy'), 'crossplay-endgame-v1')


if __name__ == '__main__':
    unittest.main()
