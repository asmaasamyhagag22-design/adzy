# -*- coding: utf-8 -*-
"""A pharmacy chain is not a shampoo, and its ad should not be a shampoo commercial.

OWNER (2026-08-01), on the AlFouad reel: «مش حلو خالص، جايب حد بيستعمل برودكت، فين السيناريو فين
الماركت؟ التليفون الشاشة اللي فيه مش صح أصلاً. فيه حاجة مش لوجيك تماماً».

The illogic was in the code, stated plainly in its own docstring: `_vertical_mode` had four modes
— beauty, elegant, food, generic — and "every mode now demands a PERSON USING THE PRODUCT". There
was no mode for a brand whose product IS its service, so a pharmacy chain, a clinic, a school and
a bank all got a person applying something.

Worse for AlFouad specifically: its own description reads "an online RETAILER of health and beauty
products", which trips every beauty keyword there is, so the beauty spine wrote a shampoo film for
a business whose actual offer is availability, authenticity, the pharmacist and the range.

AND THE PHONE SCREEN WAS UNFIXABLE, not merely wrong. The film critic named it correctly — "replace
the generic chat interface with the AlFouad app" — the revision pass applied that note, re-rendered,
and scored IDENTICALLY (5 vs 5). A video model cannot draw a specific brand's UI; it invents a
generic one, the ownership gate rejects it, and no rewording changes that. A scene that depends on
it is a scene that can never pass, so the plan must not contain one.
"""
from __future__ import annotations

import json
import pathlib


def _profile(**kw):
    base = {"name": "X", "category": "", "description": "", "tagline": ""}
    base.update(kw)
    return base


# =====================================================================
# whoever owns the product owns the mode
# =====================================================================

def test_a_pharmacy_that_stocks_beauty_brands_is_a_service():
    """The real AlFouad description, verbatim — it trips every beauty keyword and is still a
    pharmacy."""
    from reel.creative_director import _vertical_mode
    assert _vertical_mode(_profile(
        name="AlFouad Pharmacies", category="ecommerce",
        description="AlFouad Pharmacies is an online retailer of health and beauty products."
    )) == "service"


def test_a_brand_that_makes_its_own_product_is_not_a_service():
    """A skincare house with the same words in its copy MAKES the bottle, and the bottle is the ad."""
    from reel.creative_director import _vertical_mode
    assert _vertical_mode(_profile(
        name="Some Skincare", category="skincare",
        description="We make serums and cleansers for sensitive skin.")) == "beauty"


def test_the_real_saved_profiles_land_where_they_should():
    from reel.creative_director import _vertical_mode
    for slug, expected in (("alfouad", "service"), ("nanocare", "beauty")):
        p = pathlib.Path(f"outputs/{slug}_profile.json")
        if not p.is_file():
            continue
        assert _vertical_mode(json.loads(p.read_text(encoding="utf-8"))) == expected, slug


def test_service_categories_are_recognised_without_any_keyword():
    from reel.creative_director import _vertical_mode
    for cat in ("pharmacy", "clinic", "hospital", "school", "bank", "insurance", "logistics"):
        assert _vertical_mode(_profile(category=cat)) == "service", cat


def test_the_service_test_reads_arabic_too():
    """D-8: the description is in the brand's own language."""
    from reel.creative_director import _is_service
    assert _is_service(_profile(description="سلسلة صيدليات في القاهرة")) is True
    assert _is_service(_profile(description="عيادة أسنان")) is True


def test_an_ordinary_product_brand_is_untouched():
    from reel.creative_director import _vertical_mode
    assert _vertical_mode(_profile(category="restaurant")) == "food"
    assert _vertical_mode(_profile(category="jewelry")) == "elegant"
    assert _vertical_mode(_profile(category="widgets")) == "generic"


# =====================================================================
# what the service mode actually asks for
# =====================================================================

def test_every_mode_has_guidance_and_a_delivery_note():
    from reel.creative_director import _DELIVERY_EG, _MOTION_GUIDANCE
    assert "service" in _MOTION_GUIDANCE
    assert "service" in _DELIVERY_EG, "a mode with no delivery note falls back to another's voice"


def test_the_service_film_is_about_the_business_not_one_item():
    from reel.creative_director import _MOTION_GUIDANCE
    g = _MOTION_GUIDANCE["service"]
    assert "BUSINESS ITSELF" in g
    assert "DO NOT build the film around one product being used" in g
    assert "not for the business that stocked it" in g


def test_the_service_film_still_demands_movement():
    """The fix must not trade one defect for the slideshow the motion contract exists to prevent."""
    from reel.creative_director import _MOTION_GUIDANCE
    g = _MOTION_GUIDANCE["service"].lower()
    assert "move" in g or "motion" in g


# =====================================================================
# a scene that can never pass must never be written
# =====================================================================

def test_the_plan_may_not_depend_on_rendering_the_brands_own_ui():
    """MEASURED: the critic named it, the revision applied the note, the re-render scored
    IDENTICALLY (5 vs 5). This is a capability limit, not a prompt problem."""
    from reel.creative_director import _MOTION_GUIDANCE
    g = _MOTION_GUIDANCE["service"]
    assert "NEVER write a scene that depends on rendering" in g
    assert "app, website, dashboard" in g
    assert "cannot draw a real product's UI" in g


def test_the_ban_offers_what_to_shoot_instead():
    """A ban with no alternative is a scene the director cannot write at all."""
    from reel.creative_director import _MOTION_GUIDANCE
    g = _MOTION_GUIDANCE["service"]
    assert "phone from behind" in g or "parcel that arrives" in g


# =====================================================================
# a brand whose product is EATEN
# =====================================================================

def test_a_snack_brand_stored_as_ecommerce_is_food():
    """MEASURED 2026-08-01 on the real Abu Auf profile: `category` is `ecommerce` — which records
    how it SELLS — so `_FOOD_CATS` (restaurant / cafe / bakery / …) never matched and a
    nuts-and-dried-fruit brand fell through to `generic`. That is the mode whose own docstring
    demands "a PERSON using the product": the shampoo-commercial shape the owner rejected for
    AlFouad, applied to a bag of almonds."""
    from reel.creative_director import _vertical_mode
    assert _vertical_mode(_profile(
        name="Abu Auf", category="ecommerce",
        description=("Abu Auf offers a variety of high-quality, fresh and nutritious snacks, "
                     "including nuts, dried fruits and traditional sweets."))) == "food"


def test_the_food_test_reads_arabic_too():
    """D-8: the description is in the brand's own language."""
    from reel.creative_director import _is_food
    assert _is_food(_profile(description="حلويات شرقية ومكسرات طازجة")) is True
    assert _is_food(_profile(description="قهوة مختصة ومخبوزات طازة")) is True
    assert _is_food(_profile(description="عيادة أسنان")) is False


def test_a_shop_that_only_stocks_food_is_a_service_not_a_food_brand():
    """Whoever OWNS the product owns the mode — the same rule that makes a pharmacy a service.
    A supermarket's offer is the range and the availability, not any one item, so a film built
    around one product is the wrong film for it."""
    from reel.creative_director import _vertical_mode
    assert _vertical_mode(_profile(
        category="ecommerce",
        description="An online supermarket delivering groceries to your door.")) == "service"


def test_food_is_decided_before_beauty():
    """A food brand's copy is full of 'natural', 'fresh' and 'care', which the beauty test reads
    as skincare. Order is the fix, not a longer keyword list."""
    from reel.creative_director import _vertical_mode
    assert _vertical_mode(_profile(
        category="ecommerce",
        description="Natural roasted nuts and dried fruit — fresh, with care in every batch."
    )) == "food"


def test_the_real_brands_land_where_they_should():
    """Every brand actually stored on this machine, checked at once."""
    from reel.creative_director import _vertical_mode
    try:
        from api.brand_store import BrandStore
        brands = BrandStore().list()
    except Exception:  # noqa: BLE001
        return
    expected = {"abuauf_com": "food", "alfouadpharmacies_com": "service",
                "nahdionline_com": "service", "nanocareegypt_com": "beauty"}
    for b in brands:
        want = expected.get(str(b.get("domain_key")))
        if want:
            assert _vertical_mode(b.get("_profile") or {}) == want, b.get("domain_key")


def test_every_mode_the_router_can_return_has_guidance():
    """A mode with no entry silently falls back to another vertical's spine."""
    from reel.creative_director import _DELIVERY_EG, _MOTION_GUIDANCE
    for mode in ("service", "beauty", "elegant", "food", "generic"):
        assert mode in _MOTION_GUIDANCE, mode
        assert mode in _DELIVERY_EG, mode


def test_a_stockist_directing_a_named_product_is_not_directed_as_a_stockist():
    """MEASURED 2026-08-08 on the reel the owner asked for. She pasted a Vichy Dercos spray link,
    and the prompt that reached the director carried BOTH of these:

        "FEATURED OFFERING: … This reel advertises ONLY this offering — every line of copy is
         about it"
        "DO NOT build the film around one product being used. An ad that shows a customer using a
         single item is an ad for that item's maker, not for the business that stocked it"

    Two instructions in one message that cancel each other, and the stockist clause wins because it
    is the more concrete. It is why these reels kept coming back about the PHARMACY.

    The stockist rule is right for a BRAND reel. It cannot also govern a reel whose subject the
    caller named.
    """
    from reel.creative_director import _system_prompt, _vertical_mode

    pharmacy = _profile(category="ecommerce",
                        description="online retailer of health and beauty products, skincare, "
                                    "hair care, vitamins and pharmaceutical items")
    pharmacy["catalog"] = [{"name": "Vichy Dercos Densi-Solutions", "url": "https://x/p/1",
                            "image_url": "https://x/1.webp"}]

    assert _vertical_mode(pharmacy) == "service", (
        "a pharmacy with no named product is still a stockist — the brand reel is about the shop")
    assert _vertical_mode(pharmacy, "Vichy Dercos Densi-Solutions Hair Mass Creator") != "service", (
        "a named product must not be directed under the do-not-feature-one-product rule")

    got = _system_prompt(4, "ar", _vertical_mode(pharmacy, "Vichy Dercos Densi-Solutions"),
                         featured="Vichy Dercos Densi-Solutions")
    assert "DO NOT build the film around one product being used" not in got, (
        "the prompt still forbids the very thing it was asked to advertise")
    assert "advertises ONLY this offering" in got, "the featured offering vanished from the brief"


def test_a_clinic_featuring_its_own_service_stays_a_service():
    """The stockist rule was never about a brand whose offering IS the service. A clinic has no
    catalogue of goods, so naming one of its treatments must not turn it into a product ad."""
    from reel.creative_director import _vertical_mode

    clinic = _profile(category="clinic",
                      description="a dermatology clinic offering laser and skin treatments")
    clinic.pop("catalog", None)
    assert _vertical_mode(clinic) == "service"
    assert _vertical_mode(clinic, "Laser hair removal") == "service", (
        "a clinic naming its own treatment is still a service brand")
