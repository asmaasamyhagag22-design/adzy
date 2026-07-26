"""Creative Concept layer — ONE coherent campaign idea drives every element.

Root-cause fix (per the build brief): the poster used to generate the headline, the chips,
and the image INDEPENDENTLY → three unrelated things, no single message, often English on an
Arabic brand. Now a single LLM call produces a structured `CreativeConcept` (audience,
single_message, core_benefit, tone, visual_idea, proof_points) AND the customer-facing COPY
(headline / subheadline / cta / chips) built FROM that one message, in the BRAND'S language.

Hard rules enforced here:
  * LANGUAGE LOCK — for an Arabic brand, every visible copy field must be Arabic with ZERO
    Latin characters. A regex validator rejects any `[A-Za-z]`; we regenerate (stricter
    prompt) up to N times before giving up to a grounded Arabic fallback. (Brief Step B.)
  * AD COPY, not a mission statement — headline is 2–6 words selling a benefit. (Step D.)
  * Chips come ONLY from the concept's proof_points, in consumer language. (Step E.)
  * GROUNDED — the concept is conditioned on the scraped profile (facts) + the BrandCreativeDNA
    (tone/style). It never invents prices/claims; copy is brand-voice paraphrase (design domain).

Side-effect free (caller injected). Degrades: no caller → a grounded fallback concept from the
profile. Never raises for ordinary failures.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from pydantic import BaseModel, Field

_LATIN_RE = re.compile(r"[A-Za-z]")
# Arabic / Arabic-presentation ranges — to detect an Arabic brand.
_AR_RE = re.compile(r"[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]")


class CreativeConcept(BaseModel):
    """One campaign idea + the copy derived from it (all customer-facing fields in the
    brand's language)."""
    audience: str = ""
    single_message: str = ""
    core_benefit: str = ""
    emotional_tone: str = ""
    visual_idea: str = ""               # text-free scene for the image (brand design language)
    product_placement: str = ""         # where the featured product physically sits in that scene
    proof_points: list[str] = Field(default_factory=list)   # 2-3 -> become the chips
    headline: str = ""                  # 2-6 words, ad copy (NOT a mission statement)
    subheadline: str = ""               # optional single supporting line
    cta: str = ""                       # short verb
    language: str = "ar"                # 'ar' | 'en'
    note: Optional[str] = None
    # What the grounding gate CAUGHT and how it was handled — the transparency the audit
    # trail needs (so the proof shows the system worked, not just that the result is clean).
    # Each entry: {field, original_text, unsourced_claims:[kind], action: softened|dropped|
    # softened_to_fallback}. Empty when grounding is off or nothing was caught.
    remediation: list[dict] = Field(default_factory=list)


class _ConceptResponse(BaseModel):
    audience: str
    single_message: str
    core_benefit: str
    emotional_tone: str
    visual_idea: str
    product_placement: str = ""         # additive: permissive default keeps older callers valid
    proof_points: list[str]
    headline: str
    subheadline: str
    cta: str


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def _fv(profile: dict, key: str) -> str:
    v = (profile or {}).get(key)
    if isinstance(v, dict):
        v = v.get("value")
    return "" if v is None else str(v)


def brand_is_arabic(profile: dict) -> bool:
    """True when the brand should speak Arabic: an 'ar' site language, or Arabic script in
    its name / tagline / description."""
    langs = [str(x).lower() for x in ((profile or {}).get("languages") or [])]
    if any(l.startswith("ar") for l in langs):
        return True
    blob = " ".join(_fv(profile, k) for k in ("name", "tagline", "description"))
    return bool(_AR_RE.search(blob))


def has_latin(text: str) -> bool:
    return bool(_LATIN_RE.search(text or ""))


def _offering_names(profile: dict, limit: int = 8) -> list[str]:
    out = []
    for o in ((profile or {}).get("offerings") or []):
        n = o.get("name") if isinstance(o, dict) else o
        if n:
            out.append(str(n))
    return out[:limit]


def _dna_tone_lines(brand_dna: Any) -> str:
    if brand_dna is None:
        return ""
    parts = []
    for label, attr in (("Mood", "mood"), ("Signature", "signature_moves"),
                        ("Typography", "typographic_character")):
        v = (getattr(brand_dna, attr, "") or "").strip()
        if v:
            parts.append(f"{label}: {v}")
    return "; ".join(parts)


def _concept_clean(c: CreativeConcept, *, arabic: bool, ledger: Any = None) -> CreativeConcept:
    """Drop any chip that violates the language lock (Latin in an Arabic brand) OR — when a
    grounding ledger is supplied — carries an UNSOURCED falsifiable claim (a final
    deterministic safety so a fabricated chip never ships even if the regenerate loop
    didn't clear it)."""
    # CLAIM SAFETY BEFORE STYLE (reviewer 2026-07-26): a therapeutic verb about a cosmetic is
    # regulatory exposure, not copy flair — softened deterministically on every field, so a
    # model that ignores the prompt's ban still cannot ship «بيعالج».
    try:
        from poster.copy_rules import soften_therapeutic_claims
        for _f in ("headline", "subheadline", "cta", "single_message"):
            _t, _ch = soften_therapeutic_claims(getattr(c, _f, "") or "")
            if _ch:
                setattr(c, _f, _t)
        c.proof_points = [soften_therapeutic_claims(p)[0] for p in c.proof_points]
    except Exception:  # noqa: BLE001 — safety net failing must not kill the concept
        pass
    if arabic:
        c.proof_points = [p for p in c.proof_points if p and not has_latin(p)][:3]
    else:
        c.proof_points = [p for p in c.proof_points if p][:3]
    if ledger is not None:
        c.proof_points = [
            p for p in c.proof_points
            if not any(not v.sourced for v in ledger.audit_text(p))
        ]
    return c


def _grounding_problems(c: CreativeConcept, ledger: Any) -> list[str]:
    """Targeted regenerate feedback for every UNSOURCED falsifiable claim in the copy.

    Two truth domains: subjective puffery ('crafted with care', 'تجربة راقية') asserts
    nothing checkable and passes untouched; a falsifiable claim — a number/year, a
    certification/award, or a ranking/comparison (best / leading / first / only / الأقوى /
    الأكبر / الأول / الوحيد) — must resolve to real evidence or be SOFTENED. The feedback
    tells the model to remove the ranking/number/credential and keep the message (rewrite,
    not blind reject), and never to invent a replacement claim."""
    problems: list[str] = []
    # The SPINE (headline/sub/cta) drives regeneration — softening preserves the message.
    # A fabricated CHIP is surgically dropped in _concept_clean instead (keeps the spine).
    checks = [("headline", c.headline), ("subheadline", c.subheadline), ("cta", c.cta)]
    for label, text in checks:
        unsourced = [v for v in ledger.audit_text(text or "") if not v.sourced]
        if unsourced:
            toks = ", ".join(sorted({str(v.claim.token) for v in unsourced}))
            problems.append(
                f"the {label} \"{text}\" makes an UNVERIFIABLE claim ({toks}) that is NOT "
                f"supported by the brand's real facts below — SOFTEN it: remove the "
                f"ranking/number/credential and keep the core message; do NOT invent any "
                f"replacement number, certification, or superlative"
            )
    return problems


# ---------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------


def product_facts_block(*, name: str = "", description: str = "",
                        price: str = "", currency: str = "") -> str:
    """The featured product's OWN facts, for the copywriter.

    REVIEWER (2026-07-26): «مفيش أي إشارة لـ SPF 50+ ولا لتقنية UVMUNE… أهم selling point في
    المنتج ضايع» and «مفيش سعر… الـ conversion path مقطوع». The writer was handed the product's
    NAME and nothing else, so the only thing it could sell was the pharmacy.

    Everything here is VERBATIM from the product's own page — the name, the page's own
    description, and the price exactly as shown. A page with no price yields no price line: an
    invented number on a pharmacy ad is the worst kind of hallucination, so the absence is
    stated rather than filled."""
    name = str(name or "").strip()
    if not name:
        return ""
    lines = [
        "\n\nTHE FEATURED PRODUCT — these facts are VERBATIM from its own product page and are "
        "the ONLY product claims you may make:",
        f"- name: {name}",
    ]
    desc = str(description or "").strip()
    if desc:
        lines.append(f"- what its page says: {desc[:400]}")
    price = str(price or "").strip()
    cur = str(currency or "").strip()
    if price:
        lines.append(f"- price EXACTLY as shown: {(cur + ' ' + price).strip()}")
        lines.append("  You MAY name this price in the copy; write it exactly, never rounded, "
                     "never as an offer or discount unless the page says so.")
    else:
        lines.append("- no price is shown on the page: do NOT state, imply or invent one.")
    lines.append(
        "Lead with what makes THIS product different (its own technology, protection factor, "
        "actives or claim) — naming the pharmacy is not a product benefit.")
    lines.append(
        "If the name or page carries a SPEC (SPF50+, UVMUNE, a dosage, a strength), that spec "
        "MUST appear in the copy language as one of the proof chips — it is the product's "
        "biggest number and it may not live only in the pack's fine print.")
    lines.append(
        "CLAIM SAFETY: this is a COSMETIC ad. Never write therapeutic verbs (يعالج / يشفي / "
        "treats / cures) about it; say what the pack says (يقلل ظهور / يحمي / anti-dark-spots).")
    return "\n".join(lines)


def _placement_rule(featured_product: Optional[str]) -> str:
    """Ask the concept WHERE the featured product physically belongs in the scene it just invented.

    Owner (2026-07-20): "البوستر بيحط علبة الدوا في أي مكان وبأي شكل مش مهتم هو ده مكان منطقي ولا
    لأ." The renderer now enforces placement PHYSICS, but the creative decision — which support,
    which moment, whose hand — belongs with the concept that authored the scene. Same model call,
    so no extra spend.

    Universal by construction (owner's D-8 correction): a box rests on a counter, but shoes sit on
    the ground, a car on a road, a sofa in a room — so the field asks for the NATURAL SUPPORT of
    this product's own kind, never "a surface".
    """
    if not (featured_product or "").strip():
        return ""
    return (
        "- product_placement: ONE sentence in ENGLISH saying exactly where the featured product "
        "physically sits inside your visual_idea, and why that spot is true to life. Name (a) the "
        "NATURAL SUPPORT that carries a product of ITS kind — a counter, a shelf, the ground, a "
        "road, a room's floor, a hand mid-use, worn on the body — never floating and never a blank "
        "studio void; (b) the surrounding context that makes the moment real; (c) the person's "
        "relation to it if a person is in the scene (holding it, reaching for it, using it). Choose "
        "the place where this kind of product is genuinely used, kept or displayed.\n"
    )


def _fallback_concept(profile: dict, arabic: bool) -> CreativeConcept:
    """A grounded concept when there's no caller / all retries fail — copy comes from the
    profile so it is real, and (for an Arabic brand) Arabic-only fields are preferred."""
    offers = _offering_names(profile, 3)
    tagline = _fv(profile, "tagline")
    if arabic:
        # Never ship Latin on an Arabic brand: prefer an Arabic tagline, else a safe generic
        # Arabic hook — and keep the headline DISTINCT from the proof line (the offering),
        # so a fallback never renders headline == subheadline (MEASURED weak run on WE).
        offers = [o for o in offers if not has_latin(o)]
        headline = tagline if (tagline and not has_latin(tagline)) else "خدمات تقربك أكتر"
        sub = offers[0] if offers else ""
        if headline == sub:
            headline = "خدمات تقربك أكتر"
    else:
        headline = tagline or _fv(profile, "name") or ""
        sub = offers[0] if offers else ""
    return CreativeConcept(
        audience=_fv(profile, "audience_type"),
        single_message=headline,
        core_benefit="",
        visual_idea="",
        proof_points=offers,
        headline=headline,
        subheadline=sub,                       # the proof line — kept DISTINCT from headline
        cta=("تسوق الآن" if arabic else "Learn more"),
        language=("ar" if arabic else "en"),
        note="fallback concept (no caller or language-lock retries exhausted)",
    )


def build_creative_concept(
    profile: dict[str, Any],
    *,
    caller: Any = None,
    brand_dna: Any = None,
    variation: Optional[dict] = None,
    max_retries: int = 2,
    enforce_grounding: bool = False,
    arabic: Optional[bool] = None,
    research: Any = None,
    trend_context: Optional[str] = None,
    raw_facts: Optional[str] = None,
    featured_product: Optional[str] = None,
    product_facts: Optional[str] = None,
) -> CreativeConcept:
    """One coherent campaign concept + brand-language copy. Arabic brands -> Arabic copy with
    ZERO Latin (validated + regenerated). Never raises.

    `arabic` (optional): the OWNER's explicit output-language choice — True forces Arabic
    copy (with the full zero-Latin lock), False forces English; None (default) infers from
    the brand as before. Language is a DESIGN choice (two truth domains); the facts inside
    the copy stay Ledger-gated regardless of language.

    `enforce_grounding` (opt-in; ON in the live pipeline): every falsifiable claim in the
    generated copy must trace to the profile's real evidence via the Evidence Ledger, else
    the line is regenerated (softened) — capped by `max_retries`, then a grounded fallback.
    Off by default so the language-lock layer's unit contract is unchanged."""
    if arabic is None:
        arabic = brand_is_arabic(profile)
    ledger = None
    if enforce_grounding:
        try:
            from grounding import EvidenceLedger, make_subject_judge
            # When web RESEARCH is supplied its sourced facts join the evidence, so
            # a research-derived claim in the copy RESOLVES instead of being blanked.
            research_dump = None
            if research is not None:
                research_dump = (research.model_dump()
                                 if hasattr(research, "model_dump") else research)
            # C2 residual: a CHEAP (Flash) semantic judge for the ambiguous "same number/
            # superlative, different SUBJECT" case ("100 gifts" vs "100 stores"). Fires only
            # on token-disjoint subjects and is cached, so it costs a few Flash calls at most;
            # any failure -> the gate stays lenient (never blocks a real claim).
            judge = None
            try:
                from business_profile.llm import default_caller
                judge = make_subject_judge(default_caller(strong=False))
            except Exception:  # noqa: BLE001 — no judge -> deterministic/lenient gate
                judge = None
            ledger = EvidenceLedger.from_profile(profile, research=research_dump,
                                                 subject_judge=judge)
        except Exception:  # noqa: BLE001 — grounding must never break copy generation
            ledger = None
    if caller is None:
        return _fallback_concept(profile, arabic)

    name = _fv(profile, "name") or "the brand"
    desc = _fv(profile, "description")
    tagline = _fv(profile, "tagline")
    offers = _offering_names(profile, 8)
    # FIX-C3: the brand's evidence-derived country conditions the CONCEPT itself (the
    # visual_idea used to be written with no market context -> generic-Western scenes).
    try:
        from poster.locale import country_of
        country = country_of(profile)
    except Exception:  # noqa: BLE001
        country = ""
    tone_block = _dna_tone_lines(brand_dna)
    # CS-04 (D-8): the copy register follows the brand's EVIDENCED country, not a hardcoded
    # 'Egyptian Arabic' for every Arabic brand (a Saudi brand was commissioned in Masri). Same
    # resolver the content calendar uses (P-04) — now the single country->dialect map in
    # poster.locale, keyed on `country` which is already resolved at :252 above.
    from poster.locale import arabic_dialect_for
    lang_name = arabic_dialect_for(profile) if arabic else "English"
    vary = ""
    if variation:
        vary = (f"\nThis run's creative angle: {variation.get('mood','')}, "
                f"{variation.get('energy','')}. Pick a DIFFERENT single_message angle than an "
                f"obvious one so repeated runs differ.")
        # Vary the WRITING itself (owner: the copy kept one fixed hook+proof formula):
        # a per-run rhetorical FORM + VOICE. Design-domain — facts stay Ledger-gated.
        try:
            from poster.variation import copy_style_cue
            cue = copy_style_cue(variation)
            if cue:
                vary += "\n" + cue
        except Exception:  # noqa: BLE001
            pass

    # FRESH sourced raw material (web research): real facts the homepage may not carry,
    # each with its source — rotates the copy's CONTENT between runs, not just its look.
    research_block = ""
    facts = list(getattr(research, "facts", None) or []) if research is not None else []
    if facts:
        lines = []
        for f in facts[:6]:
            text = str(getattr(f, "text", "") or (f.get("text") if isinstance(f, dict) else "")).strip()
            src = str(getattr(f, "source_url", "") or (f.get("source_url") if isinstance(f, dict) else "")).strip()
            if text:
                lines.append(f"- {text}" + (f" (source: {src})" if src else ""))
        if lines:
            research_block = ("\nFRESH SOURCED FACTS from live web research (REAL — you may "
                              "build the message on any of them):\n" + "\n".join(lines) + "\n")

    # Current cultural/market TRENDS to optionally RIDE (H7). Inspiration only — NOT facts:
    # a trend can shape the ANGLE, but every claim in the copy still passes the grounding
    # gate below, so riding a trend can never license a fabricated number/superlative.
    trend_block = ""
    if trend_context and str(trend_context).strip():
        trend_block = ("\nCURRENT TRENDS you MAY tie the concept to WHERE IT GENUINELY FITS the "
                       "brand (inspiration only — do NOT force it, and do NOT invent any fact, "
                       "price or claim to match a trend; the proof must stay REAL):\n"
                       + str(trend_context).strip() + "\n")

    lang_rule = (
        "EVERY customer-facing field (headline, subheadline, cta, proof_points) MUST be in "
        f"{lang_name}, in the brand's voice. ABSOLUTELY NO Latin letters/words in those fields "
        "(no English, no transliteration). Do NOT translate literally — write native, punchy ad copy."
        if arabic else
        f"Write all copy in {lang_name}, in the brand's voice."
    )

    from poster.contracts import CRAFT_CONTRACT
    system = (
        CRAFT_CONTRACT + "\n\n"
        "From the brand's real facts, craft ONE coherent campaign concept for a single poster, "
        "then derive the copy FROM it so the headline, the chips and the visual all express the "
        "SAME idea. The concept's distinctiveness must come from THIS brand's specific offer and "
        "proof, not from a generic 'premium ad' template — two different businesses must never "
        "produce interchangeable concepts.\n"
        "THE BAR — the STRANGER TEST: a first-time viewer who has never heard of this brand must, "
        "in one glance, be able to say FOUR things: (1) WHO it is; (2) WHAT THEY DO — the category "
        "in plain words (e.g. 'a government tech-training institute', 'a hair-care brand'); (3) the "
        "SPECIFIC thing on offer — a NAMED service/product, not a mood; (4) ONE real proof. A poster "
        "that sells only a FEELING ('your unfair advantage') and never says what the brand actually "
        "DOES has FAILED the test — anchor it in the concrete offering instead.\n"
        f"{lang_rule}\n"
        "Fields:\n"
        "- audience: who this poster talks to.\n"
        "- single_message: ONE message only (the spine). It MUST be anchored on a CONCRETE, NAMED "
        "offering/service from the facts below (or, if none is given, the single STRONGEST concrete "
        "fact) — never an abstract slogan that any brand could run.\n"
        "- core_benefit: the benefit to the customer.\n"
        "- emotional_tone: the feeling.\n"
        "- visual_idea: a TEXT-FREE scene that shows the REAL WORLD of THIS specific service — the "
        "actual context a customer or graduate is in (for a training institute: real learners "
        "coding/building in a lab, a graduate at work; for a product: the product in real use). "
        "GROUND it in this brand's world, not a stock cliché. FORBIDDEN: a random model staring at "
        "the camera holding a laptop; ANY text, logo, UI, or a face/photo rendered INSIDE a screen "
        "or sign (the image tool garbles these into artefacts); a scene generic enough to sell any "
        "company. Write visual_idea in ENGLISH — a production brief, no words in the image."
        + (f" The scene is set in {country} with authentic local people from {country} — "
           f"say so explicitly in the visual_idea." if country else "") + "\n"
        + _placement_rule(featured_product)
        + str(product_facts or "")
        + "- proof_points: 2-3 SHORT consumer-language points that support single_message (these "
        "become on-poster chips). Each must serve the message — drop anything unrelated. NOT a "
        "dump of internal/B2B product names.\n"
        "BRAND-COHERENCE (HARD RULE — owner caught 'بطل مذاكرة' shipped for an education "
        "institute): the hook must NEVER mock, negate or tell people to stop the brand's own "
        "category or core activity — an education brand never disparages studying, a restaurant "
        "never says stop eating, a gym never mocks training. Tension is allowed ONLY against a "
        "PAIN the brand solves (waiting, cost, theory-without-practice, complexity) and must be "
        "phrased so the brand's own activity stays desirable.\n"
        "- CREATIVE BAR (البند 4 — owner: 'مش كريتيف'): start from a HUMAN INSIGHT — a real "
        "moment, tension or desire from the brand's actual pain-points/reviews — not from the "
        "product's spec sheet. Banned shapes: 'جرب X الآن', 'X الأفضل في Y', any line a "
        "competitor could run unchanged, and any generic benefit platitude. The idea should "
        "make a stranger FEEL something specific before it informs them. Stay 100% inside the "
        "real facts below — creativity is in the ANGLE, never in inventing claims.\n"
        "- headline: AD COPY — 2 to 6 words, an ENTICING hook that sells the core_benefit and "
        "stops a scroll. NOT a company mission statement, NOT generic corporate values, NOT a "
        "category platitude a competitor could run unchanged ('control in your hand', 'your unfair "
        "advantage' are too generic). The headline TOGETHER WITH the subheadline must make a "
        "stranger understand WHAT this brand does and the SPECIFIC service — a clever hook that "
        "hides what's being sold has failed.\n"
        "- subheadline: THE دليل (PROOF) — a SHORT concrete reason-to-believe that backs the "
        "headline, sitting right under it. Lead with the brand's STRONGEST real fact and NAME the "
        "service/category so 'what they do' is unmistakable. It MUST carry at least one of: a real "
        "named service, a capability the customer can do, 'مجاناً/ببلاش', 'أول/لأول مرة', or a "
        "concrete number — derived from the brand's real facts below. Never empty, never vague.\n"
        "- cta: an ACTION the reader can take — a verb + destination (e.g. 'نزّل تطبيق ...', "
        "'فعّل دلوقتي', 'جرّب مجاناً'). It must NOT just restate the headline as a synonym.\n"
        "Ground everything in the facts below; never invent prices, numbers, or guarantees that "
        "are not supported by the facts (the proof must be REAL, not fabricated).\n"
        "SELF-CHECK before returning (the STRANGER TEST): does a first-time viewer learn WHO + WHAT "
        "THEY DO + the SPECIFIC service + one REAL proof? Is the visual the real world of THIS "
        "service, not a stock person? If any is missing, rewrite."
    )
    # CS-14 (P-72 pattern): scraped fields are UNTRUSTED text. Neutralize the characters that
    # let a page's own copy break out of its slot or counterfeit prompt structure — the same
    # 3-char neutralization the evidence pack applies (" -> ', brackets -> parens), plus a
    # newline-collapse for inline fields. Semantic injection stays caught downstream by the
    # spine/ledger gates.
    def _neutral(v: str, *, inline: bool = True) -> str:
        v = str(v or "").replace('"', "'").replace("[", "(").replace("]", ")")
        return " ".join(v.split()) if inline else v

    name = _neutral(name)
    tagline = _neutral(tagline)
    desc = _neutral(desc)
    offers = [_neutral(o) for o in (offers or [])]
    raw_facts_block = (
        "\nREAL PRODUCT-PAGE FACTS (verbatim from the brand's own page — the owner's "
        "raw-data-first rule; ground the concept in THESE specifics):\n"
        + _neutral(raw_facts[:1800], inline=False) + "\n") if raw_facts and raw_facts.strip() else ""
    user = (
        f"Brand: {name}\n"
        + (f"Country/market: {country}\n" if country else "")
        + (f"Tagline: {tagline}\n" if tagline else "")
        + (f"What they do: {desc[:400]}\n" if desc else "")
        + (f"FEATURED PRODUCT/SERVICE — the ENTIRE concept is about THIS one item, the user "
           f"picked it explicitly: {_neutral(featured_product)}. The headline or subheadline "
           f"MUST name it (its real name; customer wording allowed) and must NOT write about "
           f"a different product.\n" if featured_product else "")
        + (f"Real offerings (raw — may be internal jargon; render them in the words a real "
           f"customer would use, but never change what they factually ARE): {', '.join(offers)}\n"
           if offers else "")
        + (f"Brand tone/style (from its real creatives): {tone_block}\n" if tone_block else "")
        + raw_facts_block
        + research_block
        + trend_block
        + vary
        + "\nReturn the structured concept."
    )

    remediation: list[dict] = []
    caught_spine: dict[str, dict] = {}   # field -> {original_text, unsourced_claims} (first seen)

    def _to_fallback() -> list[dict]:
        """Flush every caught spine claim as 'softened_to_fallback' (shipped grounded copy)."""
        return remediation + [
            {**rec, "action": "softened_to_fallback",
             "note": "claim could not be sourced; shipped grounded fallback copy"}
            for rec in caught_spine.values()
        ]

    strict_suffix = ""
    for attempt in range(max_retries + 1):
        try:
            resp, _u = caller(system + strict_suffix, user, _ConceptResponse,
                              group_name="poster_concept_brief")
        except Exception as exc:  # noqa: BLE001
            return _fallback_concept(profile, arabic).model_copy(
                update={"note": f"concept call failed ({type(exc).__name__})",
                        "remediation": _to_fallback()})

        c = CreativeConcept(
            audience=resp.audience.strip(), single_message=resp.single_message.strip(),
            core_benefit=resp.core_benefit.strip(), emotional_tone=resp.emotional_tone.strip(),
            visual_idea=resp.visual_idea.strip(),
            product_placement=(getattr(resp, "product_placement", "") or "").strip(),
            proof_points=[p.strip() for p in (resp.proof_points or []) if p.strip()],
            headline=resp.headline.strip(), subheadline=resp.subheadline.strip(),
            cta=resp.cta.strip(), language=("ar" if arabic else "en"),
        )
        # COPY CRITIC gates (review BEFORE it's rendered): language lock + the دليل/proof
        # gate + an actionable CTA. Any failure -> regenerate with targeted feedback.
        problems: list[str] = []
        if arabic and (has_latin(c.headline) or has_latin(c.cta) or has_latin(c.subheadline)):
            problems.append("there were Latin characters — write headline, subheadline, cta and "
                            "proof_points 100% in Arabic, not one Latin letter")
        if not c.subheadline.strip():
            problems.append("subheadline (the دليل/PROOF line) was empty — add a SHORT concrete "
                            "proof/offer/feature that backs the headline")
        if c.cta.strip() and c.cta.strip() == c.headline.strip():
            problems.append("the cta just repeated the headline — make it an action + "
                            "destination (e.g. download the app / activate now)")
        # GROUNDING gate (opt-in): a falsifiable claim with no real source is softened. Record
        # what is caught (first fabricated form per field) for the audit trail's remediation log.
        if ledger is not None:
            for label, text in (("headline", c.headline), ("subheadline", c.subheadline),
                                ("cta", c.cta)):
                uns = [v for v in ledger.audit_text(text or "") if not v.sourced]
                if uns:
                    caught_spine.setdefault(label, {
                        "field": label, "original_text": text,
                        "unsourced_claims": sorted({v.claim.kind for v in uns})})
            problems.extend(_grounding_problems(c, ledger))
        if not problems:
            pre_chips = list(c.proof_points)
            c = _concept_clean(c, arabic=arabic, ledger=ledger)
            # The spine survived clean -> the claims caught earlier were softened away.
            for rec in caught_spine.values():
                remediation.append({**rec, "action": "softened",
                                    "note": "rewritten to remove the unverifiable claim"})
            if ledger is not None:   # record GROUNDING chip drops (a fabricated proof removed)
                for chip in pre_chips:
                    if chip not in c.proof_points:
                        uns = sorted({v.claim.kind for v in ledger.audit_text(chip)
                                      if not v.sourced})
                        if uns:
                            remediation.append({"field": "chip", "original_text": chip,
                                                "unsourced_claims": uns, "action": "dropped",
                                                "note": "fabricated proof chip removed"})
            c.remediation = remediation
            return c
        strict_suffix = ("\n\nREGENERATE and fix ALL of these: " + "; ".join(problems) +
                         ". While fixing these, the four-part STRANGER TEST still must hold — "
                         "don't fix one line and break the glance.")

    # Retries exhausted -> grounded fallback (never ship Latin; keep a proof from the offerings).
    fb = _fallback_concept(profile, arabic)
    if not fb.subheadline:
        chips = [p for p in fb.proof_points if p]
        fb.subheadline = chips[0] if chips else fb.subheadline
    return fb.model_copy(update={"note": "copy-critic retries exhausted; grounded fallback",
                                 "remediation": _to_fallback()})
