# -*- coding: utf-8 -*-
"""A poster that FAILED the gate must never become the delivered file.

OWNER-FACING FAILURE (Volume Qatar, 2026-07-21): the delivered poster read "speciaity coffee
coffee" and "Mall of Maqur". The verbatim-text gate had correctly REJECTED that render
(fidelity 0.667 < 1.0) — but the canonical sink was refreshed from every attempt the moment it was
generated, so the rejected image overwrote the good poster on disk anyway.

The canonical sink is an accepted delivery surface, so it only receives an attempt carrying the
authoritative typed PASS decision for those exact bytes. A rejected/unverified attempt stays in
`_attempts/` where it can be inspected, and the previous good deliverable survives untouched."""
from __future__ import annotations

from pathlib import Path

from poster.pipeline import _CANONICAL_SINK, _refresh_canonical
from poster.vision_qa import (
    POSTER_QA_SOURCE, PosterQAVerdict, decide_poster_acceptance,
    poster_attempt_identity, required_poster_qa_policy,
)


def _setup(tmp_path: Path) -> tuple[Path, Path]:
    canonical = tmp_path / "brand_poster.png"
    canonical.write_bytes(b"GOOD-POSTER")
    attempt = tmp_path / "_attempts" / "try2.png"
    attempt.parent.mkdir(parents=True, exist_ok=True)
    attempt.write_bytes(b"REJECTED-RENDER")
    _CANONICAL_SINK["path"] = canonical
    return canonical, attempt


def _decision(attempt: Path, overall):
    policy = required_poster_qa_policy(
        arabic=False, logo_composited=False,
        expect_real_products=False, product_named=False)
    identity = poster_attempt_identity(
        attempt, attempt_id="sink-test", candidate_id=attempt.stem)
    checked = overall is not None
    qa = PosterQAVerdict(
        checked=checked, overall_pass=overall, critic_pass=overall,
        text_lines=["Frozen headline"], score=8,
        reason="clean" if overall is True else "rejected or unavailable",
        qa_source=POSTER_QA_SOURCE, policy_id=policy.policy_id,
        policy_version=policy.version, policy_context=dict(policy.context),
        mandatory_checks_expected=list(policy.mandatory_checks),
        mandatory_checks_completed=(list(policy.mandatory_checks) if checked else []),
        non_applicable_checks=dict(policy.non_applicable_checks),
        attempt_identity=identity)
    return decide_poster_acceptance(
        qa, attempt_identity=identity, required_policy=policy,
        current_attempt_identity=identity)


def test_a_gate_rejected_attempt_never_replaces_the_deliverable(tmp_path: Path):
    canonical, attempt = _setup(tmp_path)
    try:
        _refresh_canonical(attempt, decision=_decision(attempt, False))
        assert canonical.read_bytes() == b"GOOD-POSTER"
    finally:
        _CANONICAL_SINK["path"] = None


def test_a_passing_attempt_does_replace_it(tmp_path: Path):
    canonical, attempt = _setup(tmp_path)
    try:
        _refresh_canonical(attempt, decision=_decision(attempt, True))
        assert canonical.read_bytes() == b"REJECTED-RENDER"   # the new, PASSING render
    finally:
        _CANONICAL_SINK["path"] = None


def test_the_first_unverified_render_never_becomes_a_deliverable(tmp_path: Path):
    canonical = tmp_path / "brand_poster.png"
    attempt = tmp_path / "_attempts" / "try1.png"
    attempt.parent.mkdir(parents=True, exist_ok=True)
    attempt.write_bytes(b"FIRST")
    _CANONICAL_SINK["path"] = canonical
    try:
        _refresh_canonical(attempt, decision=_decision(attempt, None))
        assert not canonical.exists()
    finally:
        _CANONICAL_SINK["path"] = None


def test_no_sink_configured_is_a_noop(tmp_path: Path):
    _CANONICAL_SINK["path"] = None
    _refresh_canonical(tmp_path / "nope.png", decision=None)      # must not raise
