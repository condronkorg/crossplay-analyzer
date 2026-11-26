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
        self.assertLessEqual(len(plan['moves']), 15)
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

    def test_ordinary_kibitz_uses_authoritative_crossplay_equity(self):
        grid = list(endgame.EndgameSimulationTests.GRID)
        grid[0] = '..........G....'
        result = self.page.evaluate("""grid => {
          const args=[JSON.stringify(grid),'WEAFTUO',110,100];
          return {all:testEngine.kibitz(...args,1000000).moves,
            normal:testEngine.kibitz(...args,15).moves,
            plan:testEngine.planBagEmptying(...args,null,123456789)};
        }""", grid)
        move = next(move for move in result['all']
                    if move['tiles'] == 'FET' and move['position'] == '15B')
        self.assertEqual(move['score'], 41)
        self.assertAlmostEqual(move['equity'], 41.4140625, places=10)
        self.assertLessEqual(result['all'].index(move) + 1, 15)
        self.assertEqual(result['plan']['moves'], result['normal'])
        self.assertEqual(result['plan']['equityMoves'], result['normal'])

    def test_authoritative_equity_bag_boundaries_and_physical_overlap(self):
        result = self.page.evaluate("""base => [0,1,7,8].map(bag => {
          const grid=[...base];
          if(bag===1)grid[0]='.D........G....';
          if(bag>=7){grid[0]='..........G....';grid[1]=bag===7?'..............Y':'...............';}
          const result=testEngine.kibitz(JSON.stringify(grid),'WEAFTUO',110,100,1000000);
          if(result.error)throw Error(result.error);
          const move=result.moves.find(m=>m.position==='D1'&&m.tiles==='OUTWEAR');
          if(!move)throw Error('Missing overlap fixture');
          return {bag,move};
        })""", endgame.EndgameSimulationTests.GRID)
        expected = {0: 51, 1: 59.89453125, 7: 49.89453125, 8: 55.7890625}
        for case in result:
            with self.subTest(bag=case['bag']):
                self.assertEqual(case['move']['leave'], 'F')
                self.assertAlmostEqual(case['move']['equity'], expected[case['bag']], places=10)

    def test_authoritative_cache_separates_swap_from_draining_placement_and_blanks(self):
        result = self.page.evaluate("""base => [false,true].map(blank => {
          const grid=[...base];
          let removed=0;
          for(let row=0;row<15&&removed<6;row++) {
            const letters=[...grid[row]];
            for(let col=0;col<15&&removed<6;col++)if(letters[col]!=='.'){letters[col]='.';removed++;}
            grid[row]=letters.join('');
          }
          if(blank)grid[4]=grid[4].replace('a','A');
          const rack=blank?'WE?FTUO':'WEAFTUO';
          const all=testEngine.kibitz(JSON.stringify(grid),rack,110,100,1000000).moves;
          const placement=all.find(m=>m.action==='place'&&m.position==='D1'&&m.tiles.toUpperCase()==='OUTWEAR'&&m.leave==='F');
          const swap=all.find(m=>m.action==='exchange'&&m.leave==='F');
          return {blank,placement,swap};
        })""", endgame.EndgameSimulationTests.GRID)
        for case in result:
            with self.subTest(blank=case['blank']):
                self.assertIsNotNone(case['placement'])
                self.assertIsNotNone(case['swap'])
                self.assertAlmostEqual(case['placement']['equity'] - case['placement']['score'],
                                       8.89453125, places=10)
                self.assertAlmostEqual(case['swap']['equity'], -1.10546875, places=10)

    def test_generated_equity_exactly_matches_direct_evaluator_across_scores(self):
        grid = list(endgame.EndgameSimulationTests.GRID)
        grid[0] = '..........G....'
        result = self.page.evaluate("""grid => {
          const args=[JSON.stringify(grid),'WEAFTUO',110,100];
          const all=testEngine.kibitz(...args,1000000).moves;
          const groups=new Map();
          for(const move of all.slice(1)) {
            const key=move.action+':'+move.leave;
            if(!groups.has(key))groups.set(key,[]);
            groups.get(key).push(move);
          }
          let checked=0,distinctScoreGroups=0;
          for(const group of groups.values()) {
            if(new Set(group.map(m=>m.score)).size<2)continue;
            distinctScoreGroups++;
            for(const move of [group[0],group.at(-1)]) {
              const plan=testEngine.planReviewSimulation(...args,1,2,0,move,123456789);
              if(plan.error)throw Error(plan.error);
              if(plan.actualIndex!==1)throw Error('Fixture must append independently evaluated action');
              const direct=plan.moves[plan.actualIndex];
              if(direct.equity!==move.equity)throw Error('Cached/direct floating-point equity mismatch');
              checked++;
            }
            if(distinctScoreGroups===12)break;
          }
          return {checked,distinctScoreGroups};
        }""", grid)
        self.assertEqual(result['distinctScoreGroups'], 12)
        self.assertEqual(result['checked'], 24)

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

    def test_authoritative_shortlist_retains_known_emptying_winner_and_loser(self):
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
        self.assertLessEqual(result['rank'], 15)
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
