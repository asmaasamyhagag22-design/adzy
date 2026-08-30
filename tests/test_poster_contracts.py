"""Batch 2 — the two shared poster prompt contracts and where they inject.

CRAFT_CONTRACT replaces the "senior / world-class / premium" priming that made every brand's
copy converge; VERBATIM_RENDER_CONTRACT is the frozen-strings rule shared by the one-shot
renderer and the OCR read-back reader. Hermetic (no network)."""
from __future__ import annotations

from poster.contracts import CRAFT_CONTRACT, VERBATIM_RENDER_CONTRACT
from poster.oneshot import build_oneshot_prompt


def test_craft_contract_sets_a_bar_and_bans_the_empty_primers():
    assert "CRAFT BAR" in CRAFT_CONTRACT
    for banned in ("premium", "world-class", "award-winning", "cutting-edge",
                   "unparalleled", "elevate", "unleash"):
        assert banned in CRAFT_CONTRACT                       # named as banned
    assert "Specificity replaces intensity" in CRAFT_CONTRACT
    assert "the FORM is yours" in CRAFT_CONTRACT              # facts gated, form free


def test_verbatim_render_contract_is_arabic_aware():
    assert "FROZEN strings" in VERBATIM_RENDER_CONTRACT
    assert "character-for-character" in VERBATIM_RENDER_CONTRACT
    assert "ة vs ه" in VERBATIM_RENDER_CONTRACT and "dot counts" in VERBATIM_RENDER_CONTRACT


def test_concept_system_uses_craft_contract_not_the_director_persona():
    import poster.concept as concept

    captured = {}
    class _Rec:
        def __call__(self, system, user, response_model, group_name="", images=None):
            captured["system"] = system
            captured["user"] = user
            # minimal valid concept
            return response_model(audience="a", single_message="m", core_benefit="b",
                                  emotional_tone="t", visual_idea="v", proof_points=[],
                                  headline="h", subheadline="s", cta="c"), None

    profile = {"name": {"value": "ITI"}, "languages": ["en"],
               "offerings": [{"name": "Software Engineering Diploma"}]}
    concept.build_creative_concept(profile, caller=_Rec(), arabic=False)
    s, u = captured["system"], captured["user"]
    assert "CRAFT BAR" in s                                   # the shared bar is injected
    assert "senior advertising CREATIVE DIRECTOR" not in s    # the priming persona is gone
    assert "STRANGER TEST" in s                               # the good part is kept
    assert "interchangeable concepts" in s                    # anti-convergence line
    # user prompt teaches jargon -> customer language without changing the facts
    assert "internal jargon" in u and "words a real customer would use" in u


def test_oneshot_prompt_fences_copy_without_contract_prose_and_is_de_primed():
    # 1.3 (owner-caught): the contract paragraph is READER instruction — embedding it in the
    # IMAGE prompt got its wording painted into posters. It must be ABSENT here (it still pins
    # the OCR read-back; see test_oneshot_prompt_fence.py).
    p = build_oneshot_prompt({"headline": "H", "subheadline": "S", "cta": "C"},
                             brand_name="B", palette_names="brick red")
    assert VERBATIM_RENDER_CONTRACT not in p                  # meta-instruction never drawable
    assert "BEGIN TEXT" in p and "END TEXT" in p              # the single drawable-text fence
    assert "EXACTLY as written" in p                          # the exact-render demand survives
    assert "premium social-media advertising poster" not in p # de-primed opener


def test_compliance_for_maps_categories_to_ad_safety_rules():
    from poster.contracts import compliance_for
    assert "before/after" in compliance_for("clinic") and "patients" in compliance_for("hospital")
    assert "before/after" in compliance_for("beauty") and "clinical claims" in compliance_for("beauty")
    assert "guaranteed" in compliance_for("professional_services")
    assert compliance_for("restaurant") == "" and compliance_for("education") == ""
    assert compliance_for(None) == ""


def test_llm_image_path_gets_compliance_for_medical_not_restaurant():
    # The owner's #1 Batch-3 fix: the LLM image path had NO compliance gate. Now a clinic brand's
    # image prompt carries the ad-safety rules; a restaurant's does not.
    from poster.art_director import build_llm_concept_prompt
    from poster.schemas import PosterBrief

    cap = {}
    class _M:
        def __call__(self, system, user, response_model, group_name="", images=None):
            cap["user"] = user
            return response_model(concept_title="t", image_prompt="p"), None

    clinic = PosterBrief(business_name="DermaCare", category="clinic", headline="Header",
                         subheadline="Sub", cta_text="Book", palette_hex=["#111111"])
    # WITNESS THE OUTPUT, NOT THE INTERMEDIATE HOP. The old test asserted on cap["user"] — the
    # concept-LLM's INPUT — which is exactly the value art_director throws away when it rebuilds
    # `lead` from scratch (:836-866). So a clinic's REAL image prompt shipped with no ad-safety
    # gate while a green test "certified" it (the CS-02 finding). Assert on the RETURN VALUE —
    # the prompt that actually reaches the image model.
    prompt = build_llm_concept_prompt(clinic, caller=_M())
    assert "COMPLIANCE (ad-safety" in prompt and "before/after" in prompt
    assert "COMPLIANCE (ad-safety" in cap["user"]        # the concept LLM still receives it too

    rest = PosterBrief(business_name="Grill", category="restaurant", headline="Header",
                       subheadline="Sub", cta_text="Order", palette_hex=["#111111"])
    prompt = build_llm_concept_prompt(rest, caller=_M())
    assert "COMPLIANCE (ad-safety" not in prompt          # a restaurant carries no medical gate


def test_reel_director_gets_compliance_for_medical_not_restaurant():
    from reel.creative_director import _system_prompt
    from poster.contracts import compliance_for
    clinic = _system_prompt(4, "ar", "generic", compliance=compliance_for("clinic"))
    rest = _system_prompt(4, "ar", "generic", compliance=compliance_for("restaurant"))
    assert "COMPLIANCE (ad-safety" in clinic and "before/after" in clinic
    assert "COMPLIANCE (ad-safety" not in rest
