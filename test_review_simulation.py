"""Real browser/WASM checks for recorded-move win estimates. Synthetic positions only."""
import functools
import json
import threading
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import Error, sync_playwright


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


class ReviewSimulationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        handler = functools.partial(QuietHandler, directory=Path(__file__).resolve().parent)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.playwright = sync_playwright().start()
        try:
            cls.browser = cls.playwright.chromium.launch(headless=True)
        except Error:
            cls.browser = cls.playwright.chromium.launch(channel="chrome", headless=True)
        cls.page = cls.browser.new_page()
        cls.page.goto(f"http://127.0.0.1:{cls.server.server_port}/privacy.html")
        cls.page.evaluate("""() => {
          window.engineWorker = new Worker('quackle-worker.js');
          window.engineRequestId = 0;
          window.engineRequest = (type, payload) => new Promise((resolve, reject) => {
            const id = ++window.engineRequestId;
            const timer = setTimeout(() => { cleanup(); reject(new Error('Engine timed out')); }, 120000);
            const cleanup = () => { clearTimeout(timer); engineWorker.removeEventListener('message', listener); };
            const listener = event => {
              if (event.data.id !== id) return;
              cleanup();
              resolve(event.data);
            };
            engineWorker.addEventListener('message', listener);
            engineWorker.postMessage({ id, type, payload });
          });
        }""")

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def payload(self, **changes):
        return {
            "gridJson": json.dumps(["." * 15] * 15), "rack": "AEIRST?",
            "playerScore": 30, "opponentScore": 25, "bagCount": 86,
            "finalTurnsRemaining": 0, "numMoves": 15, "iterations": 3,
            "played": {"action": "pass", "position": "", "tiles": ""},
            "seed": 123456789, **changes,
        }

    def request(self, payload, type="simulateReview"):
        return self.page.evaluate("([type, payload]) => engineRequest(type, payload)", [type, payload])

    def result(self, payload):
        response = self.request(payload)
        self.assertFalse(response.get("error"), response)
        result = response["result"]
        self.assertFalse(result.get("error"), result)
        self.assertTrue(result["simulated"])
        self.assertEqual(result["seed"], payload["seed"])
        self.assertEqual(result["samplingVersion"], "paired-review-v1")
        self.assertLessEqual(len(result["moves"]), payload["numMoves"] + 1)
        self.assertEqual(result["iterationsCompleted"], payload["iterations"])
        self.assertEqual(result["totalPlayaheads"], payload["iterations"] * len(result["moves"]))
        self.assertIn(result["selected"], result["moves"])
        self.assertIn(result["best"], result["moves"])
        self.assertEqual(result["best"]["win"], max(move["win"] for move in result["moves"]))
        self.assertEqual(result["bestStaticEquity"], max(move["equity"] for move in result["staticMoves"]))
        for move in result["moves"]:
            self.assertEqual(move["samples"], payload["iterations"])
            self.assertGreaterEqual(move["win"], 0)
            self.assertLessEqual(move["win"], 1)
            self.assertIsInstance(move["staticEquity"], (int, float))
        return result

    def test_forces_pass_and_exchange_into_bounded_simulation(self):
        passed = self.result(self.payload())
        self.assertEqual(passed["selected"]["action"], "pass")
        exchanged = self.result(self.payload(played={"action": "exchange", "position": "", "tiles": "AE"}))
        self.assertEqual(exchanged["selected"]["action"], "exchange")
        self.assertEqual(exchanged["selected"]["tiles"], "AE")

    def test_includes_actual_placement_outside_shortlist(self):
        static = self.request(self.payload(numMoves=1000), "kibitz")["result"]["moves"]
        actual = next(move for move in static[30:] if move["action"] == "place")
        result = self.result(self.payload(played={key: actual[key] for key in ("action", "position", "tiles")}))
        self.assertEqual(result["selected"]["position"], actual["position"])
        self.assertEqual(result["selected"]["tiles"], actual["tiles"])
        self.assertEqual(result["selected"]["staticEquity"], actual["equity"])

    def test_seed_is_independent_of_other_requests_and_candidate_count(self):
        payload = self.payload(numMoves=1)
        first = self.result(payload)
        self.result(self.payload(seed=0))
        self.assertEqual(first, self.result(payload))
        expanded = self.result(self.payload(numMoves=2))
        self.assertEqual(first["selected"], expanded["selected"])

    def test_physical_blanks_and_perpendicular_single_tile(self):
        blank = self.result(self.payload(numMoves=1, played={"action": "place", "position": "8G", "tiles": "At"}))
        normal = self.result(self.payload(numMoves=1, played={"action": "place", "position": "8G", "tiles": "AT"}))
        self.assertEqual(blank["selected"]["tiles"], "At")
        self.assertEqual(normal["selected"]["tiles"], "AT")
        self.assertNotEqual(blank["selected"]["score"], normal["selected"]["score"])
        grid = ["." * 15] * 15
        grid[7] = "......CAT......"
        across = self.result(self.payload(gridJson=json.dumps(grid), bagCount=83, numMoves=1,
            played={"action": "place", "position": "8G", "tiles": "...S"}))
        down = self.result(self.payload(gridJson=json.dumps(grid), bagCount=83, numMoves=1,
            played={"action": "place", "position": "J8", "tiles": "S"}))
        self.assertEqual(across["selected"]["score"], down["selected"]["score"])
        self.assertEqual(across["selected"]["win"], down["selected"]["win"])

    def test_endgame_samples_reach_official_finish_and_reject_exchange(self):
        # The existing engine regression board, with two additional board tiles
        # to make the bag empty. Racks and scores here are test inputs.
        grid = [
            "CD........G....", ".......A.NOUN.Y", ".......L...TAME",
            "......YEAH...E.", ".........ab.yA.", "......LOB.OWED.",
            "..PROG..I.TEN.F", "....D..JOGS...I", "...LASSI.O..RUN",
            ".......V.R.MI..", "...C...E..HIPS.", "...A.BASE.A.E..",
            "...R.O..D.V....", "..RENT.LITER...", ".....H..T......",
        ]
        payload = self.payload(gridJson=json.dumps(grid), rack="WEAFTUO", bagCount=0, finalTurnsRemaining=1)
        result = self.result(payload)
        self.assertTrue(result["playToEnd"])
        self.assertEqual(result["winModel"], "terminal")
        self.assertTrue(all(move["terminalSamples"] == payload["iterations"] for move in result["moves"]))
        payload["played"] = {"action": "exchange", "position": "", "tiles": "W"}
        self.assertIn("not legal", self.request(payload)["error"])

    def test_rejects_invalid_recorded_actions_and_parameters(self):
        for payload in [
            self.payload(played={"action": "exchange", "tiles": ""}),
            self.payload(played={"action": "exchange", "tiles": "ZZ"}),
            self.payload(played={"action": "place", "position": "15O", "tiles": "AT"}),
            self.payload(played={"action": "place", "position": "1A", "tiles": "AT"}),
            self.payload(played={"action": "other", "tiles": ""}),
            self.payload(seed=-1), self.payload(seed=2 ** 32),
            self.payload(iterations=278), self.payload(numMoves=16),
        ]:
            with self.subTest(payload=payload):
                response = self.request(payload)
                self.assertTrue(response.get("error") or response.get("result", {}).get("error"), response)


if __name__ == "__main__":
    unittest.main()
