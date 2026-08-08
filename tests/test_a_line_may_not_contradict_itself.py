"""A SWOT line and its own priority block must not tell the client two different things.

MEASURED 2026-08-02 on the written alfouadpharmacies_com record, found by an adversarial audit of
a change I had already checked myself and called clean:

    "source_theme": "brand signal (value_propositions) — no compared peer promises this (2 checked)"
    "priority": { "peers_measured": 0,
                  "basis": "ادعاء البراند عن نفسه (من موقعه)، لم يُقارَن بأحد — …" }

One object, saying both that two peers were checked and that nobody was. `brand_signals` really did
compare the claim against the peers' page words; `score_opinion_item` had no way to know, so it
stamped every self-claim as uncompared. The comparison happened — only the record disagreed with
itself, and it is the record the client reads.

The second half is the same disease in its quieter form: a claim with no comparable promise got NO
annotation at all, so a fourth strength from the identical field sat blank next to three annotated
ones and nothing said whether it had been checked and passed or never checked.
"""
from __future__ import annotations

from types import SimpleNamespace

from competitor.peer_words import PeerWords, differentiation_note
from competitor.swot_priority import score_opinion_item


def _peer(name, promises, checked=True):
    return (name, PeerWords(categories=[], headings=[], badges=list(promises),
                            promises=list(promises), languages=[], checked=checked))


# --- the scorer stops contradicting the line it scores -----------------------------------

def test_a_claim_that_was_checked_does_not_report_zero_peers():
    p = score_opinion_item(claim_strength="internally_supported", peers_compared=2)
    assert p.peers_measured == 2
    assert "لم يُقارَن بأحد" not in (p.basis or ""), "the basis still says nobody was compared"
    assert "2" in (p.basis or "")


def test_a_claim_that_was_not_checked_still_says_so():
    """The honest half must survive the fix — a self-claim nobody compared is exactly that."""
    p = score_opinion_item(claim_strength="internally_supported")
    assert p.peers_measured == 0
    assert "لم يُقارَن" in (p.basis or "")


def test_being_checked_does_not_promote_the_claim():
    """A promise no rival makes is still the brand's own promise, not a proven advantage. Only the
    sentence changes; the rank must not move, or the fix would smuggle in a score change."""
    checked = score_opinion_item(claim_strength="internally_supported", peers_compared=3)
    unchecked = score_opinion_item(claim_strength="internally_supported")
    assert checked.rank == unchecked.rank
    assert checked.importance == unchecked.importance
    assert checked.focus is unchecked.focus


def test_an_explicit_basis_still_wins():
    p = score_opinion_item(claim_strength="internally_supported", peers_compared=2, basis="custom")
    assert p.basis == "custom"


def test_a_site_observation_is_not_relabelled_as_a_compared_claim():
    """"No clear call-to-action on the page" is something the crawler READ, not something the brand
    claimed — that distinction was itself a measured fix and must not be undone here."""
    p = score_opinion_item(claim_strength="internally_supported", kind="site_observation",
                           peers_compared=2)
    assert "ادعاء البراند" not in (p.basis or "")


# --- silence is not an answer ------------------------------------------------------------

_PEERS = [_peer("bloom", ["fast free shipping"]), _peer("eds", ["same day delivery"])]


def test_a_claim_with_no_comparable_promise_says_so_rather_than_nothing():
    """MEASURED: "Broad Delivery Coverage" produced no note, so it sat blank beside three annotated
    siblings from the same field. "Not comparable" and "not checked" are different facts."""
    note = differentiation_note("Broad Delivery Coverage Nationwide Reach", _PEERS)
    assert note, "a checked claim must never come back with nothing to say"
    assert "2" in note


def test_the_three_verdicts_are_distinguishable():
    shared = differentiation_note("fast free shipping on every order", _PEERS)
    unique = differentiation_note("24/7 pharmacist hotline", _PEERS)
    silent = differentiation_note("Licensed pharmacists on staff", _PEERS)
    assert shared and unique and silent
    assert len({shared, unique, silent}) == 3, "the three verdicts must be tellable apart"
    assert "promise the same thing" in shared
    assert "no compared peer promises this" in unique
    assert "not comparable" in silent


def test_no_readable_peer_is_the_one_case_that_stays_silent():
    """With nothing read there is genuinely nothing to say, and inventing a verdict would be worse
    than the blank. This is the boundary of the fix, stated on purpose."""
    assert differentiation_note("free consultation", [_peer("x", [], checked=False)]) is None
    assert differentiation_note("free consultation", []) is None


# --- the number travels with the item ----------------------------------------------------

def test_the_item_carries_the_peer_count_not_just_the_prose():
    """`evidence` is English prose for a human; the scorer needs a number. Parsing the sentence back
    out would make the two drift apart the first time the wording changed."""
    from grounding import EvidenceLedger

    from competitor.brand_signals import strengths_from_profile
    prof = {"value_propositions": [
        {"value": "24/7 pharmacist hotline",
         "evidence": [{"quote": "24/7 pharmacist hotline", "page_url": "https://b.com/en"}]}]}
    # The ledger is built from the profile MINUS the field it is about to judge — the non-circular
    # construction this repo settled on. The evidence quote is real page text and stays.
    ledger = EvidenceLedger.from_profile(prof, exclude_fields=("value_propositions",))
    out = strengths_from_profile(prof, ledger, peers=_PEERS)
    assert out, "the claim should have survived"
    assert getattr(out[0], "peers_compared", 0) == 2


def test_a_run_with_no_peers_records_zero():
    from grounding import EvidenceLedger

    from competitor.brand_signals import strengths_from_profile
    prof = {"value_propositions": [
        {"value": "24/7 pharmacist hotline",
         "evidence": [{"quote": "24/7 pharmacist hotline", "page_url": "https://b.com/en"}]}]}
    out = strengths_from_profile(
        prof, EvidenceLedger.from_profile(prof, exclude_fields=("value_propositions",)))
    assert out and getattr(out[0], "peers_compared", 0) == 0


def test_peer_promises_are_understood_as_text_or_as_concept_ids():
    """`PeerWords.promises` stores concept ids; a hand-built peer naturally holds the promise TEXT.

    The mismatch used to be silent AND flattering: "fast free shipping" compared against
    `{free_shipping}` found no overlap and returned "no compared peer promises this" — a claimed
    differentiator manufactured out of a shape difference. Both shapes must mean the same thing."""
    from competitor.peer_words import claim_is_commoditised
    as_text = [_peer("bloom", ["fast free shipping"])]
    as_ids = [_peer("bloom", ["free_shipping"])]
    claim = "free shipping on every order"
    assert claim_is_commoditised(claim, as_text, min_peers=1)[1] == ["bloom"]
    assert claim_is_commoditised(claim, as_ids, min_peers=1)[1] == ["bloom"]
    assert differentiation_note(claim, as_text) == differentiation_note(claim, as_ids)
