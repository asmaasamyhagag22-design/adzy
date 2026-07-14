"""U9 Tier-3 snapshot provider — parses HUMAN-SAVED HTML (PD-10: no login/automation), hashes authors
(PD-6), offline. Hermetic."""
from __future__ import annotations

from social_intel.providers.snapshot_parser import SnapshotSocialProvider
from social_intel.schemas import SocialSignal, hash_author

_HTML = """<html><body>
  <div class="post" data-author="Brand X" data-kind="post" data-lang="ar" data-likes="120" data-own="true">
    عرض خاص هذا الأسبوع</div>
  <li class="comment" data-author="Mona" data-kind="comment" data-lang="ar" data-likes="8">بكام الجلسة؟</li>
  <li class="comment" data-author="Ali" data-lang="en">great service</li>
  <footer>ignore me — no data-author</footer>
</body></html>"""


def test_parses_saved_html_into_signals(tmp_path):
    (tmp_path / "brandx.html").write_text(_HTML, encoding="utf-8")
    sigs = SnapshotSocialProvider(str(tmp_path)).fetch("brandx")
    assert len(sigs) == 3                                   # footer (no data-author) ignored
    assert all(isinstance(s, SocialSignal) and s.source == "snapshot" for s in sigs)
    post, c1, c2 = sigs
    assert post.signal_kind == "post" and post.is_own_brand is True and post.like_count == 120
    assert c1.signal_kind == "comment" and "بكام" in c1.text and c1.like_count == 8
    assert c2.signal_kind == "comment" and c2.lang == "en"   # data-kind absent -> defaults to comment


def test_author_names_are_hashed_never_raw(tmp_path):
    (tmp_path / "b.html").write_text(_HTML, encoding="utf-8")
    sigs = SnapshotSocialProvider(str(tmp_path)).fetch("b")
    assert all("Mona" not in s.author_hash and "Brand X" not in s.author_hash for s in sigs)
    assert sigs[1].author_hash == hash_author("Mona") and len(sigs[1].author_hash) >= 8


def test_missing_snapshot_returns_empty(tmp_path):
    assert SnapshotSocialProvider(str(tmp_path)).fetch("nobody") == []


def test_committed_fixture_snapshot_parses():
    # the committed demo fixture (social_intel/fixtures/snapshots/demo_snap.html) is parseable
    sigs = SnapshotSocialProvider().fetch("demo_snap")
    assert len(sigs) == 4 and {s.signal_kind for s in sigs} == {"post", "comment", "reply"}
    assert any(s.is_own_brand for s in sigs) and any("بكام" in s.text for s in sigs)
