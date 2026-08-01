# -*- coding: utf-8 -*-
"""The engagement number the system collected and never once read.

OWNER (2026-08-01): «عايزه اسكراب السوشيال ميديا، اكتر حاجه جايبه لايك كدا فاهمني».

MEASURED BEFORE THE MODULE EXISTED. `like_count` is declared in `social_intel/schemas.py` and
populated by every provider — apify_facebook, ig_business, snapshot_parser, fixture — and a grep
for any sort on it across the repo returned NOTHING. The engagement was collected, privacy-hashed,
validated by a strict schema, and thrown away. The nearest thing that existed
(`competitor/social_gaps.py`) compares which PLATFORMS each side is on: presence, not performance.

The load-bearing design decision is that RAW LIKES ARE NOT COMPARABLE ACROSS BRANDS. A pharmacy
with 400k followers out-likes a clinic with 4k on every post it will ever publish, and reporting
that as "their content is better" would be a true number answering a different question — the
exact failure class this repo keeps paying for.
"""
from __future__ import annotations

import types


def _sig(kind="post", likes=10, own=True, ref="own.test", text="hello"):
    return types.SimpleNamespace(signal_kind=kind, like_count=likes, is_own_brand=own,
                                 brand_ref=ref, text=text, source_url="https://x/1",
                                 posted_at=None, lang="ar")


# =====================================================================
# it reads the number at all
# =====================================================================

def test_the_top_post_is_the_most_liked_one():
    from social_intel.performance import top_performers
    r = top_performers([_sig(likes=3, text="quiet"), _sig(likes=91, text="the winner"),
                        _sig(likes=12, text="middling")])
    assert r.checked is True
    assert r.own.top[0].likes == 91
    assert "winner" in r.own.top[0].text


def test_comments_do_not_count_as_the_brands_content():
    """A comment's likes measure the COMMENTER. Counting them would credit the brand for a
    stranger's popular reply."""
    from social_intel.performance import top_performers
    r = top_performers([_sig(kind="comment", likes=9000, text="a viral reply"),
                        _sig(kind="post", likes=40, text="our actual post")])
    assert r.own.top[0].likes == 40
    assert r.published_signals == 1


def test_only_comments_is_reported_as_unmeasurable_not_as_nothing_works():
    from social_intel.performance import top_performers
    r = top_performers([_sig(kind="comment", likes=500), _sig(kind="reply", likes=200)])
    assert r.checked is False
    assert "comment" in r.reason


# =====================================================================
# a missing like count is not a zero
# =====================================================================

def test_a_post_with_no_like_count_is_unranked_not_worst():
    """The third state. Treating None as 0 would rank an unmeasured post below a post with one
    like, and quietly poison every median in the report."""
    from social_intel.performance import top_performers
    r = top_performers([_sig(likes=None, text="unmeasured"), _sig(likes=5, text="measured"),
                        _sig(likes=9, text="best")])
    assert r.own.ranked == 2
    assert r.own.unranked == 1
    assert all("unmeasured" not in p.text for p in r.own.top)
    assert r.own.median_likes == 7.0          # median of 5 and 9 — the None is absent, not zero


def test_all_posts_unmeasured_says_so():
    from social_intel.performance import top_performers
    r = top_performers([_sig(likes=None), _sig(likes=None)])
    assert r.checked is False
    assert "never measured" in r.reason
    assert "does not work" in r.reason        # names the wrong conclusion it is preventing


def test_a_single_like_is_not_a_top_post():
    from social_intel.performance import MIN_LIKES_TO_RANK, top_performers
    assert MIN_LIKES_TO_RANK >= 2
    r = top_performers([_sig(likes=1), _sig(likes=1)])
    assert r.own.top == []
    assert r.own.ranked == 2, "the posts were still counted, just not called winners"


# =====================================================================
# brands are compared by LIFT, never by raw likes
# =====================================================================

def test_a_bigger_audience_does_not_win_by_default():
    """The whole point. A brand with 100x the following out-likes everyone on every post, and
    that says nothing about whose content works."""
    from social_intel.performance import top_performers
    big = [_sig(own=False, ref="giant", likes=n) for n in (9000, 9500, 9200, 9100, 40000)]
    small = [_sig(own=True, ref="us", likes=n) for n in (10, 11, 9, 10, 300)]
    r = top_performers(big + small)
    assert r.own.lift == 30.0, r.own.lift        # 300 / median 10
    giant = next(p for p in r.peers if p.brand_ref == "giant")
    assert giant.lift is not None and giant.lift < r.own.lift, (
        "the larger audience was credited with better content")


def test_lift_is_withheld_when_there_is_too_little_to_say():
    from social_intel.performance import MIN_POSTS_FOR_LIFT, top_performers
    r = top_performers([_sig(likes=5), _sig(likes=500)])
    assert r.own.ranked < MIN_POSTS_FOR_LIFT
    assert r.own.lift is None, "a 100x multiple was quoted off two posts"


def test_peers_are_ranked_by_lift_not_by_size():
    from social_intel.performance import top_performers
    flat = [_sig(own=False, ref="flat", likes=n) for n in (500, 510, 505, 495, 520)]
    spiky = [_sig(own=False, ref="spiky", likes=n) for n in (10, 12, 11, 10, 400)]
    r = top_performers(flat + spiky)
    assert r.peers[0].brand_ref == "spiky", [(p.brand_ref, p.lift) for p in r.peers]


# =====================================================================
# own vs competitor stays straight
# =====================================================================

def test_own_and_peer_are_kept_apart():
    from social_intel.performance import top_performers
    r = top_performers([_sig(own=True, ref="us", likes=50),
                        _sig(own=False, ref="them", likes=80)])
    assert r.own.brand_ref == "us" and r.own.top[0].likes == 50
    assert [p.brand_ref for p in r.peers] == ["them"]


def test_two_peers_are_reported_separately():
    from social_intel.performance import top_performers
    r = top_performers([_sig(own=False, ref="a", likes=30), _sig(own=False, ref="b", likes=60)])
    assert {p.brand_ref for p in r.peers} == {"a", "b"}


# =====================================================================
# the payoff: the brief can act on it
# =====================================================================

def test_the_winners_become_lines_a_brief_can_use():
    """Ranking posts for a dashboard tells a human something; feeding the winners into the brief
    is what makes the NEXT reel learn from what already worked."""
    from social_intel.performance import creative_lessons, top_performers
    own = [_sig(own=True, ref="us", likes=n, text=f"post {n}") for n in (10, 11, 9, 10)]
    own.append(_sig(own=True, ref="us", likes=300, text="before and after, 30 days"))
    peer = [_sig(own=False, ref="rival", likes=n, text=f"r{n}") for n in (5, 6, 5, 5)]
    peer.append(_sig(own=False, ref="rival", likes=90, text="a pharmacist answers your question"))
    lessons = creative_lessons(top_performers(own + peer))
    assert lessons
    assert any("before and after" in l for l in lessons)
    assert any("pharmacist" in l for l in lessons)
    assert any("x" in l for l in lessons), "no lift multiple reached the brief"


def test_no_measurement_yields_no_lessons_rather_than_invented_ones():
    from social_intel.performance import creative_lessons, top_performers
    assert creative_lessons(top_performers([])) == []
    assert creative_lessons(top_performers([_sig(likes=None)])) == []


def test_nothing_here_calls_a_model():
    """Ranking is arithmetic. A judgement call would make the same input give different answers."""
    import inspect

    import social_intel.performance as p
    src = inspect.getsource(p)
    for forbidden in ("caller(", "generate_content", "gemini", "openai"):
        assert forbidden not in src.lower(), forbidden


# =====================================================================
# wired, one function away from where the signals already were
# =====================================================================

def test_the_analysis_reads_the_engagement_it_already_loads():
    """`_social_themes_for` loads these very signals and passes them to theme extraction ALONE —
    the engagement rode all the way into the analysis and was dropped at the last step."""
    import inspect

    import api.webapp_analysis as wa
    assert hasattr(wa, "social_performance_for")
    src = inspect.getsource(wa.social_performance_for)
    assert "top_performers" in src and "creative_lessons" in src


def test_a_brand_with_no_snapshot_gets_an_empty_result_not_a_crash(tmp_path, monkeypatch):
    import api.webapp_analysis as wa
    monkeypatch.chdir(tmp_path)
    assert wa.social_performance_for({"source_url": "https://nothing.test"}) == {}


def test_an_unmeasurable_snapshot_says_why(tmp_path, monkeypatch):
    """An empty dict would read as "nothing works here"; the reason distinguishes it from
    "engagement was never measured"."""
    import json

    import api.webapp_analysis as wa
    from api.brand_translate import domain_key_from_url
    monkeypatch.chdir(tmp_path)
    slug = domain_key_from_url("https://brand.test")
    d = tmp_path / "runs" / "social_snapshots"
    d.mkdir(parents=True)
    (d / f"{slug}_ig.json").write_text(json.dumps({"items": []}), encoding="utf-8")
    out = wa.social_performance_for({"source_url": "https://brand.test"})
    assert out == {} or out.get("checked") is False


def test_the_analysis_path_never_bills():
    """It reads a snapshot off disk and does arithmetic. A brand with no data must not trigger a
    paid pull just because someone opened its dashboard."""
    import inspect

    import api.webapp_analysis as wa
    src = inspect.getsource(wa.social_performance_for)
    for billing in ("apify_client", "ApifyClient", "requests.post", "httpx.post", "caller("):
        assert billing not in src, billing
