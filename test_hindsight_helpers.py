"""Explicit hindsight fixtures for legacy display and navigation tests only."""

HINDSIGHT_FIXTURE_JS = r"""() => {
  window.syntheticPositionTrend = (review, quality, state = 'running') => {
    // Fixture numbers are supplied by each test. Production never derives
    // position WP from move grades.
    const points = (quality?.points || []).filter(p => Number.isFinite(p.playedWin)).flatMap(p => {
      const turn = review.turns[p.turnIndex];
      const mine = value => (turn.mine ? value : 1-value)*100;
      const draw = p.draw, measured = draw && draw.drawCount > 0 && Number.isFinite(draw.beforeWin) && Number.isFinite(draw.afterWin);
      const before = {x:p.turnIndex,phase:'before-draw',mine:mine(measured?draw.beforeWin:p.playedWin),
        provisional:Boolean(measured?draw.provisional:p.provisional),samples:p.samples || (p.provisional?64:277)};
      return measured ? [before,{...before,phase:'after-draw',mine:mine(draw.afterWin),draw}] : [before];
    });
    return window.syntheticTrendFromPoints(review, {state,points}, quality?.opening);
  };
  window.syntheticTrendFromPoints = (review, trend, opening) => {
    const points=trend.points.map(p=>({...p,informationModel:'hindsight-state-v1'}));
    const missing=()=>({mine:null});
    const openingKnown=opening && !opening.unavailable && !opening.retryable && Number.isFinite(opening.afterWin);
    const firstMine=opening?.firstMine ?? review.turns[0]?.mine;
    const initial=openingKnown ? {mine:(firstMine?opening.afterWin:1-opening.afterWin)*100,
      provisional:Boolean(opening.provisional),samples:opening.samples} : {...missing(),unavailable:Boolean(opening?.unavailable),reason:opening?.reason};
    const turns=review.turns.map((turn,x)=>{
      const move=points.find(p=>p.x===x&&p.phase!=='after-draw') || missing();
      const draw=points.find(p=>p.x===x&&p.phase==='after-draw');
      let noPreviousDraw=false;
      if(x>0){try{noPreviousDraw=CrossplayReview.rackDraw(review.turns[x-1]).drawCount===0;}catch(_){}}
      const before=x===0?initial:(points.find(p=>p.x===x-1&&p.phase==='after-draw') ||
        (noPreviousDraw?points.find(p=>p.x===x-1&&p.phase!=='after-draw'):null) || missing());
      return {before,move,draw:draw || move,hasDraw:Boolean(draw)};
    });
    return deriveWinTrend({review,timeline:{...trend,informationModel:'hindsight-state-v1',points,turns,opening:initial}});
  };
}"""


def install_hindsight_fixtures(page):
    page.evaluate(HINDSIGHT_FIXTURE_JS)
