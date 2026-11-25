"""Check bounded terminal review and draw calls in real WASM."""
import unittest

import test_endgame_simulation as endgame


class TerminalBatchTests(unittest.TestCase):
    setUpClass = classmethod(endgame.EndgameSimulationTests.setUpClass.__func__)
    tearDownClass = classmethod(endgame.EndgameSimulationTests.tearDownClass.__func__)

    def require_exports(self, *names):
        for name in names:
            self.assertTrue(self.page.evaluate('name => typeof testEngine[name] === "function"', name),
                            f"Shipped engine lacks {name}")

    def test_review_split_matches_monolithic(self):
        self.require_exports("planReviewSimulation", "simulateReviewBatch")
        cases = self.page.evaluate("""grid => {
          const engine = testEngine;
          const pass = {action:'pass', position:'', tiles:''};
          const exchange = {action:'exchange', position:'', tiles:'W'};
          const key = m => JSON.stringify([m.action,m.position,m.tiles]);
          const check = result => {
            if (result.error) throw new Error(result.error);
            return result;
          };
          let remove = 21;
          const sparse = grid.map(row => row.replace(/[A-Za-z]/g, tile => remove-- > 0 ? '.' : tile));
          return [
            [grid,0,2,pass,1,3],
            [grid,0,1,pass,8,3],
            [[ '.D........G....', ...grid.slice(1) ],1,0,exchange,3,7],
            [[ '..........G....', '...............', ...grid.slice(2) ],8,0,pass,3,7],
            [sparse,21,0,exchange,1,5]
          ].map(([board,bag,turns,played,numMoves,iterations]) => {
            const args = [JSON.stringify(board),'WEAFTUO',110,100];
            const seed = 123456789;
            const legacy = check(engine.simulateReviewMove(...args,numMoves,iterations,bag,turns,played,seed));
            const plan = check(engine.planReviewSimulation(...args,numMoves,bag,turns,played,seed));
            const withoutActual = check(engine.planReviewSimulation(...args,numMoves,bag,turns,null,seed));
            const moves = plan.moves.map(() => ({wins:0,terminal:0,residual:0,scores:new Map()}));
            for (const [start,end] of [[0,1],[1,plan.moves.length]]) {
              for (let offset=0; offset<iterations;) {
                const batch = check(engine.simulateReviewBatch(
                  ...args,bag,turns,plan.moves.slice(start,end),seed,offset,Math.min(2,iterations-offset)));
                if (batch.offset !== offset || batch.completed < 1) throw new Error('Invalid batch progress');
                batch.moves.forEach((move,i) => {
                  if (key(move) !== key(plan.moves[start+i])) throw new Error('Candidate order changed');
                  if (move.samples !== batch.completed) throw new Error('Sample count changed');
                  const total = moves[start+i];
                  total.wins += move.winSum;
                  total.terminal += move.terminalSamples;
                  total.residual += move.residualSum;
                  for (const [level,player,score,count] of move.equityStatistics) {
                    const slot = 2*level+player;
                    const [previousScore,previousCount] = total.scores.get(slot) || [0,0];
                    total.scores.set(slot,[previousScore+score,previousCount+count]);
                  }
                });
                offset += batch.completed;
              }
            }
            const tieRank = new Map(plan.tieOrder.map((index,rank) => [index,rank]));
            const computed = moves.map((m,index) => ({
              index, key:key(plan.moves[index]), win:m.wins/iterations, terminal:m.terminal,
              equity:[...m.scores].sort((a,b) => a[0]-b[0]).reduce(
                (sum,[slot,[score,count]]) => sum+(slot%2?-1:1)*score/count,0) + m.residual/iterations
            })).sort((a,b) => b.win-a.win || b.equity-a.equity || tieRank.get(a.index)-tieRank.get(b.index));
            return {bag,turns,seed,iterations,plan,withoutActual,legacy,computed,
              expected:legacy.moves.map(move => ({key:key(move),win:move.win,equity:move.equity}))};
          });
        }""", endgame.EndgameSimulationTests.GRID)
        for case in cases:
            with self.subTest(bag=case["bag"], final_turns=case["turns"]):
                plan, legacy = case["plan"], case["legacy"]
                self.assertTrue(legacy["simulated"])
                self.assertEqual(plan["moves"], legacy["staticMoves"])
                self.assertEqual(plan["equityMoves"], legacy["staticMoves"])
                self.assertEqual(plan["bagEmptyingIndices"], [])
                self.assertEqual(sorted(plan["tieOrder"]), list(range(len(plan["moves"]))))
                for field in ("seed", "bagCount", "finalTurnsRemaining"):
                    self.assertEqual(plan[field], legacy[field])
                key = lambda move: tuple(move[field] for field in ("action", "position", "tiles"))
                self.assertEqual(key(plan["moves"][plan["actualIndex"]]), key(legacy["selected"]))
                without_actual = case["withoutActual"]
                self.assertEqual(without_actual["actualIndex"], -1)
                self.assertTrue(all(move in plan["moves"] for move in without_actual["moves"]))
                self.assertEqual(len(case["computed"]), len(case["expected"]))
                for computed, expected in zip(case["computed"], case["expected"]):
                    self.assertEqual(computed["key"], expected["key"])
                    self.assertEqual(computed["win"], expected["win"])
                    self.assertEqual(computed["terminal"], case["iterations"])
                    self.assertAlmostEqual(computed["equity"], expected["equity"], delta=1e-9)
                if case["turns"] == 2:
                    self.assertEqual(without_actual["moves"][0]["tiles"], "WET")
                    self.assertEqual(legacy["best"]["win"], 1)
                if case["turns"] == 1:
                    pairs = zip(legacy["moves"], legacy["moves"][1:])
                    self.assertTrue(any(a["win"] == b["win"] and a["equity"] == b["equity"]
                                        for a, b in pairs))

    def test_draw_split_matches_monolithic_and_reused_review(self):
        self.require_exports("simulateDrawBatch")
        results = self.page.evaluate("""grid => {
          const engine = testEngine;
          const check = result => {
            if (result.error) throw new Error(result.error);
            return result;
          };
          const pass = {action:'pass', position:'', tiles:''};
          const exchange = {action:'exchange', position:'', tiles:'W'};
          return [
            [grid,'WEAFTUO',0,2,pass,'WEAFTUO',0,110,100],
            [[ 'CD.............', ...grid.slice(1) ],'EIIKQXG',1,0,
              {action:'place',position:'K1',tiles:'GO'},'EIIKQXZ',1,95,110],
            [[ '..........G....', '...............', ...grid.slice(2) ],
              'WEAFTUO',8,0,exchange,'EAFTUOC',1,110,100],
            [[ '..........G....', '..............Y', ...grid.slice(2,4),
               '..........b.yA.', ...grid.slice(5) ],
              '?EAFTUO',8,0,{action:'place',position:'15B',tiles:'wET'},'AFUOCDW',3,110,100]
          ].map(([board,rack,bag,turns,played,rackAfter,drawCount,score,opp]) => {
            const args = [JSON.stringify(board),rack,score,opp];
            const seed = 123456789;
            const iterations = 7;
            const legacyArgs = [...args,3,iterations,bag,turns,played,seed];
            const review = check(engine.simulateReviewMove(...legacyArgs));
            const draw = check(engine.simulateReviewDraw(...legacyArgs,rackAfter));
            const batchArgs = [...args,bag,turns,played,seed,rackAfter];
            const split = (known) => {
              const parts = [];
              for (let offset=0; offset<iterations;) {
                const part = check(engine.simulateDrawBatch(
                  ...batchArgs,offset,Math.min(3,iterations-offset),known));
                if (part.offset !== offset || part.completed < 1) throw new Error('Invalid draw progress');
                parts.push(part);
                offset += part.completed;
              }
              return parts;
            };
            return {bag,drawCount,draw,reviewWin:review.selected.win,
              computed:split(-1),reused:split(review.selected.win)};
          });
        }""", endgame.EndgameSimulationTests.GRID)
        for case in results:
            with self.subTest(bag=case["bag"]):
                draw = case["draw"]
                self.assertEqual(draw["beforeWin"], case["reviewWin"])
                self.assertEqual(draw["drawCount"], case["drawCount"])
                if case["bag"] == 1:
                    self.assertEqual(draw["afterWin"], 0)
                iterations = draw["iterationsCompleted"]
                for run in (case["computed"], case["reused"]):
                    self.assertEqual(sum(part["completed"] for part in run), iterations)
                    for part in run:
                        self.assertTrue(part["simulated"])
                        self.assertEqual(part["iterationsCompleted"], part["completed"])
                        self.assertLessEqual(part["completed"], part["iterationsRequested"])
                        for field in ("seed", "bagCount", "finalTurnsRemaining", "selected",
                                      "samplingVersion", "rackAfter", "drawCount", "playToEnd",
                                      "plies", "winModel", "endgamePolicy", "simulationMode"):
                            self.assertEqual(part[field], draw[field])
                        if draw["drawCount"] == 0:
                            self.assertEqual(part["beforeWin"], part["afterWin"])
                            self.assertEqual(part["beforeWinSum"], part["afterWinSum"])
                    self.assertAlmostEqual(sum(p["beforeWinSum"] for p in run) / iterations,
                                           draw["beforeWin"], delta=1e-15)
                    self.assertEqual(sum(p["afterWinSum"] for p in run) / iterations, draw["afterWin"])

    def test_budget_stops_between_paired_trials(self):
        self.require_exports("simulateReviewBatch", "simulateDrawBatch")
        results = self.page.evaluate("""grid => {
          const engine = testEngine;
          const board = [ 'CD.............', ...grid.slice(1) ];
          const args = [JSON.stringify(board),'EIIKQXG',95,110,1,0];
          const played = {action:'place',position:'K1',tiles:'GO'};
          const review = (count) => engine.simulateReviewBatch(...args,[played],123456789,4,count);
          const draw = (count) => engine.simulateDrawBatch(...args,played,123456789,'EIIKQXZ',4,count,-1);
          const setNow = Object.getOwnPropertyDescriptor(Performance.prototype,'now');
          let time = 0;
          Object.defineProperty(Performance.prototype,'now',{...setNow, value:() => time += 8001});
          let stoppedReview, stoppedDraw;
          try {
            stoppedReview = review(16);
            stoppedDraw = draw(16);
          } finally {
            Object.defineProperty(Performance.prototype,'now',setNow);
          }
          return {stoppedReview, stoppedDraw, singleReview:review(1), singleDraw:draw(1)};
        }""", endgame.EndgameSimulationTests.GRID)
        review, draw = results["stoppedReview"], results["stoppedDraw"]
        for stopped in (review, draw):
            self.assertEqual(stopped["error"], "")
            self.assertEqual(stopped["offset"], 4)
            self.assertEqual(stopped["completed"], 1)
        self.assertEqual(review, results["singleReview"])
        self.assertEqual(draw["iterationsRequested"], 16)
        for field in ("beforeWinSum", "afterWinSum", "iterationsCompleted"):
            self.assertEqual(draw[field], results["singleDraw"][field])

    def test_invalid_batch_bounds_and_reused_draw_validation(self):
        self.require_exports("simulateReviewBatch", "simulateDrawBatch")
        result = self.page.evaluate("""grid => {
          const engine = testEngine;
          const args = [JSON.stringify(grid),'WEAFTUO',110,100,0,2];
          const pass = {action:'pass',position:'',tiles:''};
          const draw = (rackAfter,offset,count,known) =>
            engine.simulateDrawBatch(...args,pass,123456789,rackAfter,offset,count,known);
          const review = (moves,offset,count) =>
            engine.simulateReviewBatch(...args,moves,123456789,offset,count);
          return {
            invalid:[
              review([],0,1), review(Array(9).fill(pass),0,1), review(null,0,1),
              review([pass],0,0), review([pass],0,17), review([pass],276,2),
              review([pass],-1,1), review([pass],1.5,1), review([pass],2147483647,1),
              draw('WEAFTUZ',0,1,0.25), draw('WEAFTUO',0,1,NaN),
              draw('WEAFTUO',0,1,-0.1), draw('WEAFTUO',0,1,1.1), draw('WEAFTUO',NaN,1,-1),
              draw('WEAFTUO',277,1,-1), draw('WEAFTUO',0,17,-1)],
            last:review([pass],276,1),
            known:draw('WEAFTUO',276,1,.25)
          };
        }""", endgame.EndgameSimulationTests.GRID)
        self.assertTrue(all(entry["error"] for entry in result["invalid"]))
        self.assertEqual(result["last"]["error"], "")
        self.assertEqual(result["last"]["completed"], 1)
        self.assertEqual(result["last"]["offset"], 276)
        self.assertEqual(result["known"]["beforeWin"], .25)
        self.assertEqual(result["known"]["afterWin"], .25)


if __name__ == "__main__":
    unittest.main()
