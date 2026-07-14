# -*- coding: utf-8 -*-
"""Render #3 §0 — the orchestrator. Hermetic (fake caller, fake nano, fake Veo).

The two laws under test:
  HITL #1: prepare_render3 spends ZERO seed/Veo calls — only director + 1 charsheet.
  G2 invariant: Veo is NEVER constructed while the seed set fails the gate.
"""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from PIL import Image

import reel.render3.orchestrate as orch
from reel.render3.g2 import G2Verdict
from tests.test_render3_director import _treatment

_PROFILE = {"profile": {"name": "NTI", "description": "training institute",
                        "value_props": ["free courses"], "trust_signals": [],
                        "offerings": []}}


def _fake_nano(tmp_path, calls):
    """Records every generate_with_refs call; writes a real PNG."""
    def gen(prompt, out_path, *, refs=None, pro=False, log=print):
        calls.append({"prompt": prompt, "n_refs": len(refs or []), "pro": pro,
                      "out": str(out_path)})
        Image.new("RGB", (120, 200), (80 + 20 * len(calls), 90, 120)).save(out_path, "PNG")
        return Path(out_path)
    return gen


class _DirectorCaller:
    """Returns the fixture treatment for the director group."""

    def __init__(self, treatments):
        self.treatments = list(treatments)

    def __call__(self, system, user, response_model, group_name="", images=None):
        assert group_name == "render3_director"
        return self.treatments.pop(0), None


class _GateCaller:
    def __init__(self, verdicts):
        self.verdicts = list(verdicts)

    def __call__(self, system, user, response_model, group_name="", images=None):
        assert group_name == "render3_g2"
        return self.verdicts.pop(0), None


class _VeoRecorder:
    def __init__(self):
        self.calls = []

    def generate(self, prompt, *, out_path, duration_s, width, height,
                 palette=None, reference_image=None):
        self.calls.append({"prompt": prompt, "duration_s": duration_s,
                           "seed": reference_image})
        Path(out_path).write_bytes(b"fake-mp4")
        return Path(out_path)


def test_prepare_zero_seed_spend_and_package(tmp_path, monkeypatch):
    nano_calls = []
    monkeypatch.setattr(orch, "generate_with_refs", _fake_nano(tmp_path, nano_calls))
    prep = orch.prepare_render3(_PROFILE, caller=_DirectorCaller([_treatment()]),
                                out_dir=tmp_path, log=lambda *a: None)
    assert "error" not in prep
    # EXACTLY one image call (the character sheet) — zero seeds, zero Veo (HITL law).
    assert len(nano_calls) == 1 and nano_calls[0]["n_refs"] == 0
    assert "character reference sheet" in nano_calls[0]["prompt"]
    assert Path(prep["sheet_path"]).is_file() and Path(prep["treatment_path"]).is_file()
    saved = json.loads(Path(prep["treatment_path"]).read_text(encoding="utf-8"))
    assert saved["big_idea"] and len(saved["shots"]) == 6
    assert len(prep["hashes"]) == 6                       # 3 style + 3 character locks


def test_prepare_generate_card_false_is_text_only(tmp_path, monkeypatch):
    # §5: after a concept pick, the clean treatment is presented text-only BEFORE any card
    nano_calls = []
    monkeypatch.setattr(orch, "generate_with_refs", _fake_nano(tmp_path, nano_calls))
    prep = orch.prepare_render3(_PROFILE, caller=_DirectorCaller([_treatment()]),
                                out_dir=tmp_path, generate_card=False, log=lambda *a: None)
    assert "error" not in prep and prep["card_pending"] is True
    assert prep["sheet_path"] is None
    assert nano_calls == []                               # ZERO image spend at concept approval
    assert Path(prep["treatment_path"]).is_file()         # treatment still saved


def test_prepare_concept_lock_pins_story(tmp_path, monkeypatch):
    monkeypatch.setattr(orch, "generate_with_refs", _fake_nano(tmp_path, []))
    seen = {}

    class _LockCaller:
        def __call__(self, system, user, response_model, group_name="", images=None):
            seen["prompt"] = user
            return _treatment(), None

    orch.prepare_render3(_PROFILE, caller=_LockCaller(), out_dir=tmp_path,
                         concept_lock="  BIG IDEA: a clock that runs backward",
                         generate_card=False, log=lambda *a: None)
    assert "LOCKED CONCEPT" in seen["prompt"] and "clock that runs backward" in seen["prompt"]


def test_prepare_g1_fail_retries_then_loud(tmp_path, monkeypatch):
    monkeypatch.setattr(orch, "generate_with_refs",
                        _fake_nano(tmp_path, []))
    bad = _treatment()
    bad.shots[0].action = bad.shots[0].action + " she is smiling warmly"   # smile ban
    prep = orch.prepare_render3(_PROFILE, caller=_DirectorCaller([bad] * 4),
                                out_dir=tmp_path, log=lambda *a: None)
    assert "error" in prep and "after 4 attempts" in prep["error"]
    # recovery on the LAST corrective retry still ships
    good = _treatment()
    prep2 = orch.prepare_render3(_PROFILE, caller=_DirectorCaller([bad, bad, bad, good]),
                                 out_dir=tmp_path, log=lambda *a: None)
    assert "error" not in prep2


def test_continue_g2_fail_blocks_veo(tmp_path, monkeypatch):
    nano_calls = []
    monkeypatch.setattr(orch, "generate_with_refs", _fake_nano(tmp_path, nano_calls))
    prep = orch.prepare_render3(_PROFILE, caller=_DirectorCaller([_treatment()]),
                                out_dir=tmp_path, log=lambda *a: None)
    veo = _VeoRecorder()
    bad = G2Verdict(same_person=False,
                    identity_offenders=[{"frame": 1, "reason": "drift"}],
                    same_world_grade=True, junk_screens=[], arc_readable=True)
    with pytest.raises(RuntimeError, match="Veo blocked"):
        orch.continue_render3(prep, caller=_GateCaller([bad, bad]),
                              out_dir=tmp_path / "run", veo_provider=veo,
                              approval=prep["approval_token"],
                              max_g2_retries=2, log=lambda *a: None)
    assert veo.calls == []                                 # the invariant: zero Veo spend


def test_needs_pro_lane_detects_legible_text():
    from reel.render3.orchestrate import _needs_pro_lane

    class _S:
        def __init__(self, sr, action="", camera=""):
            self.screen_rule, self.action, self.camera = sr, action, camera

    assert _needs_pro_lane(_S("REAL_CONTENT:a terminal with text"))          # screen text
    assert _needs_pro_lane(_S("NONE", camera="the keycard title is legible"))  # in-frame text
    assert _needs_pro_lane(_S("NONE", action="the sign clearly see-able"))
    assert not _needs_pro_lane(_S("NONE", action="he types", camera="close-up"))  # no text
    assert not _needs_pro_lane(_S("OUT_OF_FOCUS", camera="soft glow"))


def test_stop_after_g2_returns_verified_seeds_without_veo(tmp_path, monkeypatch):
    nano_calls = []
    monkeypatch.setattr(orch, "generate_with_refs", _fake_nano(tmp_path, nano_calls))
    prep = orch.prepare_render3(_PROFILE, caller=_DirectorCaller([_treatment()]),
                                out_dir=tmp_path, log=lambda *a: None)
    veo = _VeoRecorder()
    ok = G2Verdict(same_person=True, same_world_grade=True, junk_screens=[],
                   arc_readable=True)
    res = orch.continue_render3(prep, caller=_GateCaller([ok]),
                                out_dir=tmp_path / "run", veo_provider=veo,
                                approval=prep["approval_token"],
                                stop_after_g2=True, log=lambda *a: None)
    assert res["veo_pending"] is True and res["clips"] == []
    assert veo.calls == []                                   # NO Veo spend at the G2 checkpoint
    assert res["g2"]["verdict"] == "PASS" and len(res["seeds"]) == 6
    # resuming animates exactly the verified seeds
    clips = orch.render_veo_from_seeds(res["treatment"], res["seeds"],
                                       out_dir=tmp_path / "run", veo_provider=veo,
                                       log=lambda *a: None)
    assert len(clips) == 6 and [c["seed"] for c in veo.calls] == res["seeds"]


def test_continue_pass_animates_every_verified_seed(tmp_path, monkeypatch):
    nano_calls = []
    monkeypatch.setattr(orch, "generate_with_refs", _fake_nano(tmp_path, nano_calls))
    prep = orch.prepare_render3(_PROFILE, caller=_DirectorCaller([_treatment()]),
                                out_dir=tmp_path, log=lambda *a: None)
    veo = _VeoRecorder()
    ok = G2Verdict(same_person=True, same_world_grade=True, junk_screens=[],
                   arc_readable=True)
    res = orch.continue_render3(prep, caller=_GateCaller([ok]),
                                out_dir=tmp_path / "run", veo_provider=veo,
                                approval=prep["approval_token"],
                                log=lambda *a: None)
    t = res["treatment"]
    assert len(veo.calls) == len(t.shots) == len(res["clips"])
    assert [c["seed"] for c in veo.calls] == res["seeds"]  # animated FROM the verified seeds
    assert all("Animate this exact frame" in c["prompt"] for c in veo.calls)
    # seeds carried the identity ref sheet (>=1 ref on every seed call after the charsheet)
    seed_calls = nano_calls[1:]
    assert len(seed_calls) == len(t.shots)
    assert all(c["n_refs"] >= 1 for c in seed_calls)
    assert res["g2"]["verdict"] == "PASS"


def test_continue_without_approval_token_refuses_all_spend(tmp_path, monkeypatch):
    # H2: the HITL law is code-enforced — no token, no seed and no Veo spend.
    nano_calls = []
    monkeypatch.setattr(orch, "generate_with_refs", _fake_nano(tmp_path, nano_calls))
    prep = orch.prepare_render3(_PROFILE, caller=_DirectorCaller([_treatment()]),
                                out_dir=tmp_path, log=lambda *a: None)
    charsheet_spend = len(nano_calls)                          # only the HITL #1 card so far
    veo = _VeoRecorder()
    ok = G2Verdict(same_person=True, same_world_grade=True, junk_screens=[], arc_readable=True)
    with pytest.raises(PermissionError, match="HITL"):
        orch.continue_render3(prep, caller=_GateCaller([ok]),
                              out_dir=tmp_path / "run", veo_provider=veo,
                              log=lambda *a: None)                 # approval omitted
    assert len(nano_calls) == charsheet_spend                  # ZERO seed spend
    assert veo.calls == []                                      # ZERO Veo spend


def test_continue_refuses_mutated_treatment(tmp_path, monkeypatch):
    # Content-bound: approving concept A never authorizes rendering a mutated concept B.
    nano_calls = []
    monkeypatch.setattr(orch, "generate_with_refs", _fake_nano(tmp_path, nano_calls))
    prep = orch.prepare_render3(_PROFILE, caller=_DirectorCaller([_treatment()]),
                                out_dir=tmp_path, log=lambda *a: None)
    approved = prep["approval_token"]
    prep["treatment"].shots[0].action += " (edited after approval)"   # mutate post-sign-off
    veo = _VeoRecorder()
    ok = G2Verdict(same_person=True, same_world_grade=True, junk_screens=[], arc_readable=True)
    with pytest.raises(PermissionError, match="token"):
        orch.continue_render3(prep, caller=_GateCaller([ok]),
                              out_dir=tmp_path / "run", veo_provider=veo,
                              approval=approved, log=lambda *a: None)  # stale token
    assert veo.calls == []
