"""WP0: exact reviewed subject reaches the transaction; no existence oracle."""
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from api.persistence import PostgresPersistence
from api.routes import analysis
from db.errors import NotVisible
from db.services import PersistenceService


class MemoryUow:
    def __init__(self):
        self.brand_id, self.user_id = uuid4(), uuid4()
        self.runs_by_id = {}
        self.rows = []
        self.current = None
        self.brands = SimpleNamespace(get_visible=self.brand)
        self.runs = SimpleNamespace(get_visible=self.run, current_successful=lambda **kw: self.current)
        self.approvals = SimpleNamespace(approved_for_run=self.approved, create_live=self.insert)

    def __enter__(self): return self
    def __exit__(self, *args): pass
    def commit(self): pass
    def brand(self, *, user_id, brand_id, **kw):
        if (brand_id, user_id) != (self.brand_id, self.user_id): raise NotVisible()
    def run(self, *, run_id, user_id):
        if run_id not in self.runs_by_id: raise NotVisible()
        return self.runs_by_id[run_id]
    def add(self, *, brand_id=None, kind="swot", status="succeeded"):
        row = SimpleNamespace(id=uuid4(), brand_id=brand_id or self.brand_id, kind=kind, status=status)
        self.runs_by_id[row.id] = row
        if row.brand_id == self.brand_id and kind == "swot" and status == "succeeded": self.current = row
        return row
    def approved(self, run_id): return next((r for r in self.rows if r.subject_run_id == run_id), None)
    def insert(self, *, brand_id, run_id, actor_id, **kw):
        row = SimpleNamespace(id=uuid4(), subject_run_id=run_id, decision="approved")
        self.rows.append(row)
        return row


@pytest.fixture
def boundary(monkeypatch):
    uow = MemoryUow()
    service = PersistenceService(lambda: uow)
    provider = PostgresPersistence(service)
    provider._brand_row = lambda **kw: SimpleNamespace(id=uow.brand_id) if kw['user_id'] == str(uow.user_id) else None
    provider.visible_brand = lambda **kw: ({"has_swot": True, "swot_run_id": str(uow.current.id)}
        if kw['user_id'] == str(uow.user_id) else None)
    monkeypatch.setattr(analysis, 'get_persistence', lambda: provider)
    return uow, provider


def approve(uow, subject):
    return analysis.swot_approve(analysis.ApproveRequest(domain_key="wp0.test", swot_run_id=subject),
                                 user={"id": str(uow.user_id)})


def test_preflight_a_then_b_commits_never_approves_unreviewed_b(boundary):
    uow, provider = boundary
    a = uow.add()
    def preflight(**kw):
        document = {"has_swot": True, "swot_run_id": str(a.id)}
        uow.add()  # B commits AFTER route read, BEFORE write boundary.
        return document
    provider.visible_brand = preflight
    with pytest.raises(HTTPException) as caught: approve(uow, str(a.id))
    assert caught.value.status_code == 409
    assert not uow.rows


def test_success_returns_persisted_subject_and_repeat_has_no_duplicate(boundary):
    uow, _ = boundary
    a = uow.add()
    result = approve(uow, str(a.id))
    assert result['run_id'] == str(a.id)
    approve(uow, str(a.id))
    assert [r.subject_run_id for r in uow.rows] == [a.id]


def test_old_approved_a_replay_after_b_is_stale_before_idempotency(boundary):
    uow, _ = boundary
    a = uow.add()
    approve(uow, str(a.id))
    uow.add()
    with pytest.raises(HTTPException) as caught: approve(uow, str(a.id))
    assert caught.value.status_code == 409
    assert [r.subject_run_id for r in uow.rows] == [a.id]


@pytest.mark.parametrize('subject', ['', None, 'bad-id', 12, {}, []])
def test_missing_or_malformed_is_422_after_brand_authorization(boundary, subject):
    uow, _ = boundary
    uow.add()
    with pytest.raises(HTTPException) as caught: approve(uow, subject)
    assert caught.value.status_code == 422
    with pytest.raises(HTTPException) as foreign:
        analysis.swot_approve(analysis.ApproveRequest(domain_key="wp0.test", swot_run_id=subject), user={"id": str(uuid4())})
    assert foreign.value.status_code == 404
    assert not uow.rows


@pytest.mark.parametrize('kind', ['unknown', 'foreign', 'wrong_kind', 'running'])
def test_invisible_subjects_all_404_without_mutation(boundary, kind):
    uow, _ = boundary
    uow.add()
    subject = uuid4()
    if kind == 'foreign': subject = uow.add(brand_id=uuid4()).id
    if kind == 'wrong_kind': subject = uow.add(kind='strategy').id
    if kind == 'running': subject = uow.add(status='running').id
    with pytest.raises(HTTPException) as caught: approve(uow, str(subject))
    assert caught.value.status_code == 404
    assert caught.value.detail == 'SWOT run not found.'
    assert not uow.rows
