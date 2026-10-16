"""QA failure projections preserve fail-closed acceptance; no provider traffic."""
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from poster.vision_qa import poster_acceptance_from_output


@pytest.mark.parametrize('qa', [None, {'overall_pass': False, 'checked': False}])
def test_qa_refusal_names_unverified_state_without_gallery_write(monkeypatch, qa):
    from api.routes import generate as route
    decision = poster_acceptance_from_output({'visual_qa': qa})
    assert decision.accepted is False
    failed = []
    class Store:
        def supports_gallery_writes(self): return True
        def begin_canonical_poster_run(self, **kwargs):
            return {'run_id': 'run', 'attempt_id': 'attempt'}
        def fail_canonical_poster_run(self, **kwargs): failed.append(kwargs)
        def record_gallery_entry(self, **kwargs): pytest.fail('QA refusal must not publish')
    monkeypatch.setattr(route, 'get_persistence', Store)
    def refuse(*a, **kw): raise route.images.CanonicalPosterQARejected(decision)
    monkeypatch.setattr(route.images, 'generate_image', refuse)
    contract = SimpleNamespace(model_dump=lambda **kw: {})
    with pytest.raises(HTTPException) as caught:
        route._emit({'domain_key': 'synthetic'}, headline='', subtext='', cta='', style='',
                    content_type='brand_poster', owner_id='owner', creative_contract=contract)
    assert caught.value.status_code == 502
    detail = caught.value.detail
    assert detail['accepted'] is False
    assert detail['qa_status'] == decision.qa_status
    assert detail['reason_codes'] == decision.reason_codes
    assert detail['generation_run_id'] == 'run'
    assert 'explicitly retry' in detail['safe_alternative']
    assert len(failed) == 1 and failed[0]['user_id'] == 'owner'
