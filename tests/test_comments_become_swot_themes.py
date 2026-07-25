# -*- coding: utf-8 -*-
"""Customer comments are the SWOT's missing voice — and the wiring is a bridge, not a new brain.

THE OWNER'S POINT: "كان كومنت اكتر كان ادق واحسن" — the more comments, the sharper the analysis.
She is right, and the measurement says why: Places reviews reach 29% of runs while social reaches
82%, and a review theme is the ONLY generator that produces a SWOT item from a real customer
sentence rather than from a site mechanic.

MEASURED: the comments are already collected, already analysed, and already stored. From
`outputs/buffaloburger_com_result.json`, produced from a saved Instagram snapshot:

    pain point : "The staff at The Isle branch provide very bad service"
    evidence   : «الفرع اللي في the isle تعاملهم وحش اووي وتعاملهم صعب»
    source_url : https://www.instagram.com/p/DaDdrEUqobZ/

That is a textbook SWOT weakness — a real complaint, quoted verbatim, with the post it came from.
`grep marketing_intel competitor/swot.py` returns ZERO. The system gathers its sharpest evidence
and drops it on the floor.

WHY THIS IS A CONVERTER AND NOT A GENERATOR: `ReviewTheme` already carries `polarity` and
`is_own_brand`, and `synthesize_swot` already routes them exactly as the owner described — own
complaint to Weaknesses, own praise to Strengths, PEER complaint to Opportunities ("a gap you can
fill"), peer praise to Threats. `SocialSignal` even carries the same `is_own_brand` flag. So a
comment becomes a theme, and the routing that already works does the rest.

Hermetic — signals in, themes out, no network and no model."""
from __future__ import annotations

from datetime import datetime, timezone

from competitor.social_themes import themes_from_signals


def _sig(text, *, own=True, kind="comment", url="https://www.instagram.com/p/ABC/", likes=0):
    from social_intel.schemas import SocialSignal
    return SocialSignal(source="snapshot", source_url=url, signal_kind=kind,
                        author_hash="hash1234", text=text, brand_ref="b",
                        is_own_brand=own, like_count=likes,
                        scraped_at=datetime.now(timezone.utc))


# --- the owner's mapping, which the SWOT already implements -------------------------------------

def test_a_complaint_about_the_brand_becomes_an_own_brand_complaint_theme():
    themes = themes_from_signals([_sig("الفرع اللي في the isle تعاملهم وحش اووي")])
    assert themes and themes[0].polarity == "complaint"
    assert themes[0].is_own_brand is True


def test_praise_for_the_brand_becomes_an_own_brand_praise_theme():
    themes = themes_from_signals([_sig("والله أحسن برجر أكلته، تسلم إيديكم")])
    assert themes and themes[0].polarity == "praise"
    assert themes[0].is_own_brand is True


def test_a_complaint_about_a_COMPETITOR_is_flagged_as_an_unmet_need():
    """The owner's ruling: a rival's complaints are a gap YOU can fill — an Opportunity."""
    themes = themes_from_signals([_sig("طلبت منهم واتأخر ساعتين", own=False)])
    assert themes and themes[0].polarity == "complaint"
    assert themes[0].is_own_brand is False
    assert themes[0].is_unmet_need is True, "a peer complaint must route to Opportunities"


def test_praise_for_a_COMPETITOR_stays_a_plain_peer_complaint_free_theme():
    themes = themes_from_signals([_sig("أسرع توصيل في المنطقة، ممتازين", own=False)])
    assert themes and themes[0].polarity == "praise"
    assert themes[0].is_own_brand is False
    assert themes[0].is_unmet_need is False


# --- every theme stays traceable ----------------------------------------------------------------

def test_the_post_url_travels_with_the_theme():
    """The whole value is that a client can open the comment and read it."""
    url = "https://www.instagram.com/p/DaDdrEUqobZ/"
    themes = themes_from_signals([_sig("تعاملهم وحش اووي", url=url)])
    assert any(url in s for s in themes[0].support)


def test_the_customers_own_words_are_preserved_not_paraphrased():
    themes = themes_from_signals([_sig("تعاملهم وحش اووي وتعاملهم صعب")])
    assert "وحش" in themes[0].text


def test_no_author_name_can_leak_into_a_theme():
    """`SocialSignal` stores `author_hash`, never a name — nothing here may reintroduce one."""
    themes = themes_from_signals([_sig("تعاملهم وحش")])
    assert "hash1234" not in themes[0].text
    assert all("hash1234" not in s for s in themes[0].support)


# --- what must NOT become a theme ----------------------------------------------------------------

def test_the_brands_own_marketing_posts_are_not_customer_voice():
    """A caption the brand wrote about itself is an opinion it holds, not evidence from a customer.
    Treating it as one is exactly the defect the SWOT rebuild removed from `value_propositions`."""
    assert themes_from_signals([_sig("More patties. More flavor.", kind="caption")]) == []
    assert themes_from_signals([_sig("عرض جديد النهاردة", kind="post")]) == []


def test_an_empty_or_emoji_only_comment_is_not_a_signal():
    assert themes_from_signals([_sig("🔥🔥🔥"), _sig("   "), _sig("❤️")]) == []


def test_a_neutral_question_is_not_praise_or_a_complaint():
    themes = themes_from_signals([_sig("بتفتحوا الساعة كام؟")])
    assert themes == []


def test_no_signals_yields_no_themes_rather_than_an_invented_one():
    assert themes_from_signals([]) == []
    assert themes_from_signals(None) == []


# --- volume is the point ---------------------------------------------------------------------

def test_repeated_complaints_are_merged_and_their_weight_recorded():
    """"كان كومنت اكتر كان ادق" — three people saying the same thing is stronger than one, and
    the theme must carry that rather than appearing three times."""
    sigs = [_sig("التوصيل بطيء جدا", url=f"https://www.instagram.com/p/P{i}/") for i in range(3)]
    themes = themes_from_signals(sigs)
    slow = [t for t in themes if "بطيء" in t.text]
    assert len(slow) == 1, f"the same complaint appeared {len(slow)} times"
    assert len(slow[0].support) >= 3, "the number of people who said it was lost"


def test_praising_a_rival_is_a_complaint_about_you():
    """MEASURED on Coca-Cola's real comments: «محدش بيبدل حاجة عروض وهميه بيبسى افضل منكم»
    ("...Pepsi is BETTER THAN YOU") was filed as PRAISE, because it contains «افضل». The word is
    genuinely a praise word; the sentence is not about this brand. A praise word aimed at somebody
    else is a complaint about you."""
    themes = themes_from_signals([_sig("عروض وهميه بيبسى افضل منكم")])
    assert themes and themes[0].polarity == "complaint", themes[0].polarity


def test_the_english_form_is_caught_too():
    themes = themes_from_signals([_sig("their delivery is better than yours honestly")])
    assert themes and themes[0].polarity == "complaint"


def test_plain_praise_is_still_praise():
    themes = themes_from_signals([_sig("الدواء رائع فرق مع بشرتي جدا بصراحة")])
    assert themes and themes[0].polarity == "praise"


def test_the_caller_can_declare_whose_account_the_snapshot_came_from():
    """MEASURED: `scripts.pull_instagram_apify` emits signals with NO `is_own_brand` field, so
    comments pulled from the brand's OWN Instagram defaulted to peer voice — a customer thanking
    this brand would have been filed as a rival's advantage."""
    class _Bare:
        signal_kind = "comment"
        text = "الدواء رائع فرق مع بشرتي جدا"
        source_url = "https://www.instagram.com/p/X/"

    assert themes_from_signals([_Bare()])[0].is_own_brand is False          # what the row says
    assert themes_from_signals([_Bare()], is_own_brand=True)[0].is_own_brand is True
