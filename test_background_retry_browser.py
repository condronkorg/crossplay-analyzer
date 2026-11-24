"""Background failures explain the affected work and preserve successful moves."""

import unittest

import test_review_browser as review_browser


class BackgroundRetryBrowserTest(unittest.TestCase):
    setUpClass = classmethod(review_browser.ReviewBrowserTest.setUpClass.__func__)
    setUp = review_browser.ReviewBrowserTest.setUp
    tearDown = review_browser.ReviewBrowserTest.tearDown
    route_request = review_browser.ReviewBrowserTest.route_request

    def test_completed_analysis_shows_reasons_without_claiming_work_is_running(self):
        result = self.page.evaluate('''() => {
          const q = getReviewQueue(); q.concurrency = 0;
          q.ids = ['unrated', 'save', 'load'];
          q.contexts.set('unrated', {review:{id:'unrated',opponent:'<b>Opponent</b>'},
            states:['done','done'], cache:{}, quality:{points:[
              {turnIndex:0,category:'unrated',retryable:false,reason:'Missing recorded swap tiles.'},
              {turnIndex:1,category:'unrated',retryable:true,reason:'Worker failed <script>bad()</script>'}]}});
          q.contexts.set('save', {review:{id:'save',opponent:'Save Opponent'},
            states:['done'],cache:{saveError:'Save permission denied'},quality:{points:[]}});
          q.errors.set('load', 'Saved review could not load');
          updateBackgroundStatus();
          return {text:byId('backgroundStatus').textContent,
            unsafe:byId('backgroundStatus').querySelectorAll('script,b').length,
            retryVisible:!byId('retryBackground').classList.contains('hidden')};
        }''')
        self.assertIn('2 unrated', result['text'])
        self.assertIn('3 games need a retry', result['text'])
        self.assertIn('Unrated moves have no win estimate', result['text'])
        self.assertIn('Turn 1', result['text'])
        self.assertIn('Missing recorded swap tiles.', result['text'])
        self.assertIn('Turn 2', result['text'])
        self.assertIn('Worker failed <script>bad()</script>', result['text'])
        self.assertIn('Save permission denied', result['text'])
        self.assertIn('Saved review could not load', result['text'])
        self.assertNotIn('Keep this page open to continue', result['text'])
        self.assertEqual(result['unsafe'], 0)
        self.assertTrue(result['retryVisible'])

    def test_save_only_retry_saves_without_restarting_successful_turns(self):
        result = self.page.evaluate('''async () => {
          const q = getReviewQueue(); q.concurrency = 0;
          const context = {review:{id:'saved',opponent:'Saved Opponent'},
            states:['done'],cache:{saveError:'Offline'},quality:{points:[{turnIndex:0,playedWin:.7}]}};
          q.ids=['saved'];q.contexts.set('saved',context);
          let saved=0;
          flushAnalysisSave=async()=>{saved++;context.cache.saveError=null;};
          updateBackgroundStatus();
          byId('retryBackground').click();
          await Promise.resolve();
          return {saved,state:context.states[0],win:context.quality.points[0].playedWin,
            retryVisible:!byId('retryBackground').classList.contains('hidden')};
        }''')
        self.assertEqual(result['saved'], 1)
        self.assertEqual(result['state'], 'done')
        self.assertEqual(result['win'], .7)
        self.assertFalse(result['retryVisible'])

    def test_paused_work_is_not_reported_as_a_finished_pass(self):
        text = self.page.evaluate('''() => {
          const q=getReviewQueue(); q.concurrency=0; q.ids=['paused'];
          q.contexts.set('paused',{review:{id:'paused'},states:['pending'],paused:true,cache:{}});
          updateBackgroundStatus(); return byId('backgroundStatus').textContent;
        }''')
        self.assertIn('Analysis paused', text)
        self.assertNotIn('Analysis pass finished', text)
        self.assertNotIn('Keep this page open to continue', text)
