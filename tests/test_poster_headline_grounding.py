"""H6 gate: an EXTERNAL poster headline (--headline, a content-calendar hook OR topic, or
a trend) must pass the SAME Evidence-Ledger grounding gate as the concept copy.

Regression origin (2026-07-05 audit): the poster used `headline_override or
concept.headline` — the override replaced the Ledger-gated concept headline WITHOUT being
gated itself. The strategy calendar's `topic` field is not gated, so a topic like "Egypt's
#1 pharmacy" reached the rendered poster verbatim, bypassing the moat.

Hermetic: the Evidence Ledger is built from a profile dict, no network / no LLM.
"""
from grounding import EvidenceLedger
from poster.pipeline import _verified_external_headline


def _ledger():
    profile = {
        "name": {"value": "Nahdi", "evidence": []},
        "description": {"value": "A pharmacy chain serving customers.", "evidence": []},
        "offerings": [{"name": "Medicines"}, {"name": "Personal care"}],
    }
    return EvidenceLedger.from_profile(profile)


def test_fabricated_override_falls_back_to_grounded_concept_headline():
    led = _ledger()
    # "#1" is an unsourced ranking/first claim -> must not render.
    hl, rem = _verified_external_headline("Egypt's #1 pharmacy", "Your trusted pharmacy", led)
    assert hl == "Your trusted pharmacy"
    assert rem is not None
    assert rem["action"] == "replaced_with_grounded_concept"
    assert "first" in rem["unsourced_claims"]


def test_unsourced_number_override_is_blocked():
    led = _ledger()
    hl, rem = _verified_external_headline("Save 70% today", "Your trusted pharmacy", led)
    assert hl == "Your trusted pharmacy"
    assert rem is not None and "number" in rem["unsourced_claims"]


def test_grounded_override_is_kept():
    led = _ledger()
    hl, rem = _verified_external_headline("Your trusted pharmacy", "Concept HL", led)
    assert hl == "Your trusted pharmacy"
    assert rem is None


def test_no_override_uses_concept_headline():
    led = _ledger()
    hl, rem = _verified_external_headline(None, "Concept HL", led)
    assert hl == "Concept HL" and rem is None


def test_ledger_unavailable_falls_back_to_the_grounded_headline():
    """P6 #3 — "cannot verify" is not "verified".

    This test used to assert that "Egypt's #1 pharmacy" SHIPS when no ledger could be built —
    i.e. it pinned the hole this gate's own docstring exists to close ("a calendar topic like
    'Egypt's #1 pharmacy' would ship unverified"). An ungated external override must never win
    over the concept headline, which IS ledger-gated. Fail closed, and record why.
    """
    hl, rem = _verified_external_headline("Egypt's #1 pharmacy", "Concept HL", None)
    assert hl == "Concept HL"
    assert rem is not None and rem["action"] == "replaced_with_grounded_concept"
    assert rem["unsourced_claims"] == ["gate_unavailable"]


def test_ledger_error_also_falls_back_rather_than_shipping_the_override():
    class _Boom:
        def audit_text(self, _t):
            raise RuntimeError("ledger exploded")
    hl, rem = _verified_external_headline("Egypt's #1 pharmacy", "Concept HL", _Boom())
    assert hl == "Concept HL" and rem is not None


def test_ledger_unavailable_with_no_concept_headline_DROPS_the_override():
    # nothing grounded to fall back to -> drop. Never render the unverifiable line.
    hl, rem = _verified_external_headline("Egypt's #1 pharmacy", None, None)
    assert hl is None and rem is not None and rem["action"] == "dropped"


def test_fabricated_override_drops_when_no_grounded_alternative():
    led = _ledger()
    hl, rem = _verified_external_headline("Egypt's #1 pharmacy", None, led)
    assert hl is None
    assert rem is not None and rem["action"] == "dropped"
