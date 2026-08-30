# -*- coding: utf-8 -*-
"""The engagement was collected, stored, and read by nothing — end to end this time.

OWNER: «أكتر حاجة جايبة لايك». The chain was broken in three places at once, and each break was
invisible from either side of it:

1. `IgSignal` carried THREE fields; `social_intel/performance.py` reads FIVE off a signal. So
   `ranked` was empty for every input ever passed, `measured` was False, `checked` was False, and
   `social_performance_for` COULD NOT return anything else no matter what was pulled. And because
   the signal had no `is_own_brand`, the brand's OWN posts were filed under `peers` and the lessons
   layer described them as "a competitor's top post".
2. `social_performance_for` had ZERO non-test callers — a function computing a result for nobody.
3. Two different pullers write `runs/social_snapshots/{slug}_ig.json` in two different shapes
   (`{"items"}` from Apify, `{"media"}` from the Graph API) and every reader understood only the
   first, so a Graph-shaped snapshot read as "this brand has no social presence".

Meanwhile FOUR brands' snapshots sit on disk with 15 posts and real like counts each, and
`media_plan/maturity.py` has been reading that exact `likesCount` key successfully the whole time.

MEASURED AFTER, on those real files: nanocare 15 ranked / median 4 / top 378 (94.5x), buffaloburger
15 / median 91 / top 607, coca-cola 15 / median 314 / top 113409, alfouad 15 / median 3 / top 12.

The existing tests all build their input from a `SimpleNamespace` that hand-supplies the three
fields production never produced. They were not wrong; they tested one side of a seam nothing
crossed. Every test here crosses it.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.pull_instagram_apify import IgSignal, signals_from_apify_items
from social_intel.performance import creative_lessons, top_performers
from social_intel.snapshot import load_ig_snapshot

# A trimmed copy of a REAL Apify row. Checked in so a change in their row shape is caught here
# rather than silently returning `checked: False` forever.
APIFY_ROW = {
    "url": "https://www.instagram.com/p/ABC123/",
    "shortCode": "ABC123",
    "caption": "إيه سر بياض المشاهير المفاجئ؟",
    "likesCount": 378,
    "commentsCount": 11,
    "timestamp": "2026-07-02T10:15:00.000Z",
    "ownerUsername": "nanocareegypt",
    "latestComments": [{"text": "المنتج ده تحفة"}, {"text": "بكام؟"}],
}


# =====================================================================
# the seam: an Apify row becomes a rankable signal
# =====================================================================

def test_a_real_row_produces_a_measurable_report():
    """The single assertion that would have caught all of defect 1."""
    rep = top_performers(signals_from_apify_items([APIFY_ROW]))
    assert rep.checked is True, rep.reason
    assert rep.own is not None, "the brand's own posts were not recognised as its own"
    assert rep.own.ranked == 1
    assert rep.own.top and rep.own.top[0].likes == 378


def test_the_like_count_survives_ingestion():
    sigs = signals_from_apify_items([APIFY_ROW])
    posts = [s for s in sigs if s.signal_kind == "post"]
    assert posts and posts[0].like_count == 378
    assert posts[0].posted_at
    assert posts[0].brand_ref == "nanocareegypt"


def test_a_comment_inherits_no_engagement():
    """A comment's parent post's likes measure the POST. Attaching them here would rank a
    commenter's sentence as if it were the brand's content."""
    sigs = signals_from_apify_items([APIFY_ROW])
    comments = [s for s in sigs if s.signal_kind == "comment"]
    assert comments, "comments were not ingested at all"
    assert all(c.like_count is None for c in comments)


def test_own_posts_are_not_filed_as_a_competitors():
    """`creative_lessons` called the brand's own best post "a competitor's top post" — because
    `is_own_brand` did not exist on the signal, so `getattr(..., False)` answered for it."""
    rep = top_performers(signals_from_apify_items([APIFY_ROW], is_own_brand=True))
    assert rep.own is not None and not rep.peers
    assert not any("competitor" in l for l in creative_lessons(rep))


def test_a_peer_pull_is_labelled_as_a_peer():
    rep = top_performers(signals_from_apify_items([APIFY_ROW], is_own_brand=False,
                                                  brand_ref="rival"))
    assert rep.own is None
    assert [p.brand_ref for p in rep.peers] == ["rival"]


# =====================================================================
# unmeasured is not zero
# =====================================================================

@pytest.mark.parametrize("raw", [None, -1, "", "12", {}, [], True])
def test_a_hidden_or_absent_like_count_is_none_not_zero(raw):
    """`int(x or 0)` is the defect, not the fix. Apify emits -1 for a hidden count and omits the
    key on a limited response; coercing either to 0 turns "we could not measure" into "this content
    gets zero likes" — the same absence-as-a-value shape the module exists to correct."""
    row = dict(APIFY_ROW)
    if raw is None:
        row.pop("likesCount")
    else:
        row["likesCount"] = raw
    posts = [s for s in signals_from_apify_items([row]) if s.signal_kind == "post"]
    assert posts and posts[0].like_count is None


def test_a_genuine_zero_is_kept():
    """Zero likes IS a measurement. Only the unmeasurable becomes None."""
    row = dict(APIFY_ROW, likesCount=0)
    posts = [s for s in signals_from_apify_items([row]) if s.signal_kind == "post"]
    assert posts[0].like_count == 0


def test_an_unmeasurable_snapshot_still_says_why():
    row = dict(APIFY_ROW)
    row.pop("likesCount")
    rep = top_performers(signals_from_apify_items([row]))
    assert rep.checked is False
    assert "never measured" in rep.reason


# =====================================================================
# both snapshot shapes
# =====================================================================

def test_the_apify_shape_loads(tmp_path):
    p = tmp_path / "a.json"
    p.write_text(json.dumps({"username": "x", "items": [APIFY_ROW]}), encoding="utf-8")
    rows, meta = load_ig_snapshot(p)
    assert len(rows) == 1 and rows[0]["likesCount"] == 378
    assert meta["username"] == "x"


def test_the_graph_shape_loads_too(tmp_path):
    """Two pullers write this filename in two shapes; every reader spoke only one of them, so a
    Graph-shaped file read as "this brand has no social presence"."""
    p = tmp_path / "b.json"
    p.write_text(json.dumps({"username": "y", "media": [
        {"permalink": "u", "caption": "hi", "like_count": 7, "comments_count": 2,
         "timestamp": "2026-07-01T00:00:00Z"}]}), encoding="utf-8")
    rows, meta = load_ig_snapshot(p)
    assert len(rows) == 1
    assert rows[0]["likesCount"] == 7 and rows[0]["caption"] == "hi"
    rep = top_performers(signals_from_apify_items(rows))
    assert rep.checked is True


def test_a_missing_or_broken_snapshot_is_empty_not_an_error(tmp_path):
    assert load_ig_snapshot(tmp_path / "nope.json") == ([], {})
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert load_ig_snapshot(bad) == ([], {})


def test_the_writers_were_not_touched():
    """Fixed on READ deliberately: renaming the files or normalising the writers would orphan the
    snapshots already collected, which are the only real engagement data this system has."""
    src = Path("social_intel/ig_autopull.py").read_text(encoding="utf-8")
    assert '"items"' in src, "the Apify writer's shape changed"


# =====================================================================
# it reaches a route, and the three states stay distinct
# =====================================================================

def test_the_route_exists_and_is_free():
    """It reads a file and does arithmetic. Gating a free read behind the paid-job machinery would
    make it look expensive and wait on a worker thread for nothing."""
    import inspect

    import api.routes.analysis as a
    src = inspect.getsource(a)
    assert '@router.get("/social/performance/{domain_key}")' in src
    at = src.find('@router.get("/social/performance')
    body = src[at:at + 900]
    assert "require_user" in body
    assert "require_spend_budget" not in body
    assert "_start_job" not in body


def test_the_route_is_registered():
    from api.main import app
    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/api/social/performance/{domain_key}" in paths


def test_the_three_states_are_distinguishable_in_the_response():
    import inspect

    import api.routes.analysis as a
    src = inspect.getsource(a.social_performance)
    assert '"collected": False' in src, "a brand with no snapshot is indistinguishable from a failure"


def test_the_analysis_reads_both_shapes_and_labels_ownership():
    import inspect

    import api.webapp_analysis as wa
    src = inspect.getsource(wa.social_performance_for)
    assert "load_ig_snapshot" in src
    assert "is_own_brand=True" in src


# =====================================================================
# the screen
# =====================================================================

def test_the_panel_exists_and_is_loaded():
    html = Path("frontend/index.html").read_text(encoding="utf-8")
    assert 'id="social-perf-panel"' in html
    assert "loadSocialPerformance()" in html
    assert "/api/social/performance/" in html


def test_the_panel_renders_the_three_states_differently():
    """Collapsing "never collected" and "collected but unmeasurable" into one empty panel would
    recreate the backend's own defect in the presentation layer."""
    html = Path("frontend/index.html").read_text(encoding="utf-8")
    at = html.find("async function loadSocialPerformance")
    body = html[at:at + 2600]
    assert "No social snapshot collected" in body
    assert "d.reason" in body
    assert "d.checked" in body


# =====================================================================
# the competitors that were discovered and dropped
# =====================================================================

def test_the_discovered_peers_are_no_longer_thrown_away():
    import inspect

    import api.webapp_analysis as wa
    src = inspect.getsource(wa.generate_swot)
    assert "discovered_competitors" in src


def test_the_owners_own_competitor_list_is_not_overwritten():
    """`competitors` is the registry she curates by hand; a generated list must not silently
    replace a chosen one."""
    import inspect

    import api.webapp_analysis as wa
    src = inspect.getsource(wa.generate_swot)
    assert '"competitors"' not in src


# =====================================================================
# the real files on disk
# =====================================================================

@pytest.mark.parametrize("slug", ["nanocareegypt_com", "alfouadpharmacies_com",
                                  "buffaloburger_com", "coca_cola_com"])
def test_the_snapshots_already_on_disk_are_measurable(slug):
    p = Path("runs/social_snapshots") / f"{slug}_ig.json"
    if not p.is_file():
        pytest.skip(f"{slug} snapshot not present")
    rows, meta = load_ig_snapshot(p)
    rep = top_performers(signals_from_apify_items(
        rows, is_own_brand=True, brand_ref=str(meta.get("username") or "")))
    assert rep.checked is True, rep.reason
    assert rep.own and rep.own.ranked >= 1
    assert rep.own.top[0].likes >= 1


def test_the_dataclass_did_not_lose_its_defaults():
    """Added with defaults so every existing constructor keeps working — a frozen dataclass with a
    new required field would break callers silently at import time."""
    s = IgSignal(source_url="u", text="t")
    assert s.like_count is None and s.is_own_brand is True and s.brand_ref == ""


# =====================================================================
# the path must not depend on where the server was started
# =====================================================================

def test_the_snapshot_directory_is_anchored_to_the_repo(tmp_path, monkeypatch):
    """MEASURED 2026-08-01, and it is why the panel said "no snapshot collected" over a file that
    was sitting right there: `Path("runs/social_snapshots")` resolves against the CURRENT WORKING
    DIRECTORY. Called from the repo the analysis returned `checked: True` with 15 ranked posts;
    called through the running API — started from a different folder — it returned {}.

    Same bug class `competitor/registry.py` already fixed for the registry, with the same override
    door. `tests/test_webapp_paths_are_anchored.py` exists because this repo has paid for it before.
    """
    import os

    from social_intel.snapshot import default_snapshot_dir, snapshot_path_for
    here = Path(__file__).resolve().parents[1]
    monkeypatch.chdir(tmp_path)                     # stand somewhere else entirely
    assert os.getcwd() != str(here)
    assert default_snapshot_dir() == here / "runs" / "social_snapshots"
    p = snapshot_path_for({"source_url": "https://alfouadpharmacies.com/"})
    assert p is not None and p.is_absolute()
    assert str(here) in str(p)


def test_the_snapshot_directory_can_be_overridden(tmp_path, monkeypatch):
    from social_intel.snapshot import default_snapshot_dir
    monkeypatch.setenv("BASEERA_SNAPSHOT_DIR", str(tmp_path))
    assert default_snapshot_dir() == tmp_path


def test_the_analysis_reads_the_anchored_directory():
    """Both readers in `webapp_analysis` — the themes one and the performance one."""
    import inspect

    import api.webapp_analysis as wa
    src = inspect.getsource(wa)
    assert 'Path("runs/social_snapshots")' not in src, "a cwd-relative snapshot path is back"
    assert "default_snapshot_dir" in src or "snapshot_path_for" in src


def test_a_failure_to_read_is_not_reported_as_never_collected():
    """The swallow that hid all of the above. An exception and an absent file are different
    facts, and the caller renders {} as "we never collected this brand's social"."""
    import ast
    import inspect

    import api.webapp_analysis as wa
    # CODE ONLY — the comment above this handler quotes the very string it forbids, because
    # explaining the defect means naming it. Read the statements, not the prose.
    import textwrap
    tree = ast.parse(textwrap.dedent(inspect.getsource(wa.social_performance_for)))
    fn = tree.body[0]
    handler = next(h for n in ast.walk(fn) if isinstance(n, ast.Try) for h in n.handlers)
    code = ast.unparse(ast.Module(body=handler.body, type_ignores=[]))
    assert "return {}" not in code, "an error still reads as 'never collected'"
    assert "could not read the snapshot" in code
