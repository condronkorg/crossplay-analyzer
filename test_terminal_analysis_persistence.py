"""Save failed terminal analyses without marking them complete. Synthetic data only."""
import copy
import unittest
from unittest.mock import Mock

from crossplay_server import ANALYSIS_VERSION, CrossplayError, Handler
from test_background_analysis import review_analysis_entry


class TerminalAnalysisPersistenceTests(unittest.TestCase):
    def body(self):
        return {
            'analysis_version': ANALYSIS_VERSION,
            'move_quality': {
                'state': 'error', 'evaluation': 'win-probability-v1',
                'points': [{
                    'turnIndex': 0, 'turn': 1, 'category': 'unrated', 'loss': None,
                    'retryable': True, 'reason': 'Synthetic worker timeout.',
                    'executionPolicy': 'bounded-terminal-v1',
                }],
            },
            'win_trend': {'state': 'error', 'points': []},
        }

    def test_error_summaries_and_successful_raw_work_round_trip(self):
        body = self.body()
        key, result = review_analysis_entry()
        body['turn_analyses'] = {key: result}
        original = copy.deepcopy(body)
        handler = object.__new__(Handler)
        handler.saved_review_status = Mock(return_value='completed')
        handler.supabase_data_request = Mock()
        handler.save_game_analysis('synthetic-owner', 'synthetic-game', body)
        saved = handler.supabase_data_request.call_args.kwargs['payload']['snapshot']
        self.assertEqual(saved, {key: value for key, value in body.items() if key != 'analysis_version'})
        self.assertEqual(body, original)
        handler.supabase_data_request = Mock(return_value=[{
            'analysis_version': ANALYSIS_VERSION, **saved,
        }])
        restored = handler.saved_game_analysis('synthetic-owner', 'synthetic-game')
        self.assertEqual(restored['move_quality']['state'], 'error')
        self.assertEqual(restored['win_trend']['state'], 'error')
        self.assertEqual(restored['turn_analyses'], {key: result})

    def test_error_state_requires_a_real_retryable_failure(self):
        for change in ({'retryable': False}, {'retryable': 1}, {'reason': ''}, {'reason': None}):
            body = self.body()
            body['move_quality']['points'][0].update(change)
            with self.subTest(change=change), self.assertRaises(CrossplayError):
                Handler.validate_game_analysis(body)
        for field in ('points', 'evaluation'):
            body = self.body()
            body['move_quality'][field] = [] if field == 'points' else 'other'
            with self.subTest(field=field), self.assertRaises(CrossplayError):
                Handler.validate_game_analysis(body)

    def test_error_trend_can_save_alone_but_rejects_conflicting_legacy_quality(self):
        body = self.body()
        del body['move_quality']
        self.assertEqual(Handler.validate_game_analysis(body)['win_trend'], body['win_trend'])
        body['move_quality'] = {'state': 'done', 'points': []}
        with self.assertRaises(CrossplayError):
            Handler.validate_game_analysis(body)

    def test_error_quality_can_save_alone_but_rejects_conflicting_legacy_trend(self):
        body = self.body()
        del body['win_trend']
        self.assertEqual(Handler.validate_game_analysis(body)['move_quality'], body['move_quality'])
        body['win_trend'] = {'state': 'done', 'points': []}
        with self.assertRaises(CrossplayError):
            Handler.validate_game_analysis(body)

    def test_failed_work_cannot_be_saved_as_complete(self):
        body = self.body()
        body['move_quality']['state'] = body['win_trend']['state'] = 'done'
        with self.assertRaises(CrossplayError):
            Handler.validate_game_analysis(body)

    def test_error_trend_cannot_contain_a_completion_marker(self):
        body = self.body()
        body['win_trend']['points'] = [{'actual': True, 'mine': 100, 'theirs': 0}]
        with self.assertRaises(CrossplayError):
            Handler.validate_game_analysis(body)

    def test_error_summary_cannot_hide_invalid_or_incomplete_raw_results(self):
        body = self.body()
        key, result = review_analysis_entry()
        for change in ({'iterationsCompleted': 16}, {'error': 'Synthetic failure.'}):
            body['turn_analyses'] = {key: {**result, **change}}
            with self.subTest(change=change), self.assertRaises(CrossplayError):
                Handler.validate_game_analysis(body)
        del body['turn_analyses']
        body['move_quality']['points'][0]['provisional'] = True
        with self.assertRaises(CrossplayError):
            Handler.validate_game_analysis(body)

    def test_draw_and_opening_failures_can_mark_quality_incomplete(self):
        for field in ('draw', 'opening'):
            body = self.body()
            failure = body['move_quality']['points'].pop()
            failure = {'unavailable': True, 'retryable': True, 'reason': failure['reason']}
            if field == 'opening':
                body['move_quality']['opening'] = failure
            else:
                body['move_quality']['points'] = [{
                    'category': 'best', 'playedWin': .5, 'bestWin': .5, 'loss': 0,
                    'playedEquity': 2, 'bestEquity': 2, 'equityLoss': 0,
                    'playedMove': {'action': 'place'}, 'draw': failure,
                }]
            with self.subTest(field=field):
                self.assertEqual(Handler.validate_game_analysis(body)['move_quality']['state'], 'error')
