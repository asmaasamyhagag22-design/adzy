"""F-06 root cause: the crawler must not choose the language it then measures.

A server that negotiates on `Accept-Language` returns a DIFFERENT representation depending on
what we ask for. So a language preference we send is not an observation — it is an instruction,
and measuring its result and calling it "the brand's language" is circular.

MEASURED 2026-08-11 on a real negotiating site: asking EN-first returned `/en` + `<html lang=en>`
+ a 100% English crawl; asking AR-first returned `/` + `<html lang=ar>` + a 100% Arabic crawl.
Same code, same site, same minute.

Every test here is about the CONTRACT, not about any one brand: a fake origin stands in for the
negotiating server, so the properties hold for any language pair, including non-Arabic ones.
Hermetic — no socket, no browser, no provider.
"""
from __future__ import annotations

import importlib

import pytest

import scraper.browser_identity as identity
import scraper.config as config
import scraper.fetcher as fetcher


# ---------------------------------------------------------------------------
# A fake origin that serves a different representation per Accept-Language
# ---------------------------------------------------------------------------

class _NegotiatingOrigin:
    """One URL, several representations, chosen by the request's Accept-Language.

    Deliberately NOT Arabic/English-specific: the languages are parameters.
    """

    def __init__(self, *, default_lang: str, variants: dict[str, str]):
        self.default_lang = default_lang
        self.variants = variants          # primary subtag -> representation language
        self.requests: list[str | None] = []

    def fetch(self, accept_language: str | None) -> str:
        """Return the language of the representation this request would receive."""
        self.requests.append(accept_language)
        if not accept_language:
            return self.default_lang      # no preference -> the server's own default
        for token in str(accept_language).split(","):
            primary = token.split(";")[0].strip().lower().split("-")[0]
            if primary in self.variants:
                return self.variants[primary]
        return self.default_lang


class _Request:
    def __init__(self, headers: dict):
        self.headers = headers
        self.url = "https://origin.public.invalid/"
        self.resource_type = "document"


class _Route:
    """Records what `continue_` was called with. Mirrors the two Playwright outcomes."""

    def __init__(self, headers: dict):
        self.request = _Request(headers)
        self.continued_headers: dict | None = None
        self.aborted = False

    def continue_(self, **kwargs):
        self.continued_headers = kwargs.get("headers", dict(self.request.headers))

    def abort(self):
        self.aborted = True


def _sent_accept_language(route: _Route) -> str | None:
    headers = route.continued_headers or {}
    for key, value in headers.items():
        if key.lower() == "accept-language":
            return value
    return None


@pytest.fixture
def neutral_policy(monkeypatch):
    monkeypatch.setattr(fetcher, "ACCEPT_LANGUAGE", None)


# ---------------------------------------------------------------------------
# A. a preference CHANGES what becomes observable — the defect being closed
# ---------------------------------------------------------------------------

def test_a_a_language_preference_changes_the_representation_that_can_be_measured():
    """If this property did not hold, none of the rest would matter."""
    origin = _NegotiatingOrigin(default_lang="ar", variants={"ar": "ar", "en": "en"})

    assert origin.fetch("en-US,en;q=0.9,ar;q=0.8") == "en"
    assert origin.fetch("ar-EG,ar;q=0.9,en;q=0.5") == "ar"
    assert origin.fetch(None) == "ar", "no preference must yield the SERVER's default"


def test_a_the_same_property_holds_for_a_non_arabic_language_pair():
    """The contract is not an Arabic/English special case."""
    origin = _NegotiatingOrigin(default_lang="de", variants={"de": "de", "en": "en", "fr": "fr"})
    assert origin.fetch("en-GB,en;q=0.9") == "en"
    assert origin.fetch("fr-FR,fr;q=0.9") == "fr"
    assert origin.fetch(None) == "de"


# ---------------------------------------------------------------------------
# B. neutral mode sends no preference at all
# ---------------------------------------------------------------------------

def test_b_the_default_crawl_sends_no_accept_language_header(neutral_policy):
    route = _Route({"user-agent": "x", "accept-language": "en-US"})
    fetcher._continue_with_language_policy(route)

    assert route.continued_headers is not None, "the request was dropped, not continued"
    assert _sent_accept_language(route) is None, "the crawl still expressed a language preference"
    assert route.aborted is False


def test_b_absence_is_not_a_wildcard_or_an_empty_string(neutral_policy):
    """`*` and `""` are still preferences; the neutral state is the header being GONE."""
    route = _Route({"accept-language": "en-US"})
    fetcher._continue_with_language_policy(route)
    sent = _sent_accept_language(route)
    assert sent is None
    assert sent not in ("*", "")


def test_b_other_headers_are_untouched(neutral_policy):
    route = _Route({"user-agent": "UA/1", "sec-ch-ua-mobile": "?0", "accept-language": "en-US"})
    fetcher._continue_with_language_policy(route)
    assert route.continued_headers["user-agent"] == "UA/1"
    assert route.continued_headers["sec-ch-ua-mobile"] == "?0"


def test_b_the_browser_identity_headers_carry_no_language_preference():
    assert "Accept-Language" not in identity.browser_headers("131.0.0.0")
    assert not any(k.lower() == "accept-language" for k in identity.browser_headers("131.0.0.0"))


def test_b_an_explicit_operator_preference_is_still_honoured(monkeypatch):
    """Opting IN stays possible — and now reaches the request, which is what was broken."""
    monkeypatch.setattr(fetcher, "ACCEPT_LANGUAGE", "fr-FR,fr;q=0.9")
    route = _Route({"accept-language": "en-US"})
    fetcher._continue_with_language_policy(route)
    assert _sent_accept_language(route) == "fr-FR,fr;q=0.9"


# ---------------------------------------------------------------------------
# C. the host machine cannot decide the crawl's language
# ---------------------------------------------------------------------------

def test_c_the_context_locale_is_pinned_not_inherited_from_the_host(monkeypatch):
    """An unset Playwright locale takes the machine's. The crawl must not vary by laptop."""
    captured = {}

    class _Browser:
        version = "131.0.0.0"

        def new_context(self, **kwargs):
            captured.update(kwargs)
            return object()

    fetcher.make_browser_context(_Browser())
    assert captured.get("locale") == config.BROWSER_LOCALE
    assert captured["locale"], "an empty locale would inherit the host machine's"


def test_c_host_locale_environment_does_not_change_acquisition(monkeypatch):
    """Reloading config under a different machine locale must not move the language policy."""
    for var in ("LANG", "LC_ALL", "LANGUAGE"):
        monkeypatch.setenv(var, "ar_EG.UTF-8")
    monkeypatch.delenv("SCRAPER_ACCEPT_LANGUAGE", raising=False)
    monkeypatch.delenv("SCRAPER_BROWSER_LOCALE", raising=False)

    reloaded = importlib.reload(config)
    try:
        assert reloaded.ACCEPT_LANGUAGE is None, "a host locale leaked into the crawl preference"
        assert reloaded.BROWSER_LOCALE == "en-US"
    finally:
        importlib.reload(config)


def test_c_neutral_acquisition_is_stable_across_simulated_machines():
    """Same URL + same remote state + different machine => same observed language."""
    origin = _NegotiatingOrigin(default_lang="ar", variants={"ar": "ar", "en": "en"})
    observed = {origin.fetch(None) for _ in range(5)}
    assert observed == {"ar"}
    assert origin.requests == [None] * 5


# ---------------------------------------------------------------------------
# D / E / F — signals that must NOT decide a language
# ---------------------------------------------------------------------------

def test_d_hreflang_order_does_not_determine_preference():
    """`declared_languages` is alphabetically sorted, so its order carries no preference."""
    from poster.locale import content_language_of

    # A site whose crawled representation is German and which also offers English.
    profile = {"languages": [{"code": "de", "proportion": 1.0}],
               "declared_languages": ["de", "en"]}
    assert content_language_of(profile) == "de"

    # Reversing the declared list must change nothing: order is not evidence.
    profile_reversed = {**profile, "declared_languages": ["en", "de"]}
    assert content_language_of(profile_reversed) == content_language_of(profile)


def test_e_declaring_variants_alone_does_not_choose_a_language():
    """Having an Arabic edition is not a statement that Arabic is the brand's language."""
    from poster.locale import content_language_of

    english_site_with_arabic_edition = {
        "languages": [{"code": "en", "proportion": 1.0}],
        "declared_languages": ["ar", "en"],
    }
    assert content_language_of(english_site_with_arabic_edition) == "en"


@pytest.mark.parametrize("country", ["Egypt", "Qatar", "Saudi Arabia", "United Arab Emirates"])
def test_f_market_country_alone_cannot_select_a_language(country):
    """The verified F-06 invariant: an Arab market does not make English content Arabic."""
    from poster.locale import content_language_of

    profile = {"languages": [{"code": "en", "proportion": 1.0}],
               "locations": [{"country": country}]}
    assert content_language_of(profile) == "en"


def test_f_readable_country_evidence_does_not_leak_into_language():
    """Separately: where the country IS resolvable, it still must not move the language."""
    from poster.locale import content_language_of, country_of

    profile = {"languages": [{"code": "en", "proportion": 1.0}],
               "locations": [{"country": "Egypt"}]}
    assert country_of(profile) == "Egypt"
    assert content_language_of(profile) == "en"


def test_f_country_still_chooses_the_dialect_once_arabic_is_established():
    """Country may refine an ALREADY-chosen Arabic; it may never choose Arabic."""
    from poster.locale import arabic_dialect_for, content_language_of

    egypt = {"languages": [{"code": "ar", "proportion": 1.0}],
             "locations": [{"country": "Egypt"}]}
    gulf = {"languages": [{"code": "ar", "proportion": 1.0}],
            "locations": [{"country": "Qatar"}]}
    assert content_language_of(egypt) == content_language_of(gulf) == "ar"
    assert arabic_dialect_for(egypt) != arabic_dialect_for(gulf)


# ---------------------------------------------------------------------------
# G. neutral representation + unambiguous evidence => a stable AUTO answer
# ---------------------------------------------------------------------------

def test_g_auto_follows_the_neutrally_observed_representation():
    """The whole point: AUTO reads a representation the SERVER chose, not one we requested."""
    from poster.concept import brand_is_arabic
    from poster.locale import content_language_of
    from strategy.builder import _brand_is_arabic as calendar_is_arabic

    origin = _NegotiatingOrigin(default_lang="ar", variants={"ar": "ar", "en": "en"})
    observed = origin.fetch(None)                       # neutral acquisition
    profile = {"languages": [{"code": observed, "proportion": 1.0}],
               "declared_languages": ["ar", "en"],
               "locations": [{"country": "Egypt"}]}

    assert content_language_of(profile) == "ar"
    assert brand_is_arabic(profile) is True
    assert calendar_is_arabic(profile) is True


def test_g_a_biased_acquisition_would_have_produced_the_opposite_answer():
    """States the causal link the fix removes, without asserting the old behaviour is present."""
    from poster.locale import content_language_of

    origin = _NegotiatingOrigin(default_lang="ar", variants={"ar": "ar", "en": "en"})
    biased = origin.fetch("en-US,en;q=0.9,ar;q=0.8")
    neutral = origin.fetch(None)
    assert biased != neutral
    assert content_language_of({"languages": [{"code": biased, "proportion": 1.0}]}) == "en"
    assert content_language_of({"languages": [{"code": neutral, "proportion": 1.0}]}) == "ar"


def test_g_a_monolingual_site_is_unaffected_by_neutrality():
    """A site with one representation answers the same however we ask — the negative control."""
    origin = _NegotiatingOrigin(default_lang="en", variants={"en": "en"})
    assert origin.fetch(None) == "en"
    assert origin.fetch("ar-EG,ar;q=0.9") == "en"
    assert origin.fetch("en-US,en;q=0.9") == "en"
