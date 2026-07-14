# -*- coding: utf-8 -*-
"""Render #3 §1 — the Director schema + G1 lint. Hermetic (MockCaller / pure code).

G1 is the first gate: a treatment violating any hard rule never reaches spend."""
from __future__ import annotations

from reel.render3.director import (
    ActPalette, CharacterSheet, ColorConstants, ColorScript, Motif, ReelTreatment, Shot,
    direct_reel, evidence_bullets_from_profile, g1_lint,
)


def _treatment(**over) -> ReelTreatment:
    shots = over.pop("shots", None) or [
        Shot(id=i + 1, act=(1 if i < 2 else 2 if i < 4 else 3), duration_s=5,
             location="bedroom desk" if i < 2 else "NTI lab" if i < 4 else "office",
             action=f"action {i + 1}", emotion="focused", story_function="beat",
             camera="close-up", screen_rule="ANGLED_AWAY",
             motif_placement="notebook on desk",
             match_cut_out="" if i == 5 else "hand reaches right")
        for i in range(6)
    ]
    base = dict(
        big_idea="From rejection to hired", logline="A graduate's journey",
        vo_script_ar=("كل يوم كنت أدور على فرصة ومفيش أي رد يوصلني. " * 5
                      + "قدمت في المعهد وبدأت أتعلم بجد كل يوم. "
                      + "النهاردة أنا جاهزة للشغل وواثقة في نفسي. قدم دلوقتي وابدأ."),
        motif=Motif(object="worn notebook", state_act1="closed", state_act2="open with notes",
                    state_act3="closed with a badge on top"),
        color_script=ColorScript(
            act1=ActPalette(hex=["#5B6770", "#8A93A2"], grade="cool desaturated"),
            act2=ActPalette(hex=["#A98F68", "#C9A66B"], grade="warming neutral"),
            act3=ActPalette(hex=["#C9A66B", "#E8C987"], grade="warm golden"),
            constants=ColorConstants(lens="50mm", dof="f/2.0 shallow",
                                     lighting="soft practical", grain="subtle film grain")),
        character_sheet=CharacterSheet(
            name="Salma", age=24, skin_tone="warm brown", face="round",
            distinctive_features=["beauty mark above right eyebrow", "rounded chin"],
            eyes="dark brown", hijab="chocolate-brown with cream polka dots, modern wrap",
            outfit_act1="beige cardigan over dark top", outfit_act3="navy blazer",
            constant_accessory="worn A4 spiral notebook with a blue sticker"),
        locations=["bedroom desk", "NTI lab", "office"],
        shots=shots,
    )
    base.update(over)
    return ReelTreatment(**base)


def test_valid_treatment_passes_g1():
    r = g1_lint(_treatment())
    assert r.ok, r.issues


def test_g1_catches_each_violation_class():
    t = _treatment()
    # 1 distinctive feature (needs exactly 2)
    bad = t.model_copy(deep=True)
    bad.character_sheet.distinctive_features = ["only one"]
    assert not g1_lint(bad).ok
    # smile outside the final shot
    bad = t.model_copy(deep=True)
    bad.shots[1].action = "she smiles at her laptop"
    r = g1_lint(bad)
    assert any("smile" in i for i in r.issues)
    # missing match_cut_out mid-sequence
    bad = t.model_copy(deep=True)
    bad.shots[2].match_cut_out = ""
    assert any("match_cut_out" in i for i in g1_lint(bad).issues)
    # alien screen rule
    bad = t.model_copy(deep=True)
    bad.shots[0].screen_rule = "FUTURISTIC_UI"
    assert any("screen_rule" in i for i in g1_lint(bad).issues)
    bad.shots[0].screen_rule = "N/A"                       # measured live: N/A rejected
    assert any("screen_rule" in i for i in g1_lint(bad).issues)
    # safe token + descriptive ":detail" suffix is allowed (measured live drift)
    ok = t.model_copy(deep=True)
    ok.shots[0].screen_rule = "OUT_OF_FOCUS:A monitor blurred into bokeh."
    assert not any("screen_rule" in i for i in g1_lint(ok).issues)
    # act out of order (arc broken)
    bad = t.model_copy(deep=True)
    bad.shots[0].act = 3
    assert any("order" in i or "acts" in i for i in g1_lint(bad).issues)
    # VO too short
    bad = t.model_copy(deep=True)
    bad.vo_script_ar = "قصير جداً"
    assert any("VO is" in i for i in g1_lint(bad).issues)
    # location not declared
    bad = t.model_copy(deep=True)
    bad.shots[0].location = "undeclared alley"
    assert any("not in locations" in i for i in g1_lint(bad).issues)
    # H1: shot durations must sum to the target runtime (each shot still 3-8s, but sum blows out)
    bad = t.model_copy(deep=True)
    for s in bad.shots:
        s.duration_s = 8                                       # 6x8 = 48s, far past 30±5
    assert any("sum to" in i for i in g1_lint(bad).issues)
    # L1: duplicate shot ids would collide seed/clip filenames
    bad = t.model_copy(deep=True)
    bad.shots[1].id = bad.shots[0].id
    assert any("duplicate shot ids" in i for i in g1_lint(bad).issues)


def test_g1_moat_skip_is_visible_not_silent(monkeypatch):
    # C2: if the VO<->evidence ledger can't run, that must FAIL VISIBLE (moat_skipped + a G1
    # issue), never pass as silently clean.
    import grounding

    class _Boom:
        @staticmethod
        def from_profile(_p):
            raise RuntimeError("ledger unavailable")

    monkeypatch.setattr(grounding, "EvidenceLedger", _Boom)
    r = g1_lint(_treatment(), profile={})
    assert r.moat_skipped is True and not r.ok
    assert any("moat could not run" in i for i in r.issues)


def test_g1_vo_ledger_trace_flags_unsourced_claims():
    t = _treatment()
    t.vo_script_ar = ("إحنا المعهد رقم واحد في مصر وحاصلين على جايزة أفضل تدريب. " * 3
                      + "قدم دلوقتي وابدأ مستقبلك من غير تردد وهتوصل لهدفك بسرعة.")
    r = g1_lint(t, profile={})            # empty profile -> nothing can source the claims
    assert not r.ok and r.unsourced_vo_claims
    # a claim-free emotive VO passes the trace against the same empty profile
    r2 = g1_lint(_treatment(), profile={})
    assert not r2.unsourced_vo_claims


def test_evidence_bullets_projection():
    prof = {"description": {"value": "NTI trains engineers."},
            "value_propositions": [{"value": "Workforce-ready graduates"}],
            "trust_signals": [{"value": "Accredited by the Supreme Council"}],
            "offerings": [{"name": {"value": "HireReady"}, "price_text": {"value": "free"}}]}
    b = evidence_bullets_from_profile(prof)
    assert "NTI trains engineers." in b[0]
    assert any("Workforce-ready" in x for x in b)
    assert any("Accredited" in x for x in b)
    assert any("HireReady — free" in x for x in b)


def test_director_returns_validated_treatment_and_retries_once():
    calls = {"n": 0}

    class _Flaky:
        def __call__(self, system, user, model, group_name="", images=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ValueError("parse failure")
            assert "R1 ONE PROTAGONIST" in user and "R8 VO" in user   # the pack prompt verbatim
            assert "EVIDENCE" in user
            return _treatment(), None

    t = direct_reel({"name": {"value": "NTI"}}, caller=_Flaky())
    assert t is not None and t.character_sheet.name == "Salma"
    assert calls["n"] == 2                     # one retry, then success
    assert direct_reel({}, caller=None) is None
