"""Real WASM checks for rack-draw win estimates. Synthetic positions only."""
import functools
import json
import threading
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import Error, sync_playwright


class RackDrawSimulationTests(unittest.TestCase):
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
        cls.page.add_script_tag(url=f"http://127.0.0.1:{cls.server.server_port}/quackle.js")
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

    def payload(self, **changes):
        return {
            "gridJson": json.dumps(["." * 15] * 15), "rack": "AEIRST?",
            "playerScore": 30, "opponentScore": 25, "bagCount": 86,
            "finalTurnsRemaining": 0, "numMoves": 1, "iterations": 8,
            "played": {"action": "place", "position": "8G", "tiles": "AT"},
            "seed": 123456789, "rackAfter": "AEEIRS?", **changes,
        }

    def request(self, payload, method="simulateReviewDraw"):
        return self.page.evaluate("""([method, p]) => testEngine[method](
          p.gridJson, p.rack, p.playerScore, p.opponentScore, p.numMoves,
          p.iterations, p.bagCount, p.finalTurnsRemaining, p.played, p.seed,
          ...(method === 'simulateReviewDraw' ? [p.rackAfter] : []))""", [method, payload])

    def result(self, payload):
        result = self.request(payload)
        self.assertFalse(result.get("error"), result)
        self.assertTrue(result["simulated"])
        self.assertEqual(result["samplingVersion"], "rack-draw-v1")
        self.assertEqual(result["seed"], payload["seed"])
        self.assertEqual(result["rackAfter"], payload["rackAfter"])
        self.assertEqual(result["iterationsRequested"], payload["iterations"])
        self.assertEqual(result["iterationsCompleted"], payload["iterations"])
        self.assertEqual(result["bagCount"], payload["bagCount"])
        self.assertEqual(result["finalTurnsRemaining"], payload["finalTurnsRemaining"])
        self.assertEqual(result["playToEnd"], payload["bagCount"] <= 21)
        for field in ("beforeWin", "afterWin"):
            self.assertGreaterEqual(result[field], 0)
            self.assertLessEqual(result[field], 1)
        return result

    def test_before_draw_matches_recorded_move_and_is_deterministic(self):
        payload = self.payload()
        draw = self.result(payload)
        review = self.request(payload, "simulateReviewMove")
        self.assertEqual(draw["beforeWin"], review["selected"]["win"])
        self.assertEqual(draw["drawCount"], 2)
        self.assertEqual(draw, self.result(payload))
        self.assertEqual(draw["beforeWin"], self.result(self.payload(numMoves=5))["beforeWin"])

    def test_actual_rack_changes_only_the_after_draw_probability(self):
        first = self.result(self.payload(iterations=64))
        other = self.result(self.payload(iterations=64, rackAfter="EIRS?QZ"))
        reordered = self.result(self.payload(iterations=64, rackAfter="?SRIEEA"))
        self.assertEqual(first["beforeWin"], other["beforeWin"])
        self.assertGreater(abs(first["afterWin"] - other["afterWin"]), .01)
        self.assertEqual(first["afterWin"], reordered["afterWin"])

    def test_pass_has_no_draw_and_keeps_probability_exactly(self):
        result = self.result(self.payload(
            played={"action": "pass", "position": "", "tiles": ""}, rackAfter="AEIRST?"))
        self.assertEqual(result["drawCount"], 0)
        self.assertEqual(result["beforeWin"], result["afterWin"])

    def test_exchange_and_assigned_blanks_use_physical_tiles(self):
        exchanged = self.result(self.payload(
            played={"action": "exchange", "position": "", "tiles": "AE"}, rackAfter="EIRST?O"))
        self.assertEqual(exchanged["drawCount"], 2)
        blank = self.result(self.payload(
            played={"action": "place", "position": "8G", "tiles": "At"}, rackAfter="EEIRSTO"))
        self.assertEqual(blank["drawCount"], 2)

    def test_partial_bag_uses_only_remaining_tiles_and_empty_bag_has_no_draw(self):
        grid = [
            "CD........G....", ".......A.NOUN.Y", ".......L...TAME",
            "......YEAH...E.", ".........ab.yA.", "......LOB.OWED.",
            "..PROG..I.TEN.F", "....D..JOGS...I", "...LASSI.O..RUN",
            ".......V.R.MI..", "...C...E..HIPS.", "...A.BASE.A.E..",
            "...R.O..D.V....", "..RENT.LITER...", ".....H..T......",
        ]
        payload = self.payload(gridJson=json.dumps(grid), rack="WEAFTUO", bagCount=0,
            finalTurnsRemaining=1, played={"action": "pass", "position": "", "tiles": ""},
            rackAfter="WEAFTUO")
        empty = self.result(payload)
        self.assertEqual(empty["drawCount"], 0)
        self.assertEqual(empty["beforeWin"], empty["afterWin"])
        grid[0] = "..........G...."
        payload.update(gridJson=json.dumps(grid), bagCount=2, finalTurnsRemaining=0)
        generated = self.page.evaluate("""p => testEngine.kibitz(
          p.gridJson, p.rack, p.playerScore, p.opponentScore, 100).moves""", payload)
        move = next(move for move in generated if move["action"] == "place" and len(move["leave"]) <= 5)
        payload.update(played={key: move[key] for key in ("action", "position", "tiles")},
            rackAfter=move["leave"] + "CD")
        partial = self.result(payload)
        self.assertEqual(partial["drawCount"], 2)
        self.assertEqual(partial["winModel"], "terminal")

    def test_rejects_missing_retained_tiles_impossible_draws_and_bad_input(self):
        for payload in [
            self.payload(rackAfter="AEIRST"),
            self.payload(rackAfter="AEIRSTZ"),
            self.payload(rackAfter="EIRS?ZZ"),
            self.payload(rackAfter="aEEIRS?"),
            self.payload(rackAfter=""),
            self.payload(rack="AEIRSTZ", played={"action": "exchange", "position": "", "tiles": "Z"},
                rackAfter="AEIRSTZ"),
            self.payload(seed=-1),
            self.payload(iterations=0),
            self.payload(iterations=278),
            self.payload(numMoves=0),
        ]:
            with self.subTest(payload=payload):
                self.assertTrue(self.request(payload).get("error"))
        self.result(self.payload())


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


if __name__ == "__main__":
    unittest.main()
