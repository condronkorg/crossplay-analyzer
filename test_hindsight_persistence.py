"""Known-rack export and independent hindsight cache checks with synthetic data."""
import copy
import json
import unittest
from collections import Counter
from pathlib import Path

from crossplay_server import (ANALYSIS_VERSION, TILE_COUNTS, CrossplayError, Handler,
                             backfill_review_racks, game_review)

POLICY = 'hindsight-state-v1/crossplay-endgame-v3'


def state_for(bag=21, racks=('?ACERST', 'DGILNOU'), pending=None):
    remaining = Counter(TILE_COUNTS)
    remaining.subtract(''.join(racks))
    remaining.subtract((pending or {}).get('returnedTiles', ''))
    tiles = ''.join(letter * count for letter, count in sorted(remaining.items()))
    board = tiles[:len(tiles) - bag] if bag else tiles
    board = board.replace('?', 'a').ljust(225, '.')
    state = {'grid': [board[i:i + 15] for i in range(0, 225, 15)],
             'racks': list(racks), 'scores': [110, 100], 'toMove': 1 if pending else 0,
             'bagCount': bag, 'finalTurnsRemaining': 0 if bag else 2}
    if pending:
        state['pendingDraw'] = pending
    return state


def entry(state=None, samples=192):
    state = state or state_for()
    identity = ['hindsight-state-v1', state, samples, POLICY]
    seed = 2166136261
    for char in json.dumps([identity[0], state, POLICY], separators=(',', ':')):
        seed = ((seed ^ ord(char)) * 16777619) & 0xffffffff
    pending = state.get('pendingDraw') or {}
    settled = state['bagCount'] - pending.get('count', 0) + len(pending.get('returnedTiles', ''))
    terminal = settled <= 21
    result = {'error': '', 'winSum': samples / 2, 'win': .5, 'samples': samples,
              'completed': samples, 'iterationsRequested': samples, 'iterationsCompleted': samples,
              'terminalSamples': samples if terminal else 0, 'offset': 0, 'seed': seed,
              'informationModel': 'hindsight-state-v1', 'policyVersion': POLICY,
              'plies': -1 if terminal else 2, 'playToEnd': terminal,
              'simulationMode': 'play-to-end' if terminal else '2-ply',
              'bagCount': state['bagCount'], 'settledBagCount': settled,
              'finalTurnsRemaining': state['finalTurnsRemaining'], 'simulated': True}
    return json.dumps(identity, separators=(',', ':')), result


class HindsightPersistenceTest(unittest.TestCase):
    def test_private_and_hindsight_summaries_persist_independent_failures(self):
        failed_quality = {'state': 'error', 'evaluation': 'win-probability-v1', 'points': [
            {'turnIndex': 0, 'category': 'unrated', 'loss': None,
             'reason': 'Engine failure.', 'retryable': True}]}
        done_quality = {'state': 'done', 'evaluation': 'win-probability-v1', 'points': []}
        failed_hindsight = {'state': 'error', 'informationModel': 'hindsight-state-v1',
                            'evaluation': POLICY, 'points': [
                                {'x': 0, 'mine': None, 'retryable': True, 'reason': 'Position failed.'}]}
        done_hindsight = {**failed_hindsight, 'state': 'done', 'points': []}
        # A running hindsight summary is omitted by the UI; quality must still save.
        for extra in ({'move_quality': failed_quality},
                      {'move_quality': failed_quality, 'win_trend': done_hindsight},
                      {'move_quality': done_quality, 'win_trend': failed_hindsight},
                      {'win_trend': failed_hindsight}):
            body = {'analysis_version': ANALYSIS_VERSION, **extra}
            with self.subTest(extra=extra):
                self.assertEqual(Handler.validate_game_analysis(body), extra)

    def test_independent_summaries_keep_legacy_and_individual_guards(self):
        failed_quality = {'state': 'error', 'evaluation': 'win-probability-v1', 'points': [
            {'category': 'unrated', 'loss': None, 'reason': 'Failed.', 'retryable': True}]}
        done_quality = {'state': 'done', 'evaluation': 'win-probability-v1', 'points': []}
        hindsight = {'state': 'error', 'informationModel': 'hindsight-state-v1',
                     'evaluation': POLICY, 'points': []}
        for extra in ({'move_quality': failed_quality, 'win_trend': {'state': 'done', 'points': []}},
                      {'move_quality': done_quality, 'win_trend': {'state': 'error', 'points': []}},
                      {'win_trend': {**hindsight, 'state': 'running'}},
                      {'win_trend': {**hindsight, 'points': [{'actual': True}]}},
                      {'win_trend': {**hindsight, 'evaluation': 'old-policy'}},
                      {'win_trend': {key: value for key, value in hindsight.items() if key != 'informationModel'}},
                      {'win_trend': {**hindsight, 'points': None}},
                      {'move_quality': {**failed_quality, 'points': []}, 'win_trend': hindsight}):
            with self.subTest(extra=extra), self.assertRaises(CrossplayError):
                Handler.validate_game_analysis({'analysis_version': ANALYSIS_VERSION, **extra})

    def test_import_exports_both_actual_hands(self):
        fixture = json.loads((Path(__file__).parent / 'tools/fixtures/nyt.json').read_text())
        game = fixture['subscription']['msgs'][0]['recent-games']['games'][0]
        turns = game_review(game, game['players'][0]['id'])['turns']
        for index, turn in enumerate(turns[:-1]):
            self.assertEqual(turn['opponent_rack'], turns[index + 1]['rack'])
        self.assertEqual(turns[-1]['opponent_rack'], turns[-2]['rack_after'])

    def test_import_distinguishes_unknown_and_empty_opponent_rack(self):
        fixture = json.loads((Path(__file__).parent / 'tools/fixtures/nyt.json').read_text())
        game = fixture['subscription']['msgs'][0]['recent-games']['games'][0]
        owner = game['players'][0]['id']
        game['game_data']['players'][0].pop('tray', None)
        self.assertIsNone(game_review(game, owner)['turns'][-1]['opponent_rack'])
        game['game_data']['players'][0]['tray'] = ''
        self.assertEqual(game_review(game, owner)['turns'][-1]['opponent_rack'], '')

    def test_backfill_ignores_unknown_player_identity(self):
        review = backfill_review_racks({'turns': [
            {'mine': None, 'rack': 'ABC', 'rack_after': 'DEF'},
            {'mine': True, 'rack': 'XYZ'},
        ]})
        self.assertIsNone(review['turns'][1]['opponent_rack'])

    def test_backfill_same_hand_and_terminal_empty_hand(self):
        original = {'turns': [
            {'mine': True, 'rack': 'ABC', 'rack_after': ''},
            {'mine': False, 'rack': 'DEF', 'rack_after': 'GHI'},
            {'mine': True, 'rack': '', 'rack_after': ''},
        ], 'stored_analysis': {'move_quality': {'sentinel': 1}}}
        review = backfill_review_racks(original)
        self.assertEqual([t['opponent_rack'] for t in review['turns']], ['DEF', '', 'GHI'])
        self.assertEqual(review['stored_analysis'], original['stored_analysis'])
        self.assertNotIn('opponent_rack', original['turns'][0])

    def test_backfill_does_not_skip_unknown_next_hand_or_conflict(self):
        review = backfill_review_racks({'turns': [
            {'mine': True, 'rack': 'ABC'},
            {'mine': False, 'rack': None},
            {'mine': True, 'rack': 'XYZ'},
            {'mine': False, 'rack': 'DEF'},
        ]})
        self.assertIsNone(review['turns'][0]['opponent_rack'])
        conflict = backfill_review_racks({'turns': [
            {'mine': False, 'rack': 'ABC', 'rack_after': 'DEF'},
            {'mine': True, 'rack': 'XYZ'},
            {'mine': False, 'rack': 'GHI'},
        ]})
        self.assertIsNone(conflict['turns'][1]['opponent_rack'])

    def test_hindsight_entries_save_beside_private_grade_cache(self):
        from test_background_analysis import review_analysis_entry
        old_key, old_result = review_analysis_entry()
        key, result = entry()
        body = {'analysis_version': ANALYSIS_VERSION, 'turn_analyses':
                {old_key: old_result, key: result}}
        self.assertEqual(Handler.validate_game_analysis(body)['turn_analyses'], body['turn_analyses'])

    def test_dense_eighty_turn_cache_fits_bounded_save(self):
        from test_background_analysis import review_analysis_entry
        old_key, old_result = review_analysis_entry()
        old_identity = json.loads(old_key)
        entries = {}
        for index in range(80 * 4):
            identity = copy.deepcopy(old_identity)
            identity[4] = index
            entries[json.dumps(identity, separators=(',', ':'))] = old_result
        for index in range(80 * 2 + 1):
            state = state_for()
            state['scores'][0] = 1000 + index
            for samples in (64, 192):
                key, result = entry(state, samples)
                entries[key] = result
        body = {'analysis_version': ANALYSIS_VERSION, 'turn_analyses': entries}
        self.assertEqual(len(entries), 642)
        self.assertLess(len(json.dumps(body).encode()), 2097152)
        self.assertEqual(Handler.validate_game_analysis(body)['turn_analyses'], entries)
        with self.assertRaises(CrossplayError):
            Handler.validate_game_analysis({'analysis_version': ANALYSIS_VERSION,
                                            'turn_analyses': {str(i): {} for i in range(1025)}})

    def test_supported_profiles_and_pending_refills(self):
        for bag, profiles in ((0, [64, 277]), (5, [64, 192]), (21, [64, 128, 192]),
                              (22, [64, 277])):
            for n in profiles:
                key, result = entry(state_for(bag), n)
                Handler.validate_hindsight_result(json.loads(key), result)
        for state in [state_for(4, ('ACER', 'DGILNOU'), {'player': 0, 'count': 3, 'returnedTiles': ''}),
                      state_for(3, ('ACER', 'DGILNOU'), {'player': 0, 'count': 3, 'returnedTiles': ''}),
                      state_for(4, ('ACERT', 'DGILNOU'), {'player': 0, 'count': 2, 'returnedTiles': 'S?'})]:
            # Canonical order places a physical blank before letters.
            if state.get('pendingDraw', {}).get('returnedTiles'):
                state['pendingDraw']['returnedTiles'] = '?S'
            settled = state['bagCount'] - state['pendingDraw']['count'] + len(state['pendingDraw']['returnedTiles'])
            key, result = entry(state, 277 if settled == 0 else 192)
            Handler.validate_hindsight_result(json.loads(key), result)

    def test_rejects_result_mismatch_partial_old_model_and_wrong_seed(self):
        key, result = entry()
        identity = json.loads(key)
        for changes in [{'win': .6}, {'samples': 191}, {'terminalSamples': 191},
                        {'offset': 16}, {'completed': True}, {'seed': result['seed'] ^ 1},
                        {'informationModel': 'win-probability-v1'}, {'plies': 2},
                        {'settledBagCount': 20}, {'policyVersion': 'hindsight-state-v1'},
                        {'winSum': float('nan')}, {'error': 'failed'}, {'simulated': False},
                        {'simulated': None}, {'finalTurnsRemaining': 1},
                        {'finalTurnsRemaining': None}, {'finalTurnsRemaining': False}]:
            with self.subTest(changes=changes), self.assertRaises(CrossplayError):
                Handler.validate_hindsight_result(identity, {**result, **changes})

    def test_rejects_noncanonical_or_physically_impossible_states(self):
        base = state_for()
        states = []
        for changes in [{'bagCount': 20}, {'toMove': True}, {'finalTurnsRemaining': 2},
                        {'racks': ['?ACERST', '?ACERST']}, {'scores': [True, 10]},
                        {'racks': ['ACERST?', 'DGILNOU']}, {'futureRack': 'ABCDEFG'}, {'pendingDraw': None},
                        {'pendingDraw': {'player': 0, 'count': 0, 'returnedTiles': ''}}]:
            states.append({**base, **changes})
        for state in states:
            key, result = entry(state)
            with self.subTest(state=state), self.assertRaises(CrossplayError):
                Handler.validate_hindsight_result(json.loads(key), result)

    def test_identity_must_change_with_known_rack(self):
        key, result = entry()
        identity = json.loads(key)
        identity[1]['racks'] = ['?ACERST', 'DGILNOV']
        with self.assertRaises(CrossplayError):
            Handler.validate_hindsight_result(identity, result)


if __name__ == '__main__':
    unittest.main()
