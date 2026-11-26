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
        self.assertEqual(result.get('endgamePolicy'), 'crossplay-endgame-v2')

    def test_every_low_bag_candidate_finishes_after_recorded_pass_or_swap(self):
        for bag_count in (1, 2):
            grid = list(self.GRID)
            grid[0] = ('.D' if bag_count == 1 else '..') + '........G....'
            for action, tiles in (('pass', ''), ('exchange', 'W' if bag_count == 1 else 'WE')):
                with self.subTest(bag=bag_count, action=action):
                    result = self.request(self.payload(
                        gridJson=json.dumps(grid), rack='WEAFTUO',
                        playerScore=110, opponentScore=100,
                        bagCount=bag_count, finalTurnsRemaining=0,
                        played={'action': action, 'position': '', 'tiles': tiles},
                        iterations=277, numMoves=15), 'simulateReviewMove')
                    self.assertFalse(result.get('error'), result)
                    self.assertTrue(result['simulated'])
                    self.assertEqual(result['selected']['action'], action)
                    # A future-policy cycle must not hide behind a completed
                    # selected move: every candidate must finish every sample.
                    self.assertTrue(result['moves'])
                    for move in result['moves']:
                        self.assertEqual(move['terminalSamples'], 277)

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
        self.assertEqual(result.get('endgamePolicy'), 'crossplay-endgame-v2')

    def test_final_score_reuse_preserves_score_dependent_wins_and_ties(self):
        result = self.page.evaluate('''grid => {
          return [[110,100],[210,100],[189,100],[100,110],[110,100]].map(scores=> {
            const r=testEngine.simulateReviewMove(JSON.stringify(grid),'WEAFTUO',
              ...scores,15,1,0,2,{action:'pass',position:'',tiles:''},123456789);
            if(r.error)throw new Error(r.error);
            return {selected:r.selected,best:r.best};
          });
        }''', self.GRID)
        self.assertEqual([r['selected']['win'] for r in result], [0, 1, 0.5, 0, 0])
        for row in result:
            self.assertEqual(row['selected']['equity'], -89)
            self.assertEqual(row['selected']['terminalSamples'], 1)
            self.assertEqual(row['best']['tiles'], 'WET')
            self.assertEqual(row['best']['equity'], -1)
        self.assertEqual(result[0], result[-1])


class NativeCacheTests(unittest.TestCase):
    def test_native_cache_retains_new_working_set_and_promotes_hits(self):
        import shutil
        import subprocess
        import tempfile
        from pathlib import Path

        compiler = shutil.which('clang++') or shutil.which('g++') or shutil.which('c++')
        if not compiler:
            self.skipTest('A C++17 compiler is needed for the native cache check.')
        patch = Path(__file__).with_name('tools').joinpath('quackle-review-simulation.patch').read_text()
        chunk = next(chunk for chunk in patch.split('diff --git ') if chunk.splitlines() and chunk.splitlines()[0].endswith('/crossplaycache.h'))
        header = '\n'.join(line[1:] for line in chunk.splitlines() if line.startswith('+') and not line.startswith('+++'))
        source = r"""
#include "crossplaycache.h"
#include <cassert>
#include <string>
int main() {
  Quackle::CrossplayLruCache<int> cache(16);
  for(int i=0;i<16;++i) cache.emplace("old:"+std::to_string(i),i);
  for(int i=0;i<8;++i) cache.emplace("hot:"+std::to_string(i),100+i);
  for(int i=0;i<8;++i) {
    const auto value=cache.find("hot:"+std::to_string(i));
    assert(value && *value==100+i);
  }
  assert(cache.size()==16);
  Quackle::CrossplayLruCache<int> small(2);
  small.emplace("a",1); small.emplace("b",2);
  assert(*small.find("a")==1);
  small.emplace("c",3);
  assert(small.find("b")==nullptr && *small.find("a")==1);
  small.emplace("a",9);
  assert(small.size()==2 && *small.find("a")==9);
  Quackle::CrossplayLruCache<int> physical(128);
  const std::string left("board\0a",7), right("board\0b",7);
  physical.emplace(left,11); physical.emplace(right,22);
  for(int i=0;i<100;++i) physical.emplace(std::string(500,'x')+std::to_string(i),i);
  assert(*physical.find(left)==11 && *physical.find(right)==22);
  Quackle::CrossplayLruCache<int> disabled(0);
  disabled.emplace("x",1);
  assert(disabled.size()==0);
}
"""
        with tempfile.TemporaryDirectory(prefix='crossplay-cache-test-') as directory:
            root = Path(directory)
            root.joinpath('crossplaycache.h').write_text(header)
            root.joinpath('cache-test.cpp').write_text(source)
            subprocess.run([compiler, '-std=c++17', '-O2', '-I', directory,
                            str(root / 'cache-test.cpp'), '-o', str(root / 'cache-test')],
                           check=True, capture_output=True, text=True)
            subprocess.run([str(root / 'cache-test')], check=True, capture_output=True, text=True)


if __name__ == '__main__':
    unittest.main()
