"""Evidence Ledger — central grounding gate (`resolve_claim`).

WHY THIS EXISTS (the moat, not a feature)
-----------------------------------------
The product sells *provable* zero-hallucination to marketing agencies: for any
customer-facing creative we can show the client a line-by-line trail of every hard
claim and its real source. Today that guarantee is enforced in scattered, weak gates
(e.g. `poster/concept.py` checks only that a "proof" line is non-empty and Arabic — NOT
that the proof is real). So an LLM can write "بخبرة 170 سنة" / "Serving for 170 years"
and it sails onto the poster. This module replaces the scattered gates with ONE gate
every channel passes through.

TWO TRUTH DOMAINS (this is the key design principle)
----------------------------------------------------
Ad copy is *paraphrase* by nature — "خدمات تقربك أكتر" is design, not a factual claim,
and must NOT be rejected just because it isn't a verbatim scrape line. What MUST be
sourced is a HARD CLAIM: a concrete, checkable assertion — a number ("170 years",
"5000 trained", "50% off"), a superlative/first ("the only", "أول", "leading"), a free
offer ("مجاناً"), or a credential ("ISO certified", "معتمد"). The Ledger therefore does
two things:
    1. `extract_claims(text)`  — pull the hard claims out of a copy line (ignore pure
       paraphrase).
    2. `resolve_claim(text)`   — for a claim, search all real evidence for support and
       return its source URL, or None (REJECT).

A copy line is grounded iff every hard claim it contains resolves to a source. A line
with no hard claim passes (it's allowed paraphrase). This is what lets the gate be
strict on fabrication without freezing creativity.

EVIDENCE SOURCES (everything that carries a real source URL)
------------------------------------------------------------
Built from a serialized BusinessProfile dict (the form the live pipeline passes):
every `EvidencedField` value + its `evidence[].quote` (verbatim scrape snippet) +
`evidence[].page_url`; offerings; CTAs; trust signals; etc. Optionally also a SWOT
(citation strings), brand-research facts (`source_url`), and deep-search sources
(`url`) — so the same gate covers web-sourced claims too.

AUDIT TRAIL FROM DAY ONE
------------------------
`AuditReport.export()` emits the (claim -> source) trail per asset. That export IS the
brand-safety artifact the agency resells — the Ledger is a gate AND the proof object,
by construction.

This module is pure/deterministic and never raises for ordinary input (it returns
empty/None). No I/O, no LLM, no network.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional


# ---------------------------------------------------------------------
# Normalization (Arabic-aware)
# ---------------------------------------------------------------------

# Arabic diacritics (harakat), superscript alef, and tatweel — stripped for matching.
_TASHKEEL_RE = re.compile(r"[ـً-ٰٟ]")
# Arabic-Indic (٠-٩) and extended/Persian (۰-۹) digits -> ASCII.
_DIGIT_MAP = {0x0660 + i: str(i) for i in range(10)}
_DIGIT_MAP.update({0x06F0 + i: str(i) for i in range(10)})
# Alef variants -> bare alef; alef-maqsura -> ya. (ta-marbuta ة kept: merging it with ه
# over-collapses distinct words.)
_LETTER_MAP = {
    0x0622: "ا", 0x0623: "ا", 0x0625: "ا", 0x0671: "ا",  # آأإٱ -> ا
    0x0649: "ي",                                                          # ى -> ي
}
_WS_RE = re.compile(r"\s+")
_AR_RE = re.compile(r"[؀-ۿ]")
# Arabic clitics that attach to the front of a word: conjunctions/prepositions + the
# definite article. Stripped (longest-first) so "والرواد" / "الرائد" match "رائد".
_AR_PREFIXES = ("وال", "فال", "بال", "كال", "لل", "ال", "و", "ف", "ب", "ك", "ل")


def normalize(text: str) -> str:
    """Lowercase + NFKC + Arabic-Indic digits->ASCII + strip tashkeel + unify alef/ya +
    collapse whitespace. For matching ONLY (never shown to a user)."""
    if not text:
        return ""
    s = unicodedata.normalize("NFKC", str(text))
    s = s.translate(_DIGIT_MAP)
    s = _TASHKEEL_RE.sub("", s)
    s = s.translate(_LETTER_MAP)
    s = s.lower()
    return _WS_RE.sub(" ", s).strip()


def _ar_stem(word: str) -> str:
    """Strip a leading Arabic clitic/article so inflected forms compare equal
    ("الرواد" -> "رواد", "وأول" -> "اول")."""
    for p in _AR_PREFIXES:
        if word.startswith(p) and len(word) - len(p) >= 2:
            return word[len(p):]
    return word


def _words(norm_text: str) -> list[str]:
    return [w for w in re.split(r"[^\w؀-ۿ]+", norm_text) if w]


def _lang(text: str) -> str:
    """Coarse script of a copy/evidence line — 'ar' if it contains Arabic, else 'en'. Used
    to cite same-language evidence so the matched_quote visually matches the claim."""
    return "ar" if _AR_RE.search(text or "") else "en"


# ---------------------------------------------------------------------
# Hard-claim lexicon — synonym GROUPS (English + Arabic)
# ---------------------------------------------------------------------
# A copy token in a group is "sourced" iff ANY member of the SAME group appears in the
# evidence. So a headline "روّاد التحول الرقمي" resolves against a tagline "الرواد
# الرقميون" (both in `first`), while "the only clinic" needs a real "only/الوحيد" in
# evidence. Members are normalized() at module load.

_RAW_GROUPS: dict[str, set[str]] = {
    "first": {
        "first", "1st", "number one", "leading", "leader", "pioneer", "pioneering",
        "foremost", "أول", "الأول", "اول", "الاول", "رائد", "الرائد", "رواد", "الرواد",
        "ريادة", "الريادة",
    },
    "only": {
        "only", "sole", "exclusive", "exclusively", "unique", "one of a kind",
        "الوحيد", "الوحيدة", "وحيد", "حصري", "حصرية", "حصريا", "الحصري",
    },
    "best": {
        "best", "top", "top rated", "finest", "premier", "world class", "number one",
        "أفضل", "الأفضل", "افضل", "الافضل", "الأحسن", "الاحسن", "أقوى", "الأقوى", "اقوى",
    },
    "largest": {
        "largest", "biggest", "greatest", "most", "أكبر", "الأكبر", "اكبر", "الاكبر",
        "أضخم", "الأضخم", "اضخم",
    },
    "free": {
        "free", "complimentary", "no cost", "gratis", "مجانا", "مجاناً", "مجاني",
        "مجانية", "ببلاش", "بلاش", "هدية",
    },
    # CS-122 CLOSED (owner ruling 2026-07-19): a discount/offer is a FACT that needs grounding —
    # the system must never invent a promo for the client. Same two-truth-domains line as ever:
    # the LLM may DESIGN around a documented offer; it may not CREATE one.
    "sale": {
        "sale", "discount", "% off", "offer", "promo", "promotion", "deal",
        "خصم", "خصومات", "تخفيض", "تخفيضات", "عرض", "عروض", "أوفر", "اوفر", "تنزيلات",
    },
    # Credentials split by KIND so evidence of one cannot source a claim of another (an
    # award won is NOT proof of an ISO certification — a precision the brand-safety bar needs).
    "award": {
        "award", "awarded", "award winning", "prize", "prizes", "honored", "honoured",
        "جائزة", "جوائز", "تكريم", "مكرّم", "مكرم",
    },
    "certification": {
        "certified", "certification", "accredited", "accreditation", "iso", "licensed",
        "license", "patented", "trademarked", "معتمد", "معتمدة", "اعتماد", "شهادة", "مرخص",
        "ترخيص", "براءة",
    },
    "guarantee": {
        "guaranteed", "guarantee", "warranty", "ضمان", "مضمون", "مضمونة",
    },
    # Generic comparative/ranking superlatives — the productive "-est" class (EN) and the
    # أفعل elative (AR). A LEXICON (closed list), NOT a morphological pattern, ON PURPOSE:
    # a naive EN `\w+est` over-matches honest/interest/request/forest/suggest/latest…, and a
    # naive AR `الأ\w+` over-matches الأساس/الأسبوع/الأسعار/الأخبار/الأحداث — so the closed
    # list is what keeps false-positives at zero. Exact word/stem matching is what lets
    # 'أسرع' (elative) be caught while 'الأساس'/'الأحداث'/'راقية' are not. NEW superlative
    # phrasings are caught by the canary in tests/test_ledger_superlatives.py (it fails
    # loudly when one is uncovered) — so the next hole surfaces in CI, not at an agency.
    "superlative": {
        "fastest", "quickest", "easiest", "simplest", "smartest", "strongest", "safest",
        "healthiest", "freshest", "cheapest", "highest", "lowest", "widest", "longest",
        "cleanest", "purest",
        "أسرع", "أسهل", "أرخص", "أذكى", "أحدث", "أأمن", "أنظف", "أنقى", "أجود", "أطول",
        "أوسع", "أعلى", "أدنى",
    },
}

# Normalized lexicon: group -> {normalized members}; plus single-word members tracked
# for fast word-level Arabic matching.
_GROUPS: dict[str, set[str]] = {
    g: {normalize(m) for m in members} for g, members in _RAW_GROUPS.items()
}


def _member_present(norm_text: str, words: set[str], stems: set[str], members: set[str]) -> bool:
    """True if any group member appears in `norm_text`. Multi-word members use a
    substring check; single-word members use Arabic-stem / word-boundary matching to
    avoid spurious substring hits (e.g. 'اول' inside 'تناول')."""
    for m in members:
        if " " in m:
            if m in norm_text:
                return True
            continue
        if _AR_RE.search(m):
            ms = _ar_stem(m)
            if m in words or ms in stems:
                return True
        else:
            if re.search(r"\b" + re.escape(m) + r"\b", norm_text):
                return True
    return False


# ---------------------------------------------------------------------
# Number claims — only SIGNIFICANT numbers (a bare "3 tracks" is not a hard claim,
# "170 years" / "5000" / "50%" / "since 1998" are).
# ---------------------------------------------------------------------

_SIGNIFICANT_NUMBER_RE = re.compile(
    r"""
    (?P<since>(?:since|est\.?|منذ|عام|سنة)\s*(?P<since_yr>\d{4}))   # since 1998 / عام 1998
    | (?P<unit>(?P<unit_n>\d+)\s*(?:%|\+|k\b|m\b|million|thousand|
        years?|yrs?|months?|days?|hours?|سنة|سنوات|عام|أعوام|اعوام|شهر|شهور|يوم|أيام|ايام|
        مليون|ألف|الف|الاف|آلاف))                                   # 50% / 170 years / 5000 عام
    | (?P<big>(?<!\d)\d{3,}(?!\d))                                  # any 3+ digit run (5000)
    | (?P<two>(?<!\d)\d{2}(?!\d))                                   # standalone 2-digit (24, 99)
    """,
    re.VERBOSE | re.IGNORECASE,
)


# A symbolic #1 / no.1 ranking ("#1 in the market", "no.1 provider") — a ranking claim the
# word lexicon can't catch because '#' breaks word boundaries. Maps to the 'first' group.
# NOT generalized to #N: a "#5 tip" is a list item, not a boast.
_RANK_RE = re.compile(r"(?:#\s*1|\bno\.\s*1|\brank\s*#?\s*1)(?!\d)")


def _number_tokens(norm_text: str) -> list[str]:
    """Extract the checkable digit tokens from a normalized copy line."""
    tokens: list[str] = []
    for m in _SIGNIFICANT_NUMBER_RE.finditer(norm_text):
        tok = m.group("since_yr") or m.group("unit_n") or m.group("big") or m.group("two")
        if tok and tok not in tokens:
            tokens.append(tok)
    return tokens


# --- Number UNIT CLASS (context-aware resolution) --------------------------------------
# A %/price/duration/scale number claim must be sourced by evidence carrying the SAME kind
# of number. The bare-digit match certified fabrications: "Save 50%" resolved against
# "50 years", "The best coffee" against "best regards" (MEASURED 2026-07-05 — a FALSE
# 'verified' row in the client-facing compliance sheet, the moat's core artifact).
# SAFE BY DEFAULT: only a POSITIVELY-identified strong unit tightens; anything unrecognized
# (incl. Arabic forms this misses) degrades to the prior lenient "digit present" match, so
# this can never turn a legitimate claim UNSOURCED — only stop a cross-unit false match.
_PCT_AFTER_RE = re.compile(r"^\s*(?:%|percent|بالما)")
_SCALE_AFTER_RE = re.compile(r"^\s*(?:\+|k\b|m\b|bn\b|million|billion|thousand|الف|الاف|مليون|مليار)")
_DUR_AFTER_RE = re.compile(
    r"^\s*(?:years?|yrs?|months?|weeks?|days?|hours?|"
    r"عام|اعوام|سن|شهر|اشهر|يوم|ايام|ساع|اسبوع|اسابيع)")
_CUR_AFTER_RE = re.compile(r"^\s*(?:جنيه|egp|le\b|usd|دولار|ريال|sar|aed|درهم|يورو|eur)")
_CUR_BEFORE_RE = re.compile(r"(?:\$|€|£|جنيه|egp|usd|دولار|ريال|sar|aed|درهم|يورو|eur)\s*$")
_STRONG_CLASSES = ("pct", "scale", "dur", "cur")


def _number_class_at(norm_text: str, start: int, end: int) -> str:
    """Classify the unit of the digit at [start:end] in a normalized string."""
    after = norm_text[end:end + 14]
    if _PCT_AFTER_RE.match(after):
        return "pct"
    if _SCALE_AFTER_RE.match(after):
        return "scale"
    if _DUR_AFTER_RE.match(after):
        return "dur"
    if _CUR_AFTER_RE.match(after) or _CUR_BEFORE_RE.search(norm_text[max(0, start - 8):start]):
        return "cur"
    return "bare"


def _digit_classes(norm_text: str, digit: str) -> set[str]:
    """The set of unit classes the exact `digit` token appears with in `norm_text`."""
    classes: set[str] = set()
    for m in re.finditer(r"(?<!\d)" + re.escape(digit) + r"(?!\d)", norm_text):
        classes.add(_number_class_at(norm_text, m.start(), m.end()))
    return classes


# --- Number SUBJECT context (C2 residual) ----------------------------------------------
# The deterministic scaffold: read the noun a number counts (the content word right after
# it) so the gate can tell whether two "100"s count the SAME thing. Used only to detect the
# AMBIGUOUS case (token-disjoint subjects) that a semantic judge then resolves; on its own it
# never rejects (that over-tightens synonyms/inflection — measured).
_SUBJECT_STOP = {
    "the", "a", "an", "of", "our", "your", "their", "its", "his", "her", "my", "this",
    "that", "these", "those", "and", "or", "for", "to", "in", "on", "at", "with", "by",
    "from", "as", "is", "are", "was", "were", "be", "been", "every", "all", "more", "most",
    "than", "per", "up", "over", "under", "new", "only", "just", "get", "got",
    "من", "في", "على", "الى", "و", "او", "كل", "لكل", "هذا", "هذه", "التي", "الذي", "عن",
    "مع", "ال", "دي", "ده",
}
_NUM_UNIT_WORDS = {
    "percent", "years", "year", "yrs", "yr", "months", "month", "days", "day", "hours",
    "hour", "weeks", "week", "k", "m", "million", "billion", "thousand",
    "سنة", "سنوات", "عام", "اعوام", "شهر", "شهور", "يوم", "ايام", "ساعة", "ساعات",
    "الف", "الاف", "مليون", "مليار",
}


def _subject_stems_after(norm_text: str, digit: str, window: int = 2) -> set[str]:
    """Stems of the content words a `digit` counts (the noun right after it) across every
    occurrence: '100 free gifts' -> {free, gift-stem}. Skips stopwords + unit words; empty
    when there is no clear subject (caller then stays lenient)."""
    stems: set[str] = set()
    for m in re.finditer(r"(?<!\d)" + re.escape(digit) + r"(?!\d)", norm_text):
        taken = 0
        for w in _words(norm_text[m.end():m.end() + 48]):
            if taken >= window:
                break
            if w.isdigit() or len(w) < 2 or w in _SUBJECT_STOP or w in _NUM_UNIT_WORDS:
                continue
            stems.add(_ar_stem(w))
            taken += 1
    return stems


# The groups whose SUBJECT matters. "best COFFEE" / "largest NETWORK" / "accredited by HARVARD" /
# "won the CANNES prize" all name a specific subject (the thing, the issuer, the awarder), so a
# disjoint subject is a mismatch to be judged — NOT auto-verified.
#
# CS-75 (owner ruling 2026-07-18): certification & award ARE subject-gated — "معتمد من هارفارد" must
# not resolve against "معتمد من المجلس المحلي" (a DIFFERENT issuer). This is SAFE without a judge:
# the disjoint-subject path stays LENIENT when `subject_judge is None` (a bare "نحن معتمدون" with no
# issuer named has an empty subject and skips the check entirely), so it only bites in production
# once the judge is wired (poster/concept.py + poster/audit.py). `guarantee` stays out (a bare
# "guaranteed" rarely names a distinct subject; its numbers are gated by the number path).
_SUBJECT_GROUPS = frozenset({"first", "only", "best", "largest", "superlative",
                             "certification", "award"})


def _content_stems(norm_text: str, exclude: frozenset = frozenset()) -> set[str]:
    """Content-word stems of a line minus stopwords and the group's own member words — the
    SUBJECT a superlative/ranking is about ('best coffee in town' -> {coffee, town})."""
    out: set[str] = set()
    for w in _words(norm_text):
        if w.isdigit() or len(w) < 2 or w in _SUBJECT_STOP:
            continue
        s = _ar_stem(w)
        if w in exclude or s in exclude:
            continue
        out.add(s)
    return out


# ---------------------------------------------------------------------
# Public claim / resolution / verdict types
# ---------------------------------------------------------------------

@dataclass
class Claim:
    """One hard (checkable) assertion pulled from a copy line."""
    kind: str          # number|first|only|best|largest|superlative|free|award|certification|guarantee
    token: str         # the checkable token (a digit run, or a normalized lexicon member)
    text: str          # the normalized copy the claim was found in (context)


@dataclass
class Resolution:
    """A claim's supporting evidence."""
    source_url: str
    confidence: str       # the evidence's own confidence (high/medium/low/none/unknown)
    source_type: str      # which evidence channel (profile_evidence/offering/swot/research/...)
    matched_quote: str    # the verbatim evidence snippet that supports the claim
    tier: str = "brand"   # 'brand' = the brand's own scraped site (trusted); 'web' = a search
                          # snippet/SWOT (weaker — a snippet can be SEO junk or a competitor)
    matched_lang: str = ""  # 'ar'|'en' — language of the cited evidence quote
    copy_lang: str = ""     # 'ar'|'en' — language of the audited copy line (for visual-match)


@dataclass
class ClaimVerdict:
    claim: Claim
    resolution: Optional[Resolution]

    @property
    def sourced(self) -> bool:
        return self.resolution is not None


@dataclass
class LedgerEntry:
    """One unit of real evidence + where it came from."""
    raw: str
    norm: str = ""
    source_url: str = ""
    source_type: str = "profile_evidence"
    confidence: str = "unknown"
    tier: str = ""        # 'brand' (the brand's own site) vs 'web' (search/SWOT) — see Resolution

    def __post_init__(self) -> None:
        if not self.norm:
            self.norm = normalize(self.raw)
        if not self.tier:
            self.tier = "brand" if self.source_type.startswith("profile") else "web"


# ---------------------------------------------------------------------
# Claim extraction
# ---------------------------------------------------------------------

def extract_claims(text: str) -> list[Claim]:
    """Pull the HARD claims out of a copy line. Pure paraphrase -> []."""
    norm = normalize(text)
    if not norm:
        return []
    words = set(_words(norm))
    stems = {_ar_stem(w) for w in words}
    claims: list[Claim] = []

    for tok in _number_tokens(norm):
        claims.append(Claim(kind="number", token=tok, text=norm))

    for group, members in _GROUPS.items():
        if _member_present(norm, words, stems, members):
            # record the canonical group name as the token's kind; token = group
            claims.append(Claim(kind=group, token=group, text=norm))

    # Symbolic #1 / no.1 ranking -> a 'first' claim (only if not already flagged 'first').
    if _RANK_RE.search(norm) and not any(c.kind == "first" for c in claims):
        claims.append(Claim(kind="first", token="first", text=norm))

    return claims


# ---------------------------------------------------------------------
# Evidence Ledger
# ---------------------------------------------------------------------

def is_reputable_web_source(url: str) -> bool:
    """A WEB-tier claim is proof only if its source is REPUTABLE — a snippet from an
    aggregator / listicle / junk-SEO host is not (the POLICY the ledger states). Brand-tier
    evidence (the brand's OWN site) is always trusted and is never routed here. Lazy-imports
    the competitor-discovery denylist to avoid a load-time import cycle; on any error it does
    NOT over-reject (the url already passed upstream search/prefer_safe gates)."""
    if not url or not str(url).startswith("http"):
        return False
    try:
        from urllib.parse import urlparse

        from competitor.web_discovery import _AGGREGATOR_HOSTS, _registrable
        reg = _registrable(urlparse(url).hostname)
        return not (reg and reg in _AGGREGATOR_HOSTS)
    except Exception:  # noqa: BLE001 — never over-reject on a lookup failure
        return True


def _as_text(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, dict):  # an EvidencedField-shaped dict
        return _as_text(v.get("value"))
    return str(v)


class EvidenceLedger:
    """All of a brand's real evidence, indexed for `resolve_claim`."""

    def __init__(self, entries: list[LedgerEntry], subject_judge: Optional[Any] = None) -> None:
        # Brand-tier evidence first so resolution prefers the brand's OWN site over a web
        # snippet when both could support a claim (stable sort preserves within-tier order).
        self.entries = sorted((e for e in entries if e.norm),
                              key=lambda e: 0 if e.tier == "brand" else 1)
        # Optional SUBJECT judge (C2 residual): a callable (claim_text, evidence_text, token)
        # -> True (same subject) / False (different) / None (unknown). Consulted ONLY when a
        # claim's number/superlative SUBJECT is token-disjoint from the evidence's (the
        # genuinely ambiguous synonym/inflection-vs-fabrication case). None -> the gate stays
        # deterministic + lenient (the project's designed number-only grounding), no regression.
        self._subject_judge = subject_judge
        self._judge_cache: dict = {}
        # P-24: WHICH MODE DID THIS GATE ACTUALLY RUN IN? The leniency above is deliberate and
        # documented, and it was also invisible — a ledger built with no judge certified text under
        # a weaker rule than the one its callers describe, and nothing downstream could tell the
        # difference. Exposed as state so a caller can disclose it instead of guessing.
        self.strict_subject_gate: bool = subject_judge is not None
        # Precomputed blobs for fast checks.
        self._digit_blob = " ".join(e.norm for e in self.entries)
        self._word_set: set[str] = set()
        self._stem_set: set[str] = set()
        for e in self.entries:
            ws = _words(e.norm)
            self._word_set.update(ws)
            self._stem_set.update(_ar_stem(w) for w in ws)

    # ---- construction ----

    @classmethod
    def from_profile(
        cls,
        profile: dict[str, Any],
        *,
        swot: Optional[dict] = None,
        research: Optional[dict] = None,
        deep_search: Optional[dict] = None,
        subject_judge: Optional[Any] = None,
    ) -> "EvidenceLedger":
        """Build the ledger from a serialized BusinessProfile dict (+ optional
        web-sourced evidence). Robust to a full-run wrapper ({'profile': {...}}).

        `subject_judge` (optional): a semantic same-subject judge for the C2 residual — see
        `EvidenceLedger.__init__`. Omit for the deterministic, offline (lenient) behavior."""
        if isinstance(profile, dict) and "value_propositions" not in profile \
                and isinstance(profile.get("profile"), dict):
            profile = profile["profile"]
        entries: list[LedgerEntry] = []
        p = profile or {}
        # 1.5 (owner ask, 2026-07-19): brand-tier evidence (contacts, own fields) used to carry
        # source_url="" — the evidence page then showed the verbatim quote with NO clickable
        # origin. The brand's own homepage IS the original source for profile-tier facts, so it
        # becomes the default. Web-tier evidence keeps its strict URL requirement (fail-closed).
        _src = p.get("source_url")
        brand_home = str((_src.get("value") if isinstance(_src, dict) else _src)
                         or p.get("final_url") or "").strip()

        def add(raw: str, url: str, stype: str, conf: str = "unknown") -> None:
            if raw and str(raw).strip():
                # Web-tier evidence with a KNOWN non-reputable source (an aggregator/junk host)
                # is not proof — drop it so a claim can't be "verified" by SEO junk in ANY
                # gate (concept/calendar/TOWS/reel), not just pick_angle. Brand-tier (profile*)
                # is always kept.
                #
                # FAIL-CLOSED (P6 #1, 2026-07-15): a web entry with NO url is dropped too. It used
                # to be kept ("can't judge, don't over-drop") — but poster/brand_research.py
                # declares `source_url: str = ""` BY DEFAULT, so an LLM-emitted fact with no source
                # entered as evidence, resolved a hard claim, and the client's compliance sheet
                # printed "Verified against a sourced web result" with source_url: None. The
                # model's own output became the proof of the model's own claim. If we cannot show
                # the client a source, it is not evidence.
                is_web = not str(stype).startswith("profile")
                if is_web and (not url or not is_reputable_web_source(url)):
                    return
                if not url and not is_web:
                    url = brand_home        # 1.5: profile-tier facts originate on the brand site
                entries.append(LedgerEntry(raw=str(raw), source_url=url or "",
                                           source_type=stype, confidence=conf))

        def add_evidenced(ef: Any, stype: str) -> None:
            """An EvidencedField dict: index its value AND every verbatim evidence quote
            (the quote is the strongest match target — it's real scrape text)."""
            if not isinstance(ef, dict):
                add(_as_text(ef), "", stype)
                return
            conf = str(ef.get("confidence") or "unknown")
            ev = ef.get("evidence") or []
            url = ""
            for item in ev:
                if isinstance(item, dict):
                    url = item.get("page_url") or url
            add(_as_text(ef.get("value")), url, stype, conf)
            for item in ev:
                if isinstance(item, dict) and item.get("quote"):
                    add(item["quote"], item.get("page_url") or "", stype + ":quote", conf)

        # Scalar EvidencedFields.
        for f in ("name", "tagline", "description"):
            add_evidenced(p.get(f), f"profile:{f}")

        # List-of-EvidencedField fields.
        for f in ("value_propositions", "audience_signals", "other_unique_insights",
                  "trust_signals", "service_areas", "existing_ctas"):
            for item in (p.get(f) or []):
                add_evidenced(item, f"profile:{f}")

        # Offerings (name/short_description/price_text + evidence quotes).
        for off in (p.get("offerings") or []):
            if not isinstance(off, dict):
                continue
            conf = str(off.get("confidence") or "unknown")
            url = ""
            for item in (off.get("evidence") or []):
                if isinstance(item, dict):
                    url = item.get("page_url") or url
            for k in ("name", "short_description", "price_text"):
                add(off.get(k), url, "profile:offering", conf)
            for item in (off.get("evidence") or []):
                if isinstance(item, dict) and item.get("quote"):
                    add(item["quote"], item.get("page_url") or "", "profile:offering:quote", conf)

        # Contact / locations — real strings that legitimately carry numbers.
        cc = p.get("contact_channels") or {}
        if isinstance(cc, dict):
            for ph in (cc.get("phones") or []):
                if isinstance(ph, dict):
                    # Index BOTH the clean e164 AND the raw. A creative shows the canonical
                    # e164 (reel contact scene, poster contact line); a short code shows raw.
                    # `raw or e164` indexed only ONE — and a scraped `raw` is often junk
                    # page-text (the clean number lives only in e164), so a displayed e164
                    # could never resolve (MEASURED: reel narration floor flagged 3 real
                    # scraped phones as "unsourced"). Both are real brand-tier evidence.
                    add(ph.get("e164"), "", "profile:contact")
                    add(ph.get("raw"), "", "profile:contact")
            for key in ("emails", "whatsapp_numbers", "physical_addresses"):
                for v in (cc.get(key) or []):
                    add(v, "", "profile:contact")
        for loc in (p.get("locations") or []):
            if isinstance(loc, dict):
                url = ""
                for item in (loc.get("evidence") or []):
                    if isinstance(item, dict):
                        url = item.get("page_url") or url
                for k in ("label", "address_text", "city", "region", "country"):
                    add(loc.get(k), url, "profile:location")

        # Optional web-sourced evidence (the gate covers these too).
        if isinstance(swot, dict):
            for quad in ("strengths", "weaknesses", "opportunities", "threats"):
                for it in (swot.get(quad) or []):
                    if isinstance(it, dict):
                        cites = it.get("citation") or []
                        url = next((c for c in cites if isinstance(c, str) and c.startswith("http")), "")
                        add(it.get("text"), url, "swot")
                        add(it.get("evidence"), url, "swot")
        if isinstance(research, dict):
            for fct in (research.get("facts") or []):
                if isinstance(fct, dict):
                    add(fct.get("text"), fct.get("source_url") or "", "research")
        if isinstance(deep_search, dict):
            add(deep_search.get("business_name"), "", "deep_search")
            add(deep_search.get("description"), "", "deep_search")
            for src in (deep_search.get("sources") or []):
                if isinstance(src, dict):
                    add(src.get("snippet") or src.get("title"), src.get("url") or "", "deep_search")

        return cls(entries, subject_judge=subject_judge)

    # ---- resolution ----

    def _pick(self, matches: list["LedgerEntry"], copy_lang: str = "") -> Optional[Resolution]:
        """Choose which evidence to cite. Entries are already brand-tier-first; keep that
        TIER priority, then PREFER an entry in the SAME LANGUAGE as the copy so the cited
        matched_quote visually matches the claim. A cross-language quote (Arabic claim,
        English quote) reads as a mismatch to a human auditor and undermines the proof even
        when the value is technically right — so we fall to another language only if no
        same-language evidence exists, and label that on the Resolution (matched_lang vs
        copy_lang) so the difference is documented, not a surprise."""
        if not matches:
            return None
        brand = [e for e in matches if e.tier == "brand"]
        pool = brand or matches            # tier dominates language
        chosen = pool[0]
        if copy_lang:
            same = [e for e in pool if _lang(e.raw) == copy_lang]
            if same:
                chosen = same[0]
        return Resolution(chosen.source_url, chosen.confidence, chosen.source_type, chosen.raw,
                          tier=chosen.tier, matched_lang=_lang(chosen.raw), copy_lang=copy_lang)

    def _ask_subject_judge(self, claim_text: str, ev_text: str, token: str) -> Optional[bool]:
        """Consult the optional semantic judge (cached). True/False/None; any error -> None
        (lenient). Only called on the token-disjoint-subject ambiguous case."""
        key = (claim_text, ev_text, token)
        if key in self._judge_cache:
            return self._judge_cache[key]
        try:
            res = self._subject_judge(claim_text, ev_text, token)
        except Exception:  # noqa: BLE001 — a judge error must never fabricate a rejection
            res = None
        if res is not None and not isinstance(res, bool):
            res = None
        self._judge_cache[key] = res
        return res

    def _number_subject_ok(self, claim_text: str, entry: "LedgerEntry", token: str) -> bool:
        """C2 residual — does this evidence count the SAME thing as the claim's number?
        Fast path: a shared subject stem -> yes. Token-disjoint subjects are AMBIGUOUS
        (synonym/inflection vs fabrication); defer to the semantic judge, and REJECT only on
        a confident 'different'. No judge -> lenient (the project's number-only grounding)."""
        claim_subj = _subject_stems_after(claim_text, token)
        if not claim_subj:
            return True                                  # no clear claim subject -> lenient
        ev_subj = _subject_stems_after(entry.norm, token)
        if not ev_subj or not claim_subj.isdisjoint(ev_subj):
            return True                                  # plain evidence / shared stem -> ok
        if self._subject_judge is None:
            return True                                  # no judge -> lenient (no regression)
        return self._ask_subject_judge(claim_text, entry.raw, token) is not False

    def _resolve_number(self, claim: "Claim", copy_lang: str = "") -> Optional[Resolution]:
        token = claim.token
        # The claim's OWN strong unit class(es) (from its copy context). If the copy uses a
        # strong unit (%, price, duration, scale), the evidence must carry the same number in
        # a compatible class — a "50%" claim is NOT sourced by "50 years". We keep the FULL
        # SET (a copy can use the digit in two classes, e.g. "50% today, valid 50 days") and
        # accept evidence matching ANY of them: deterministic (a single arbitrary pick over a
        # set is PYTHONHASHSEED-dependent) and never regresses a value the evidence supports.
        # A plain number carries no strong class -> prior lenient rule + the optional SUBJECT
        # judge for the "100 gifts" vs "100 stores" residual.
        claim_strong = {c for c in _digit_classes(claim.text, token) if c in _STRONG_CLASSES}
        matches = []
        for e in self.entries:
            ev_classes = _digit_classes(e.norm, token)
            if not ev_classes:
                continue
            if claim_strong and claim_strong.isdisjoint(ev_classes):
                continue
            if not claim_strong and not self._number_subject_ok(claim.text, e, token):
                continue
            matches.append(e)
        return self._pick(matches, copy_lang)

    def _resolve_group(self, claim: "Claim", copy_lang: str = "") -> Optional[Resolution]:
        group = claim.kind
        members = _GROUPS.get(group, set())
        # For a ranking/superlative group the SUBJECT matters: "best coffee" is NOT sourced by
        # "best regards". Fast path (shared subject stem) accepts; a token-disjoint subject is
        # ambiguous (inflection رقمي/رقميون vs fabrication coffee/regards) -> defer to the
        # judge, rejecting only on a confident 'different'. No judge -> lenient (unchanged).
        subj_gate = group in _SUBJECT_GROUPS
        member_stems = frozenset(_ar_stem(m) for m in members if " " not in m) if subj_gate else frozenset()
        claim_subj = _content_stems(claim.text, member_stems) if subj_gate else set()
        matches = []
        for e in self.entries:
            ws = set(_words(e.norm))
            stems = {_ar_stem(w) for w in ws}
            if not _member_present(e.norm, ws, stems, members):
                continue
            if subj_gate and claim_subj:
                ev_subj = _content_stems(e.norm, member_stems)
                if ev_subj and claim_subj.isdisjoint(ev_subj):
                    # CS-46 — KNOWN OPEN HOLE (measured: a fabricated "أفضل قهوة في المدينة" is
                    # certified brand-tier on 12/18 real profiles, matching incidental words like a
                    # "Best Sellers" nav item). The clean fix is hard: with no judge you cannot tell
                    # a FABRICATION ("best coffee" ← "Best Sellers") from a legitimate PARAPHRASE
                    # ("الأسرع في السوق" ← "الأسرع في توصيل الطرود") — both are subject-disjoint. A
                    # blanket fail-closed, and a char-similarity heuristic, each broke legitimate
                    # grounded-copy tests (false negatives on the moat, worse than the hole). The real
                    # fix is to WIRE a subject judge into the production ledger build (today every
                    # path passes judge=None) — a semantics + spend decision for the owner. Tracked in
                    # logs/OPEN_PROBLEMS.md as the #1 open moat item. Behaviour here is UNCHANGED
                    # (lenient) pending that decision — documented, not silently accepted.
                    if (self._subject_judge is not None
                            and self._ask_subject_judge(claim.text, e.raw, group) is False):
                        continue
            matches.append(e)
        return self._pick(matches, copy_lang)

    def _resolve_one(self, claim: Claim) -> Optional[Resolution]:
        copy_lang = _lang(claim.text)      # prefer evidence in the audited copy's language
        if claim.kind == "number":
            return self._resolve_number(claim, copy_lang)
        return self._resolve_group(claim, copy_lang)

    def resolve_claim(self, text: str) -> Optional[Resolution]:
        """The doc-named gate: return a Resolution iff EVERY hard claim in `text` is
        supported by real evidence; None if any hard claim is unsourced. Text with no
        hard claim resolves to None (nothing to source — the caller treats that as
        'pass', see `audit_text`)."""
        claims = extract_claims(text)
        if not claims:
            return None
        first: Optional[Resolution] = None
        for c in claims:
            r = self._resolve_one(c)
            if r is None:
                return None
            first = first or r
        return first

    def audit_text(self, text: str) -> list[ClaimVerdict]:
        """Per-claim verdicts for one copy line. The real gate engine: a line passes iff
        it has no UNSOURCED verdict (no hard claim, or every hard claim resolved)."""
        return [ClaimVerdict(c, self._resolve_one(c)) for c in extract_claims(text)]

    def audit_fields(self, fields: dict[str, str]) -> "AuditReport":
        """Audit a set of named customer-facing copy fields (headline, subheadline, ...)."""
        audits = [FieldAudit(field=name, text=(text or ""), verdicts=self.audit_text(text or ""))
                  for name, text in fields.items()]
        return AuditReport(field_audits=audits, evidence_count=len(self.entries))


# ---------------------------------------------------------------------
# Audit report (the exportable, agency-facing trail)
# ---------------------------------------------------------------------

@dataclass
class FieldAudit:
    field: str
    text: str
    verdicts: list[ClaimVerdict] = field(default_factory=list)

    @property
    def unsourced(self) -> list[ClaimVerdict]:
        return [v for v in self.verdicts if not v.sourced]

    @property
    def has_hard_claim(self) -> bool:
        return bool(self.verdicts)

    @property
    def grounded(self) -> bool:
        """True if every hard claim resolved (or there were none)."""
        return not self.unsourced


@dataclass
class AuditReport:
    field_audits: list[FieldAudit] = field(default_factory=list)
    evidence_count: int = 0

    @property
    def fields_with_claims(self) -> int:
        return sum(1 for fa in self.field_audits if fa.has_hard_claim)

    @property
    def unsourced_fields(self) -> list[FieldAudit]:
        return [fa for fa in self.field_audits if fa.unsourced]

    @property
    def total_claims(self) -> int:
        return sum(len(fa.verdicts) for fa in self.field_audits)

    @property
    def total_unsourced(self) -> int:
        return sum(len(fa.unsourced) for fa in self.field_audits)

    @property
    def passes(self) -> bool:
        """The gate verdict for this asset: no field carries an unsourced hard claim."""
        return self.total_unsourced == 0

    def export(self) -> dict:
        """The (claim -> source) trail an agency can show its client. This is the
        brand-safety artifact, emitted per asset from day one."""
        return {
            "passes": self.passes,
            "evidence_count": self.evidence_count,
            "fields": [
                {
                    "field": fa.field,
                    "text": fa.text,
                    "grounded": fa.grounded,
                    "claims": [
                        {
                            "kind": v.claim.kind,
                            "token": v.claim.token,
                            "sourced": v.sourced,
                            "source_url": (v.resolution.source_url if v.resolution else None),
                            "source_tier": (v.resolution.tier if v.resolution else None),
                            "matched_quote": (v.resolution.matched_quote if v.resolution else None),
                            "confidence": (v.resolution.confidence if v.resolution else None),
                            # Visual-match transparency: when the cited quote is in a different
                            # language than the copy, say so explicitly (documented, not a surprise).
                            "copy_lang": (v.resolution.copy_lang if v.resolution else None),
                            "matched_lang": (v.resolution.matched_lang if v.resolution else None),
                            "lang_mismatch": bool(
                                v.resolution and v.resolution.copy_lang and v.resolution.matched_lang
                                and v.resolution.copy_lang != v.resolution.matched_lang),
                        }
                        for v in fa.verdicts
                    ],
                }
                for fa in self.field_audits
            ],
        }
