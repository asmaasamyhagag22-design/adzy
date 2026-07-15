"""Vertex spend guard (vertex_spend.py) — meters + caps Veo + image renders so credit can't burn
invisibly (the 2026-07-14 $228/4-days scare). Hermetic: a temp ledger, no network, no creds."""
from __future__ import annotations

from pathlib import Path

import pytest

import vertex_spend as vs


@pytest.fixture()
def ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("VERTEX_MONTHLY_CAP_USD", "10")
    monkeypatch.setenv("VEO_USD_PER_SEC", "0.40")
    monkeypatch.setenv("PRO_IMAGE_USD", "0.12")
    monkeypatch.setenv("NANO_IMAGE_USD", "0.04")
    return tmp_path / "vertex_spend.jsonl"


def test_estimates_video_and_images(ledger):
    assert vs.est_video_cost_usd(8) == 3.2
    assert vs.est_image_cost_usd(1, "gemini-3.1-flash-image-preview") == 0.04   # nano
    assert vs.est_image_cost_usd(1, "gemini-3-pro-image-preview") == 0.12       # pro (legible text)
    assert vs.est_image_cost_usd(3, "flash-image") == 0.12


def test_month_spend_sums_only_this_month(ledger):
    vs.record("veo", 8, 3.2, ledger=ledger)
    vs.record("image", 1, 0.04, ledger=ledger)
    # a row stamped to a different month must not count
    with ledger.open("a", encoding="utf-8") as f:
        f.write('{"month":"2000-01","est_cost_usd":99}\n')
    assert vs.month_spend_usd(ledger) == 3.24


def test_check_budget_allows_under_and_refuses_over(ledger):
    for _ in range(2):
        vs.record("veo", 8, 3.2, ledger=ledger)          # $6.40 metered
    ok, _ = vs.check_budget(3.2, ledger=ledger)           # 9.60 <= 10 -> ok
    assert ok
    vs.record("veo", 8, 3.2, ledger=ledger)               # $9.60 metered
    ok, msg = vs.check_budget(3.2, ledger=ledger)         # 12.80 > 10 -> refused
    assert not ok and "REFUSED" in msg and "VERTEX_MONTHLY_CAP_USD" in msg


def test_guard_video_raises_over_cap(ledger):
    vs.record("veo", 8, 9.9, ledger=ledger)
    with pytest.raises(vs.SpendRefused):
        vs.guard_video(8, ledger=ledger)                  # would push over $10


def test_guard_image_raises_over_cap(ledger):
    vs.record("image", 1, 9.99, ledger=ledger)
    with pytest.raises(vs.SpendRefused):
        vs.guard_image(1, "gemini-3-pro-image-preview", ledger=ledger)


def test_spend_summary_breaks_down_by_kind(ledger):
    vs.record("veo", 8, 3.2, ledger=ledger)
    vs.record("image", 1, 0.04, ledger=ledger)
    s = vs.spend_summary(ledger)
    assert s["events"] == 2 and s["total_usd"] == 3.24
    assert s["by_kind"]["veo"] == 3.2 and s["by_kind"]["image"] == 0.04


def test_a_junk_line_with_no_cost_is_simply_skipped(ledger):
    # a line that never carried a cost can be dropped safely — dropping $0 cannot open budget.
    ledger.write_text("not json\n" + '{"month":"%s","est_cost_usd":5}\n' % vs.month_key(),
                      encoding="utf-8")
    assert vs.month_spend_usd(ledger) == 5.0


def test_a_TRUNCATED_SPEND_line_never_opens_budget(ledger):
    """P6 #6 — the guard must fail CLOSED when the month total is unknowable.

    The old test here corrupted `"not json"` — a line that never had a cost — so it proved
    nothing: dropping a $0 line cannot re-open budget. The REAL risk is a truncated line that WAS
    a spend record, which is exactly what a killed render appends. Dropping it silently erases
    $40 from the month and re-authorises a $50 month. The module docstring claimed a corrupt line
    "never opens budget — callers add their estimate before comparing"; that reasoning is wrong,
    because the caller only ever adds ITS OWN estimate and never recovers the dropped one.
    """
    mk = vs.month_key()
    ledger.write_text(
        '{"month":"%s","est_cost_usd":40.0,"kind":"veo"' % mk + "\n"        # TRUNCATED mid-record
        '{"month":"%s","est_cost_usd":5.0,"kind":"image"}\n' % mk,
        encoding="utf-8")
    # $40 is unreadable => the month total is UNKNOWABLE. It must not quietly read as $5.
    ok, msg = vs.check_budget(1.0, ledger=ledger, cap=50.0)
    assert ok is False, "a truncated SPEND line silently re-opened the budget"
    assert "unreadable" in msg.lower()
    # and the corruption must be visible, not inferred
    assert vs.corrupt_line_count(ledger) == 1


def test_the_SHIPPED_default_vertex_cap_is_50(monkeypatch):
    """P6 #5 — tests/conftest.py raises VERTEX_MONTHLY_CAP_USD to 100000 for EVERY test, so
    nothing in the suite bound the LIVE default: you could edit the "50" literal to "50000" and
    all tests would still pass. This reads the cap exactly as production does with the env seam
    DEFEATED — the tests/test_ssrf_coverage.py pattern — so it fails if the shipped default moves.
    This is the guard built after the 2026-07-14 $228/4-days scare; its value must be pinned.
    """
    monkeypatch.delenv("VERTEX_MONTHLY_CAP_USD", raising=False)
    assert vs.monthly_cap_usd() == 50.0


def test_veo_provider_refuses_render_over_cap_before_any_network(monkeypatch, tmp_path):
    # The guard fires BEFORE self._client(), so a real VeoProvider refuses with no creds/network.
    monkeypatch.setenv("VERTEX_MONTHLY_CAP_USD", "1")
    monkeypatch.setattr(vs, "DEFAULT_LEDGER", tmp_path / "v.jsonl")
    vs.record("veo", 8, 0.99, ledger=tmp_path / "v.jsonl")
    from reel.video_provider import VeoProvider

    def _boom():
        raise AssertionError("_client() must not run when the spend cap already refused")
    prov = VeoProvider()
    monkeypatch.setattr(prov, "_client", _boom)
    with pytest.raises(vs.SpendRefused):
        prov.generate("a prompt", out_path=tmp_path / "clip.mp4", duration_s=6,
                      width=1080, height=1920)


# ---------------------------------------------------------------------------------------------
# C1 — PER-RUN Veo budget. The monthly cap cannot stop ONE runaway reel.
# ---------------------------------------------------------------------------------------------

def test_the_SHIPPED_default_per_run_veo_budget_is_8(monkeypatch):
    """Sibling of the shipped-monthly-cap guard: pin the LIVE default with the env seam defeated."""
    monkeypatch.delenv("VEO_MAX_USD_PER_RUN", raising=False)
    assert vs.veo_max_usd_per_run() == 8.0


def test_a_RUN_that_would_exceed_its_veo_budget_is_refused_even_with_an_empty_month(monkeypatch, tmp_path):
    """C1 — the unit is the RUN, not the clip.

    guard_video is PER CLIP: video_provider.py snaps to 4/6/8s, so ONE call can never exceed
    $3.20 and a per-render cap could never bind. But a reel is 6-8 clips = $12-26, which is what
    ate half a $50 month. Money accumulates per RUN, so the ceiling lives there.
    """
    monkeypatch.setenv("VERTEX_MONTHLY_CAP_USD", "100000")   # month is wide open...
    monkeypatch.setenv("VEO_MAX_USD_PER_RUN", "8")
    monkeypatch.setenv("TELEMETRY_RUN_ID", "run_abc")
    led = tmp_path / "v.jsonl"
    # this run has already rendered ~$7.20 of clips (3 x 6s)
    for i in range(3):
        vs.record("veo", 6, 2.40, ledger=led, detail=f"clip{i}.mp4")
    assert vs.run_spend_usd("veo", ledger=led) == 7.2
    # the next 8s clip ($3.20) would take the RUN to $10.40 > $8 -> REFUSED, month notwithstanding
    with pytest.raises(vs.SpendRefused) as e:
        vs.guard_video(8, ledger=led, detail="clip3.mp4", model="veo-3.1")
    msg = str(e.value)
    assert "per-run" in msg.lower() or "this run" in msg.lower()
    assert "VEO_MAX_USD_PER_RUN" in msg          # the refusal names the override that unblocks it
    assert "clip3.mp4" in msg                    # detail= was accepted-and-discarded before; now live


def test_an_explicit_override_lets_a_long_reel_through(monkeypatch, tmp_path):
    monkeypatch.setenv("VERTEX_MONTHLY_CAP_USD", "100000")
    monkeypatch.setenv("VEO_MAX_USD_PER_RUN", "8")
    monkeypatch.setenv("TELEMETRY_RUN_ID", "run_ovr")
    led = tmp_path / "v.jsonl"
    for i in range(3):
        vs.record("veo", 6, 2.40, ledger=led)
    assert vs.guard_video(8, ledger=led, max_usd=50.0) == 3.2      # kwarg overrides, like check_budget(cap=)


def test_run_spend_is_scoped_to_THIS_run_not_the_whole_ledger(monkeypatch, tmp_path):
    led = tmp_path / "v.jsonl"
    monkeypatch.setenv("TELEMETRY_RUN_ID", "run_1")
    vs.record("veo", 8, 3.20, ledger=led)
    monkeypatch.setenv("TELEMETRY_RUN_ID", "run_2")
    vs.record("veo", 8, 3.20, ledger=led)
    assert vs.run_spend_usd("veo", ledger=led) == 3.2              # only run_2's clip
    assert vs.month_spend_usd(led) == 6.4                          # the month still sees both


def test_legacy_rows_without_a_run_id_never_count_toward_a_run(monkeypatch, tmp_path):
    """Rows written before C1 carry no run_id. They must not be attributed to the current run —
    otherwise every old clip would poison the first new reel's budget."""
    led = tmp_path / "v.jsonl"
    led.write_text('{"month":"%s","kind":"veo","est_cost_usd":40.0}\n' % vs.month_key(),
                   encoding="utf-8")
    monkeypatch.setenv("TELEMETRY_RUN_ID", "run_new")
    assert vs.run_spend_usd("veo", ledger=led) == 0.0


def test_a_budget_refusal_reaches_HITL_and_is_NOT_reported_as_a_provider_failure(monkeypatch, tmp_path, capsys):
    """C1 (owner ruling) — "REFUSE the next clip, don't silently truncate — log it, surface it at
    HITL as 'this reel exceeds the per-run budget; approve an override or shorten it.'"

    The compositor caught SpendRefused in its generic per-clip `except Exception` and logged
    "provider 'veo' failed (SpendRefused: ...)" — which is FALSE. The provider did not fail; the
    RUN ran out of budget. That is a decision needing a human, not an infra blip, and conflating
    the two hides it. The reel still completes on the fallback (a budget stop must not kill the
    deliverable) but the result now SAYS so.
    """
    from reel.compositor import render_reel
    from reel.schemas import ReelScene, Storyboard

    monkeypatch.setenv("VEO_MAX_USD_PER_RUN", "0.01")      # any clip busts the run budget
    monkeypatch.setenv("TELEMETRY_RUN_ID", "run_budget")
    monkeypatch.setattr(vs, "DEFAULT_LEDGER", tmp_path / "v.jsonl")

    class _BrokeProvider:
        name = "veo"

        def generate(self, prompt, **kw):
            vs.guard_video(8, detail="clip1.mp4")          # raises SpendRefused
            raise AssertionError("unreachable")

    class _StubFallback:                                   # hermetic: no ffmpeg, no network
        name = "kenburns"

        def generate(self, prompt, **kw):
            p = tmp_path / "fb.mp4"
            p.write_bytes(b"\x00")
            return str(p)

    monkeypatch.setattr("reel.compositor.render_text_sequences",
                        lambda sb, *a, **k: [str(tmp_path / "tx_%02d.png")] * len(sb.scenes))
    monkeypatch.setattr("reel.compositor.run_ffmpeg",
                        lambda args, **k: Path(str(args[-1])).write_bytes(b"\x00"))
    sb = Storyboard(business_name="X", primary_dir="rtl", total_duration_s=6.0,
                    scenes=[ReelScene(kind="gallery", duration_s=6.0, visual_prompt="p")],
                    palette_hex=["#111111"])
    res = render_reel(sb, provider=_BrokeProvider(), out_path=tmp_path / "r.mp4",
                      fallback_provider=_StubFallback())

    err = capsys.readouterr().err
    assert "budget" in err.lower(), "a budget stop was not surfaced"
    assert "provider 'veo' failed" not in err, "a budget stop was mislabelled as a provider failure"
    assert res.budget_stopped is True                       # HITL can now offer the override
    assert res.fallback_used is True                        # the reel still shipped, degraded
