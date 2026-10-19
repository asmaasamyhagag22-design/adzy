"""Run the actual classic loop; only provider seams are fake. Never sends or accepts stale QA."""
from pathlib import Path
from types import SimpleNamespace
import re
import pytest
from PIL import Image
from poster.schemas import PosterBrief,PosterDesignSpec
from poster.vision_qa import PosterQAVerdict,decide_poster_acceptance,poster_attempt_identity


def classic(monkeypatch,tmp_path,*,always_bad=False,reuse_old=False):
    import poster.pipeline as pl
    import poster.art_director as ad
    import poster.paid_boundary as boundary
    from test_poster_oneshot_best_attempt import _PROFILE,_concept
    bg=tmp_path/'bg.png';Image.new('RGB',(1080,1350),'#ddd8ef').save(bg)
    sends=[];seen=[];snapshots=[]
    monkeypatch.setattr(pl,'build_creative_concept',lambda *a,**k:_concept())
    monkeypatch.setattr(boundary,'external_research_allowed',lambda:False)
    monkeypatch.setattr(ad,'build_design_spec',lambda *a,**k:PosterDesignSpec(
        text_box=[.075,.5,.85],show=['headline','cta'],support_font_size=46,cta_font_size=38))
    monkeypatch.setattr(ad,'build_llm_concept_prompt',lambda *a,**k:'SYNTHETIC no provider')
    def background(*a,**k):sends.append(1);return str(bg),'fake-image'
    monkeypatch.setattr(pl,'_generate_background',background)
    monkeypatch.setattr(pl,'_trim_letterbox',lambda p:p)
    from poster.render_playwright import render_html_to_png
    def render(html,path):
        html=re.sub(r'<link[^>]*>','',html)
        return render_html_to_png(html,path,scale=1)
    monkeypatch.setattr(pl,'render_html_to_png',render)
    monkeypatch.setattr('poster.contrast_gate.check_copy_contrast',lambda *a,**k:SimpleNamespace(checked=True,passed=True,worst_ratio=8))
    # Geometry still runs against the compositor; strip remote fonts for hermetic test execution.
    from poster.render_playwright import inspect_layout
    monkeypatch.setattr('poster.render_playwright.inspect_layout',lambda html:inspect_layout(re.sub(r'<link[^>]*>','',html)))
    monkeypatch.setattr(boundary,'checkpoint_candidate',lambda p,**k:snapshots.append(k))
    def qa(path,**kwargs):
        if reuse_old and seen:return seen[0]
        policy=kwargs['required_policy'];identity=kwargs['expected_attempt_identity']
        bad=always_bad or not seen
        q=PosterQAVerdict(checked=True,overall_pass=not bad,critic_pass=not bad,cta_clipped=bad,
            text_lines=['Build clear ideas','Learn More'],reason='CTA clipped' if bad else 'clean',score=8,
            qa_source='poster.vision_qa.poster_vision_qa',policy_id=policy.policy_id,policy_version=policy.version,
            policy_context=policy.context,mandatory_checks_expected=policy.mandatory_checks,
            mandatory_checks_completed=policy.mandatory_checks,non_applicable_checks=policy.non_applicable_checks,
            attempt_identity=identity)
        seen.append(q);return q
    monkeypatch.setattr(pl,'poster_vision_qa',qa)
    result=pl.generate_poster(_PROFILE,caller=None,qa_caller=object(),use_dna=False,upscale=False,
        max_qa_retries=2,out_dir=str(tmp_path),language='en',log=lambda s:None)
    return result,sends,seen,snapshots


def test_cta_repair_recomposes_same_background_before_any_new_image(monkeypatch,tmp_path):
    r,sends,qa,snapshots=classic(monkeypatch,tmp_path)
    assert len(sends)==1 and len(qa)==2
    assert qa[0].attempt_identity.content_sha256!=qa[1].attempt_identity.content_sha256
    assert qa[0].attempt_identity.candidate_id!=qa[1].attempt_identity.candidate_id
    assert r.acceptance.accepted


def test_local_repair_stops_when_defect_persists(monkeypatch,tmp_path):
    r,sends,qa,_=classic(monkeypatch,tmp_path,always_bad=True)
    assert len(sends)==1 and len(qa)==2
    assert not r.acceptance.accepted


def test_old_qa_does_not_qualify_recomposed_bytes(monkeypatch,tmp_path):
    r,sends,qa,snapshots=classic(monkeypatch,tmp_path,reuse_old=True)
    assert len(sends)==1 and not r.acceptance.accepted
    assert 'QA_CONTENT_MISMATCH' in snapshots[-1]['acceptance'].reason_codes
