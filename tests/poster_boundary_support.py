"""Synthetic external transport/render seam for application and subprocess crash proofs."""
import base64
import io
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
import httpx
from PIL import Image


def install(monkeypatch, directory, mode='pass', crash='', calls=1):
    from google import genai
    from google.genai import types
    from api import webapp_images
    from poster import oneshot
    from business_profile.llm.caller import GeminiCaller
    from pydantic import BaseModel
    from test_poster_acceptance_truth import _verdict
    directory=Path(directory)
    image=io.BytesIO(); Image.new('RGB',(32,40),'#'+hashlib.sha256(str(directory).encode()).hexdigest()[:6]).save(image,format='PNG')
    png=image.getvalue(); counter=directory/'acceptances.jsonl'
    transport_count=[]
    def transport(request):
        transport_count.append(str(request.url))
        if mode=='503': return httpx.Response(503,json={'error':{'code':503,'message':'synthetic ambiguous'}})
        if mode=='429': return httpx.Response(429,json={'error':{'code':429,'message':'synthetic rejected'}})
        with counter.open('a') as stream:
            stream.write(json.dumps({'accepted':True})+'\n');stream.flush();os.fsync(stream.fileno())
        if crash=='external_acceptance': os._exit(72)
        if mode=='timeout': raise httpx.ReadTimeout('synthetic response loss')
        body=json.loads(request.content)
        if 'IMAGE' in body.get('generationConfig',{}).get('responseModalities',[]):
            parts=[{'inlineData':{'mimeType':'image/png','data':base64.b64encode(png).decode()}}]
        else: parts=[{'text':'{"ok":true}'}]
        return httpx.Response(200,json={'responseId':'synthetic-response','candidates':[{'content':{'role':'model','parts':parts},'finishReason':'STOP'}],
            'usageMetadata':{'promptTokenCount':10,'candidatesTokenCount':2,'thoughtsTokenCount':3}})
    client=genai.Client(api_key='SYNTHETIC',http_options=types.HttpOptions(httpx_client=httpx.Client(transport=httpx.MockTransport(transport)),retry_options=types.HttpRetryOptions(attempts=5,initial_delay=.001)))
    def render(brand,*,generation_attempt_id='',**kwargs):
        if crash=='before_send': os._exit(71)
        # Real SDK image path, including its internal transport retry/model fallback loops.
        for i in range(calls):
            oneshot.generate_oneshot_poster('synthetic',directory/f'candidate-{i}.png',client=client,model='gemini-2.5-flash-image')
        if mode=='multistage':
            class Answer(BaseModel): ok: bool
            caller=GeminiCaller(model='gemini-2.5-pro',max_retries=2);caller._client=client
            caller('synthetic','synthetic',Answer,group_name='poster_vision_qa')
        verdict=_verdict(payload=png,attempt_id=generation_attempt_id,overall=mode!='qa_fail',critic_pass=mode!='qa_fail')
        result=SimpleNamespace(qa=verdict,strategy_verification={'overall_pass':True},trust_violation=False,trust_note_ar='',
            brief=SimpleNamespace(headline='Guided specialist onboarding',subheadline='',cta_text='Visit website'))
        return webapp_images.RealRender(png=png,result=result)
    monkeypatch.setattr(webapp_images,'_render_real',render)
    monkeypatch.setattr(webapp_images,'_caption_for',lambda *a,**k:{})
    monkeypatch.setattr(oneshot,'_MODEL_CHAIN',[])
    # No 20/45/90-second waits in retry proofs; retry budget/accounting remains real.
    import time
    monkeypatch.setattr(time,'sleep',lambda _:None)
    if crash=='dispatch_intent':
        from db.poster_operations import PosterOperations
        original_start=PosterOperations.start_call
        def start(self,*args,**kwargs):
            original_start(self,*args,**kwargs)
            os._exit(74)
        monkeypatch.setattr(PosterOperations,'start_call',start)
    if crash=='local_result':
        from api.persistence import get_persistence
        persistence=get_persistence();original=persistence.record_gallery_entry
        def record(**kwargs):
            result=original(**kwargs)
            os._exit(73)
        monkeypatch.setattr(persistence,'record_gallery_entry',record)
    return counter,transport_count,client
