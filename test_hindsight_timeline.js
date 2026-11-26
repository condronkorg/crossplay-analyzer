const {test} = require('node:test');
const assert = require('node:assert/strict');
const H = require('./hindsight-timeline.js');
const grid = Array(15).fill('.'.repeat(15));
const first = {grid, rack:'CATERS?', opponent_rack:'DEFINOU', mine:true,
  mover_score:0, other_score:0, bag_count:86, final_turns_remaining:0,
  action:'CAT 8G 10', score:10, rack_after:'ERS?ING'};
test('known states separate physical move, unknown refill and observed draw',()=>{
  const states=H.turnStates({turns:[first]},0);
  assert.equal(states.before.toMove,0);
  assert.equal(states.move.toMove,1);
  assert.deepEqual(states.move.pendingDraw,{player:0,count:3,returnedTiles:''});
  assert.equal(states.move.racks[0],'?ERS');
  assert.equal(states.move.bagCount,86);
  assert.equal(states.draw.bagCount,83);
  assert.equal(states.draw.racks[0],'?EGINRS');
  assert.equal(states.draw.pendingDraw,undefined);
});
test('after draw and next before share identity, independent of future actions',()=>{
  const prior=H.turnStates({turns:[first]},0);
  const next={...first,grid:prior.draw.grid,rack:'DEFINOU',opponent_rack:'ERS?ING',
    mine:false,mover_score:0,other_score:10,bag_count:83,score:0,action:'PASS 0',rack_after:'DEFINOU'};
  assert.equal(H.stateId(prior.draw),H.stateId(H.turnStates({turns:[next]},0).before));
  assert.equal(H.seed(prior.draw),H.seed(H.turnStates({turns:[{...next,action:'swap -- 0',swapped_tiles:'DE'}]},0).before));
  assert.equal(H.stateId(H.turnStates({turns:[next]},0).move),H.stateId(H.turnStates({turns:[next]},0).draw));
});
test('unknown actual refill does not erase the known pending-move state',()=>{
  const states=H.turnStates({turns:[{...first,rack_after:null}]},0);
  assert.ok(states.before);assert.ok(states.move);assert.equal(states.draw,null);
  assert.equal(H.turnStates({turns:[{...first,opponent_rack:null}]},0).before,null);
});
test('exchange escrow stays outside the bag until refill',()=>{
  const states=H.turnStates({turns:[{...first,score:0,action:'swap -- 0',swapped_tiles:'CA',rack_after:'TERS?IN'}]},0);
  assert.deepEqual(states.move.pendingDraw,{player:0,count:2,returnedTiles:'AC'});
  assert.equal(states.move.racks[0],'?ERST');assert.equal(states.draw.bagCount,86);
  assert.throws(()=>H.canonical({...states.move,pendingDraw:{...states.move.pendingDraw,count:87}}));
  assert.equal(H.turnStates({turns:[{...first,action:'swap -- 0',swapped_tiles:null}]},0).move,null);
});
test('canonical keys retain both racks and model, but not sample-dependent seeds',()=>{
  const state=H.turnStates({turns:[first]},0).before;
  assert.equal(H.stateId(state),H.stateId({...state,racks:['?SRE TAC'.replace(' ',''),'UONIFED']}));
  assert.notEqual(H.key(state,64),H.key(state,277));
  assert.equal(JSON.parse(H.key(state,64))[0],H.MODEL);
  assert.throws(()=>H.canonical({...state,racks:['ZZZZZZZ','DEFINOU']}));
});
test('explicit unknown rack and conflicting adjacent evidence stay unavailable',()=>{
  const next={...first,mine:false,rack:'DEFINOU'};
  assert.equal(H.opponentRack({turns:[{...first,opponent_rack:null},next]},0),null);
  const legacy={...first};delete legacy.opponent_rack;
  assert.equal(H.opponentRack({turns:[legacy,next]},0),'DEFINOU');
  assert.equal(H.opponentRack({turns:[{...next,rack_after:'AAAAAAA'},legacy,next]},1),null);
});
