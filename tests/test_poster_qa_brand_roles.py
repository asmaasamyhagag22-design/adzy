import pytest
from poster.vision_qa import poster_vision_qa,required_poster_qa_policy,decide_poster_acceptance,poster_attempt_identity,poster_acceptance_from_output


@pytest.mark.parametrize('line,complete,model_pass,model_latin,expected',[
 ('نزّل Acme',True,True,False,True),
 ('نزّل Acme SALE',True,True,False,False),
 ('نزّل Acme',False,True,False,False),
 ('نزّل Acme',True,False,True,False),
])
def test_exact_brand_other_latin_ambiguity_and_explicit_model_veto(line,complete,model_pass,model_latin,expected):
    captured={}
    def caller(system,user,model,**kw):
        captured['system']=system
        values=dict(has_latin_text=model_latin,cta_clipped=False,on_brand_color=True,candid_violation=False,
            overall_pass=model_pass,reason='SYNTHETIC role test',latin_review_complete=complete,
            text_lines=[line],logo_ok=True,single_focal=True,strong_typography=True,cta_prominent=True,
            script_wellformed=True,invented_product=False,third_party_mark=False,placement_logical=True,
            garbled_text=False,product_count=0,score=8)
        return model(**values),None
    policy=required_poster_qa_policy(arabic=True,logo_composited=True,expect_real_products=False,authorized_brand_names=['Acme'])
    identity=poster_attempt_identity(b'new-image',attempt_id='test',candidate_id='one')
    qa=poster_vision_qa(b'new-image',caller=caller,required_policy=policy,expected_attempt_identity=identity)
    decision=decide_poster_acceptance(qa,attempt_identity=identity,required_policy=policy)
    assert decision.accepted is expected
    assert 'exact evidenced brand names' in captured['system'] and 'Acme' in captured['system']
    assert 'no Latin in the copy' not in captured['system']
    if not model_pass:assert qa.critic_pass is False and qa.overall_pass is False
    if expected:
        changed=poster_acceptance_from_output({'visual_qa':qa.model_dump(),'creative_contract':{'copy_metadata_evidence':{'business_name':[{'value':'Other'}]}}},payload=b'new-image')
        assert not changed.accepted and 'QA_POLICY_MISMATCH' in changed.reason_codes


@pytest.mark.parametrize('medium',['illustration','typography','photography'])
def test_medium_is_a_design_input_and_reaches_background_prompt_without_photo_override(medium):
    from poster.art_director import build_llm_concept_prompt
    from poster.schemas import PosterBrief,PosterDesignSpec
    captured=[]
    def caller(system,user,model,**kw):
        captured.append(system)
        return model(concept_title='SYNTHETIC',image_prompt='SYNTHETIC '+medium),None
    prompt=build_llm_concept_prompt(PosterBrief(business_name='Acme',headline='A clear message'),
        caller,spec=PosterDesignSpec(visual_medium=medium))
    assert 'SYNTHETIC' in prompt or medium=='photography'
    if medium!='photography':
        assert 'photorealistic' not in prompt
        assert ('NO text' in captured[0] if medium=='typography' else 'not a photo' in captured[0])
