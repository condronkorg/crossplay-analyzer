"""Real opening-deal WASM checks. Synthetic racks only."""
import functools
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import Error, sync_playwright
from test_rack_draw_simulation import QuietHandler


class InitialDealSimulationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        handler = functools.partial(QuietHandler, directory=Path(__file__).resolve().parent)
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.playwright = sync_playwright().start()
        try:
            cls.browser = cls.playwright.chromium.launch(headless=True)
        except Error:
            cls.browser = cls.playwright.chromium.launch(channel='chrome', headless=True)
        cls.page = cls.browser.new_page()
        cls.page.goto(f'http://127.0.0.1:{cls.server.server_port}/privacy.html')
        cls.page.add_script_tag(url=f'http://127.0.0.1:{cls.server.server_port}/quackle.js')
        cls.page.evaluate("""async () => {
          window.testEngine = await QuackleModule();
          if (!testEngine.initEngine('/data')) throw new Error('Engine initialization failed');
        }""")

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def request(self, first='AEIRST?', second='ADEINRT', iterations=8, seed=123456789):
        return self.page.evaluate("""args => typeof testEngine.simulateInitialDeal === 'function'
          ? testEngine.simulateInitialDeal(...args) : {error: 'Missing simulateInitialDeal binding'}""",
          [first, second, iterations, seed])

    def result(self, **changes):
        result = self.request(**changes)
        self.assertFalse(result.get('error'), result)
        self.assertIs(result['simulated'], True)
        self.assertEqual(result['samplingVersion'], 'initial-deal-v1')
        self.assertEqual(result['policy'], 'static-best-v1')
        self.assertEqual(result['firstRack'], ''.join(sorted(changes.get('first', 'AEIRST?'))))
        self.assertEqual(result['secondRack'], ''.join(sorted(changes.get('second', 'ADEINRT'))))
        self.assertEqual(result['seed'], changes.get('seed', 123456789))
        self.assertEqual(result['iterationsRequested'], changes.get('iterations', 8))
        self.assertEqual(result['iterationsCompleted'], result['iterationsRequested'])
        self.assertEqual(result['plies'], 2)
        self.assertIs(result['playToEnd'], False)
        self.assertEqual(result['simulationMode'], '2-ply')
        self.assertEqual(result['winModel'], 'bogowin')
        for field in ('baselineWin', 'firstOnlyWin', 'secondOnlyWin', 'afterWin'):
            self.assertGreaterEqual(result[field], 0)
            self.assertLessEqual(result[field], 1)
        b, a, c, j = [result[key] for key in ('baselineWin', 'firstOnlyWin', 'secondOnlyWin', 'afterWin')]
        self.assertAlmostEqual(result['firstWpa'], 100 * ((a - b) + (j - c)) / 2, places=12)
        self.assertAlmostEqual(result['secondWpa'], -100 * ((c - b) + (j - a)) / 2, places=12)
        self.assertAlmostEqual(result['firstWpa'] - result['secondWpa'], 100 * (j - b), places=12)
        return result

    def test_01_binding_and_complete_contract(self):
        self.result()

    def test_determinism_and_canonical_rack_order(self):
        original = self.result()
        self.assertEqual(original, self.result())
        self.assertEqual(original, self.result(first='?TSRIEA', second='TRNIEDA'))

    def test_conditionals_ignore_unrevealed_inputs_at_fixed_seed(self):
        original = self.result()
        first_changed = self.result(first='EEEEIII')
        second_changed = self.result(second='EEEEIII')
        self.assertEqual(original['baselineWin'], first_changed['baselineWin'])
        self.assertEqual(original['baselineWin'], second_changed['baselineWin'])
        self.assertEqual(original['firstOnlyWin'], second_changed['firstOnlyWin'])
        self.assertEqual(original['secondOnlyWin'], first_changed['secondOnlyWin'])
        self.assertNotEqual(original['afterWin'], second_changed['afterWin'])
        self.assertNotEqual(original['afterWin'], first_changed['afterWin'])

    def test_rejects_short_malformed_and_excess_physical_tiles(self):
        for first, second in [('AEIRST', 'ADEINRT'), ('aeirst?', 'ADEINRT'),
                              ('AEIRST_', 'ADEINRT'), ('AEIRST!', 'ADEINRT'),
                              ('QQAEIRS', 'ADEINRT'), ('AEIRST?', 'ADEINR?'),
                              ('AAAAAAA', 'AAAAAAA')]:
            with self.subTest(first=first, second=second):
                result = self.request(first, second)
                # Crossplay has three blanks; test their physical limit separately.
                if (first, second) == ('AEIRST?', 'ADEINR?'):
                    self.assertTrue(result.get('simulated'), result)
                else:
                    self.assertFalse(result.get('simulated'), result)
                    self.assertTrue(result.get('error'), result)
        self.assertTrue(self.request('AEIRS??', 'ADEIN??').get('error'))
        for iterations, seed in [(0, 1), (8.5, 1), (278, 1), (8, -1), (8, 1.5), (8, 4294967296)]:
            self.assertTrue(self.request(iterations=iterations, seed=seed).get('error'))

    def test_full_sample_run_uses_real_probabilities(self):
        start = time.monotonic()
        result = self.result(iterations=277)
        print(f'Opening 277-sample runtime: {time.monotonic() - start:.2f}s; estimates: ' +
              ', '.join(f'{key}={result[key]:.8f}' for key in
                        ('baselineWin', 'firstOnlyWin', 'secondOnlyWin', 'afterWin')))
        self.assertNotEqual(result['baselineWin'], .5)
        self.assertGreater(result['firstOnlyWin'], result['baselineWin'])
        self.assertLess(result['secondOnlyWin'], result['baselineWin'])
        # Advancing the base seed by 64 trial steps exposes exactly the
        # remaining suffix. Thus the preview must be the full run's prefix.
        prefix = self.result(iterations=64)
        suffix_seed = (123456789 + 0x9e3779b9 * 64) & 0xffffffff
        suffix = self.result(iterations=213, seed=suffix_seed)
        for key in ('baselineWin', 'firstOnlyWin', 'secondOnlyWin', 'afterWin'):
            self.assertAlmostEqual(result[key],
                (prefix[key] * 64 + suffix[key] * 213) / 277, places=14)

    def test_worker_dispatch_uses_native_opening_deal(self):
        result = self.page.evaluate("""() => new Promise((resolve, reject) => {
          const worker = new Worker('quackle-worker.js');
          worker.onmessage = event => { worker.terminate(); resolve(event.data); };
          worker.onerror = error => { worker.terminate(); reject(error.message); };
          worker.postMessage({id: 17, type: 'simulateOpening', payload: {
            firstRack: 'AEIRST?', secondRack: 'ADEINRT', iterations: 64, seed: 123456789}});
        })""")
        self.assertFalse(result.get('error'), result)
        self.assertEqual(result['id'], 17)
        self.assertEqual(result['result'], self.result(iterations=64))
