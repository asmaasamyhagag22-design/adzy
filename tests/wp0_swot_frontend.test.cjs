/* Real inline functions, synthetic DOM/fetch. This is offline behavior, not a browser proof. */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(process.env.WP0_FRONTEND_SOURCE || path.join(__dirname, '../frontend/index.html'), 'utf8').replace(/\r\n/g, '\n');
function extract(name) {
  const start = source.search(new RegExp(`^(?:async )?function ${name}\\(`, 'm'));
  assert.ok(start >= 0, name);
  const tail = source.slice(start);
  const next = tail.slice(1).search(/^function |^async function /m);
  return next < 0 ? tail : tail.slice(0, next + 1);
}
function harness() {
  const elements = new Map();
  function element() { return {style:{}, dataset:{}, value:'', disabled:false, innerHTML:'', textContent:'', appendChild(){}, querySelectorAll(){return []}, closest(){return element()}}; }
  const document = {getElementById(id){ if(!elements.has(id))elements.set(id,element()); return elements.get(id); }};
  const state = {stratView:'swot'};
  const messages = [], requests = [];
  const context = {BaseeraStrategyState: require('../frontend/assets/strategy-state.js'), state, document, console:{error(){}}, URL, Object, encodeURIComponent,
    STRAT_TAB_ACTIONS:{swot:['strat-approve-swot-btn']}, authHeaders:()=>({}), logout(){},
    toast:(message)=>messages.push(message), el:element, escHtml:s=>String(s||''), swotItemHtml:i=>i.title,
    renderMediaPlanV2(){}, fillOwnerInputs(){}, renderSwotCaveat(){}, renderInfluencerView(){},
    applyStratStageGate(){}, loadCompetitors(){}, loadSocialPerformance(){}, loadBrandIntel(){},
    activeBrand:()=>state.studioBrand||state.dashboardBrand,
    fetch:async (url, options)=>{ requests.push({url, method:options.method, body:options.body&&JSON.parse(options.body)}); return context.response(url,options); },
  };
  vm.createContext(context);
  for(const name of ['api','renderStrategyDashboard','approveSwot'])vm.runInContext(extract(name),context);
  return {context,state,elements,messages,requests};
}
const A='00000000-0000-4000-8000-00000000000a', B='00000000-0000-4000-8000-00000000000b';
const brand = (id=A, summary='Reviewed A')=>({domain_key:'wp0.test',has_swot:true,swot_run_id:id,swot:{summary,strengths:[{title:summary}]}});
const ok = body=>({ok:true,status:200,json:async()=>body});

test('A remains displayed while current/state moves to B: POST A, stale refresh B, no automatic approval',async()=>{
  const h=harness(), a=brand();
  h.context.renderStrategyDashboard(a);
  a.swot_run_id=B; // mutable state must not change the identity captured with the DOM.
  h.state.studioBrand=brand(B,'Updated B');
  h.context.response=(url)=>url==='/api/swot/approve'
    ? {ok:false,status:409,json:async()=>({detail:'The SWOT changed'})}
    : ok(brand(B,'Updated B'));
  await h.context.approveSwot();
  assert.equal(h.requests[0].body.swot_run_id,A);
  assert.equal(h.requests[0].method,'POST');
  assert.equal(h.requests.filter(r=>r.method==='POST').length,1);
  assert.equal(h.elements.get('swot-summary').textContent,'Updated B');
  assert.equal(h.state.reviewedSwot.swot_run_id,B);
  assert.ok(h.messages.some(m=>m.includes('Review the updated analysis')));
  h.context.response=url=>ok(url==='/api/swot/approve'?{run_id:B,swot_approved:true}:brand(B,'Updated B'));
  await h.context.approveSwot(); // another explicit human action only.
  assert.equal(h.requests.filter(r=>r.method==='POST')[1].body.swot_run_id,B);
});

test('late approval result cannot replace a different displayed brand',async()=>{
  const h=harness();h.context.renderStrategyDashboard(brand());
  let finish;
  h.context.response=url=>url==='/api/swot/approve'?new Promise(resolve=>{finish=resolve}):ok(brand());
  const pending=h.context.approveSwot();
  h.context.renderStrategyDashboard({...brand(B,'Other brand'),domain_key:'other.test'});
  finish(ok({run_id:A,swot_approved:true}));await pending;
  assert.equal(h.state.reviewedSwot.domain_key,'other.test');
  assert.equal(h.elements.get('swot-summary').textContent,'Other brand');
});

test('empty successful SWOT still binds its real Run; legacy sends null without invented identity',async()=>{
  for(const run of [A,null]){
    const h=harness();h.context.renderStrategyDashboard({...brand(run),swot:{}});
    h.context.response=()=>ok({...brand(run),swot_approved:true});
    await h.context.approveSwot();
    assert.equal(h.requests[0].body.swot_run_id,run);
  }
});

test('a failed render clears previous reviewed identity',()=>{
  const h=harness();h.context.renderStrategyDashboard(brand());
  h.context.renderMediaPlanV2=()=>{throw new Error('render failed')};
  assert.throws(()=>h.context.renderStrategyDashboard(brand(B)));
  assert.equal(h.state.reviewedSwot,null);
});
