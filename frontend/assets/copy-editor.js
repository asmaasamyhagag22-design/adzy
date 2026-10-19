/* Local copy editing. Server checks every source, revision and delivery decision. */
(function () {
  'use strict';
  const fields = ['headline','subheadline','cta_text'];
  let epoch = 0, active = null, context = null, panel = null, previewVersion = 0;
  const root = () => document.getElementById('adzy-copy-editor');
  const owner = () => (window.adzyLegacyState.currentUser || {}).id;
  const element = (tag, text, parent) => { const e=document.createElement(tag); if(text)e.textContent=text; if(parent)parent.append(e); return e; };
  async function api(path, body) {
    const response = await fetch(path,{method:body?'POST':'GET',headers:{...window.authHeaders(),'Content-Type':'application/json'},...(body?{body:JSON.stringify(body)}:{})});
    const data=await response.json();
    if(!response.ok)throw new Error(Array.isArray(data.detail) ? data.detail.map(e=>`${e.loc.at(-1)}: ${e.msg}`).join('; ') : ((data.detail||{}).code || 'Network request failed. Your text is retained.'));
    return data;
  }
  const url = (id, action='copy-editor') => `/api/poster/runs/${encodeURIComponent(id)}/${action}`;
  function valid(ticket, item) { return ticket===epoch && active===item && window.Adzy.creative===item && window.Adzy.brand?.domain_key===item.domain_key && owner(); }
  function copyValues() { return Object.fromEntries(fields.map(f=>[f,panel.querySelector(`[name="${f}"]`).value])); }
  function remember() {
    if(!panel||!context)return;
    try { localStorage.setItem(context.buffer_key,JSON.stringify({copy:copyValues(),base:context.session_base||context.base_hash,head:context.session_head||context.head_run_id,at:Date.now(),idempotency:panel.dataset.key})); } catch(_) { /* Server revision remains separate. */ }
  }
  function status(text) { panel.querySelector('[data-status]').textContent=text; }
  function action(label, parent, fn) { const b=element('button',label,parent);b.type='button';b.className='adzy-btn flat';b.addEventListener('click',fn);return b; }
  function showChecks(data, parent) {
    parent.replaceChildren();
    const a=data.factual_assessment, s=data.strategy_verification, l=data.local_layout;
    element('p',`Source support: ${a.status==='SUPPORTED'?'Exact source wording':'Unresolved'} · Strategy compatibility: ${s.overall_pass?'Within approved message':'Needs strategy review'}`,parent);
    element('p',`Local layout: ${l.defects.length?l.defects.join(', '):'Measured; no local defects'} · Visual review: Not checked · Final eligibility: Draft only`,parent);
    element('p','Copy quotations are source assertions, not independent truth. Image text and general meaning are not certified.',parent);
    for (const p of a.propositions.filter(p=>fields.includes(p.location))) {
      const j=a.judgments.find(j=>j.proposition_id===p.proposition_id);
      const detail=element('details','',parent);element('summary',`${p.location}: ${p.kind==='NONFACTUAL_INVITATION'?'Approved invitation · factual check excluded':j.verdict}`,detail);
      element('p',j.reason,detail);
      for(const e of j.evidence) { element('blockquote',e.quote,detail);sourceLink(e.page_url,detail); }
    }
    for(const d of (s.defects||[]))element('p',`${d.field}: ${d.detail}`,parent);
  }
  function sourceLink(href,parent) {
    try { const u=new URL(href);if(!['http:','https:'].includes(u.protocol))return;
      const a=element('a','Source page ↗',parent);a.href=u.href;a.target='_blank';a.rel='noopener noreferrer';
    }catch(_) { /* Unsupported links are never made active. */ }
  }
  function openEditor(ctx,ticket,item) {
    root().replaceChildren(); ctx.buffer_key=ctx.buffer_key || `adzy.copy.buffer:${owner()}:${ctx.result.domain_key}:${ctx.run_id}`; context=ctx;
    panel=element('section','',root());panel.className='adzy-copy-panel';panel.setAttribute('aria-label','Edit copy');
    const title=element('h2','Edit copy',panel);title.tabIndex=-1;
    element('p','Keep the direction. Refine the words.',panel).className='adzy-copy-lead';
    element('p','Only headline, subheadline and CTA label can change. The background, brand and approved strategy stay attached to this revision.',panel);
    element('p','Save and export drafts here. Each edited render needs a new visual review; paid visual re-review is not implemented.',panel).className='adzy-copy-limit';
    const row=element('div','',panel);row.className='adzy-copy-grid';
    const visual=element('figure','',row);element('figcaption','Revised · unsaved preview',visual);
    const image=element('img','',visual);image.alt='Revised local copy preview';image.src='data:image/png;base64,'+ctx.image_b64;image.id='adzy-editor-image';
    const form=element('form','',row);form.addEventListener('submit',e=>e.preventDefault());
    let buffer=null;
    try {buffer=JSON.parse(localStorage.getItem(ctx.buffer_key));if(!buffer||Date.now()-buffer.at>86400000)buffer=null;}catch(_){}
    ctx.session_base=buffer?.base||ctx.base_hash;ctx.session_head=buffer?.head||ctx.head_run_id;
    panel.dataset.key=(buffer&&buffer.idempotency)||crypto.randomUUID();
    for(const [i,f] of fields.entries()) {
      const label=element('label',['Headline','Subheadline','CTA label'][i],form);label.htmlFor='adzy-edit-'+f;
      const input=element(f==='cta_text'?'input':'textarea','',form);input.id=label.htmlFor;input.name=f;input.dir='auto';const limit=[160,260,80][i];input.required=f!=='subheadline';input.minLength=f==='headline'?2:0;
      input.value=buffer?.copy?.[f]??ctx.copy[f];
      const hint=element('small',`Up to ${limit} characters`,form);hint.id=input.id+'-hint';input.setAttribute('aria-describedby',hint.id);
      input.addEventListener('input',()=>{input.setCustomValidity(input.value.length>limit ? `Use at most ${limit} characters; your text has been retained.` : '');hint.textContent=`${input.value.length} / ${limit} characters`; ++previewVersion;panel.dataset.key=crypto.randomUUID();status('Unsaved · preview and checks need updating');checks.replaceChildren();element('p','Wording changed. Source support, strategy compatibility and local layout need recalculation.',checks);remember();});
      const detail=element('details','',form);element('summary','Source & approved wording',detail);
      if(f==='cta_text') {
        element('p','An approved invitation is excluded from factual checks only under the current policy. A promise in a CTA still needs evidence.',detail);
        action('Use approved CTA',detail,()=>{input.value=ctx.cta_direction;input.dispatchEvent(new Event('input'));});
      } else for(const option of ctx.sources) {
        element('blockquote',option.text,detail);
        element('p',option.strategy_compatible?'Within approved message':'Source supported · outside this strategy',detail);
        for(const e of option.evidence)sourceLink(e.page_url,detail);
        const use=action('Use source wording',detail,()=>{input.value=option.text;input.dispatchEvent(new Event('input'));});
        if(option.text.length>limit){use.disabled=true;element('p',`This quotation exceeds this field’s ${limit}-character limit.`,detail);}
      }
    }
    const live=element('p',buffer?'Unsaved text restored · checks need updating':'Saved base · ready to edit',panel);live.dataset.status='';live.setAttribute('role','status');live.setAttribute('aria-live','polite');
    if(buffer&&(buffer.base!==ctx.base_hash||buffer.head!==ctx.head_run_id))status('Base or latest revision changed. Your text is retained. Reload saved base before saving.');
    const checks=element('section','',panel);checks.id='adzy-editor-checks';checks.setAttribute('aria-label','Revision checks');
    if(ctx.result.factual_assessment)showChecks({factual_assessment:ctx.result.factual_assessment,strategy_verification:ctx.result.strategy_verification,local_layout:ctx.checks?.local_layout || {defects:['Not measured for this editing session']}},checks);
    if(ctx.checks)element('p',`Saved checks: local ${ctx.checks.local_layout.defects.length?'needs review':'measured'} · visual ${ctx.checks.visual_review} · ${ctx.delivery.final_eligible?'eligible':'draft only'}`,checks);
    const actions=element('div','',panel);actions.className='adzy-copy-actions';
    const branch=element('label','',actions);const branchInput=element('input','',branch);branchInput.type='checkbox';branchInput.id='adzy-edit-branch';
    branch.append(document.createTextNode(' Create an explicit branch from this older revision'));branch.hidden=ctx.head_run_id===ctx.run_id;
    const busy = value => { for(const b of actions.querySelectorAll('button'))b.disabled=value; };
    action('Preview locally',actions,async()=>{
      if(!form.reportValidity())return;
      const version=++previewVersion;const values=copyValues();remember();busy(true);status('Rendering locally · unsaved');
      try {const data=await api(url(ctx.run_id,'copy-preview'),values);
        if(!valid(ticket,item)||version!==previewVersion)return;
        image.src='data:image/png;base64,'+data.image_b64;showChecks(data,checks);status('Unsaved · local preview ready');
      }catch(e){if(valid(ticket,item))status('Render failed: '+e.message+' · your text is retained');}
      finally{if(valid(ticket,item))busy(false);}
    }).id='adzy-edit-preview';
    action('Save revision',actions,async()=>{
      if(!form.reportValidity())return;
      const values=copyValues(),version=++previewVersion;
      const request={copy:values,expected_base_hash:ctx.session_base,expected_head_run_id:ctx.session_head,idempotency_key:panel.dataset.key,branch:branchInput.checked};
      remember();busy(true);status('Saving · composing the same background locally');
      try {const saved=await api(url(ctx.run_id,'copy-revisions'),request);
        if(!valid(ticket,item))return;
        try {localStorage.removeItem(ctx.buffer_key);}catch(_){}
        // Input during an in-flight save is a separate unsaved buffer; never label it saved.
        if(version!==previewVersion){remember();status('Earlier text saved. Newer text remains unsaved. Reopen the saved revision from the list.');return;}
        panel=null;context=null;
        window.adzySelectCopyRevision(saved.result);
        const nextTicket=epoch,nextItem=active;
        openEditor(saved,nextTicket,nextItem);status('Saved · independent revision · needs visual review');
      }catch(e){if(valid(ticket,item)){status('Save failed: '+e.message+' · your text is retained');remember();}}
      finally{if(valid(ticket,item))busy(false);}
    }).id='adzy-edit-save';
    action('Cancel',actions,()=>{++previewVersion;localStorage.removeItem(ctx.buffer_key);++epoch;panel=null;window.adzyCopyPosterChanged(item);}).id='adzy-edit-cancel';
    action('Compare',actions,async()=>{
      let original;try {original=await api(url(ctx.root_run_id));}catch(e){if(valid(ticket,item))status('Compare unavailable: '+e.message);return;}if(!valid(ticket,item))return;
      const target=panel.querySelector('[data-compare]');target.replaceChildren();target.hidden=false;
      for(const [name,picture,copy] of [['Original',original.image_b64,original.copy],['Revised',image.src.split(',')[1],copyValues()]]) {
        const figure=element('figure','',target);element('figcaption',name,figure);const img=element('img','',figure);img.src='data:image/png;base64,'+picture;img.alt=name+' poster';
        for(const f of fields)element('p',`${f}: ${copy[f]}`,figure);
      }
    }).id='adzy-edit-compare';
    action('Reload saved base',actions,async()=>{
      remember();try{const fresh=await api(url(ctx.run_id));if(valid(ticket,item)){localStorage.setItem(ctx.buffer_key,JSON.stringify({copy:copyValues(),base:fresh.base_hash,head:fresh.head_run_id,at:Date.now(),idempotency:crypto.randomUUID()}));openEditor(fresh,ticket,item);}}catch(e){if(valid(ticket,item))status('Refresh failed: '+e.message+' · your text is retained');}
    });
    const compare=element('div','',panel);compare.dataset.compare='';compare.className='adzy-copy-grid';compare.hidden=true;
    const label=element('label','Open saved revision',panel);label.htmlFor='adzy-copy-versions';
    const versions=element('select','',panel);versions.id=label.htmlFor;
    for(const r of ctx.revisions){const option=element('option',r.label,versions);option.value=r.run_id;option.selected=r.run_id===ctx.run_id;}
    versions.addEventListener('change',async()=>{remember();try{const selected=await api(url(versions.value));if(valid(ticket,item))window.adzySelectCopyRevision(selected.result);}catch(e){if(valid(ticket,item))status('Revision unavailable: '+e.message);}});
    title.focus();
  }
  window.adzyCopyPosterChanged = async function(item) {
    remember();
    const ticket=++epoch; ++previewVersion;active=item;panel=null;context=null;
    if(!root())return;root().replaceChildren();
    if(!item?.canonical||!item.generation_run_id)return;
    const button=action('Edit copy',root(),()=>{});button.id='adzy-edit-copy';button.disabled=true;
    try {const ctx=await api(url(item.generation_run_id));if(!valid(ticket,item))return;
      if(!ctx.available){element('p',ctx.reason,root());return;}
      button.disabled=false;button.addEventListener('click',()=>openEditor(ctx,ticket,item));
    }catch(e){if(valid(ticket,item))element('p','Editing unavailable for this revision. '+e.message,root());}
  };
  window.adzyCopyLogout=function(){++epoch;++previewVersion;active=null;context=null;panel=null;root()?.replaceChildren();
    for(const k of Object.keys(localStorage))if(k.startsWith('adzy.copy.buffer:'))localStorage.removeItem(k);
  };
  window.addEventListener('beforeunload',remember);
  if(window.Adzy.creative)window.adzyCopyPosterChanged(window.Adzy.creative);
}());
