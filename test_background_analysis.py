"""Persistence and daily refresh checks with synthetic accounts and engine data."""
import copy
import time
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock

from crossplay_server import ANALYSIS_VERSION, CrossplayError, Handler


class BackgroundAnalysisTest(unittest.TestCase):
    def setUp(self):
        self.handler = object.__new__(Handler)
        self.handler.supabase_data_request = Mock()
        self.handler.saved_game_review = Mock(return_value={'status': 'completed'})
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
        for review in (None, {'status': 'active'}):
            self.handler.saved_game_review.return_value = review
            with self.assertRaises(CrossplayError):
                self.handler.save_game_analysis('account-b', 'game0001', {'analysis_version': ANALYSIS_VERSION})
        self.handler.supabase_data_request.assert_not_called()

    def test_incomplete_and_wrong_version_results_are_rejected(self):
        for body in ({'analysis_version': 'old'},
                     {'move_quality': {'state': 'running', 'points': []}},
                     {'move_quality': None}, {'turn_analyses': []},
                     {'turn_analyses': {'bad': {'error': 'engine failed', 'moves': []}}}):
            with self.subTest(body=body), self.assertRaises(CrossplayError):
                self.handler.validate_game_analysis({'analysis_version': ANALYSIS_VERSION, **body})

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
