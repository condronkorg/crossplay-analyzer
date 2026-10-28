"""Persistence and daily refresh checks with synthetic accounts and engine data."""
import copy
import json
import time
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock

from crossplay_server import ANALYSIS_VERSION, CrossplayError, Handler


def review_analysis_entry():
    key = json.dumps(['quackle-review-v1', 'review', ['.' * 15] * 15, 'CATERS?',
                      0, 0, 86, 0, 15, 277, 2, 21, 'CAT 8G 10', '',
                      'paired-review-v1'], separators=(',', ':'))
    moves = [
        {'action': 'place', 'position': '8G', 'tiles': 'CAT', 'score': 10,
         'equity': 11, 'staticEquity': 12, 'win': .4, 'samples': 277, 'terminalSamples': 0},
        {'action': 'place', 'position': '8G', 'tiles': 'CATS', 'score': 15,
         'equity': 17, 'staticEquity': 18, 'win': .6, 'samples': 277, 'terminalSamples': 0},
    ]
    static_moves = [{**move, 'equity': move['staticEquity']} for move in moves]
    result = {'simulated': True, 'samplingVersion': 'paired-review-v1', 'seed': 123,
              'moves': moves, 'selected': moves[0], 'best': moves[1],
              'staticMoves': static_moves, 'bestStaticEquity': 18,
              'iterationsRequested': 277, 'iterationsCompleted': 277,
              'candidateCount': 2, 'totalPlayaheads': 554,
              'bagCount': 86, 'finalTurnsRemaining': 0, 'playToEnd': False,
              'simulationMode': '2-ply', 'plies': 2, 'winModel': 'bogowin'}
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
        key = json.dumps(parts)
        result.update(bagCount=21, playToEnd=True, simulationMode='play-to-end', plies=-1, winModel='terminal')
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
        self.assertTrue(Handler.recorded_move_matches(grid, 'SWAP 0', 'A?', exchange))
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

    def test_manual_refresh_bypasses_daily_check_and_failure_does_not_mark_success(self):
        self.client.recent_games.side_effect = CrossplayError('Synthetic upstream failure')
        with self.assertRaises(CrossplayError):
            self.handler.refresh_persistent_games('account-a')
        self.handler.supabase_data_request.assert_not_called()
        self.handler.upsert_game_summaries.assert_not_called()


if __name__ == '__main__':
    unittest.main()
