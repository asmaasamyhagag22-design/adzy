# -*- coding: utf-8 -*-
"""أرقام المالك (owner ask 2026-07-20 "حط مكان يحطها فيه"): the vault gets input fields whose
values persist per brand and RECOMPUTE the governing numbers — margin flips the kill-switch to
"من رقمك", capacity flips its source to human, garbage never clamps silently. Hermetic."""
from __future__ import annotations

from dashboard.owner_inputs import load_owner_inputs, margin_fraction, save_owner_inputs


def test_roundtrip_merge_and_clear(tmp_path):
    d = str(tmp_path)
    save_owner_inputs("b", {"profit_margin_pct": 45, "capacity_per_day": 80}, out_dir=d)
    save_owner_inputs("b", {"monthly_budget": 30000}, out_dir=d)          # merge, not replace
    got = load_owner_inputs("b", out_dir=d)
    assert got["profit_margin_pct"] == 45 and got["capacity_per_day"] == 80
    assert got["monthly_budget"] == 30000
    save_owner_inputs("b", {"capacity_per_day": ""}, out_dir=d)           # explicit clear
    assert "capacity_per_day" not in load_owner_inputs("b", out_dir=d)


def test_garbage_is_dropped_never_clamped(tmp_path):
    d = str(tmp_path)
    save_owner_inputs("b", {"profit_margin_pct": 400, "capacity_per_day": -5,
                            "monthly_budget": "كتير"}, out_dir=d)
    assert load_owner_inputs("b", out_dir=d) == {}              # nothing usable survived


def test_owner_margin_flips_the_kill_switch_basis():
    from media_plan.config import kill_switch_cpl
    cpl_prior, basis_prior = kill_switch_cpl(225.0, "ecommerce")
    assert basis_prior == "price_margin" and cpl_prior == 20.25
    cpl_own, basis_own = kill_switch_cpl(225.0, "ecommerce", margin=0.45)
    assert basis_own == "price_margin_owner" and cpl_own == round(225 * 0.45 * 0.3, 2)


def test_vault_page_reflects_owner_numbers(tmp_path):
    import json

    from tests.test_war_room import _SLUG, _build, _result
    (tmp_path / f"{_SLUG}_owner_inputs.json").write_text(
        json.dumps({"profit_margin_pct": 45, "capacity_per_day": 80}), encoding="utf-8")
    h = _build(tmp_path, result=_result(priced=True), serve_mode=True)
    assert "هامش ربحك اللي أدخلتيه ✓" in h                      # owner-margin basis line
    assert "طاقة الاستيعاب من رقمك ✓" in h                      # human capacity chip
    assert 'id="oi-margin"' in h and 'id="oi-save"' in h        # the inputs panel (serve)
    assert 'value="45' in h                                     # saved values pre-filled
    h2 = _build(tmp_path, result=_result(priced=True), serve_mode=False)
    assert 'id="oi-save"' not in h2                             # export ships no dead form
