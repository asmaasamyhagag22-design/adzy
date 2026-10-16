"""Brandbook identity is the domain_key — never the display name.

THE DEFECT THESE PIN (2026-09-01). `poster/pipeline.py` addressed the BrandCreativeDNA cache by
slugging the display name with `re.sub(r"[^a-z0-9]+", "_", name.lower()) or "brand"`. A name
written entirely in Arabic contains no `[a-z0-9]`, so the whole string erased and the `or
"brand"` fallback fired. THREE stored brands therefore shared one file:

    elabdfoods_com   "حلواني العبد…"        \\
    marasimltd_com   "مراسم للحلول الرقمية"   > outputs/brandbooks/brand_dna.json
    nahdionline_com  "صيدلية النهدي"         /

The file held Al Nahdi's DNA — a Saudi retail pharmacy — so a Marasim poster rendered under
turquoise/dusty-blue retail authority with a giant discount number and a "Shop Now" button,
while Marasim's own DNA (sepia, black field, fiery orange, Cairo/Giza, "no blues, greens or
purples") sat unread under its old English-name slug.

These tests would all have passed under the old code for LATIN names, which is why the defect
survived: every one of them turns on a name the old slug could not encode, on a name CHANGING,
or on two brands sharing a name. Identity is tested across the language axis, not just the
happy path.
"""
from __future__ import annotations

import json

import pytest

from brand.brandbook_identity import (BRANDBOOK_AUTHORITY_MISMATCH, BRANDBOOK_LEGACY_COLLISION,
                                      BRANDBOOK_IDENTITY_UNRESOLVED, BrandbookIdentityError,
                                      CANONICAL_DOMAIN_KEY, LEGACY_AMBIGUOUS_BLOCKED,
                                      canonical_path, dna_content_hash, domain_key_for,
                                      legacy_name_slug, load_brandbook, readable_label,
                                      resolve_brandbook, save_brandbook,
                                      validate_embedded_identity)
from brand.creative_dna import BrandCreativeDNA


def _profile(name: str, url: str) -> dict:
    """A profile shaped like the real one: EvidencedField `name`, plain `final_url`."""
    return {"name": {"value": name, "evidence": []}, "final_url": url, "source_url": url}


def _dna(name: str, **kw) -> BrandCreativeDNA:
    return BrandCreativeDNA(business_name=name, used_vision=True,
                            color_usage=kw.pop("color_usage", "warm sepia and fiery orange"),
                            **kw)


# ---------------------------------------------------------------------
# §12 — non-Latin names must not collide (was xfail; now the point of the file)
# ---------------------------------------------------------------------

ARABIC_BRANDS = [
    ("مراسم للحلول الرقمية", "https://a.example.com/", "a_example_com"),
    ("صيدلية النهدي", "https://b.example.com/", "b_example_com"),
    ("حلواني العبد", "https://c.example.com/", "c_example_com"),
]


def test_the_old_slug_really_did_collapse_every_arabic_name():
    """The premise, pinned: without this the other tests look like they prove nothing."""
    assert [legacy_name_slug(n) for n, _, _ in ARABIC_BRANDS] == ["brand", "brand", "brand"]


def test_three_arabic_brands_sharing_no_ascii_resolve_to_three_files(tmp_path):
    paths = set()
    for name, url, expected_key in ARABIC_BRANDS:
        prof = _profile(name, url)
        assert domain_key_for(prof) == expected_key
        p = save_brandbook(_dna(name), domain_key=domain_key_for(prof), profile=prof,
                           cache_dir=tmp_path)
        paths.add(p)
    assert len(paths) == 3, f"non-Latin names collided again: {sorted(str(p) for p in paths)}"
    assert not (tmp_path / "brand_dna.json").exists(), "the shared bucket was recreated"


@pytest.mark.parametrize("name,url,expected_key", ARABIC_BRANDS)
def test_each_arabic_brand_reads_back_its_own_dna(tmp_path, name, url, expected_key):
    for n, u, _ in ARABIC_BRANDS:
        prof = _profile(n, u)
        save_brandbook(_dna(n), domain_key=domain_key_for(prof), profile=prof, cache_dir=tmp_path)
    payload, lookup = load_brandbook(_profile(name, url), cache_dir=tmp_path)
    assert lookup.state == CANONICAL_DOMAIN_KEY and lookup.exists
    assert payload["business_name"] == name
    assert payload["domain_key"] == expected_key


def test_a_non_arabic_non_latin_name_also_gets_its_own_file(tmp_path):
    """Not an Arabic special case — any script with no ASCII alphanumerics hit this."""
    cases = [("株式会社テスト", "https://jp.example.com/"), ("Тестовый бренд", "https://ru.example.com/"),
             ("测试品牌", "https://cn.example.com/")]
    paths = {save_brandbook(_dna(n), domain_key=domain_key_for(_profile(n, u)),
                            profile=_profile(n, u), cache_dir=tmp_path) for n, u in cases}
    assert len(paths) == 3


# ---------------------------------------------------------------------
# §13 — a display-name change must not move, orphan or rebuild the DNA
# ---------------------------------------------------------------------

def test_renaming_a_brand_in_any_language_keeps_one_canonical_artifact(tmp_path):
    url = "https://marasimltd.com/"
    names = ["Marasim Digital Solution Agency",     # scraped in English
             "مراسم للحلول الرقمية",                  # re-scraped in Arabic
             "Marasim | Digital Solutions 2026"]    # marketing display name
    first = save_brandbook(_dna(names[0]), domain_key=domain_key_for(_profile(names[0], url)),
                           profile=_profile(names[0], url), cache_dir=tmp_path)
    original_hash = json.loads(first.read_text(encoding="utf-8"))["dna_hash"]

    for nm in names:
        prof = _profile(nm, url)
        lookup = resolve_brandbook(prof, cache_dir=tmp_path)
        assert lookup.domain_key == "marasimltd_com"
        assert lookup.path == first, f"{nm!r} resolved somewhere else"
        assert lookup.exists, f"{nm!r} orphaned the DNA"

    assert len(list(tmp_path.glob("*.json"))) == 1, "a rename created a second brandbook"
    # The creative content did not move, so neither did its hash: a rename is not a rebuild.
    assert json.loads(first.read_text(encoding="utf-8"))["dna_hash"] == original_hash


def test_the_dna_hash_covers_creative_content_not_the_display_name():
    a = _dna("English Name")
    b = _dna("مراسم للحلول الرقمية")
    assert dna_content_hash(a) == dna_content_hash(b)
    assert dna_content_hash(_dna("x", mood="calm")) != dna_content_hash(_dna("x", mood="loud"))


# ---------------------------------------------------------------------
# §14 — different domain_keys never share storage, whatever they are called
# ---------------------------------------------------------------------

@pytest.mark.parametrize("shared_name", [
    "Marasim", "مراسم", "", "  ", "!!!", "شركة", "Al Nahdi Pharmacy",
])
def test_two_domains_with_an_identical_display_name_stay_separate(tmp_path, shared_name):
    p1, p2 = _profile(shared_name, "https://one.example.com/"), _profile(shared_name, "https://two.example.com/")
    a = save_brandbook(_dna(shared_name, mood="first"), domain_key=domain_key_for(p1),
                       profile=p1, cache_dir=tmp_path)
    b = save_brandbook(_dna(shared_name, mood="second"), domain_key=domain_key_for(p2),
                       profile=p2, cache_dir=tmp_path)
    assert a != b
    assert json.loads(a.read_text(encoding="utf-8"))["mood"] == "first"
    assert json.loads(b.read_text(encoding="utf-8"))["mood"] == "second"


def test_a_hyphen_and_a_dot_domain_do_not_alias(tmp_path):
    """`foo-bar.com` vs `foo.bar.com` — the same injectivity the brand store already relies on."""
    p1 = _profile("X", "https://foo-bar.example/")
    p2 = _profile("X", "https://foo.bar.example/")
    assert domain_key_for(p1) != domain_key_for(p2)
    assert (save_brandbook(_dna("X"), domain_key=domain_key_for(p1), profile=p1, cache_dir=tmp_path)
            != save_brandbook(_dna("X"), domain_key=domain_key_for(p2), profile=p2, cache_dir=tmp_path))


# ---------------------------------------------------------------------
# §11 — cross-brand write protection
# ---------------------------------------------------------------------

def test_one_brand_cannot_overwrite_another_brands_brandbook(tmp_path):
    prof_a = _profile("A", "https://a.example.com/")
    save_brandbook(_dna("A"), domain_key="a_example_com", profile=prof_a, cache_dir=tmp_path)
    # Force the collision the filesystem would otherwise permit: B's DNA at A's path.
    dest = canonical_path("a_example_com", cache_dir=tmp_path)
    payload = json.loads(dest.read_text(encoding="utf-8"))
    payload["domain_key"] = "b_example_com"
    (tmp_path / "tmp_b.json").write_text(json.dumps(payload), encoding="utf-8")
    dest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(BrandbookIdentityError) as exc:
        save_brandbook(_dna("A"), domain_key="a_example_com", profile=prof_a, cache_dir=tmp_path)
    assert exc.value.code == BRANDBOOK_AUTHORITY_MISMATCH


def test_saving_without_a_domain_key_is_refused(tmp_path):
    with pytest.raises(BrandbookIdentityError) as exc:
        save_brandbook(_dna("nameless"), domain_key="", profile={}, cache_dir=tmp_path)
    assert exc.value.code == BRANDBOOK_IDENTITY_UNRESOLVED


# ---------------------------------------------------------------------
# §10 — embedded identity validation
# ---------------------------------------------------------------------

def test_a_file_that_claims_another_brand_is_refused_not_returned(tmp_path):
    """Guards a plain filesystem copy/rename mistake, not just the slug bug."""
    prof = _profile("A", "https://a.example.com/")
    save_brandbook(_dna("A"), domain_key="a_example_com", profile=prof, cache_dir=tmp_path)
    src = canonical_path("a_example_com", cache_dir=tmp_path)
    canonical_path("b_example_com", cache_dir=tmp_path).write_text(
        src.read_text(encoding="utf-8"), encoding="utf-8")      # a careless `cp`

    payload, lookup = load_brandbook(_profile("B", "https://b.example.com/"), cache_dir=tmp_path)
    assert payload is None
    assert lookup.code == BRANDBOOK_AUTHORITY_MISMATCH
    assert lookup.state == LEGACY_AMBIGUOUS_BLOCKED


def test_a_source_url_from_another_domain_is_refused():
    verdict = validate_embedded_identity(
        {"domain_key": "a_example_com", "source_url": "https://b.example.com/"}, "a_example_com")
    assert not verdict.ok and verdict.code == BRANDBOOK_AUTHORITY_MISMATCH


def test_a_legacy_file_with_no_embedded_key_never_satisfies_a_lookup():
    verdict = validate_embedded_identity({"business_name": "Anything"}, "a_example_com")
    assert not verdict.ok and verdict.code == BRANDBOOK_IDENTITY_UNRESOLVED


# ---------------------------------------------------------------------
# §8 / §9 — identity failure is not a cache miss
# ---------------------------------------------------------------------

def test_a_genuinely_new_brand_is_an_honest_miss_and_may_build(tmp_path):
    lookup = resolve_brandbook(_profile("New Co", "https://new.example.com/"), cache_dir=tmp_path)
    assert not lookup.exists and not lookup.code            # no error: building is legitimate
    assert lookup.state == CANONICAL_DOMAIN_KEY


def test_prior_dna_under_the_shared_slug_is_a_typed_collision_not_a_miss(tmp_path):
    """The exact Marasim situation: DNA evidence exists, canonical identity does not."""
    (tmp_path / "brand_dna.json").write_text(
        json.dumps({"business_name": "صيدلية النهدي", "layout_philosophy": "x"}), encoding="utf-8")
    lookup = resolve_brandbook(_profile("مراسم للحلول الرقمية", "https://marasimltd.com/"),
                               cache_dir=tmp_path)
    assert not lookup.exists
    assert lookup.code == BRANDBOOK_LEGACY_COLLISION
    assert lookup.state == LEGACY_AMBIGUOUS_BLOCKED
    assert any("brand_dna.json" in f for f in lookup.legacy_files)


def test_prior_dna_under_a_name_slug_blocks_a_silent_rebuild(tmp_path):
    (tmp_path / "acme_widgets_dna.json").write_text(
        json.dumps({"business_name": "Acme Widgets", "layout_philosophy": "x"}), encoding="utf-8")
    lookup = resolve_brandbook(_profile("Acme Widgets", "https://acme.example.com/"),
                               cache_dir=tmp_path)
    assert not lookup.exists and lookup.code == BRANDBOOK_IDENTITY_UNRESOLVED


def test_a_profile_with_no_url_at_all_is_unresolved_rather_than_bucketed(tmp_path):
    lookup = resolve_brandbook({"name": {"value": "مراسم"}}, cache_dir=tmp_path)
    assert lookup.code == BRANDBOOK_IDENTITY_UNRESOLVED
    assert lookup.domain_key == ""


def test_the_brand_sentinel_is_never_accepted_as_an_identity():
    """`domain_key_from_url("")` answers the literal "brand" — the same shared bucket the slug
    produced. It must be refused here rather than quietly become a second collision point.
    (An odd-but-real host is fine: it gets a digest suffix, so it stays injective.)"""
    assert domain_key_for({"final_url": ""}) == ""
    assert domain_key_for({"final_url": "", "source_url": ""}) == ""
    assert domain_key_for({"final_url": "not a url"}) == "not_a_url_d7aad9a0"
    assert domain_key_for({"final_url": "not a url"}) != "brand"


def test_a_url_bearing_field_wins_over_an_empty_one():
    """An empty `final_url` must not shadow a usable `source_url`."""
    assert domain_key_for({"final_url": "", "source_url": "https://marasimltd.com/"}) == "marasimltd_com"


# ---------------------------------------------------------------------
# §23 — transliteration is a label, never authority
# ---------------------------------------------------------------------

def test_readable_label_keeps_non_ascii_and_is_not_an_identity():
    assert readable_label({"name": "مراسم للحلول الرقمية"}) == "مراسم_للحلول_الرقمية"
    # Two brands can share a label; they can never share a domain_key.
    assert readable_label({"name": "Marasim"}) == readable_label({"name": "Marasim"})


# ---------------------------------------------------------------------
# The pipeline seam — the function that actually did the damage
# ---------------------------------------------------------------------

def test_load_or_build_dna_reads_by_domain_key_not_display_name(tmp_path):
    from poster.pipeline import load_or_build_dna
    prof = _profile("مراسم للحلول الرقمية", "https://marasimltd.com/")
    save_brandbook(_dna("Marasim Digital Solution Agency", mood="warm, Egyptian, cinematic"),
                   domain_key="marasimltd_com", profile=prof, cache_dir=tmp_path)
    dna = load_or_build_dna(prof, None, cache_dir=str(tmp_path))
    assert dna is not None and dna.mood == "warm, Egyptian, cinematic"
    assert dna.domain_key == "marasimltd_com"


def test_load_or_build_dna_refuses_a_foreign_brandbook_instead_of_rendering_it(tmp_path):
    """The regression proper: Marasim asking, Al Nahdi's file on disk, caller available."""
    from poster.pipeline import load_or_build_dna
    (tmp_path / "brand_dna.json").write_text(
        json.dumps({"business_name": "صيدلية النهدي", "layout_philosophy": "turquoise retail",
                    "references_seen": ["https://dam.nahdionline.com/x.jpg"]}), encoding="utf-8")

    def _must_not_be_called(*a, **k):                       # a rebuild here is the old bug
        raise AssertionError("silently rebuilt the DNA over an identity failure")

    with pytest.raises(BrandbookIdentityError) as exc:
        load_or_build_dna(_profile("مراسم للحلول الرقمية", "https://marasimltd.com/"),
                          _must_not_be_called, cache_dir=str(tmp_path))
    assert exc.value.code == BRANDBOOK_LEGACY_COLLISION


def test_load_or_build_dna_prefers_an_explicit_domain_key_over_the_profile(tmp_path):
    from poster.pipeline import load_or_build_dna
    prof = _profile("Anything At All", "https://wrong.example.com/")
    save_brandbook(_dna("Marasim", mood="from the stored brand object"),
                   domain_key="marasimltd_com",
                   profile=_profile("Marasim", "https://marasimltd.com/"), cache_dir=tmp_path)
    dna = load_or_build_dna(prof, None, cache_dir=str(tmp_path), domain_key="marasimltd_com")
    assert dna is not None and dna.mood == "from the stored brand object"


# ---------------------------------------------------------------------
# §17 / §22 — foreign authority cannot reach the provider request
# ---------------------------------------------------------------------

# The two real DNAs from the incident, reduced to the fields that decide the image request.
_MARASIM = dict(
    business_name="Marasim Digital Solution Agency",
    color_usage=("Warm, dusty, monochromatic: sepia, golden-hour yellows and hazy orange-browns "
                 "from the Egyptian landscape; a deep black/charcoal field with a vibrant, fiery "
                 "orange for accents."),
    imagery_style="Documentary photography of Egypt (Cairo, Giza) with a cinematic quality.",
    dont_list=["Do not use a cool color palette (no blues, greens, or purples)."],
    do_list=["Use a black background and a vibrant orange as the single accent colour."],
)
_NAHDI = dict(
    business_name="صيدلية النهدي",
    color_usage=("A high-saturation gradient from light blue sky to turquoise water; a dusty "
                 "blue (#4A7C9B) for the primary CTA button; magenta or orange price circles."),
    imagery_style="Packshots composited into cheerful 3D beach and pool scenes.",
    dont_list=["Do not use a different color or shape for the main 'Shop Now' button."],
    do_list=["Make the discount number the largest element on the canvas."],
)


def _provider_directives_for(dna_fields: dict, *, domain_key: str, tmp_path):
    """The real chain: artifact on disk -> canonical load -> direction -> spec -> request."""
    from brand.visual_direction import resolve_visual_direction
    from brand.creative_dna import load_creative_dna
    from poster.visual_spec import compile_visual_spec

    prof = _profile(dna_fields["business_name"], f"https://{domain_key.replace('_', '.')}/")
    save_brandbook(BrandCreativeDNA(used_vision=True, **dna_fields), domain_key=domain_key,
                   profile=prof, cache_dir=tmp_path)
    lookup = resolve_brandbook(prof, domain_key, cache_dir=tmp_path)
    assert lookup.exists
    dna = load_creative_dna(lookup.path)
    rvd = resolve_visual_direction(brand_dna=dna, profile=prof, dna_path=str(lookup.path),
                                   domain_key=domain_key)
    return compile_visual_spec(rvd).to_provider_directives()


def test_marasims_request_mandates_its_own_palette_and_forbids_the_cool_one(tmp_path):
    """The line the invalid render carried was:

        - COLOUR PALETTE (mandatory): orange, blue, turquoise

    blue and turquoise being exactly what Marasim's own DNA forbids.
    """
    pos, neg = _provider_directives_for(_MARASIM, domain_key="marasimltd_com", tmp_path=tmp_path)
    mandatory = next(l for l in pos.splitlines() if "COLOUR PALETTE (mandatory)" in l).lower()
    assert "orange" in mandatory
    for foreign in ("blue", "turquoise", "teal", "cyan", "green", "purple"):
        assert foreign not in mandatory, f"{foreign!r} was mandated to the image model"
    assert "blue" in neg.lower() and "turquoise" in neg.lower()   # prohibited, not prescribed


def test_two_brands_never_share_a_provider_request(tmp_path):
    """Both artifacts exist side by side; each brand must get its OWN directives."""
    m_pos, _ = _provider_directives_for(_MARASIM, domain_key="marasimltd_com", tmp_path=tmp_path)
    n_pos, _ = _provider_directives_for(_NAHDI, domain_key="nahdionline_com", tmp_path=tmp_path)
    assert m_pos != n_pos
    assert "turquoise" in n_pos.lower()          # Al Nahdi legitimately mandates it
    assert "turquoise" not in m_pos.lower()      # Marasim never does


def test_an_arabic_named_brand_does_not_inherit_the_other_arabic_brands_request(tmp_path):
    """The defect in one assertion: both display names slug to "brand"; the requests must differ."""
    assert legacy_name_slug(_NAHDI["business_name"]) == "brand"
    marasim_ar = dict(_MARASIM, business_name="مراسم للحلول الرقمية")
    assert legacy_name_slug(marasim_ar["business_name"]) == "brand"

    n_pos, _ = _provider_directives_for(_NAHDI, domain_key="nahdionline_com", tmp_path=tmp_path)
    m_pos, _ = _provider_directives_for(marasim_ar, domain_key="marasimltd_com", tmp_path=tmp_path)
    assert "turquoise" not in m_pos.lower()
    assert "sepia" in m_pos.lower() or "golden" in m_pos.lower()
    assert m_pos != n_pos
