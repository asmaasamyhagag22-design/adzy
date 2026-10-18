const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm'),{randomUUID}=require('node:crypto');
const source=fs.readFileSync(require('node:path').join(__dirname,'../frontend/assets/adzy-app.js'),'utf8');
function harness(storage=new Map()) {
 const nodes=new Map(),requests=[];
 const node=()=>({innerHTML:'',textContent:'',value:'',disabled:false,hidden:false,dataset:{},style:{setProperty(){}},classList:{add(){},remove(){},toggle(){}},querySelector(){return null},querySelectorAll(){return []},insertAdjacentHTML(_,v){this.innerHTML+=v},focus(){}});
 const get=id=>{if(!nodes.has(id))nodes.set(id,node());return nodes.get(id)};
 const window={location:{hash:''},history:{replaceState(){}},scrollTo(){},crypto:{randomUUID},localStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v)},
   AdzyStrategyState:require('../frontend/assets/strategy-state.js'),adzyLegacyState:{currentUser:{id:'owner'}},toast(){},
   api:async(method,path,body)=>{requests.push({method,path,body});return window.response(method,path,body)}};
 const context={window,document:{getElementById:get,querySelectorAll(){return []},documentElement:{style:{setProperty(){}}}},URL,URLSearchParams,console,matchMedia:()=>({matches:true})};
 vm.createContext(context);vm.runInContext(source.replace(/\n  init\(\);\n\}\(\)\);\s*$/, '\n window.__test={restorePosterAttempt,projectAttempt,renderCreative,renderHeroPoster,renderResearch,renderStrategy,bootstrapData};\n}());'),context);
 const A=window.Adzy;A.brand={domain_key:'synthetic',swot_approved:true,strategy_approval_state:'approved'};A.contract={strategy_version_id:'strategy',language:'en'};A.review={status:'READY_FOR_CREATIVE'};
 return {window,A,get,requests,storage};
}
test('lost response retries the saved exact request and operation key',async()=>{
 const h=harness();h.window.response=async()=>{throw Error('response lost')};
 await h.window.adzyMakeAd();const first=h.requests[0].body;
 h.A.contract.strategy_version_id='new strategy';await h.window.adzyMakeAd();
 assert.equal(h.requests[1].body.operation_key,first.operation_key);
 assert.equal(h.requests[1].body.strategy_version_id,'strategy');
 assert.match(h.get('adzy-generation-error').textContent,/key is retained/);
});
test('fresh module restores unknown persisted attempt, never issues another POST',async()=>{
 const shared=new Map();const old=harness(shared);old.window.response=async()=>{throw Error('lost')};await old.window.adzyMakeAd();
 const key=old.requests[0].body.operation_key;const fresh=harness(shared);
 fresh.window.response=async()=>({operation:{operation_key:key,state:'unknown'}});
 await fresh.window.__test.restorePosterAttempt(0);await fresh.window.adzyMakeAd(true);
 assert.equal(fresh.A.latestAttempt.state,'unknown');assert.ok(fresh.requests.every(r=>r.method==='GET'));
 assert.match(fresh.get('adzy-generation-error').textContent,/Reconciliation/);
});
test('latest failure remains distinct from the previous accepted image; explicit variation has new identity',async()=>{
 const h=harness();h.A.creative={generation_run_id:'old',image_b64:'old',accepted:true};
 h.window.__test.projectAttempt({operation_key:'failed',state:'failed'});
 assert.equal(h.A.creative.generation_run_id,'old');assert.match(h.get('adzy-generation-error').textContent,/previous saved result/);
 h.window.response=async(_,__,request)=>({operation_key:request.operation_key,state:'failed'});
 await h.window.adzyMakeAd(true);const key=h.requests[0].body.operation_key;
 await h.window.adzyMakeAd();assert.equal(h.requests[1].body.operation_key,key);
 await h.window.adzyMakeAd(true);assert.notEqual(h.requests[2].body.operation_key,key);
});
test('storage failure refuses dispatch',async()=>{
 const h=harness();h.window.localStorage.setItem=()=>{throw Error('unavailable')};
 await h.window.adzyMakeAd();assert.equal(h.requests.length,0);
});
test('make another after response loss checks the saved attempt before rotating identity',async()=>{
 const h=harness();h.window.response=async()=>{throw Error('lost')};await h.window.adzyMakeAd();
 const key=h.requests[0].body.operation_key;
 h.window.response=async()=>({operation_key:key,state:'unknown'});
 await h.window.adzyMakeAd(true);
 assert.equal(h.requests.filter(r=>r.method==='POST').length,1);
 assert.match(h.requests[1].path,new RegExp(key));
 assert.equal(JSON.parse([...h.storage.values()][0]).operation_key,key);
});
test('persisted draft distinguishes factual uncertainty from visual PASS and escapes evidence text',async()=>{
 const h=harness();
 const item={image_b64:'synthetic',accepted:false,qa_status:'pass',canonical:true,
  generation_run_id:'draft-run',factual_eligible:false,strategy_verification:{overall_pass:true},
  factual_assessment:{status:'UNRESOLVED',propositions:[{proposition_id:'p',text:'We never close <script>',location:'headline'}],
   judgments:[{proposition_id:'p',verdict:'INSUFFICIENT_EVIDENCE',reason:'No scoped evidence',evidence:[],actions:['REQUEST_EVIDENCE_OR_REVIEW']}]}};
 h.window.__test.projectAttempt({state:'draft',result:item,generation_run_id:'draft-run'});
 assert.match(h.get('adzy-qa-list').innerHTML,/Copy unresolved/);
 assert.match(h.get('adzy-qa-list').innerHTML,/Visual QA passed/);
 assert.match(h.get('adzy-copy-evidence').innerHTML,/Text inside image pixels is not assessed/);
 assert.match(h.get('adzy-copy-evidence').innerHTML,/Not enough evidence/);
 assert.ok(!h.get('adzy-copy-evidence').innerHTML.includes('<script>'));
 assert.match(h.get('adzy-generation-error').textContent,/not accepted or deliverable/);
});
test('download requests current server authority instead of trusting frontend accepted flag',async()=>{
 const h=harness();h.A.creative={canonical:true,generation_run_id:'exact-run',accepted:true};
 let downloads=0;h.window.fetch=async()=>{downloads++;throw Error('must not download')};
 h.window.response=async()=>({final_eligible:false,generation_attempt_id:'attempt',checksum:'digest'});
 await h.window.adzyDownloadPoster('final');
 assert.equal(downloads,0);assert.equal(h.requests[0].path,'/api/poster/runs/exact-run/delivery');
});
test('refresh retains the explicitly inspected revision separately from latest attempt',async()=>{
 const h=harness();h.storage.set('adzy.poster.operation:owner:synthetic:selected-revision','selected-old');
 const old={image_b64:'old',generation_run_id:'selected-old',accepted:true};
 h.A.history=[old];
 h.window.response=async()=>({operation:{state:'failed',operation_key:'new'},last_accepted:old});
 await h.window.__test.restorePosterAttempt(0);
 assert.equal(h.A.latestAttempt.operation_key,'new');assert.equal(h.A.creative.generation_run_id,'selected-old');
});

test('Create bootstrap keeps a deliberately selected earlier revision despite a newer draft',async()=>{
 const h=harness();const old={domain_key:'synthetic',generation_run_id:'old',image_b64:'old',accepted:true};
 h.A.history=[{domain_key:'synthetic',generation_run_id:'new',image_b64:'draft',accepted:false},old];
 h.storage.set('adzy.poster.operation:owner:synthetic:selected-revision','old');
 h.window.response=async()=>({status:'READY_FOR_CREATIVE',creative_contract:{language:'en'}});
 await h.window.__test.renderCreative();
 assert.equal(h.A.creative.generation_run_id,'old');
 h.window.__test.projectAttempt({state:'draft',result:h.A.history[0],generation_run_id:'new'});
 assert.equal(h.storage.get('adzy.poster.operation:owner:synthetic:selected-revision'),'old');
});


test('selected brand never inherits another brand poster, including after a brand switch',()=>{
 const h=harness();h.A.brand={domain_key:'firefox',organization_name:'Firefox'};
 h.A.history=[{domain_key:'other',image_b64:'other-brand',canonical:true}];
 h.A.showcase=['/assets/showcase/reference.png'];
 h.window.__test.renderHeroPoster();
 assert.doesNotMatch(h.get('adzy-hero-poster').innerHTML,/<img/);
 assert.match(h.get('adzy-hero-poster').innerHTML,/Firefox/);
 h.A.history.push({domain_key:'firefox',image_b64:'owned'});
 h.window.__test.renderHeroPoster();assert.match(h.get('adzy-hero-poster').innerHTML,/base64,owned/);
 h.A.brand={domain_key:'new',organization_name:'New Brand'};
 h.window.__test.renderHeroPoster();assert.doesNotMatch(h.get('adzy-hero-poster').innerHTML,/base64,owned/);
});
test('SWOT source_url is a visible escaped review citation',()=>{
 const h=harness();h.A.brand={domain_key:'public',has_swot:true,swot_run_id:'reviewed',swot:{strengths:[{text:'Sourced claim',source_url:'https://example.test/source'}]}};
 h.window.__test.renderResearch();
 assert.match(h.get('adzy-research').innerHTML,/href="https:\/\/example.test\/source"/);
 assert.equal(h.requests.length,0);
});


test('eligible strategy waiting only on SWOT gives the actual next action and stays unapproved',()=>{
 const h=harness();h.A.brand={domain_key:'public',has_swot:true,swot_approved:false,strategy_candidate:{run_id:'pending',approval_state:'pending',verification:{eligible_for_approval:true,status:'PASS_WITH_DISCLOSURE',defects:[]}}};
 h.A.review={status:'BLOCKED',reasons:['SWOT_NOT_APPROVED'],required_actions:['REVIEW_SWOT_IN_APPLICATION']};
 h.window.__test.renderStrategy();
 assert.match(h.get('adzy-approval').innerHTML,/Review the SWOT first/);
 assert.doesNotMatch(h.get('adzy-approval').innerHTML,/The direction needs more support/);
 assert.doesNotMatch(h.get('adzy-approval').innerHTML,/id="adzy-lock-btn"/);
 assert.equal(h.requests.length,0);
});


test('bootstrap shares concurrent restoration and never repeats initial navigation',async()=>{
 const h=harness(),navigations=[];
 h.window.adzyLegacyState.authToken='SYNTHETIC';
 h.A.initialLocation.set('stage','create');
 h.window.response=async()=>({brands:[],history:[]});
 h.window.adzyNavigate=async stage=>navigations.push(stage);
 await Promise.all([h.window.__test.bootstrapData(),h.window.__test.bootstrapData()]);
 assert.equal(h.requests.filter(r=>r.path==='/api/brands').length,1);
 assert.deepEqual(navigations,['create']);
 await h.window.__test.bootstrapData();
 assert.deepEqual(navigations,['create']);
 assert.ok(h.requests.every(r=>r.method==='GET'));
});

test('failed operation restores its protected diagnostic preview and reasons without final eligibility',async()=>{
 const h=harness();h.A.selectionEpoch=4;
 const candidate={identity:{candidate_id:'saved-rejected'},checksum:'a'.repeat(64),visual_qa:{checked:true,overall_pass:false,reason:'CTA clipped'}};
 h.window.response=async(method,path)=>{
  assert.ok(path.includes('/candidates/saved-rejected/preview?as_json=true'));
  return {image_b64:'SYNTHETIC_DIAGNOSTIC',canonical:false,accepted:false,diagnostic:true,qa_status:'fail',visual_qa:candidate.visual_qa,candidate_history:[candidate]};
 };
 h.window.__test.projectAttempt({state:'failed',operation_key:'same-key',candidates:[candidate],calls:[]});
 await new Promise(resolve=>setImmediate(resolve));
 assert.equal(h.A.creative.diagnostic,true);
 assert.equal(h.A.creative.accepted,false);
 assert.ok(h.get('adzy-copy-evidence').innerHTML.includes('CTA clipped'));
 assert.equal(h.get('adzy-download-final').disabled,true);
 assert.equal(h.get('adzy-download-draft').disabled,true);
});

test('late creative review restoration cannot replace a loaded rejected preview with older history',async()=>{
 const h=harness();h.A.selectionEpoch=1;h.A.review={workflow_id:'review'};
 h.A.history=[{domain_key:'synthetic',generation_run_id:'old',image_b64:'OLD'}];
 const candidate={identity:{candidate_id:'candidate'},checksum:'b'.repeat(64),visual_qa:{checked:true,overall_pass:false,reason:'CTA clipped'}};
 let resumeReview;
 h.window.response=async(method,path)=>path.includes('/api/review/')
   ?new Promise(resolve=>{resumeReview=resolve})
   :{image_b64:'REJECTED',canonical:false,diagnostic:true,accepted:false,visual_qa:candidate.visual_qa,candidate_history:[candidate]};
 const restoring=h.window.__test.renderCreative();
 h.window.__test.projectAttempt({state:'failed',operation_key:'op',generation_run_id:'new',candidates:[candidate],calls:[]});
 await new Promise(resolve=>setImmediate(resolve));
 resumeReview({status:'READY_FOR_CREATIVE',creative_contract:{language:'en'}});
 await restoring;
 assert.equal(h.A.creative.diagnostic,true);
 assert.equal(h.get('adzy-result-image').src,'data:image/png;base64,REJECTED');
});

test('rejected candidate has one review action and no unavailable download or invented accepted revision',async()=>{
 const h=harness();h.A.lastAccepted=null;
 const q={checked:true,overall_pass:false,reason:'CTA clipped <unsafe>'};
 h.window.response=async()=>({image_b64:'REJECTED',canonical:false,diagnostic:true,accepted:false,qa_status:'fail',visual_qa:q,candidate_history:[]});
 h.window.__test.projectAttempt({state:'failed',operation_key:'rejected',candidates:[{identity:{candidate_id:'c'},visual_qa:q}]});
 await new Promise(resolve=>setImmediate(resolve));
 assert.equal(h.get('adzy-generation-error').hidden,true);
 assert.match(h.get('adzy-candidate-status').innerHTML,/Candidate rejected/);
 assert.match(h.get('adzy-candidate-status').innerHTML,/Review saved checks/);
 assert.doesNotMatch(h.get('adzy-candidate-status').innerHTML,/<unsafe>/);
 for(const id of ['adzy-download-final','adzy-download-draft','adzy-retry-poster','adzy-last-accepted'])assert.equal(h.get(id).hidden,true,id);
 assert.match(h.get('adzy-delivery-note').textContent,/no accepted Asset/);
 assert.doesNotMatch(h.get('adzy-generation-error').innerHTML,/Make another poster/);
});

test('download controls follow current server state and prior accepted access is preserved',async()=>{
 const h=harness();h.A.lastAccepted={generation_run_id:'previous',image_b64:'OLD',accepted:true};
 h.window.response=async()=>({final_eligible:false,draft_export_available:true});
 h.window.__test.projectAttempt({state:'draft',generation_run_id:'draft',result:{canonical:true,generation_run_id:'draft',image_b64:'DRAFT'}});
 await new Promise(resolve=>setImmediate(resolve));
 assert.equal(h.get('adzy-download-final').hidden,true);assert.equal(h.get('adzy-download-draft').hidden,false);
 assert.equal(h.get('adzy-last-accepted').hidden,false);
 h.window.response=async()=>({final_eligible:true,draft_export_available:true});
 h.window.adzyShowLastAccepted();
 // Noncanonical historical results still cannot expose downloads.
 assert.equal(h.get('adzy-last-accepted').hidden,true);
 assert.equal(h.get('adzy-download-final').hidden,true);
});
