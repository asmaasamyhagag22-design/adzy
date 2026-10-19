"""Explicit offline demo provider responses, never live quality evidence."""
import io
from types import SimpleNamespace


def install(mode):
    from PIL import Image, ImageDraw
    from api import webapp_images
    from poster.vision_qa import PosterQAVerdict, required_poster_qa_policy, poster_attempt_identity
    def render(brand, *, generation_attempt_id='', **kwargs):
        headline = 'We never close' if mode == 'draft' else 'Guided specialist onboarding'
        image = Image.new('RGB',(1080,1350),'#ece1fb')
        draw = ImageDraw.Draw(image)
        draw.rectangle((70,70,1010,1280),outline='#352749',width=8)
        draw.text((120,160),'SYNTHETIC DEMO / FAKE PROVIDER',fill='#352749',font_size=42)
        draw.text((120,600),headline,fill='#352749',font_size=36)
        draw.text((120,1160),'NOT LIVE MODEL QUALITY EVIDENCE',fill='#352749',font_size=32)
        stream=io.BytesIO();image.save(stream,format='PNG');payload=stream.getvalue()
        policy=required_poster_qa_policy(arabic=False,logo_composited=False,expect_real_products=False)
        qa=PosterQAVerdict(checked=True,overall_pass=True,critic_pass=True,score=8,
            text_lines=[headline],reason='SYNTHETIC_TEST: fake provider and visual QA response; no live quality claim.',
            qa_source='poster.vision_qa.poster_vision_qa',policy_id=policy.policy_id,policy_version=policy.version,
            policy_context=policy.context,mandatory_checks_expected=policy.mandatory_checks,
            mandatory_checks_completed=policy.mandatory_checks,non_applicable_checks=policy.non_applicable_checks,
            attempt_identity=poster_attempt_identity(payload,attempt_id=generation_attempt_id,candidate_id='synthetic-demo'))
        if mode=='rejected':
            from pathlib import Path
            from api.user_auth import webapp_dir
            from poster.schemas import PosterBrief,PosterDesignSpec
            from poster.paid_boundary import checkpoint_candidate
            from poster.vision_qa import decide_poster_acceptance
            qa=qa.model_copy(update={'overall_pass':False,'critic_pass':False,'cta_clipped':True,
                'reason':'SYNTHETIC_TEST: CTA clipped. Saved rejected candidate; no live model evaluation.'})
            path=webapp_dir()/'synthetic-rejected.png';path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(payload)
            decision=decide_poster_acceptance(qa,attempt_identity=qa.attempt_identity,required_policy=policy)
            checkpoint_candidate(path,identity=qa.attempt_identity,policy=policy,qa=qa,acceptance=decision,
                brief=PosterBrief(business_name='SYNTHETIC Studio',headline=headline),spec=PosterDesignSpec(),
                local_checks={'source':'SYNTHETIC_TEST','defects':['LOCAL_CLIPPING']},background_path=path)
        result=SimpleNamespace(qa=qa,strategy_verification={'overall_pass':True},trust_violation=False,trust_note_ar='',
            brief=SimpleNamespace(headline=headline,subheadline='',cta_text='Visit website'))
        return webapp_images.RealRender(png=payload,result=result)
    webapp_images._render_real=render
    webapp_images._caption_for=lambda *a,**k:{}
