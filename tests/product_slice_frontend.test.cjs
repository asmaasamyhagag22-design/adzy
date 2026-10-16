/* Full Adzy module with startup disabled and synthetic DOM/API: unit behavior only.
 * The assembled authenticated browser journey is recorded separately. */
const {test}=require('node:test'), assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../frontend/assets/adzy-app.js'),'utf8');
function harness(){
 const nodes=new Map(),messages=[],requests=[];
 const node=()=>({innerHTML:'',textContent:'',value:'',disabled:false,hidden:false,dataset:{},
  style:{setProperty(){}},classList:{add(){},remove(){},toggle(){}},querySelector(){return null},querySelectorAll(){return []},insertAdjacentHTML(_,v){this.innerHTML+=v},focus(){}});
 const get=id=>{if(!nodes.has(id))nodes.set(id,node());return nodes.get(id)};
 const window={location:{hash:''},history:{replaceState(){}},scrollTo(){},
   AdzyStrategyState:require('../frontend/assets/strategy-state.js'),adzyLegacyState:{},
   toast:m=>messages.push(m),api:async(method,path,body)=>{requests.push({method,path,body});return window.response(method,path,body)}};
 const context={window,document:{getElementById:get,querySelectorAll(){return []},documentElement:{style:{setProperty(){}}}},URL,URLSearchParams,console,matchMedia:()=>({matches:true})};
 vm.createContext(context);vm.runInContext(source.replace(/\n  init\(\);\n\}\(\)\);\s*$/, '\n window.__test={renderResearch,renderStrategy,renderCreative,qaState,bridgeLegacyAuth};\n}());'),context);
 return {window,A:window.Adzy,nodes,get,messages,requests};
}
const a={domain_key:'synthetic',has_swot:true,swot_run_id:'A',swot_approved:false,swot:{summary:'Reviewed A'}};
test('Adzy displayed SWOT identity survives mutable data; 409 reloads B without auto approving',async()=>{
 const h=harness();h.A.brand=structuredClone(a);h.window.__test.renderResearch();
 h.A.brand.swot_run_id='B';h.window.response=async(method)=>{if(method==='POST')throw Object.assign(new Error('stale'),{status:409});return {...a,swot_run_id:'B',swot:{summary:'New B'}}};
 await h.window.adzyApproveSwot();
 assert.equal(h.requests[0].body.swot_run_id,'A');assert.equal(h.requests.filter(x=>x.method==='POST').length,1);
 assert.equal(h.A.reviewedSwot.id,'B');assert.match(h.get('adzy-research').innerHTML,/New B/);
 assert.ok(h.messages.some(m=>/Review/.test(m)));
});
test('Adzy generation leaves derived CTA to server and names exact approved StrategyVersion',async()=>{
 const h=harness();h.A.brand={...a,swot_approved:true};h.A.contract={strategy_version_id:'reviewed',cta_direction:'Visit website',language:'en'};
 h.window.response=async()=>{throw Error('Provider unavailable')};
 await h.window.adzyMakeAd();const r=h.requests[0];
 assert.equal(r.path,'/api/generate');assert.equal(r.body.strategy_version_id,'reviewed');assert.equal(r.body.cta,'');
 assert.match(h.get('adzy-poster-empty-note').textContent,/retry/);assert.equal(h.get('adzy-make-btn').disabled,false);
});
test('failed or unverified QA cannot become passed; synthetic accepted evidence is disclosed',()=>{
 const h=harness(),qa=h.window.__test.qaState;
 assert.equal(qa({qa_status:'pass',accepted:false},'visual')[1],'unknown');
 assert.equal(qa({qa_status:'not_checked',accepted:false},'visual')[1],'unknown');
 assert.equal(qa({qa_status:'fail',accepted:false},'visual')[1],'fail');
 assert.match(qa({qa_status:'pass',accepted:true,visual_qa:{reason:'SYNTHETIC_TEST: fixture'}},'visual')[0],/test evidence/);
 assert.equal(qa({claim_ids_used:['claim']},'claims')[1],'unknown');
});
test('navigation and brand changes never issue generation writes',async()=>{
 const h=harness();h.A.brand={...a,swot_approved:false};h.window.response=async()=>({...a,domain_key:'other'});
 await h.window.adzyNavigate('create');await h.window.adzySelectBrand('other',false);
 assert.ok(h.requests.every(r=>r.method==='GET'));assert.equal(h.A.contract,null);assert.equal(h.A.reviewedSwot.domain,'other');
});
test('rejected candidate remains rejected even if evidence sufficiency is still eligible',async()=>{
 const h=harness();h.A.brand={...a,swot_approved:true,strategy_approval_state:'rejected',strategy_candidate:{run_id:'rejected',approval_state:'rejected',eligible_for_approval:true}};
 h.window.__test.renderStrategy();await h.window.adzyLockStrategy();
 assert.equal(h.requests.length,0);assert.equal(h.A.reviewedStrategy.eligible,false);
 assert.match(h.get('adzy-approval').innerHTML,/This direction was rejected/);
 assert.doesNotMatch(h.get('adzy-approval').innerHTML,/id="adzy-lock-btn"/);
});
test('late SWOT approval cannot replace another brand selected while request is pending',async()=>{
 const h=harness();h.A.brand=structuredClone(a);h.window.__test.renderResearch();
 let finish;h.window.response=async(method)=>method==='POST'?new Promise(resolve=>{finish=resolve}):({...a,domain_key:'other',swot_run_id:'other-run'});
 const pending=h.window.adzyApproveSwot();await h.window.adzySelectBrand('other',false);
 finish({run_id:'A',swot_approved:true});await pending;
 assert.equal(h.A.brand.domain_key,'other');assert.equal(h.A.reviewedSwot.domain,'other');
 assert.equal(h.requests.filter(r=>r.method==='POST').length,1);
});
test('strategy 409 submits the reviewed candidate, refreshes without deciding again',async()=>{
 const h=harness(),candidate={run_id:'strategy-A',eligible_for_approval:true,approval_state:'pending'};
 h.A.brand={...a,swot_approved:true,strategy_candidate:candidate};h.window.__test.renderStrategy();candidate.run_id='strategy-B';
 h.window.response=async(method)=>{if(method==='POST')throw Object.assign(new Error('stale'),{status:409});return {...a,swot_approved:true,strategy_candidate:{...candidate}}};
 await h.window.adzyLockStrategy();assert.equal(h.requests[0].body.strategy_run_id,'strategy-A');
 assert.equal(h.requests.filter(r=>r.method==='POST').length,1);assert.equal(h.A.reviewedStrategy.id,'strategy-B');
});
test('logout invalidates in-flight creative responses and clears reviewed subjects',async()=>{
 const h=harness();h.A.brand={...a,swot_approved:true};h.A.contract={strategy_version_id:'reviewed'};
 h.window.__test.renderResearch();let finish;h.window.response=()=>new Promise(resolve=>{finish=resolve});
 h.window.logout=()=>{};h.window.__test.bridgeLegacyAuth();
 const pending=h.window.adzyMakeAd();h.window.logout();
 finish({accepted:true,image_b64:'synthetic'});await pending;
 assert.equal(h.A.brand,null);assert.equal(h.A.history.length,0);assert.equal(h.A.reviewedSwot,null);assert.equal(h.A.reviewedStrategy,null);
});
