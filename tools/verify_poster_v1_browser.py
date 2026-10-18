"""Actual synthetic app journey, two viewport sizes, exact authenticated downloads; no providers."""
import argparse
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.request import urlopen
from uuid import uuid4


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',required=True,help='New disposable demo directory, never a customer store')
    parser.add_argument('--evidence-dir',required=True)
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1];directory=Path(args.directory).resolve()
    evidence=Path(args.evidence_dir).resolve();evidence.mkdir(parents=True,exist_ok=True)
    subprocess.run([sys.executable,'-m','workflows.app_demo','init','--directory',str(directory)],cwd=root,check=True,stdout=subprocess.DEVNULL)
    m=json.loads((directory/'demo.json').read_text())
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
    origin=f'http://127.0.0.1:{port}'
    server=None;log=None
    def start(mode):
        nonlocal server,log
        log=(evidence/f'server-{mode}.txt').open('w')
        server=subprocess.Popen([sys.executable,'-m','workflows.app_demo','serve','--directory',str(directory),
            '--port',str(port),'--synthetic-posters',mode],cwd=root,stdout=log,stderr=subprocess.STDOUT)
        for _ in range(120):
            if server.poll() is not None:raise RuntimeError('Synthetic app startup failed; inspect server log')
            try:
                if urlopen(origin+'/app',timeout=1).status==200:return
            except Exception:pass
            time.sleep(.25)
        raise RuntimeError('App startup timeout')
    def stop():
        if server and server.poll() is None:
            server.terminate();server.wait(timeout=20)
        if log:log.close()
    result={'synthetic':True,'provider_calls':0,'screenshots_are_synthetic':True}
    try:
        start('accepted')
        from playwright.sync_api import sync_playwright,expect
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True,executable_path=pw.chromium.executable_path,args=['--no-sandbox'])
            context=browser.new_context(viewport={'width':1440,'height':1000},reduced_motion='reduce',accept_downloads=True)
            page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(origin+'/app');page.fill('#auth-email',m['email']);page.fill('#auth-password','SyntheticDemo42');page.click('#auth-submit-btn')
            page.wait_for_function('() => window.Adzy && window.Adzy.brand')
            page.get_by_role('button',name='Think',exact=True).click()
            page.get_by_role('button',name='Review strategy →').click()
            expect(page.locator('#adzy-lock-btn')).to_be_visible()
            page.screenshot(path=str(evidence/'synthetic-strategy-review.png'),full_page=True)
            page.set_viewport_size({'width':390,'height':844})
            page.locator('#adzy-lock-btn').scroll_into_view_if_needed()
            expect(page.locator('#adzy-lock-btn')).to_be_in_viewport()
            page.screenshot(path=str(evidence/'synthetic-strategy-review-mobile.png'),full_page=True)
            page.click('#adzy-lock-btn')
            result['mobile_strategy_approval_works']=True
            page.set_viewport_size({'width':1440,'height':1000})
            page.get_by_role('button',name='Prepare the poster',exact=True).click()
            page.click('#adzy-make-btn')
            page.wait_for_function('() => window.Adzy.latestAttempt && window.Adzy.latestAttempt.state === "accepted"',timeout=60000)
            expect(page.locator('#adzy-download-final')).to_be_enabled()
            page.locator('#adzy-copy-evidence > details > summary').click()
            expect(page.locator('#adzy-copy-evidence')).to_contain_text('Text inside image pixels is not assessed')
            page.locator('#adzy-download-final').scroll_into_view_if_needed()
            page.screenshot(path=str(evidence/'synthetic-accepted-desktop.png'),full_page=True)
            with page.expect_download() as info:page.click('#adzy-download-final')
            downloaded=info.value;downloaded.save_as(evidence/'synthetic-final.png')
            accepted=page.evaluate('() => ({...Adzy.creative.delivery})')
            assert hashlib.sha256((evidence/'synthetic-final.png').read_bytes()).hexdigest()==accepted['checksum']
            result['accepted_revision']=accepted
            result['download_matches_recorded_checksum']=True
            page.reload();page.wait_for_function('() => window.Adzy.creative && window.Adzy.creative.delivery')
            assert page.evaluate('() => Adzy.creative.delivery.generation_attempt_id')==accepted['generation_attempt_id']
            result['refresh_preserves_revision']=True
            # A second actual account cannot fetch the saved asset.
            foreign=page.request.post(origin+'/api/auth/signup',data={'email':uuid4().hex+'@synthetic.invalid','password':'SyntheticPassword42'}).json()
            url=origin+f"/api/poster/runs/{accepted['generation_run_id']}/download"
            params={'attempt_id':accepted['generation_attempt_id'],'checksum':accepted['checksum'],'mode':'final'}
            assert page.request.get(url,params=params,headers={'Authorization':'Bearer '+foreign['access_token']}).status==404
            result['foreign_owner_download_status']=404
            page.set_viewport_size({'width':390,'height':844})
            page.locator('#adzy-download-final').scroll_into_view_if_needed()
            expect(page.locator('#adzy-download-final')).to_be_in_viewport()
            with page.expect_download():page.click('#adzy-download-final')
            page.screenshot(path=str(evidence/'synthetic-accepted-mobile.png'),full_page=True)
            result['mobile_final_control_works']=True
            stop();start('draft')
            page.reload();page.wait_for_function('() => window.Adzy.creative && window.Adzy.creative.delivery')
            page.click('#adzy-retry-poster')
            page.wait_for_function('() => window.Adzy.latestAttempt && window.Adzy.latestAttempt.state === "draft"',timeout=60000)
            expect(page.locator('#adzy-download-final')).to_be_disabled()
            expect(page.locator('#adzy-download-draft')).to_be_enabled()
            draft=page.evaluate('() => ({...Adzy.creative.delivery})');assert draft['final_eligible'] is False
            token=page.evaluate('() => adzyLegacyState.authToken')
            assert page.request.get(origin+f"/api/poster/runs/{draft['generation_run_id']}/download",params={
                'mode':'final','attempt_id':draft['generation_attempt_id'],'checksum':draft['checksum']},headers={'Authorization':'Bearer '+token}).status==409
            with page.expect_download() as info:page.click('#adzy-download-draft')
            assert info.value.suggested_filename.startswith('draft-')
            page.locator('#adzy-copy-evidence > details > summary').click()
            page.screenshot(path=str(evidence/'synthetic-draft-mobile.png'),full_page=True)
            page.click('#adzy-last-accepted')
            expect(page.locator('#adzy-download-final')).to_be_enabled()
            page.reload();page.wait_for_function('() => window.Adzy.creative && window.Adzy.creative.delivery')
            page.wait_for_function('(expected) => window.Adzy.creative && window.Adzy.creative.delivery && window.Adzy.creative.delivery.generation_attempt_id === expected', arg=accepted['generation_attempt_id'])
            page.wait_for_function('() => window.Adzy.latestAttempt && window.Adzy.latestAttempt.state === "draft"')
            assert page.evaluate('() => Adzy.creative.delivery.generation_attempt_id')==accepted['generation_attempt_id']
            stop();start('rejected')
            page.reload();page.wait_for_function('() => Adzy.creative && Adzy.creative.delivery')
            page.click('#adzy-retry-poster')
            page.wait_for_function('() => Adzy.latestAttempt && Adzy.latestAttempt.state === "failed" && Adzy.creative && Adzy.creative.diagnostic',timeout=60000)
            expect(page.locator('#adzy-copy-evidence')).to_contain_text('CTA clipped')
            expect(page.locator('#adzy-download-final')).to_be_disabled()
            expect(page.locator('#adzy-download-draft')).to_be_disabled()
            expect(page.locator('#adzy-download-final')).to_be_hidden()
            expect(page.locator('#adzy-download-draft')).to_be_hidden()
            expect(page.locator('#adzy-retry-poster')).to_be_hidden()
            expect(page.locator('#adzy-generation-error')).to_be_hidden()
            expect(page.locator('#adzy-candidate-status')).to_contain_text('Candidate rejected')
            expect(page.locator('#adzy-brief-status')).to_have_text('Locked by approved strategy')
            expect(page.locator('#adzy-last-accepted')).to_be_visible()
            page.get_by_role('button',name='Review saved checks',exact=True).click()
            assert page.locator('#adzy-copy-evidence details').evaluate('(e)=>e.open')
            page.screenshot(path=str(evidence/'synthetic-rejected-mobile.png'),full_page=True)
            rejected=page.evaluate('() => Adzy.latestAttempt')
            candidate=rejected['candidates'][-1]
            preview=origin+f"/api/poster/operations/{rejected['operation_key']}/candidates/{candidate['identity']['candidate_id']}/preview"
            assert page.request.get(preview,headers={'Authorization':'Bearer '+foreign['access_token']}).status==404
            # No deliberately selected older revision: show latest diagnostic after refresh.
            page.evaluate("() => {for(const key of Object.keys(localStorage))if(key.endsWith(':selected-revision'))localStorage.removeItem(key)}")
            page.reload();page.wait_for_function('() => Adzy.creative && Adzy.creative.diagnostic',timeout=30000)
            expect(page.locator('#adzy-copy-evidence')).to_contain_text('CTA clipped')
            page.set_viewport_size({'width':1440,'height':1000})
            page.locator('#adzy-copy-evidence').scroll_into_view_if_needed()
            page.wait_for_function('() => Adzy.creative && Adzy.creative.diagnostic && document.getElementById("adzy-result-image").src === "data:image/png;base64,"+Adzy.creative.image_b64')
            expect(page.locator('#adzy-qa-list')).to_contain_text('Visual QA failed')
            expect(page.locator('#adzy-download-final')).to_be_disabled()
            page.screenshot(path=str(evidence/'synthetic-rejected-desktop.png'),full_page=True)
            # A latest operation with no previous accepted result must not suggest one.
            def no_accepted(route):
                response=route.fetch(); payload=response.json(); payload['last_accepted']=None
                route.fulfill(response=response,json=payload)
            page.route('**/api/poster/operations/latest?*',no_accepted)
            page.reload();page.wait_for_function('() => Adzy.creative && Adzy.creative.diagnostic',timeout=30000)
            expect(page.locator('#adzy-last-accepted')).to_be_hidden()
            expect(page.locator('#adzy-delivery-note')).to_contain_text('no accepted Asset')
            expect(page.locator('#adzy-brief-status')).to_have_text('Locked by approved strategy',timeout=30000)
            expect(page.locator('#adzy-brief-rows')).to_contain_text('Guided specialist onboarding')
            page.screenshot(path=str(evidence/'synthetic-rejected-no-accepted-desktop.png'),full_page=True)
            page.set_viewport_size({'width':390,'height':844})
            # Scroll the real page; account/brand and navigation stay in document flow.
            scroll_samples=[]
            for name,selector in [('status','#adzy-candidate-status'),('preview','#adzy-result-image'),('checks','#adzy-copy-evidence')]:
                page.locator(selector).scroll_into_view_if_needed()
                measured=page.evaluate('''(selector)=>{
                    const target=document.querySelector(selector),r=target.getBoundingClientRect();
                    const bars=['.adzy-topbar','.adzy-nav'].map(s=>{const e=document.querySelector(s),b=e.getBoundingClientRect();return {selector:s,position:getComputedStyle(e).position,top:b.top,bottom:b.bottom}});
                    const offenders=[...document.querySelectorAll('body *')].filter(e=>{const b=e.getBoundingClientRect();return b.width>0&&(b.right>innerWidth+.5||b.left<-.5)}).map(e=>({tag:e.tagName,id:e.id,class:e.className,right:e.getBoundingClientRect().right,left:e.getBoundingClientRect().left})).slice(0,20);
                    return {scrollY,top:r.top,bottom:r.bottom,bars,overflow:document.documentElement.scrollWidth>innerWidth,offenders};
                }''',selector)
                page.screenshot(path=str(evidence/f'synthetic-mobile-scroll-{name}.png'))
                assert not measured['overflow'],measured
                assert all(b['position']=='static' for b in measured['bars']),measured
                assert all(b['bottom']<=0 for b in measured['bars']),measured
                scroll_samples.append({'target':name,**measured})
                page.screenshot(path=str(evidence/f'synthetic-mobile-scroll-{name}.png'))
            page.screenshot(path=str(evidence/'synthetic-rejected-no-accepted-mobile.png'),full_page=True)
            result.update(mobile_scroll_samples=scroll_samples,rejected_single_primary_action=True,
                rejected_unavailable_downloads_hidden=True,no_accepted_revision_action_hidden=True,
                prior_accepted_revision_access_preserved=True)
            result.update(rejected_preview_survives_refresh=True,rejected_preview_foreign_status=404,
                rejected_preview_not_final=True)
            result.update(draft_final_download_status=409,draft_export_works=True,
                selected_accepted_revision_survives_refresh_beside_latest_draft=True,page_errors=errors)
            assert not errors,errors
            browser.close()
        (evidence/'browser_result.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(result,indent=2))
    finally:
        stop()
        subprocess.run([sys.executable,'-m','workflows.demo','cleanup','--directory',str(directory)],cwd=root,check=True,stdout=subprocess.DEVNULL)

if __name__=='__main__':main()
