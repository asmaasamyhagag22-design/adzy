"""WP0 HTTP authorization/validation and persisted PostgreSQL approval subjects."""
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import os

pytestmark = pytest.mark.skipif(
    not os.environ.get("BASEERA_TEST_DATABASE_URL"),
    reason="WP0 real PostgreSQL proof requires a guarded disposable database")
from sqlalchemy import select

from test_postgres_route_integration_2gb import client, _account, _seed_swot
from test_db_persistence_2c import _commit_brand


def setup_brand(client):
    from api.persistence import get_persistence
    headers, actor = _account(client)
    provider = get_persistence()
    brand = _commit_brand(provider._service, SimpleNamespace(id=UUID(actor))).brand
    a = _seed_swot(client, headers, brand.domain_key, 'A')
    return headers, actor, provider, brand, a


def approvals(brand_id):
    from db.models import Approval
    from db.session import get_session_factory
    with get_session_factory()() as session:
        return [str(row.subject_run_id) for row in session.scalars(select(Approval).where(Approval.brand_id == brand_id))]


def test_http_preflight_a_transaction_b_returns_409_and_no_approval(client, monkeypatch):
    from db.services import LiveRunData
    headers, actor, provider, brand, a = setup_brand(client)
    original = provider.visible_brand
    changed = False
    def preflight(**kw):
        nonlocal changed
        doc = original(**kw)
        if not changed:
            changed = True
            provider._service.create_live_run(LiveRunData(brand_id=brand.id, user_id=UUID(actor),kind='swot',status='succeeded',output={'has_swot':True,'swot':{'marker':'B'}}))
        return doc
    monkeypatch.setattr(provider, 'visible_brand', preflight)
    response = client.post('/api/swot/approve',json={'domain_key':brand.domain_key,'swot_run_id':a['swot_run_id']},headers=headers)
    assert response.status_code == 409, response.text
    assert approvals(brand.id) == []


def test_http_subject_taxonomy_and_brand_authorization_precede_validation(client):
    headers, actor, provider, brand, a = setup_brand(client)
    other_headers, other_actor = _account(client)
    own_other = _commit_brand(provider._service,SimpleNamespace(id=UUID(actor))).brand
    foreign = _commit_brand(provider._service,SimpleNamespace(id=UUID(other_actor))).brand
    other_run = _seed_swot(client,headers,own_other.domain_key,'own-other')
    foreign_run = _seed_swot(client,other_headers,foreign.domain_key,'foreign')
    def post(subject, *, auth=headers, key=brand.domain_key):
        return client.post('/api/swot/approve',json={'domain_key':key,'swot_run_id':subject},headers=auth)
    invisible = [post(subject) for subject in [str(uuid4()),other_run['swot_run_id'],foreign_run['swot_run_id']]]
    assert {r.status_code for r in invisible} == {404}
    assert all(r.json() == invisible[0].json() for r in invisible)
    for malformed in [None,'', 'bad', 3, {}, []]:
        assert post(malformed).status_code == 422
        assert post(malformed,auth=other_headers).status_code == 404
    assert client.post('/api/swot/approve',json={'domain_key':brand.domain_key},headers=headers).status_code == 422
    assert post(a['swot_run_id'],auth={}).status_code == 401
    assert approvals(brand.id) == []
    accepted = post(a['swot_run_id'])
    assert accepted.status_code == 200 and accepted.json()['run_id'] == a['swot_run_id']
    assert post(a['swot_run_id']).status_code == 200
    _seed_swot(client,headers,brand.domain_key,'B')
    assert post(a['swot_run_id']).status_code == 409
    assert approvals(brand.id) == [a['swot_run_id']]
