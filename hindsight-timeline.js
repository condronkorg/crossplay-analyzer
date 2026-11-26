/* Known physical states. Decision grades never supply this timeline's WP. */
(function(root) {
  'use strict';
  const node = typeof module !== 'undefined' && module.exports;
  const counts = node ? require('./luck-analysis.js').TILE_COUNTS : root.CrossplayLuck.TILE_COUNTS;
  const reviewApi = () => node ? require('./review-analysis.js') : root.CrossplayReview;
  const bagApi = () => node ? require('./bag-emptying-analysis.js') : root.CrossplayBagEmptying;
  const MODEL = 'hindsight-state-v1';
  const POLICY = MODEL + '/crossplay-endgame-v3';
  const sort = tiles => [...tiles].sort().join('');
  function remove(pool, tiles) {
    for (const tile of tiles) {
      const i=pool.indexOf(tile);
      if(i<0) throw Error('Known racks and board do not conserve physical tiles.');
      pool.splice(i,1);
    }
  }
  function canonical(input) {
    if (!input || !Array.isArray(input.grid) || input.grid.length!==15 ||
      !input.grid.every(row=>typeof row==='string' && /^[A-Za-z.]{15}$/.test(row)) ||
      !Array.isArray(input.racks) || input.racks.length!==2 ||
      !input.racks.every(r=>typeof r==='string' && /^[A-Z?]{0,7}$/.test(r)) ||
      !Array.isArray(input.scores) || input.scores.length!==2 || !input.scores.every(n=>Number.isInteger(n)&&n>=0&&n<=100000) ||
      ![0,1].includes(input.toMove) || !Number.isInteger(input.bagCount) || input.bagCount<0 || input.bagCount>100 ||
      !Number.isInteger(input.finalTurnsRemaining) || input.finalTurnsRemaining<0 || input.finalTurnsRemaining>2)
      throw Error('Known position data is unavailable.');
    const s={grid:input.grid.slice(),racks:input.racks.map(sort),scores:input.scores.slice(),
      toMove:input.toMove,bagCount:input.bagCount,finalTurnsRemaining:input.finalTurnsRemaining};
    const p=input.pendingDraw;
    if(p){
      if(![0,1].includes(p.player)||p.player===s.toMove||!Number.isInteger(p.count)||p.count<1||p.count>s.bagCount||
        typeof p.returnedTiles!=='string'||!/^[A-Z?]{0,7}$/.test(p.returnedTiles)||
        (p.returnedTiles.length && p.returnedTiles.length!==p.count)||s.racks[p.player].length+p.count>7)
        throw Error('Invalid pending refill.');
      s.pendingDraw={player:p.player,count:p.count,returnedTiles:sort(p.returnedTiles)};
      if(s.racks[1-p.player].length!==7 || (!p.returnedTiles && p.count!==Math.min(7-s.racks[p.player].length,s.bagCount)))
        throw Error('Pending refill does not fill the recorded hand.');
    }
    if(s.bagCount>0 && s.finalTurnsRemaining!==0) throw Error('Invalid final-turn countdown.');
    const lengths=s.racks.map(r=>r.length);
    if(p)lengths[p.player]+=p.count;
    if(s.bagCount-(p?.count||0)+(p?.returnedTiles.length||0)>0 && lengths.some(n=>n!==7))
      throw Error('Complete current hands are required.');
    const pool=Object.entries(counts).flatMap(([t,n])=>Array(n).fill(t));
    remove(pool,[...s.grid.join('')].filter(t=>t!=='.').map(t=>t===t.toLowerCase()?'?':t));
    remove(pool,s.racks.join('')); remove(pool,p?.returnedTiles||'');
    if(pool.length!==s.bagCount) throw Error('Known racks and bag count do not match.');
    return s;
  }
  function opponentRack(review,index) {
    const turn=review.turns[index];
    if(Object.hasOwn(turn,'opponent_rack'))return typeof turn.opponent_rack==='string'?turn.opponent_rack:null;
    const evidence=[];
    for(let j=index+1;j<review.turns.length;j++)if(Boolean(review.turns[j].mine)!==Boolean(turn.mine)){
      if(typeof review.turns[j].rack==='string')evidence.push(sort(review.turns[j].rack));break;
    }
    for(let j=index-1;j>=0;j--)if(Boolean(review.turns[j].mine)!==Boolean(turn.mine)){
      if(typeof review.turns[j].rack_after==='string')evidence.push(sort(review.turns[j].rack_after));break;
    }
    return evidence.length&&evidence.every(r=>r===evidence[0])?evidence[0]:null;
  }
  function turnStates(review,index) {
    const turn=review.turns[index], out={before:null,move:null,draw:null,error:null};
    try {
      const actor=turn.mine?0:1,other=1-actor,racks=[]; racks[actor]=turn.rack;racks[other]=opponentRack(review,index);
      const scores=[];scores[actor]=Number(turn.mover_score);scores[other]=Number(turn.other_score);
      out.before=canonical({grid:turn.grid,racks,scores,toMove:actor,bagCount:turn.bag_count,
        finalTurnsRemaining:turn.bag_count===0?turn.final_turns_remaining:0});
      const move=reviewApi().playedMove(turn), s={...out.before,grid:out.before.grid.slice(),racks:out.before.racks.slice(),scores:scores.slice(),toMove:other};
      let used='';
      if(move.action==='place'){
        const a=move.position.match(/^(\d+)([A-O])$/),d=move.position.match(/^([A-O])(\d+)$/);
        let row=Number(a?a[1]:d[2])-1,col='ABCDEFGHIJKLMNO'.indexOf(a?a[2]:d[1]);
        for(const t of move.tiles){
          if(!s.grid[row]||col>14)throw Error('Move is outside the board.');
          const old=s.grid[row][col];
          if(old==='.'){
            if(t==='.')throw Error('Move refers to a missing board tile.');
            used+=t===t.toLowerCase()?'?':t;
            s.grid[row]=s.grid[row].slice(0,col)+t+s.grid[row].slice(col+1);
          } else if(t!=='.'&&t.toUpperCase()!==old.toUpperCase())throw Error('Move conflicts with the board.');
          if(a)col++;else row++;
        }
        if(!used)throw Error('Move places no tiles.');
      } else if(move.action==='exchange')used=move.tiles;
      const retained=[...s.racks[actor]];remove(retained,used);s.racks[actor]=retained.join('');
      const score=Number.isFinite(turn.score)?turn.score:Number(String(turn.action).match(/(-?\d+)\s*$/)?.[1]);
      if(!Number.isInteger(score)||(move.action!=='place'&&score!==0))throw Error('Move score is unavailable.');
      s.scores[actor]+=score;
      if(s.bagCount===0)s.finalTurnsRemaining--;
      const n=move.action==='pass'?0:move.action==='exchange'?used.length:Math.min(7-retained.length,s.bagCount);
      if(n)s.pendingDraw={player:actor,count:n,returnedTiles:move.action==='exchange'?used:''};
      out.move=canonical(s);
      if(!n){out.draw=out.move;return out;}
      if(typeof turn.rack_after!=='string')return out;
      const after=sort(turn.rack_after),drawn=[...after];remove(drawn,retained);
      if(drawn.length!==n)throw Error('Recorded refill has the wrong size.');
      const pool=Object.entries(counts).flatMap(([t,c])=>Array(c).fill(t));
      remove(pool,[...s.grid.join('')].filter(t=>t!=='.').map(t=>t===t.toLowerCase()?'?':t));remove(pool,s.racks.join(''));remove(pool,s.pendingDraw.returnedTiles);remove(pool,drawn);
      const next={...s,racks:s.racks.slice(),bagCount:s.bagCount-n+s.pendingDraw.returnedTiles.length};delete next.pendingDraw;
      next.racks[actor]=after;
      if(s.bagCount>0&&next.bagCount===0)next.finalTurnsRemaining=2;
      out.draw=canonical(next);
    } catch(error){out.error=error.message;}
    return out;
  }
  function settledBag(state){return state.bagCount-(state.pendingDraw?.count||0)+(state.pendingDraw?.returnedTiles.length||0);}
  function stateId(state){return JSON.stringify(canonical(state));}
  function key(state,iterations){return JSON.stringify([MODEL,canonical(state),iterations,POLICY]);}
  function seed(state){let hash=2166136261;for(const c of JSON.stringify([MODEL,canonical(state),POLICY])){hash^=c.charCodeAt(0);hash=Math.imul(hash,16777619);}return hash>>>0;}
  function fullIterations(state){return bagApi().fullIterations(settledBag(state));}
  function validResult(r,state,iterations,offset=0,count=iterations){
    const bag=settledBag(state),end=bag<=21;
    return Boolean(r&&r.error===''&&r.informationModel===MODEL&&r.policyVersion===POLICY&&r.seed===seed(state)&&
      r.iterationsRequested===iterations&&r.offset===offset&&r.samples===count&&r.completed===count&&
      Number.isFinite(r.winSum)&&r.winSum>=0&&r.winSum<=count&&r.win===r.winSum/count&&
      Number.isInteger(r.terminalSamples)&&r.terminalSamples>=0&&r.terminalSamples<=count&&(!end||r.terminalSamples===count)&&
      r.bagCount===state.bagCount&&r.settledBagCount===bag&&r.finalTurnsRemaining===state.finalTurnsRemaining&&
      r.plies===(end?-1:2)&&r.playToEnd===end&&r.simulationMode===(end?'play-to-end':'2-ply')&&r.simulated===true);
  }
  const api={MODEL,POLICY,canonical,turnStates,opponentRack,settledBag,stateId,key,seed,fullIterations,validResult};
  if(node)module.exports=api;else root.CrossplayHindsight=api;
})(globalThis);
