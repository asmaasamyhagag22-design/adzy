"""Actual synthetic app journey; loopback PostgreSQL and fake QA fixtures only."""
import argparse
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.request import urlopen



def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',required=True);parser.add_argument('--evidence-dir',required=True)
    args=parser.parse_args();root=Path(__file__).resolve().parents[1]
    sys.path.insert(0,str(root))
    sys.path.insert(0,str(root/'tests'))
    from copy_editor_review_fixture import fake_review
    from workflows.demo import isolate
    isolate()
    directory=Path(args.directory).resolve();evidence=Path(args.evidence_dir).resolve();evidence.mkdir(parents=True,exist_ok=True)
    subprocess.run([sys.executable,'-m','workflows.app_demo','init','--directory',str(directory)],cwd=root,check=True)
    m=json.loads((directory/'demo.json').read_text());sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
    origin=f'http://127.0.0.1:{port}';server=None;log=None
    def start():
        nonlocal server,log
        log=(evidence/'server.txt').open('a')
        server=subprocess.Popen([sys.executable,'-m','workflows.app_demo','serve','--directory',str(directory),'--port',str(port),'--synthetic-posters','accepted'],cwd=root,stdout=log,stderr=subprocess.STDOUT)
        for _ in range(160):
            if server.poll() is not None:raise RuntimeError('Server failed; inspect log')
            try:
                if urlopen(origin+'/app',timeout=1).status==200:return
            except Exception:pass
            time.sleep(.25)
        raise RuntimeError('Server startup timeout')
    def stop():
        if server and server.poll() is None:server.terminate();server.wait(timeout=20)
        if log:log.close()
    result={'synthetic':True,'live_quality_evidence':False}
    try:
        start()
        from playwright.sync_api import sync_playwright,expect
        with sync_playwright() as pw:
            browser=pw.chromium.launch();page=browser.new_page(viewport={'width':1440,'height':1000},accept_downloads=True,reduced_motion='reduce')
            errors=[]
            def page_error(error):
                errors.append(str(error));(evidence/'page-errors.json').write_text(json.dumps(errors))
            page.on('pageerror',page_error)
            # UI external assets are irrelevant to this offline feature; never contact them.
            page.route('**/*',lambda r:r.continue_() if r.request.url.startswith((origin,'data:','blob:')) else r.abort())
            page.goto(origin+'/app');page.fill('#auth-email',m['email']);page.fill('#auth-password','SyntheticDemo42');page.click('#auth-submit-btn')
            page.wait_for_function('() => window.Adzy && Adzy.brand')
            page.get_by_role('button',name='Think',exact=True).click();page.get_by_role('button',name='Review strategy →').click();page.click('#adzy-lock-btn')
            page.get_by_role('button',name='Prepare the poster',exact=True).click();page.click('#adzy-make-btn')
            page.wait_for_function('() => Adzy.creative && Adzy.creative.accepted',timeout=120000)
            original=page.evaluate('Adzy.creative.generation_run_id')
            page.click('#adzy-edit-copy')
            expect(page.locator('#adzy-copy-editor')).to_contain_text('paid visual re-review is not implemented')
            assert not any('visual review' in label.lower() for label in page.locator('#adzy-copy-editor button').all_text_contents())
            page.fill('#adzy-edit-headline','We never close')
            def lose_response(route):
                response=route.fetch(timeout=120000)
                assert response.status==200
                route.abort()
                page.unroute('**/copy-revisions',lose_response)
            page.route('**/copy-revisions',lose_response)
            page.click('#adzy-edit-save')
            expect(page.locator('#adzy-copy-editor [role=status]')).to_contain_text('Save failed',timeout=120000)
            expect(page.locator('#adzy-edit-headline')).to_have_value('We never close')
            page.click('#adzy-edit-save')
            expect(page.locator('#adzy-copy-editor [role=status]')).to_contain_text('Saved',timeout=120000)
            unsupported=page.evaluate('Adzy.creative.generation_run_id');assert unsupported!=original
            assert page.evaluate('Adzy.creative.factual_eligible') is False
            page.locator('#adzy-edit-headline + small + details').locator('summary').click()
            # Choose the source wording through the actual source action.
            page.locator('#adzy-edit-headline + small + details').get_by_role('button',name='Use source wording').first.click()
            # A late preview must never replace a newer input buffer.
            page.evaluate("""() => {window.originalCopyFetch=window.fetch;window.fetch=async(...args)=>{
                const response=await window.originalCopyFetch(...args);
                if(String(args[0]).endsWith('/copy-preview'))await new Promise(resolve=>window.releaseCopyPreview=resolve);
                return response;
            }}""")
            page.click('#adzy-edit-preview');page.wait_for_function('() => window.releaseCopyPreview',timeout=120000)
            page.fill('#adzy-edit-headline','Newer unsaved text');page.evaluate('releaseCopyPreview()')
            expect(page.locator('#adzy-edit-preview')).to_be_enabled(timeout=120000)
            expect(page.locator('#adzy-edit-headline')).to_have_value('Newer unsaved text')
            expect(page.locator('#adzy-copy-editor [role=status]')).to_contain_text('Unsaved')
            page.evaluate('window.fetch=window.originalCopyFetch')
            page.locator('#adzy-edit-headline + small + details').get_by_role('button',name='Use source wording').first.click()
            page.click('#adzy-edit-preview')
            expect(page.locator('#adzy-editor-checks')).to_contain_text('Exact source wording',timeout=120000)
            expect(page.locator('#adzy-editor-checks')).to_contain_text('Within approved message')
            page.locator('#adzy-edit-headline + small + details').locator('summary').click()
            page.locator('#adzy-copy-editor').scroll_into_view_if_needed();page.locator('#adzy-copy-editor').screenshot(path=str(evidence/'copy-editor-desktop.png'))
            page.set_viewport_size({'width':390,'height':844});page.locator('#adzy-copy-editor').scroll_into_view_if_needed()
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.locator('#adzy-copy-editor').screenshot(path=str(evidence/'copy-editor-mobile.png'))
            page.click('#adzy-edit-save');expect(page.locator('#adzy-copy-editor [role=status]')).to_contain_text('Saved',timeout=120000)
            corrected=page.evaluate('Adzy.creative.generation_run_id');assert corrected not in (original,unsupported)
            page.click('#adzy-edit-compare');expect(page.locator('[data-compare]')).to_contain_text('Original');expect(page.locator('[data-compare]')).to_contain_text('Revised')
            page.screenshot(path=str(evidence/'copy-editor-compare-mobile.png'),full_page=True)
            page.reload();page.wait_for_function('(id)=>Adzy.creative && Adzy.creative.generation_run_id===id',arg=corrected,timeout=30000)
            page.click('#adzy-edit-copy');expect(page.locator('#adzy-copy-editor')).to_contain_text('NOT_CHECKED')
            stop();start();page.reload();page.wait_for_function('(id)=>Adzy.creative && Adzy.creative.generation_run_id===id',arg=corrected,timeout=30000)
            assert page.evaluate('Adzy.creative.authored_copy.headline')=='Guided specialist onboarding'
            with page.expect_download() as info:page.click('#adzy-download-draft')
            draft=evidence/'draft.png';info.value.save_as(draft)
            token=page.evaluate('adzyLegacyState.authToken');headers={'Authorization':'Bearer '+token}
            delivery=page.request.get(origin+f'/api/poster/runs/{corrected}/delivery',headers=headers).json()
            assert hashlib.sha256(draft.read_bytes()).hexdigest()==delivery['checksum']
            params={'mode':'final','attempt_id':delivery['generation_attempt_id'],'checksum':delivery['checksum']}
            assert page.request.get(origin+f'/api/poster/runs/{corrected}/download',headers=headers,params=params).status==409
            # Explicitly materialize complete fake QA fixture, then use the real delivery gate.
            fake_review(directory,corrected)
            fake_delivery=page.request.get(origin+f'/api/poster/runs/{corrected}/delivery',headers=headers).json()
            (evidence/'fake-delivery.json').write_text(json.dumps(fake_delivery,indent=2))
            assert fake_delivery['final_eligible'],fake_delivery
            page.reload();page.wait_for_function('(id)=>Adzy.creative && Adzy.creative.generation_run_id===id && Adzy.creative.delivery?.final_eligible',arg=corrected,timeout=30000)
            with page.expect_download() as info:page.click('#adzy-download-final')
            final=evidence/'synthetic-final.png';info.value.save_as(final)
            assert hashlib.sha256(final.read_bytes()).hexdigest()==delivery['checksum']
            # Original remains accepted and selected, unchanged by all copy saves.
            orig=page.request.get(origin+f'/api/poster/runs/{original}/delivery',headers=headers).json();assert orig['final_eligible']
            from workflows.demo import connect
            from db.models import UsageRecord
            from sqlalchemy import select,func
            service,_=connect(m)
            with service._uow_factory() as uow:assert uow.session.scalar(select(func.count()).select_from(UsageRecord))==0
            assert not errors,errors
            result.update(original=original,unsupported=unsupported,corrected=corrected,refresh_and_process_restart=True,
                draft_and_fake_final_checksum=delivery['checksum'],provider_dispatches=0,page_errors=errors,
                final_qa='Explicit fake fixture; no live review',desktop_and_390px=True,response_loss_idempotency=True,late_preview_ignored=True)
            browser.close()
        (evidence/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
    finally:
        stop();subprocess.run([sys.executable,'-m','workflows.demo','cleanup','--directory',str(directory)],cwd=root,check=True)

if __name__=='__main__':main()
