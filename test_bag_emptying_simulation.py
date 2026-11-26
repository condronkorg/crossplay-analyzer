"""Synthetic low-bag coverage and paired batch checks in real WASM."""
import json
import unittest
import test_endgame_simulation as endgame


class BagEmptyingSimulationTests(unittest.TestCase):
    setUpClass = classmethod(endgame.EndgameSimulationTests.setUpClass.__func__)
    tearDownClass = classmethod(endgame.EndgameSimulationTests.tearDownClass.__func__)

    def test_phase_equity_shortlist_is_bounded_and_preserves_static_moves(self):
        grid = list(endgame.EndgameSimulationTests.GRID)
        grid[0] = '..........G....'
        result = self.page.evaluate("""grid => {
          const args=[JSON.stringify(grid),'WEAFTUO',110,100];
          const all=testEngine.kibitz(...args,1000000).moves;
          const plan=testEngine.planBagEmptying(...args,null,123456789);
          return {all,plan};
        }""", grid)
        plan = result['plan']
        self.assertFalse(plan.get('error'), plan)
        self.assertLessEqual(len(plan['moves']), 30)
        self.assertEqual(plan['generatedCandidateCount'], len(result['all']))
        self.assertEqual(plan['totalBagEmptyingCount'], sum(
            move['action'] == 'place' and 7 - len(move['leave']) >= 2
            for move in result['all']))
        key = lambda move: (move['action'], move['position'], move['tiles'])
        included = set(map(key, plan['moves']))
        self.assertTrue(set(map(key, result['all'][:15])).issubset(included))
        self.assertIn(('place', '15B', 'FET'), included)

    def test_short_rack_counts_only_tiles_physically_placed(self):
        grid = list(endgame.EndgameSimulationTests.GRID)
        grid[0] = '..........G....'
        result = self.page.evaluate("""grid => {
          const rack='WEAFTO', args=[JSON.stringify(grid),rack,110,100];
          return {rack,all:testEngine.kibitz(...args,1000000).moves,
            plan:testEngine.planBagEmptying(...args,null,123456789)};
        }""", grid)
        plan = result['plan']
        self.assertFalse(plan.get('error'), plan)
        rack_size = len(result['rack'])
        drains = lambda move: (move['action'] == 'place' and
                               rack_size - len(move['leave']) >= plan['bagCount'])
        self.assertEqual(plan['totalBagEmptyingCount'], sum(map(drains, result['all'])))
        self.assertEqual(plan['bagEmptyingIndices'], [
            index for index, move in enumerate(plan['moves']) if drains(move)])

    def test_all_emptying_moves_and_sample_offsets(self):
        grid = list(endgame.EndgameSimulationTests.GRID)
        grid[0] = '..........G....'
        result = self.page.evaluate('''grid => {
          const m=testEngine;
          const args=[JSON.stringify(grid),'WEAFTUO',110,100];
          const plan=m.planBagEmptying(...args,{action:'pass',position:'',tiles:''},123456789);
          if(plan.error) throw new Error(plan.error);
          const all=m.kibitz(...args,1000000).moves;
          const required=all.filter(v=>v.action==='place' && 7-v.leave.length>=2);
          const first=plan.moves.slice(0,2);
          const batch=(offset,count)=>m.simulateCandidateBatch(...args,2,first,123456789,offset,count);
          const legacy=m.simulateReviewMove(...args,15,1,2,0,{action:'pass',position:'',tiles:''},123456789);
          return {plan,required,legacyStatic:legacy.staticMoves,whole:batch(0,4),a:batch(0,2),b:batch(2,2)};
        }''', grid)
        plan = result['plan']
        self.assertEqual(plan['bagCount'], 2)
        self.assertEqual(plan['equityMoves'], result['legacyStatic'])
        required = {(m['position'], m['tiles']) for m in result['required']}
        planned = {(plan['moves'][i]['position'], plan['moves'][i]['tiles']) for i in plan['bagEmptyingIndices']}
        self.assertTrue(planned.issubset(required))
        self.assertEqual(plan['totalBagEmptyingCount'], len(required))
        self.assertEqual(plan['moves'][plan['actualIndex']]['action'], 'pass')
        for i, whole in enumerate(result['whole']['moves']):
            self.assertEqual(whole['win'], (result['a']['moves'][i]['win'] + result['b']['moves'][i]['win']) / 2)
            self.assertEqual(whole['terminalSamples'], 4)

    def test_emptying_winner_outside_shortlist_and_emptying_loser(self):
        grid = list(endgame.EndgameSimulationTests.GRID)
        grid[0] = '..........G....'
        result = self.page.evaluate('''grid => {
          const args=[JSON.stringify(grid),'WEAFTUO',110,100];
          const all=testEngine.kibitz(...args,1000000).moves;
          const find=(tiles,position)=>all.find(m=>m.tiles===tiles && m.position===position);
          const moves=[find('FET','15B'),find('OUTWEAR','D1')];
          const plan=testEngine.planBagEmptying(...args,null,123456789);
          return {rank:all.indexOf(moves[0])+1,moves,plan,
            batch:testEngine.simulateCandidateBatch(...args,2,moves,123456789,0,8)};
        }''', grid)
        self.assertGreater(result['rank'], 15)
        self.assertTrue(all(7 - len(m['leave']) >= 2 for m in result['moves']))
        planned = result['plan']['moves']
        self.assertTrue(any(m['tiles'] == 'FET' and m['position'] == '15B' for m in planned))
        winner, inferior = result['batch']['moves']
        self.assertEqual(winner['win'], 1)
        self.assertLess(inferior['win'], winner['win'])
        self.assertEqual(winner['terminalSamples'], 8)
        self.assertEqual(inferior['terminalSamples'], 8)

    def test_bag_thresholds_use_physical_tiles(self):
        result = self.page.evaluate('''base => {
          return [0,1,7,8].map(bag=>{
            const grid=[...base];
            if(bag===1) grid[0]='.D........G....';
            if(bag>=7) {grid[0]='..........G....';grid[1]=bag===7?'..............Y':'...............';}
            const args=[JSON.stringify(grid),'WEAFTUO',110,100];
            const plan=testEngine.planBagEmptying(...args,null,123456789);
            if(plan.error)return {bag,error:plan.error};
            const all=testEngine.kibitz(...args,1000000).moves;
            const physical=m=>m.action==='place' && 7-m.leave.length>=bag;
            return {bag,observed:plan.bagCount,
              expected:all.filter(physical).map(m=>m.position+':'+m.tiles).sort(),
              actual:plan.bagEmptyingIndices.map(i=>plan.moves[i]).map(m=>m.position+':'+m.tiles).sort()};
          });
        }''', endgame.EndgameSimulationTests.GRID)
        for case in result:
            with self.subTest(bag=case['bag']):
                if case['bag'] in (0, 8):
                    self.assertIn('error', case)
                else:
                    self.assertEqual(case['observed'], case['bag'])
                    self.assertTrue(set(case['actual']).issubset(case['expected']))

    def test_partitioned_equity_matches_legacy_with_variable_game_lengths(self):
        grid = list(endgame.EndgameSimulationTests.GRID)
        grid[0] = '..........G....'
        grid[1] = '..............Y'
        self.page.add_script_tag(url=f'http://127.0.0.1:{self.server.server_port}/bag-emptying-analysis.js')
        result = self.page.evaluate("""grid => {
          const args=[JSON.stringify(grid),'WEAFTUO',110,100];
          const all=testEngine.kibitz(...args,1000000).moves;
          const move=all.find(move=>move.action==='place' && move.leave.length===6);
          const legacy=testEngine.simulateReviewMove(...args,1,64,7,0,move,123456789);
          const state=CrossplayBagEmptying.create({moves:[move],equityMoves:[move],actualIndex:0,
            bagEmptyingIndices:[],seed:123456789,bagCount:7,finalTurnsRemaining:0});
          let variable=false;
          for(let offset=0;offset<64;offset+=8) {
            const batch=testEngine.simulateCandidateBatch(...args,7,[move],123456789,offset,8);
            if(batch.error) throw new Error(batch.error);
            variable ||= batch.moves[0].equityStatistics.some(entry=>entry[3]!==8);
            CrossplayBagEmptying.incorporate(state,{...batch,start:0});
          }
          return {legacy:legacy.selected,partitioned:CrossplayBagEmptying.finish(state,64).selected,variable};
        }""", grid)
        self.assertTrue(result['variable'])
        self.assertEqual(result['legacy']['win'], result['partitioned']['win'])
        self.assertAlmostEqual(result['legacy']['equity'], result['partitioned']['equity'], places=10)
