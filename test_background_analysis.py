"""Persistence and daily refresh checks with synthetic accounts and engine data."""
import copy
import json
import time
import unittest
from collections import Counter
from datetime import datetime, timezone
from unittest.mock import Mock

from crossplay_server import ANALYSIS_VERSION, TILE_COUNTS, CrossplayClient, CrossplayError, Handler


def review_analysis_entry(kind='review'):
    move_count, iterations = (5, 64) if kind == 'preview' else (15, 277)
    key = json.dumps(['quackle-review-v1', kind, ['.' * 15] * 15, 'CATERS?',
                      0, 0, 86, 0, move_count, iterations, 2, 21, 'CAT 8G 10', '',
                      'paired-review-v1'], separators=(',', ':'))
    moves = [
        {'action': 'place', 'position': '8G', 'tiles': 'CAT', 'score': 10,
         'equity': 11, 'staticEquity': 12, 'win': .4, 'samples': iterations, 'terminalSamples': 0},
        {'action': 'place', 'position': '8G', 'tiles': 'CATS', 'score': 15,
         'equity': 17, 'staticEquity': 18, 'win': .6, 'samples': iterations, 'terminalSamples': 0},
    ]
    static_moves = [{**move, 'equity': move['staticEquity']} for move in moves]
    result = {'simulated': True, 'samplingVersion': 'paired-review-v1', 'seed': 123,
              'moves': moves, 'selected': moves[0], 'best': moves[1],
              'staticMoves': static_moves, 'bestStaticEquity': 18,
              'iterationsRequested': iterations, 'iterationsCompleted': iterations,
              'candidateCount': 2, 'totalPlayaheads': iterations * 2,
              'bagCount': 86, 'finalTurnsRemaining': 0, 'playToEnd': False,
              'simulationMode': '2-ply', 'plies': 2, 'winModel': 'bogowin'}
    return key, result


def draw_analysis_entry(kind='draw'):
    iterations = 64 if kind == 'draw-preview' else 277
    key = json.dumps(['quackle-rack-draw-v1', kind, ['.' * 15] * 15, 'CATERS?',
                      0, 0, 86, 0, iterations, 2, 21, 'CAT 8G 10', '',
                      'ERS?AIN', 'rack-draw-v1'], separators=(',', ':'))
    result = {'simulated': True, 'samplingVersion': 'rack-draw-v1', 'seed': 123,
              'rackAfter': 'ERS?AIN', 'beforeWin': .4, 'afterWin': .55, 'drawCount': 3,
              'iterationsRequested': iterations, 'iterationsCompleted': iterations,
              'bagCount': 86, 'finalTurnsRemaining': 0, 'playToEnd': False,
              'simulationMode': '2-ply', 'plies': 2, 'winModel': 'bogowin',
              'selected': {'action': 'place', 'position': '8G', 'tiles': 'CAT'}}
    return key, result


class BackgroundAnalysisTest(unittest.TestCase):
    def setUp(self):
        self.handler = object.__new__(Handler)
        self.handler.supabase_data_request = Mock()
        self.handler.saved_review_status = Mock(return_value='completed')
        self.handler.persistent_games = Mock(return_value=[{'id': 'oldergame'}, {'id': 'newgame1'}])
        self.handler.upsert_game_summaries = Mock()
        self.client = Mock()
        self.client.me.return_value = {'user_id': 'synthetic-nyt-user'}
        self.client.recent_games.return_value = []
        self.handler.current_session = Mock(return_value=('account-a', self.client))

    def test_low_bag_review_and_draw_saves_require_matching_policy_identity(self):
        for kind in ('review', 'preview', 'draw', 'draw-preview'):
            key, result = review_analysis_entry(kind) if kind in ('review', 'preview') else draw_analysis_entry(kind)
            identity = json.loads(key)
            identity[6] = result['bagCount'] = 6
            inventory = Counter(TILE_COUNTS)
            inventory.subtract(identity[3])
            inventory.subtract('BCDEFGHIJKLMN')
            cells = ''.join(letter * count for letter, count in inventory.items()).replace('?', 'a').ljust(225, '.')
            identity[2] = [cells[index:index + 15] for index in range(0, 225, 15)]
            result.update(playToEnd=True, plies=-1, simulationMode='play-to-end', winModel='terminal')
            if kind in ('review', 'preview'):
                for move in result['moves']:
                    move['terminalSamples'] = move['samples']
            else:
                identity[11], identity[13] = 'PASS 0', identity[3]
                result.update(selected={'action': 'pass', 'position': '', 'tiles': ''},
                              rackAfter=identity[3], drawCount=0, afterWin=result['beforeWin'])
            for saved_identity, policy in ((identity, None), (identity, 'crossplay-endgame-v1'),
                                           (identity + ['crossplay-endgame-v1'], None),
                                           (identity + ['crossplay-endgame-v1'], 'old-policy'),
                                           (identity + ['old-policy'], 'crossplay-endgame-v1')):
                entry = dict(result)
                if policy is not None:
                    entry['endgamePolicy'] = policy
                with self.subTest(kind=kind, identity=saved_identity[-1], policy=policy), self.assertRaises(CrossplayError):
                    self.handler.validate_game_analysis({'analysis_version': ANALYSIS_VERSION,
                        'turn_analyses': {json.dumps(saved_identity): entry}})
            identity.append('crossplay-endgame-v1')
            result['endgamePolicy'] = 'crossplay-endgame-v1'
            self.handler.validate_game_analysis({'analysis_version': ANALYSIS_VERSION,
                'turn_analyses': {json.dumps(identity): result}})

    def test_low_bag_sampled_position_saves_require_matching_policy(self):
        for kind in ('simulation', 'trend'):
            identity = ['quackle-position-v1', kind, ['.' * 15] * 15, 'CATERS?',
                        0, 0, 6, 0, 15 if kind == 'simulation' else 1,
                        277 if kind == 'simulation' else 80, 2, 21]
            result = {'simulated': True, 'bagCount': 6, 'moves': [{'win': .5}]}
            with self.assertRaises(CrossplayError):
                self.handler.validate_game_analysis({'analysis_version': ANALYSIS_VERSION,
                    'turn_analyses': {json.dumps(identity): result}})
            identity.append('crossplay-endgame-v1')
            with self.assertRaises(CrossplayError):
                self.handler.validate_game_analysis({'analysis_version': ANALYSIS_VERSION,
                    'turn_analyses': {json.dumps(identity): result}})
            result['endgamePolicy'] = 'crossplay-endgame-v1'
            self.handler.validate_game_analysis({'analysis_version': ANALYSIS_VERSION,
                'turn_analyses': {json.dumps(identity): result}})

    def test_recorded_pass_markers_round_trip_review_and_zero_draw_results(self):
        for action in ('pass -- 0', ' PASS\t--\t-5 ', 'PASS', 'pass 0'):
            for kind in ('preview', 'review'):
                key, result = review_analysis_entry(kind)
                identity = json.loads(key)
                identity[12] = action
                for moves in (result['moves'], result['staticMoves']):
                    moves[0].update(action='pass', position='', tiles='', score=0)
                body = {'analysis_version': ANALYSIS_VERSION,
                        'turn_analyses': {json.dumps(identity): result}}
                with self.subTest(action=action, kind=kind):
                    self.assertEqual(self.handler.validate_game_analysis(body)['turn_analyses'], body['turn_analyses'])
            for kind in ('draw-preview', 'draw'):
                key, result = draw_analysis_entry(kind)
                identity = json.loads(key)
                identity[11] = action
                identity[13] = identity[3]
                result.update(rackAfter=identity[3], drawCount=0, afterWin=result['beforeWin'],
                              selected={'action': 'pass', 'position': '', 'tiles': ''})
                body = {'analysis_version': ANALYSIS_VERSION,
                        'turn_analyses': {json.dumps(identity): result}}
                with self.subTest(action=action, kind=kind):
                    self.assertEqual(self.handler.validate_game_analysis(body)['turn_analyses'], body['turn_analyses'])
        for action in ('PASS --', 'PASS -- -- 0', 'PASS -- 0 extra', 'PASS AB 0', 'PASS -- 1.5'):
            with self.subTest(action=action):
                self.assertFalse(Handler.recorded_move_matches(['.' * 15] * 15, action, '',
                    {'action': 'pass', 'position': '', 'tiles': ''}))

    def test_draw_estimates_round_trip_both_profiles_without_replacing_moves(self):
        review_key, review_result = review_analysis_entry()
        for kind in ('draw', 'draw-preview'):
            key, result = draw_analysis_entry(kind)
            body = {'analysis_version': ANALYSIS_VERSION,
                    'turn_analyses': {key: result, review_key: review_result}}
            snapshot = self.handler.validate_game_analysis(body)
            self.assertEqual(snapshot['turn_analyses'], body['turn_analyses'])
            self.handler.save_game_analysis('account-a', 'game0001', body)
            self.assertEqual(self.handler.supabase_data_request.call_args.kwargs['payload']['snapshot'], snapshot)

    def test_draw_estimate_rejects_wrong_identity_profile_samples_and_numbers(self):
        key, original = draw_analysis_entry()
        cases = [
            {'beforeWin': float('nan')}, {'afterWin': float('inf')}, {'afterWin': 1.01},
            {'beforeWin': -.01}, {'beforeWin': True}, {'drawCount': True}, {'drawCount': 2},
            {'iterationsCompleted': 64}, {'iterationsRequested': 64}, {'seed': -1},
            {'seed': 2 ** 32}, {'seed': True}, {'bagCount': 85}, {'finalTurnsRemaining': 1},
            {'playToEnd': True}, {'plies': -1}, {'simulationMode': 'play-to-end'},
            {'winModel': 'terminal'}, {'simulated': 1}, {'samplingVersion': 'paired-review-v1'},
            {'rackAfter': 'ERS?ANE'}, {'selected': {'action': 'place', 'position': '8H', 'tiles': 'CAT'}},
        ]
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(CrossplayError):
                self.handler.validate_game_analysis({'analysis_version': ANALYSIS_VERSION,
                    'turn_analyses': {key: {**original, **changes}}})
        for index, value in [(0, 'bad'), (1, 'preview'), (8, 64), (9, 3), (10, 20),
                             (11, 'CAT 8H 10'), (13, 'ERS?ANE'), (14, 'bad')]:
            identity = json.loads(key)
            identity[index] = value
            with self.subTest(index=index), self.assertRaises(CrossplayError):
                self.handler.validate_game_analysis({'analysis_version': ANALYSIS_VERSION,
                    'turn_analyses': {json.dumps(identity): original}})

    def test_draw_estimate_requires_retained_tiles_and_available_physical_draws(self):
        key, original = draw_analysis_entry()
        for rack_after in ('ERS?ZZZ', 'ERS?AAAQ', 'ERS?AA', 'ERSAINT', '', None):
            identity = json.loads(key)
            identity[13] = rack_after
            result = {**original, 'rackAfter': rack_after}
            with self.subTest(rack_after=rack_after), self.assertRaises(CrossplayError):
                self.handler.validate_game_analysis({'analysis_version': ANALYSIS_VERSION,
                    'turn_analyses': {json.dumps(identity): result}})
        # All three physical blanks are already on the board or in the current rack.
        identity = json.loads(key)
        grid = identity[2].copy()
        grid[0] = 'ab' + '.' * 13
        identity[2] = grid; identity[6] = 84; identity[13] = 'ERS??AI'
        with self.assertRaises(CrossplayError):
            self.handler.validate_game_analysis({'analysis_version': ANALYSIS_VERSION,
                'turn_analyses': {json.dumps(identity): {**original, 'bagCount': 84, 'rackAfter': 'ERS??AI'}}})

    def test_pass_draw_estimate_must_have_zero_effect_and_same_rack(self):
        key, original = draw_analysis_entry()
        identity = json.loads(key)
        identity[11] = 'PASS 0'
        identity[13] = identity[3]
        result = {**original, 'selected': {'action': 'pass', 'position': '', 'tiles': ''},
                  'rackAfter': identity[3], 'drawCount': 0, 'afterWin': original['beforeWin']}
        body = {'analysis_version': ANALYSIS_VERSION, 'turn_analyses': {json.dumps(identity): result}}
        self.handler.validate_game_analysis(body)
        result['afterWin'] += .01
        with self.assertRaises(CrossplayError):
            self.handler.validate_game_analysis(body)

    def test_exchange_draw_uses_explicit_tiles_and_cannot_redraw_the_only_discarded_copy(self):
        key, original = draw_analysis_entry()
        identity = json.loads(key)
        identity[3] = 'CATERSZ'
        identity[11:14] = ['swap -- 0', 'Z', 'CATERSA']
        result = {**original, 'rackAfter': 'CATERSA', 'drawCount': 1,
                  'selected': {'action': 'exchange', 'position': '', 'tiles': 'Z'}}
        self.handler.validate_game_analysis({'analysis_version': ANALYSIS_VERSION,
            'turn_analyses': {json.dumps(identity): result}})
        identity[13] = 'CATERSZ'; result['rackAfter'] = 'CATERSZ'
        with self.assertRaises(CrossplayError):
            self.handler.validate_game_analysis({'analysis_version': ANALYSIS_VERSION,
                'turn_analyses': {json.dumps(identity): result}})

    def test_low_bag_exchange_draw_round_trips_both_profiles(self):
        # Synthetic inventory: 80 board tiles, 7 mover tiles, 6 in the bag,
        # and 7 hidden opponent tiles. Four exchanged tiles fit in that bag.
        rack = 'AAEIRST'
        inventory = Counter(TILE_COUNTS)
        inventory.subtract(rack)
        inventory.subtract('BCDEFGHIJKLMN')
        board_tiles = ''.join(letter * count for letter, count in inventory.items())
        self.assertEqual(len(board_tiles), 80)
        board_tiles = board_tiles.replace('?', 'a')
        cells = board_tiles + '.' * (225 - len(board_tiles))
        grid = [cells[index:index + 15] for index in range(0, 225, 15)]
        saved_entries = {}
        for kind in ('draw-preview', 'draw'):
            key, original = draw_analysis_entry(kind)
            identity = json.loads(key)
            identity[2:4] = [grid, rack]
            identity[6:8] = [6, 0]
            identity[11:14] = ['swap -- 0', 'AIRS', 'AETBCDE']
            identity.append('crossplay-endgame-v1')
            result = {**original, 'rackAfter': 'AETBCDE', 'drawCount': 4,
                      'endgamePolicy': 'crossplay-endgame-v1',
                      'bagCount': 6, 'playToEnd': True, 'plies': -1,
                      'simulationMode': 'play-to-end', 'winModel': 'terminal',
                      'selected': {'action': 'exchange', 'position': '', 'tiles': 'AIRS'}}
            key = json.dumps(identity)
            body = {'analysis_version': ANALYSIS_VERSION, 'turn_analyses': {key: result}}
            self.handler.save_game_analysis('account-a', 'synthetic-swap', body)
            saved_entries.update(self.handler.supabase_data_request.call_args.kwargs['payload']['snapshot']['turn_analyses'])
            # There are only six replacement tiles. Exchanging all seven is invalid.
            invalid_identity = copy.deepcopy(identity)
            invalid_identity[12:14] = [rack, 'BCDEFG']
            invalid_result = {**result, 'rackAfter': 'BCDEFG', 'drawCount': 6,
                              'selected': {'action': 'exchange', 'position': '', 'tiles': rack}}
            with self.assertRaises(CrossplayError):
                self.handler.validate_game_analysis({'analysis_version': ANALYSIS_VERSION,
                    'turn_analyses': {json.dumps(invalid_identity): invalid_result}})
        self.handler.supabase_data_request.return_value = [{
            'analysis_version': ANALYSIS_VERSION, 'turn_analyses': saved_entries}]
        restored = self.handler.saved_game_analysis('account-a', 'synthetic-swap')
        self.assertEqual(restored['turn_analyses'], saved_entries)
        self.assertEqual(len(restored['turn_analyses']), 2)

    def test_low_bag_move_results_reject_exchanges_larger_than_bag(self):
        for kind in ('preview', 'review'):
            key, result = review_analysis_entry(kind)
            identity = json.loads(key)
            inventory = Counter(TILE_COUNTS)
            inventory.subtract(identity[3])
            inventory.subtract('BCDEFGHIJKLMN')
            cells = ''.join(letter * count for letter, count in inventory.items()).replace('?', 'a')
            self.assertEqual(len(cells), 80)
            cells += '.' * (225 - len(cells))
            identity[2] = [cells[index:index + 15] for index in range(0, 225, 15)]
            identity[6] = result['bagCount'] = 6
            identity[12:14] = ['swap -- 0', 'CATS']
            identity.append('crossplay-endgame-v1')
            result.update(playToEnd=True, plies=-1, simulationMode='play-to-end', winModel='terminal',
                          endgamePolicy='crossplay-endgame-v1')
            for move in result['moves'] + result['staticMoves']:
                move.update(action='exchange', position='', tiles='CATS', terminalSamples=move['samples'])
            # A single legal comparison move keeps its static identity unique.
            result['moves'] = result['moves'][:1]
            result['staticMoves'] = result['staticMoves'][:1]
            result.update(selected=result['moves'][0], best=result['moves'][0],
                          candidateCount=1, totalPlayaheads=result['iterationsCompleted'],
                          bestStaticEquity=result['staticMoves'][0]['equity'])
            body = {'analysis_version': ANALYSIS_VERSION, 'turn_analyses': {json.dumps(identity): result}}
            self.handler.validate_game_analysis(body)
            for move in result['moves'] + result['staticMoves']:
                move['tiles'] = identity[3]
            identity[13] = identity[3]
            with self.subTest(kind=kind), self.assertRaises(CrossplayError):
                self.handler.validate_game_analysis({'analysis_version': ANALYSIS_VERSION,
                    'turn_analyses': {json.dumps(identity): result}})

    def test_previous_engine_analysis_version_is_not_accepted(self):
        with self.assertRaises(CrossplayError) as caught:
            self.handler.validate_game_analysis({'analysis_version': 'quackle-equity-v1', 'turn_analyses': {}})
        self.assertEqual(caught.exception.status, 400)

    def test_draw_estimate_counts_assigned_blank_and_partial_bag_refill(self):
        key, original = draw_analysis_entry()
        identity = json.loads(key)
        identity[11] = 'CAt 8G 10'
        identity[13] = 'ERSTAIN'
        result = {**original, 'rackAfter': 'ERSTAIN',
                  'selected': {'action': 'place', 'position': '8G', 'tiles': 'CAt'}}
        self.handler.validate_game_analysis({'analysis_version': ANALYSIS_VERSION,
            'turn_analyses': {json.dumps(identity): result}})

        # A physically possible 85-tile board leaves seven mover tiles, six
        # hidden opponent tiles and only two tiles in the bag.
        off_board = Counter('CATERS?' + 'AINLOOU' + 'E')
        board_tiles = ''.join(letter * (count - off_board[letter])
                              for letter, count in TILE_COUNTS.items()).replace('?', 'a')
        self.assertEqual(len(board_tiles), 85)
        grid = [board_tiles[index:index + 15].ljust(15, '.') for index in range(0, 225, 15)]
        identity = json.loads(key)
        identity[2] = grid
        identity[6] = 2
        identity[11] = 'CAT 7A 10'; identity[13] = 'ERS?AI'
        identity.append('crossplay-endgame-v1')
        result = {**original, 'rackAfter': 'ERS?AI', 'drawCount': 2, 'bagCount': 2,
                  'endgamePolicy': 'crossplay-endgame-v1',
                  'playToEnd': True, 'simulationMode': 'play-to-end', 'winModel': 'terminal', 'plies': -1,
                  'selected': {'action': 'place', 'position': '7A', 'tiles': 'CAT'}}
        self.handler.validate_game_analysis({'analysis_version': ANALYSIS_VERSION,
            'turn_analyses': {json.dumps(identity): result}})
        identity[13] = 'ERS?AIN'; result['rackAfter'] = 'ERS?AIN'; result['drawCount'] = 3
        with self.assertRaises(CrossplayError):
            self.handler.validate_game_analysis({'analysis_version': ANALYSIS_VERSION,
                'turn_analyses': {json.dumps(identity): result}})

    def test_saved_review_backfills_only_the_next_rack_of_the_same_player(self):
        review = {'id': 'game0001', 'status': 'completed', 'turns': [
            {'mine': True, 'rack': 'CATERS?'}, {'mine': False, 'rack': 'TILEDSO'},
            {'mine': True, 'rack': 'ERS?AIN'}, {'mine': False, 'rack': 'ILEDSOA'}]}
        original = copy.deepcopy(review)
        self.handler.supabase_data_request.return_value = [{'review': review, 'share_token': 'share'}]
        saved = self.handler.saved_game_review('account-a', 'game0001')
        self.assertEqual(saved['turns'][0].get('rack_after'), 'ERS?AIN')
        self.assertEqual(saved['turns'][1].get('rack_after'), 'ILEDSOA')
        self.assertIsNone(saved['turns'][2].get('rack_after'))
        self.assertIsNone(saved['turns'][3].get('rack_after'))
        self.assertEqual(review, original)
        review['turns'].insert(2, {'mine': True, 'rack': None})
        saved = self.handler.saved_game_review('account-a', 'game0001')
        self.assertIsNone(saved['turns'][0].get('rack_after'))

    def test_incremental_results_use_atomic_merge_scoped_to_owner(self):
        body = {'analysis_version': ANALYSIS_VERSION, 'turn_analyses': {
            'synthetic-position': {'simulated': False, 'moves': [{'tiles': 'CAT', 'equity': 4}]}}}
        original = copy.deepcopy(body)
        self.handler.save_game_analysis('account-a', 'game0001', body)
        args, kwargs = self.handler.supabase_data_request.call_args
        self.assertEqual(args, ('POST', 'rpc/save_crossplay_analysis'))
        self.assertEqual(kwargs['payload']['account_id'], 'account-a')
        self.assertEqual(kwargs['payload']['game_id'], 'game0001')
        self.assertEqual(kwargs['payload']['snapshot'], {'turn_analyses': body['turn_analyses']})
        self.assertEqual(body, original)
        # Supplying only a trend must not clear previously saved grades/candidates.
        self.handler.save_game_analysis('account-a', 'game0001', {
            'analysis_version': ANALYSIS_VERSION, 'win_trend': {'state': 'done', 'points': []}})
        self.assertNotIn('move_quality', self.handler.supabase_data_request.call_args.kwargs['payload']['snapshot'])

    def test_missing_or_unfinished_game_cannot_save_results(self):
        for status in (None, 'active'):
            self.handler.saved_review_status.return_value = status
            with self.assertRaises(CrossplayError):
                self.handler.save_game_analysis('account-b', 'game0001', {'analysis_version': ANALYSIS_VERSION})
        self.handler.supabase_data_request.assert_not_called()

    def test_save_check_reads_only_the_owner_review_status(self):
        handler = object.__new__(Handler)
        handler.supabase_data_request = Mock(return_value=[{'status': 'completed'}])
        self.assertEqual(handler.saved_review_status('account-a', 'game0001'), 'completed')
        args = handler.supabase_data_request.call_args.args
        self.assertEqual(args[:2], ('GET', 'crossplay_games'))
        self.assertEqual(args[2]['select'], 'status:review->>status')
        self.assertEqual(args[2]['user_id'], 'eq.account-a')
        handler.supabase_data_request.return_value = []
        self.assertIsNone(handler.saved_review_status('account-a', 'game0001'))

    def test_incomplete_and_wrong_version_results_are_rejected(self):
        for body in ({'analysis_version': 'old'},
                     {'move_quality': {'state': 'running', 'points': []}},
                     {'move_quality': None}, {'turn_analyses': []},
                     {'turn_analyses': {'bad': {'error': 'engine failed', 'moves': []}}}):
            with self.subTest(body=body), self.assertRaises(CrossplayError):
                self.handler.validate_game_analysis({'analysis_version': ANALYSIS_VERSION, **body})

    def test_review_probabilities_round_trip_without_replacing_previous_turns(self):
        key, result = review_analysis_entry()
        body = {'analysis_version': ANALYSIS_VERSION, 'turn_analyses': {key: result}}
        self.handler.save_game_analysis('account-a', 'game0001', body)
        snapshot = self.handler.supabase_data_request.call_args.kwargs['payload']['snapshot']
        self.assertEqual(snapshot, {'turn_analyses': {key: result}})
        self.assertNotIn('move_quality', snapshot)
        handler = object.__new__(Handler)
        handler.supabase_data_request = Mock(return_value=[{
            'analysis_version': ANALYSIS_VERSION, 'turn_analyses': snapshot['turn_analyses']}])
        self.assertEqual(handler.saved_game_analysis('account-a', 'game0001')['turn_analyses'], {key: result})
        query = handler.supabase_data_request.call_args.args[2]
        self.assertEqual(query['user_id'], 'eq.account-a')

    def test_preview_and_full_results_round_trip_as_distinct_cache_entries(self):
        preview_key, preview = review_analysis_entry('preview')
        full_key, full = review_analysis_entry()
        snapshots = []
        for key, result in ((preview_key, preview), (full_key, full)):
            self.handler.save_game_analysis('account-a', 'game0001', {
                'analysis_version': ANALYSIS_VERSION, 'turn_analyses': {key: result}})
            call = self.handler.supabase_data_request.call_args
            self.assertEqual(call.args, ('POST', 'rpc/save_crossplay_analysis'))
            snapshot = call.kwargs['payload']['snapshot']
            self.assertEqual(snapshot, {'turn_analyses': {key: result}})
            snapshots.append(snapshot)
        combined = {**snapshots[0]['turn_analyses'], **snapshots[1]['turn_analyses']}
        self.handler.validate_game_analysis({'analysis_version': ANALYSIS_VERSION, 'turn_analyses': combined})
        handler = object.__new__(Handler)
        handler.supabase_data_request = Mock(return_value=[{
            'analysis_version': ANALYSIS_VERSION, 'turn_analyses': combined}])
        saved = handler.saved_game_analysis('account-a', 'game0001')['turn_analyses']
        self.assertEqual(saved, {preview_key: preview, full_key: full})
        self.assertEqual(saved[preview_key]['iterationsCompleted'], 64)
        self.assertEqual(saved[full_key]['iterationsCompleted'], 277)

    def test_preview_results_require_the_preview_profile_and_complete_metadata(self):
        preview_key, preview = review_analysis_entry('preview')
        full_key, full = review_analysis_entry()
        for key, result in ((preview_key, full), (full_key, preview)):
            with self.subTest(key=key), self.assertRaises(CrossplayError):
                self.handler.validate_game_analysis({'analysis_version': ANALYSIS_VERSION, 'turn_analyses': {key: result}})
        for change in (
                lambda result: result.update(iterationsCompleted=63),
                lambda result: result.update(totalPlayaheads=64),
                lambda result: result.update(candidateCount=3),
                lambda result: result.update(samplingVersion='old'),
                lambda result: result.pop('samplingVersion'),
                lambda result: result.update(best=result['selected']),
                lambda result: result['moves'][0].update(samples=277),
                lambda result: result['moves'][0].update(tiles='cAT'),
                lambda result: result['staticMoves'][0].update(equity=999)):
            key, result = review_analysis_entry('preview')
            change(result)
            with self.subTest(change=change), self.assertRaises(CrossplayError):
                self.handler.validate_game_analysis({'analysis_version': ANALYSIS_VERSION, 'turn_analyses': {key: result}})
        for index, value in ((0, 'quackle-position-v1'), (1, 'other'), (8, 15), (9, 277), (10, 1), (11, 20)):
            identity = json.loads(preview_key)
            identity[index] = value
            with self.subTest(index=index), self.assertRaises(CrossplayError):
                self.handler.validate_game_analysis({
                    'analysis_version': ANALYSIS_VERSION, 'turn_analyses': {json.dumps(identity): preview}})

    def test_preview_and_full_profiles_limit_candidates_and_require_terminal_samples(self):
        for kind, maximum in (('preview', 6), ('review', 16)):
            key, result = review_analysis_entry(kind)
            for count in (maximum, maximum + 1):
                extended = copy.deepcopy(result)
                for index in range(count - len(extended['moves'])):
                    extended['moves'].append({**extended['moves'][1], 'position': f'{index + 1}A'})
                extended['staticMoves'] = [{**move, 'equity': move['staticEquity']} for move in extended['moves']]
                extended['candidateCount'] = count
                extended['totalPlayaheads'] = count * extended['iterationsRequested']
                body = {'analysis_version': ANALYSIS_VERSION, 'turn_analyses': {key: extended}}
                with self.subTest(kind=kind, count=count):
                    if count == maximum:
                        self.handler.validate_game_analysis(body)
                    else:
                        with self.assertRaises(CrossplayError):
                            self.handler.validate_game_analysis(body)
            identity = json.loads(key)
            identity[6:8] = [0, 1]
            identity.append('crossplay-endgame-v1')
            result.update(bagCount=0, finalTurnsRemaining=1, playToEnd=True,
                          simulationMode='play-to-end', plies=-1, winModel='terminal',
                          endgamePolicy='crossplay-endgame-v1')
            body = {'analysis_version': ANALYSIS_VERSION, 'turn_analyses': {json.dumps(identity): result}}
            with self.subTest(kind=kind), self.assertRaises(CrossplayError):
                self.handler.validate_game_analysis(body)
            for move in result['moves']:
                move['terminalSamples'] = result['iterationsRequested']
            self.handler.validate_game_analysis(body)

    def test_review_probabilities_reject_invalid_or_incomplete_values(self):
        changes = [
            lambda value: value.update(seed=-1),
            lambda value: value.update(seed=2 ** 32),
            lambda value: value.update(seed=True),
            lambda value: value.update(samplingVersion='old'),
            lambda value: value.update(iterationsCompleted=276),
            lambda value: value.update(iterationsRequested=10),
            lambda value: value.update(bagCount=85),
            lambda value: value.update(finalTurnsRemaining=1),
            lambda value: value.update(playToEnd=True),
            lambda value: value.update(winModel='terminal'),
            lambda value: value.update(plies=-1),
            lambda value: value.update(candidateCount=1),
            lambda value: value.update(totalPlayaheads=277),
            lambda value: value['moves'][0].update(win=float('nan')),
            lambda value: value['moves'][0].update(win=float('inf')),
            lambda value: value['moves'][0].update(win=-.1),
            lambda value: value['moves'][0].update(win=1.1),
            lambda value: value['moves'][0].update(win=True),
            lambda value: value['moves'][0].update(samples=0),
            lambda value: value['moves'][0].pop('staticEquity'),
            lambda value: value.update(selected={'win': .5}),
            lambda value: value.update(best=value['moves'][0]),
            lambda value: value.update(bestStaticEquity=1),
            lambda value: value.update(staticMoves=[]),
            lambda value: value['staticMoves'][0].update(equity=float('inf')),
        ]
        for change in changes:
            key, result = review_analysis_entry()
            change(result)
            with self.subTest(change=changes.index(change)), self.assertRaises(CrossplayError) as caught:
                self.handler.validate_game_analysis({
                    'analysis_version': ANALYSIS_VERSION, 'turn_analyses': {key: result}})
            self.assertEqual(caught.exception.status, 400)

    def test_review_result_requires_matching_cache_identity_and_terminal_samples(self):
        key, result = review_analysis_entry()
        malformed = [key.replace('quackle-review-v1', 'old-review'), 'not-json',
                     json.dumps(['quackle-review-v1', 'review'])]
        for invalid in malformed:
            with self.subTest(key=invalid), self.assertRaises(CrossplayError):
                self.handler.validate_game_analysis({
                    'analysis_version': ANALYSIS_VERSION, 'turn_analyses': {invalid: result}})
        parts = json.loads(key)
        parts[6] = 21
        parts.append('crossplay-endgame-v1')
        key = json.dumps(parts)
        result.update(bagCount=21, playToEnd=True, simulationMode='play-to-end', plies=-1, winModel='terminal',
                      endgamePolicy='crossplay-endgame-v1')
        with self.assertRaises(CrossplayError):
            self.handler.validate_game_analysis({'analysis_version': ANALYSIS_VERSION, 'turn_analyses': {key: result}})
        for move in result['moves']:
            move['terminalSamples'] = 277
        self.handler.validate_game_analysis({'analysis_version': ANALYSIS_VERSION, 'turn_analyses': {key: result}})

    def test_recorded_move_must_match_selected_physical_tiles_including_blanks(self):
        key, result = review_analysis_entry()
        result['selected'] = result['moves'][1]
        with self.assertRaises(CrossplayError):
            self.handler.validate_game_analysis({'analysis_version': ANALYSIS_VERSION, 'turn_analyses': {key: result}})
        grid = ['.' * 15] * 15
        grid[7] = '......CAT......'
        place = {'action': 'place', 'position': '8F', 'tiles': 's...'}
        self.assertTrue(Handler.recorded_move_matches(grid, 's(CAT) 8F 5', '', place))
        self.assertFalse(Handler.recorded_move_matches(grid, 'S(CAT) 8F 5', '', place))
        perpendicular = {'action': 'place', 'position': 'F8', 'tiles': 's'}
        self.assertTrue(Handler.recorded_move_matches(grid, 's(CAT) 8F 5', '', perpendicular))
        self.assertFalse(Handler.recorded_move_matches(grid, 's(CAT) 8E 5', '', perpendicular))
        self.assertFalse(Handler.recorded_move_matches(grid, 's(CAT) 8F 5', '', {**place, 'tiles': 's.D.'}))
        passing = {'action': 'pass', 'position': '', 'tiles': ''}
        self.assertTrue(Handler.recorded_move_matches(grid, 'PASS 0', '', passing))
        self.assertFalse(Handler.recorded_move_matches(grid, 'SWAP 0', 'AB', passing))
        exchange = {'action': 'exchange', 'position': '', 'tiles': '?A'}
        for notation in ('SWAP', 'SWAP 0', 'SWAP 3', 'exchange -5'):
            self.assertTrue(Handler.recorded_move_matches(grid, notation, 'A?', exchange))
        self.assertFalse(Handler.recorded_move_matches(grid, 'SWAP 0', 'AB', exchange))
        self.assertFalse(Handler.recorded_move_matches(grid, 'PASS 0', 'A?', exchange))

    def test_win_quality_validates_percentage_point_loss_and_keeps_equity_separate(self):
        point = {'turnIndex': 0, 'turn': 1, 'category': 'blunder',
                 'playedWin': .4, 'bestWin': .6, 'loss': 20, 'equityLoss': 6,
                 'playedEquity': 12, 'bestEquity': 18}
        quality = {'state': 'done', 'evaluation': 'win-probability-v1', 'points': [point,
                   {'turnIndex': 1, 'turn': 2, 'category': 'unrated', 'loss': None, 'reason': 'No valid rack.'}]}
        body = {'analysis_version': ANALYSIS_VERSION, 'move_quality': quality}
        self.assertEqual(self.handler.validate_game_analysis(body)['move_quality'], quality)
        for change in ({'loss': 6}, {'playedWin': 40}, {'bestWin': float('nan')},
                       {'category': 'best'}, {'equityLoss': -1}, {'loss': None}):
            invalid = copy.deepcopy(body)
            invalid['move_quality']['points'][0].update(change)
            with self.subTest(change=change), self.assertRaises(CrossplayError):
                self.handler.validate_game_analysis(invalid)

    def test_completed_win_quality_rejects_provisional_rated_and_unrated_points(self):
        for point in ({'category': 'blunder', 'playedWin': .4, 'bestWin': .6, 'loss': 20,
                       'equityLoss': 6, 'playedEquity': 12, 'bestEquity': 18},
                      {'category': 'unrated', 'loss': None, 'reason': 'No valid rack.'}):
            quality = {'state': 'done', 'evaluation': 'win-probability-v1', 'points': [point]}
            body = {'analysis_version': ANALYSIS_VERSION, 'move_quality': quality}
            self.handler.validate_game_analysis(body)
            point['provisional'] = False
            self.handler.validate_game_analysis(body)
            point['provisional'] = True
            with self.subTest(category=point['category']), self.assertRaises(CrossplayError):
                self.handler.validate_game_analysis(body)

    def test_pass_and_exchange_win_results_do_not_require_equity_performance(self):
        for action, notation, tiles in (('pass', 'PASS 0', ''), ('exchange', 'SWAP 0', 'CA')):
            with self.subTest(action=action):
                key, result = review_analysis_entry()
                parts = json.loads(key)
                parts[12:14] = [notation, tiles]
                key = json.dumps(parts)
                for move in (result['moves'][0], result['staticMoves'][0]):
                    move.update(action=action, position='', tiles=tiles)
                point = {'category': 'blunder', 'loss': 20, 'playedWin': .4, 'bestWin': .6,
                         'equityLoss': None, 'playedEquity': 12, 'bestEquity': 18,
                         'playedMove': {'action': action, 'position': '', 'tiles': tiles}}
                body = {'analysis_version': ANALYSIS_VERSION, 'turn_analyses': {key: result},
                        'move_quality': {'state': 'done', 'evaluation': 'win-probability-v1', 'points': [point]}}
                self.handler.save_game_analysis('account-a', 'game0001', body)
                snapshot = self.handler.supabase_data_request.call_args.kwargs['payload']['snapshot']
                self.assertIsNone(snapshot['move_quality']['points'][0]['equityLoss'])
                self.assertEqual(snapshot['turn_analyses'][key]['selected']['action'], action)
                point['playedMove']['action'] = 'place'
                with self.assertRaises(CrossplayError):
                    self.handler.validate_game_analysis(body)

    def test_exchanged_blank_notation_matches_and_round_trips_with_physical_blank(self):
        for swapped in ('A?', 'A*', 'A_', 'A ', 'Aa'):
            with self.subTest(swapped=swapped):
                key, result = review_analysis_entry()
                identity = json.loads(key)
                identity[12:14] = ['SWAP 0', swapped]
                key = json.dumps(identity)
                for move in (result['moves'][0], result['staticMoves'][0]):
                    move.update(action='exchange', position='', tiles='?A')
                self.assertTrue(Handler.recorded_move_matches(identity[2], identity[12], swapped, result['selected']))
                body = {'analysis_version': ANALYSIS_VERSION, 'turn_analyses': {key: result}}
                self.handler.save_game_analysis('account-a', 'game0001', body)
                snapshot = self.handler.supabase_data_request.call_args.kwargs['payload']['snapshot']
                self.assertEqual(snapshot['turn_analyses'][key]['selected']['tiles'], '?A')
                self.assertEqual(json.loads(next(iter(snapshot['turn_analyses'])))[13], swapped)
                result['selected']['tiles'] = 'AA'
                self.assertFalse(Handler.recorded_move_matches(identity[2], identity[12], swapped, result['selected']))

    def test_swap_placeholder_saves_only_matching_physical_tiles(self):
        key, result = review_analysis_entry()
        identity = json.loads(key)
        identity[12:14] = ['swap -- 0', 'A?']
        key = json.dumps(identity)
        for move in (result['moves'][0], result['staticMoves'][0]):
            move.update(action='exchange', position='', tiles='?A', score=0)
        body = {'analysis_version': ANALYSIS_VERSION, 'turn_analyses': {key: result}}
        self.handler.save_game_analysis('account-a', 'game0001', body)
        snapshot = self.handler.supabase_data_request.call_args.kwargs['payload']['snapshot']
        self.assertEqual(snapshot['turn_analyses'][key]['selected']['tiles'], '?A')
        self.assertEqual(json.loads(next(iter(snapshot['turn_analyses'])))[12:14], ['swap -- 0', 'A?'])
        for notation, swapped in (
                ('swap -- 0', 'AA'), ('swap -- 0', ''),
                ('swap - 0', 'A?'), ('swap --- 0', 'A?'),
                ('swap --', 'A?'), ('swap -- 0 extra', 'A?')):
            with self.subTest(notation=notation, swapped=swapped), self.assertRaises(CrossplayError):
                identity[12:14] = [notation, swapped]
                self.handler.validate_game_analysis({
                    'analysis_version': ANALYSIS_VERSION,
                    'turn_analyses': {json.dumps(identity): result}})

    def test_saved_game_pages_use_owner_and_stable_id_cursor(self):
        handler = object.__new__(Handler)
        rows = [{'nyt_game_id': f'game{index:04d}', 'summary': {'id': f'game{index:04d}'}}
                for index in range(7)]
        queries = []

        def database(method, table, params):
            queries.append(params)
            self.assertEqual((method, table), ('GET', 'crossplay_games'))
            self.assertEqual(params['user_id'], 'eq.account-a')
            self.assertEqual(params['order'], 'nyt_game_id.asc')
            cursor = params.get('nyt_game_id', 'gt.')[3:]
            return [row for row in rows if row['nyt_game_id'] > cursor][:int(params['limit'])]

        handler.supabase_data_request = database
        received, after = [], None
        while True:
            page = handler.persistent_games_page('account-a', 3, after)
            received.extend(game['id'] for game in page['games'])
            after = page['next_cursor']
            if after is None:
                break
        self.assertEqual(received, [row['nyt_game_id'] for row in rows])
        self.assertEqual(len(queries), 3)
        self.assertNotIn('nyt_game_id', queries[0])
        self.assertEqual(queries[1]['nyt_game_id'], 'gt.game0002')

    def test_paged_endpoint_uses_the_authenticated_account(self):
        handler = object.__new__(Handler)
        handler.path = '/api/crossplay/games?page_size=2&after=game0001'
        handler.current_supabase_user = Mock(return_value={'id': 'account-b'})
        handler.persistent_games_page = Mock(return_value={'games': [], 'next_cursor': None})
        handler.send_json = Mock()
        handler.handle_persistent_api('GET', 'games')
        handler.current_supabase_user.assert_called_once_with(required=True)
        handler.persistent_games_page.assert_called_once_with('account-b', 2, 'game0001')
        handler.current_supabase_user.side_effect = CrossplayError('Sign in first', 401)
        handler.persistent_games_page.reset_mock()
        with self.assertRaises(CrossplayError):
            handler.handle_persistent_api('GET', 'games')
        handler.persistent_games_page.assert_not_called()

    def test_game_page_parameters_are_optional_and_reject_invalid_or_repeated_values(self):
        handler = object.__new__(Handler)
        handler.path = '/api/crossplay/games'
        self.assertIsNone(handler.game_page_parameters())
        handler.path += '?page_size=100'
        self.assertEqual(handler.game_page_parameters(), (100, None))
        handler.path += '&after=game0001'
        self.assertEqual(handler.game_page_parameters(), (100, 'game0001'))
        for query in ('page_size=0', 'page_size=-1', 'page_size=101', 'page_size=1.5',
                      'page_size=', 'after=game0001', 'page_size=2&page_size=3',
                      'page_size=2&after=', 'page_size=2&after=bad!',
                      'page_size=2&after=game0001&after=game0002'):
            handler.path = '/api/crossplay/games?' + query
            with self.subTest(query=query), self.assertRaises(CrossplayError) as caught:
                handler.game_page_parameters()
            self.assertEqual(caught.exception.status, 400)

    def test_daily_refresh_reuses_recent_import_even_when_account_is_empty(self):
        self.handler.persistent_games.return_value = []
        self.handler.supabase_data_request.return_value = [{'refreshed_at': datetime.now(timezone.utc).isoformat()}]
        self.assertEqual(self.handler.refresh_persistent_games('account-a', True), self.handler.persistent_games.return_value)
        self.handler.current_session.assert_not_called()
        query = self.handler.supabase_data_request.call_args.args[2]
        self.assertEqual(query['user_id'], 'eq.account-a')

    def test_stale_import_refreshes_and_keeps_older_saved_games(self):
        yesterday = datetime.fromtimestamp(time.time() - 86401, timezone.utc).isoformat()
        self.handler.supabase_data_request.return_value = [{'refreshed_at': yesterday}]
        games = self.handler.refresh_persistent_games('account-a', True)
        self.client.recent_games.assert_called_once()
        self.assertEqual(games[0]['id'], 'oldergame')
        self.assertEqual(self.handler.supabase_data_request.call_args.args[:2], ('POST', 'crossplay_refresh_state'))
        self.assertEqual(self.handler.supabase_data_request.call_args.args[3]['user_id'], 'account-a')

    def test_refresh_report_counts_added_changed_and_unchanged_games(self):
        games = [{'id': f'game000{i}', 'game': 'crossplay', 'game_status': 'completed',
                  'players': [{'id': 'synthetic-nyt-user', 'score': i}, {'id': 'opponent', 'score': 10}],
                  'timestamp': 100 + i} for i in range(3)]
        self.client.recent_games.return_value = games
        from crossplay_server import summarize_game
        unchanged = summarize_game(games[0], 'synthetic-nyt-user')
        changed = {**summarize_game(games[1], 'synthetic-nyt-user'), 'my_score': -1}
        before = [unchanged, changed, {'id': 'oldergame'}]
        after = [unchanged, summarize_game(games[1], 'synthetic-nyt-user'),
                 summarize_game(games[2], 'synthetic-nyt-user'), {'id': 'oldergame'}]
        self.handler.persistent_games.side_effect = [before, after]
        report = {}
        result = self.handler.refresh_persistent_games('account-a', report=report)
        self.assertEqual(result, after)
        self.assertEqual({k: v for k, v in report.items() if k != 'checked_at'}, {
            'status': 'refreshed', 'fetched': 3, 'added': 1, 'updated': 1, 'newest_timestamp': 102})
        datetime.fromisoformat(report['checked_at'])
        self.assertEqual(report['checked_at'], self.handler.supabase_data_request.call_args.args[3]['refreshed_at'])

    def test_empty_refresh_report_has_no_newest_timestamp(self):
        report = {}
        self.handler.refresh_persistent_games('account-a', report=report)
        self.assertEqual(report['fetched'], 0)
        self.assertIsNone(report['newest_timestamp'])

    def test_daily_skip_report_has_previous_check_time(self):
        checked_at = datetime.now(timezone.utc).isoformat()
        self.handler.supabase_data_request.return_value = [{'refreshed_at': checked_at}]
        report = {}
        self.handler.refresh_persistent_games('account-a', True, report=report)
        self.assertEqual(report, {'status': 'skipped', 'checked_at': checked_at})
        self.handler.current_session.assert_not_called()

    def test_incomplete_snapshot_never_writes_import_or_success(self):
        client = CrossplayClient('synthetic-cookie')
        client.get = Mock(return_value={'msgs': []})
        client.me = Mock(return_value={'user_id': 'synthetic-nyt-user'})
        self.handler.current_session.return_value = ('account-a', client)
        with self.assertRaises(CrossplayError):
            self.handler.refresh_persistent_games('account-a')
        self.handler.upsert_game_summaries.assert_not_called()
        self.handler.supabase_data_request.assert_not_called()

    def test_authenticated_refresh_route_returns_report(self):
        self.handler.current_supabase_user = Mock(return_value={'id': 'account-a'})
        self.handler.headers = {'Content-Length': '1'}
        self.handler.read_json_body = Mock(return_value={'if_stale': False})
        self.handler.send_json = Mock(side_effect=lambda status, body: (status, body))
        status, body = self.handler.handle_persistent_api('POST', 'games/refresh')
        self.assertEqual(status, 200)
        self.assertEqual(body['games'], self.handler.persistent_games.return_value)
        self.assertEqual(body['refresh']['status'], 'refreshed')
        self.assertEqual(body['refresh']['fetched'], 0)

    def test_manual_refresh_bypasses_daily_check_and_failure_does_not_mark_success(self):
        self.client.recent_games.side_effect = CrossplayError('Synthetic upstream failure')
        with self.assertRaises(CrossplayError):
            self.handler.refresh_persistent_games('account-a')
        self.handler.supabase_data_request.assert_not_called()
        self.handler.upsert_game_summaries.assert_not_called()


if __name__ == '__main__':
    unittest.main()
