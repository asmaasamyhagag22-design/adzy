> **تنبيه أمانة (2026-07-19):** هذا الملف لقطة قديمة — الأعداد والأنكورات (#L) فيه غير موثوقة بعد إعادة هيكلة البرومبتات (GROUNDING/CRAFT/VERBATIM contracts + reel/marketing_intel/ads الجديدة غير مُدرجة). المصدر الموثوق هو الكود: `grep -rn "_SYSTEM\|system_prompt\|CONTRACT" --include=*.py`. مولّد أوتوماتيكي للملف = تذكرة مفتوحة.

# Project Prompt Catalog

> **Every LLM / image / vision prompt in this repo, in one place.** Generated from an automated sweep of the codebase, then curated. For each prompt: where it lives (`file:line`, click to open), which model receives it, what kind of prompt it is, what it does, and a short excerpt. **The source file is always the source of truth** — most prompts are *assembled* at call time (f-strings, per-vertical fragments, grounding facts spliced in), so the excerpts below are representative, not byte-exact.

**the prompts (count varies — see the honesty note)** across **5 subsystems.**

## How prompting works in this system

Two separate **truth domains**, on purpose:

- **The LLM designs** — it writes concepts, copy, scene plans, strategies. Creative freedom lives here.
- **Code renders + validates** — the Evidence Ledger and deterministic gates decide what is allowed to ship. Facts are never taken on the model's word.

So a single deliverable (a poster, a reel) passes through *several* prompts of different kinds:

| Kind | Role |
|------|------|
| **system** | Sets the model's job + hard rules for a whole call |
| **user** | Supplies the grounding facts for one specific call |
| **template fragment** | A per-vertical / per-run piece spliced into a larger prompt |
| **feedback / instructions** | Regeneration feedback appended on a retry when a gate fails |
| **retrieval intent** | An embedded query string used to *select* evidence (RAG), not sent as chat |

Models in play: **Gemini 2.5** is the sole text/reasoning provider — **Pro** for the heavy reasoning (reel director, domain-schema, review-theme, extraction/strategy/copy) and **Flash** for cheap judges; **Gemini image + vision** models for poster render and read-back QA; **Veo 3.1** for the reel video; **Imagen 4 Ultra** for classic poster image generation; **Gemini `text-embedding-004`** for RAG; **OpenAI** callers remain only as a drop-in fallback, plus `gpt-audio` for reel voice-over. All text calls go through one `Caller` protocol, so any provider is swappable and tests inject a `MockCaller`.

## Contents

- [1. Business Profile — evidence-grounded extraction](#1-business-profile--evidence-grounded-extraction) · 9 prompts
- [2. Poster — concept, copy, image, and the fidelity gate](#2-poster--concept-copy-image-and-the-fidelity-gate) · 25 prompts
- [3. Reel — Gemini creative director, Veo render, scene QA](#3-reel--gemini-creative-director-veo-render-scene-qa) · 9 prompts
- [4. Competitor & Strategy — themes, TOWS, discovery, calendar](#4-competitor--strategy--themes-tows-discovery-calendar) · 9 prompts
- [5. Grounding — the same-subject judge](#5-grounding--the-same-subject-judge) · 2 prompts

---

## 1. Business Profile — evidence-grounded extraction

Turns scraped page blocks into a structured brand profile. Every extraction call shares one strict system prompt that forbids any value without a verbatim `block_id` citation — this is the first grounding wall. Four grouped user prompts (identity / offerings / positioning / trust) each get only the RAG-selected evidence for that group.

### GROUNDING_CONTRACT

- **Where:** [`business_profile/llm/prompts.py`:29](https://github.com/asmaasamyhagag22-design/marketing-/blob/main/business_profile/llm/prompts.py#L29)
- **Kind:** system (canonical block, injected into SYSTEM_PROMPT)
- **Model:** shared across all four groups (Gemini 2.5 default / OpenAI fallback)
- **Does:** THE single source of the grounding rules — stated once and injected into SYSTEM_PROMPT instead of nine drifting copies. Five non-negotiable rules (evidence-bound + null-when-unsupported, verbatim quotes incl. Arabic, invent-nothing-falsifiable, no fabricated ids, language integrity) + a positive coverage line + the lifted anti-cliché specificity rule.

> [GROUNDING CONTRACT — non-negotiable, applies to every value you emit] 1. Evidence-bound: every value must be supported by a block_id present EXACTLY in the supplied evidence. No support -> value = null. Never guess. 2. Verbatim quotes: character-exact substring of ONE real block — including Arabic diacritics/spelling. 3. Invent nothing falsifiable... 4. No fabricated ids. 5. Language integrity... Coverage of the real, evidenced facts is the goal... Reject the generic... Specificity is the filter.

### SYSTEM_PROMPT

- **Where:** [`business_profile/llm/prompts.py`:48](https://github.com/asmaasamyhagag22-design/marketing-/blob/main/business_profile/llm/prompts.py#L48)
- **Kind:** system
- **Model:** Gemini 2.5 (GeminiCaller, default) or OpenAI gpt-4o-mini (OpenAICaller) via run_llm_extraction
- **Does:** Shared system prompt for all four grouped extraction calls; embeds GROUNDING_CONTRACT verbatim + OPERATING NOTES (may-emit-inferred-if-cited, quotes under 200 chars).

> You are a business profile extractor... [GROUNDING CONTRACT ...] ... OPERATING NOTES: - You may emit a value INFERRED from copy... only if you cite the blocks... - Keep quotes short (under 200 characters)...

### build_identity_prompt

- **Where:** [`business_profile/llm/prompts.py`:58](https://github.com/asmaasamyhagag22-design/marketing-/blob/main/business_profile/llm/prompts.py#L58)
- **Kind:** user
- **Model:** Gemini 2.5 / OpenAI via Caller (group='identity', IdentityResponse)
- **Does:** Builds the identity-group user prompt (inherits GROUNDING_CONTRACT): tagline (COPY verbatim, don't compose), description (COMPOSE from evidence, every claim traces to a block_id), and one hardened category from the fixed enum ('other' only when nothing fits, never as a shortcut for 'unsure').

> Extract three identity fields (the GROUNDING CONTRACT governs every value): 1. tagline... copied VERBATIM if one exists; null if none. Copy, don't compose. 2. description: 2-3 sentences you COMPOSE from evidenced facts... Compose, but grounded. 3. category: choose exactly ONE from {enum}... use 'other' only when no listed category genuinely applies.

### build_offerings_prompt

- **Where:** [`business_profile/llm/prompts.py`:276](https://github.com/asmaasamyhagag22-design/marketing-/blob/main/business_profile/llm/prompts.py#L276)
- **Kind:** user
- **Model:** Gemini 2.5 / OpenAI via Caller (group='offerings', OfferingsResponse)
- **Does:** Builds the offerings-group user prompt (inherits GROUNDING_CONTRACT): extract offerings[] (name/short_description/price_text/evidence) + pricing_posture, with a category-aware item cap and the swapped catalog-SHAPE guidance. The prose FORBIDDEN-CLAIMS block was DROPPED (redundant with contract rule 3 + the UNSUBSTANTIATED_CLAIM_TOKENS validator); the own-name ban + honest-empty stay.

> Extract the concrete things this business offers... Prefer SPECIFIC named offerings; if only broad categories exist capture at most 1-2; if nothing concrete is listed, return an EMPTY list — honest-empty beats padding. NEVER emit the business's OWN NAME... Then apply the structural selection rules for THIS catalog shape: {shape} ... PRICING POSTURE: choose one of budget, mid, premium, unknown.

### _CATALOG_SHAPES + _CATEGORY_TO_SHAPE

- **Where:** [`business_profile/llm/prompts.py`:94](https://github.com/asmaasamyhagag22-design/marketing-/blob/main/business_profile/llm/prompts.py#L94)
- **Kind:** template fragment
- **Model:** Gemini 2.5 / OpenAI (one shape injected into build_offerings_prompt, chosen by `_shape_for(rules_category)`)
- **Does:** REPLACES the old 18 per-vertical keys with 4 UNIVERSAL catalog shapes + a category→shape map (process.md rule 5: config keyed by a universal signal, universal default, never vertical names as logic). Shapes: `broad_catalog` (ecommerce/retail — DEPARTMENTS-FIRST, SKU≠offering, breadth), `named_menu` (restaurant/cafe), `programs` (clinic/hospital/education/agency/fitness/gov/… — named service lines), `default` (beauty/other/None/unknown — the adaptive fallback with store-detection). An unknown/None category resolves to `default` BY CONSTRUCTION, so the old fail-open None-key bug is impossible.

> broad_catalog: DEPARTMENTS FIRST... a name with pack size/weight/count/flavor is a SKU — NEVER an offering... breadth over depth... | programs: NAMED SERVICES, PROGRAMS or SPECIALTIES... capture the specific name, not the bare category... | named_menu: the SPECIFIC named items the page lists (verbatim from the menu)... | default: ...IF THE EVIDENCE SHOWS A PRODUCT STORE, apply the broad_catalog HARD RULES.

### build_positioning_prompt

- **Where:** [`business_profile/llm/prompts.py`:346](https://github.com/asmaasamyhagag22-design/marketing-/blob/main/business_profile/llm/prompts.py#L346)
- **Kind:** user
- **Model:** Gemini 2.5 / OpenAI via Caller (group='positioning', PositioningResponse)
- **Does:** Builds the positioning-group user prompt: extract audience_type (enum), audience_signals[], value_propositions[], tone_of_voice (enum), and other_unique_insights[].

> EXTRACT: 1. audience_type: one of B2C, B2B, B2G, mixed, unknown. 2. audience_signals: short phrases... 3. value_propositions: the distinctive promises... Skip generic claims like 'high quality' unless paired with specifics. 4. tone_of_voice... 5. other_unique_insights: a CATCH-ALL for a genuinely UNIQUE competitive edge... each must be a CONCRETE FACT stated on the page (cite block_id + verbatim quote).

### build_trust_prompt

- **Where:** [`business_profile/llm/prompts.py`:373](https://github.com/asmaasamyhagag22-design/marketing-/blob/main/business_profile/llm/prompts.py#L373)
- **Kind:** user
- **Model:** Gemini 2.5 / OpenAI via Caller (group='trust', TrustResponse)
- **Does:** Builds the trust-group user prompt: extract trust_signals[] (certifications, awards, clients, tenure, credentials, testimonials) and service_areas[].

> EXTRACT: 1. trust_signals: concrete proof points that build credibility... certifications... awards... notable clients or partners... tenure... team credentials... testimonials (only when a real quote with attribution is present)... Cap at 8 items. (The old "Skip vague self-praise" was removed — the contract's specificity rule now covers it globally.) 2. service_areas: geographic regions the business explicitly serves... Skip mailing addresses — only places they say they SERVE.

### _GROUP_QUERIES

- **Where:** [`business_profile/llm/rag.py`:64](https://github.com/asmaasamyhagag22-design/marketing-/blob/main/business_profile/llm/rag.py#L64)
- **Kind:** retrieval intent
- **Model:** Gemini text-embedding-004 (embed_texts) — semantic retrieval query, not a chat prompt
- **Does:** Per-group retrieval intent strings embedded and matched against block vectors to select the top-K relevant evidence blocks for each extraction group (identity/offerings/positioning/trust).

> identity: 'What this business is and does: its name, tagline, mission...'; offerings: 'The specific products, services, menu items, courses, programs, packages... including their names, descriptions and prices'; positioning: 'What makes this business distinctive: its value propositions...'; trust: 'Proof of credibility and trust: certifications, awards, accreditations...'

### _PROMPT

- **Where:** [`business_profile/domain_schema.py`:116](https://github.com/asmaasamyhagag22-design/marketing-/blob/main/business_profile/domain_schema.py#L116)
- **Kind:** user
- **Model:** Gemini 2.5 Pro (gemini-2.5-pro) via the shared Caller (default_caller(strong=True)) in build_domain_profile
- **Does:** Standalone prompt asking Gemini 2.5 Pro to (1) name the business's specific vertical and (2) design + fill 5-8 domain-specific marketing attributes, each with a verbatim evidence_quote, returned as strict JSON. Grounding is code-enforced afterward.

> You are designing a MARKETING-INTELLIGENCE schema for one specific business... Do TWO things: 1. Identify its SPECIFIC vertical — finer than a generic category... 2. Propose the 5-8 domain-specific ATTRIBUTES that matter MOST... Use ONLY the evidence. If an attribute can't be supported by a quote, OMIT it... Return ONLY a JSON object, no prose, no markdown fences.

---

## 2. Poster — concept, copy, image, and the fidelity gate

Two truth domains: an LLM WRITES the concept + brand copy (gated against the Evidence Ledger), then a Gemini IMAGE model RENDERS a finished poster that must print our copy verbatim. A vision OCR read-back + an art-director QA pass reject any render that garbles text, invents labels, or paints the brand name onto objects.

**Two shared contracts** (`poster/contracts.py`, Batch 2 — stated once, injected across the chain):
- **`CRAFT_CONTRACT`** — the copy craft BAR ("would this brand actually ship it," not "does it sound like an ad"). Bans the empty aesthetic primers (premium / world-class / award-winning / cutting-edge / …) that made every brand's copy converge; states the philosophy: facts gated, form free. Injected into `build_creative_concept (system)` (replaced the "senior creative director" persona).
- **`VERBATIM_RENDER_CONTRACT`** — the frozen-strings rule (render/read the approved copy character-for-character; Arabic dot-counts ت=2/ث=3, ة vs ه). Shared by `build_oneshot_prompt` (the renderer) and `read_rendered_text` (the OCR reader) so both speak one language.

### build_creative_concept (system)

- **Where:** [`poster/concept.py`:297](https://github.com/asmaasamyhagag22-design/marketing-/blob/main/poster/concept.py#L297)
- **Kind:** system
- **Model:** Injected text caller — GeminiCaller (gemini-2.5-pro/flash) or OpenAICaller; group_name="poster_concept_brief", structured output _ConceptResponse
- **Does:** Main Creative-Concept brief: turn brand facts into ONE campaign concept + brand-language copy (headline/sub/cta/proof chips) that all express the same message. Opens with `CRAFT_CONTRACT` (not a director persona); the falsifiable **STRANGER TEST** (WHO / WHAT-THEY-DO / SPECIFIC offer / ONE proof, in a glance) is the positive engine; an anti-convergence line forbids interchangeable concepts across brands.

> You are a senior advertising CREATIVE DIRECTOR. From the brand's real facts, craft ONE coherent campaign concept... THE BAR — the STRANGER TEST: a first-time viewer... must, in one glance, say FOUR things: (1) WHO it is; (2) WHAT THEY DO; (3) the SPECIFIC thing on offer; (4) ONE real proof.

### build_creative_concept (user)

- **Where:** [`poster/concept.py`:344](https://github.com/asmaasamyhagag22-design/marketing-/blob/main/poster/concept.py#L344)
- **Kind:** user
- **Model:** Same caller as system prompt (Gemini/OpenAI), group_name="poster_concept_brief"
- **Does:** Supplies the grounding facts (name, tagline, description, offerings, brand tone, fresh web-research facts, trend context, per-run variation cue) for the concept call.

> Brand: {name}\nTagline: {tagline}\nWhat they do: {desc}\nReal offerings (raw, may be internal jargon): ...\nBrand tone/style (from its real creatives): ...\n[research_block][trend_block][vary]\nReturn the structured concept.

### build_creative_concept (strict_suffix regenerate loop)

- **Where:** [`poster/concept.py`:426](https://github.com/asmaasamyhagag22-design/marketing-/blob/main/poster/concept.py#L426)
- **Kind:** feedback / instructions
- **Model:** Same concept caller (Gemini/OpenAI), re-sent as system+strict_suffix
- **Does:** Copy-critic regeneration feedback appended on retry when copy fails language-lock / empty-proof / CTA-repeats-headline checks.

> REGENERATE and fix ALL of these: <problems> . — e.g. 'there were Latin characters — write headline, subheadline, cta and proof_points 100% in Arabic'; 'subheadline (the دليل/PROOF line) was empty'; 'the cta just repeated the headline'.

### _grounding_problems

- **Where:** [`poster/concept.py`:147](https://github.com/asmaasamyhagag22-design/marketing-/blob/main/poster/concept.py#L147)
- **Kind:** feedback / instructions
- **Model:** Feedback fragment merged into the concept regenerate call (Gemini/OpenAI)
- **Does:** Targeted regenerate feedback for every UNSOURCED falsifiable claim (number/year/certification/superlative) in headline/sub/cta — tells the model to SOFTEN, not invent.

> the {label} "{text}" makes an UNVERIFIABLE claim ({toks}) that is NOT supported by the brand's real facts... SOFTEN it: remove the ranking/number/credential and keep the core message; do NOT invent any replacement number, certification, or superlative

### build_oneshot_prompt

- **Where:** [`poster/oneshot.py`:66](https://github.com/asmaasamyhagag22-design/marketing-/blob/main/poster/oneshot.py#L66)
- **Kind:** template fragment
- **Model:** Gemini image model (ONESHOT_IMAGE_MODEL, default gemini-3.1-flash-image-preview) via generate_oneshot_poster on Vertex location=global
- **Does:** One-shot image design brief: instruct a Gemini IMAGE model to compose a complete designed poster (layout+typography+graphics) rendering OUR gated copy VERBATIM, with real logo/product photos as props and zero invented text.

> Design a COMPLETE, premium social-media advertising poster (portrait 4:5, 1080x1350)... RENDER THIS TEXT — EXACTLY, character for character, no word added, removed, translated or 'improved'... ABSOLUTELY NO other text... ONLY the exact lines specified above.

### read_rendered_text (system)

- **Where:** [`poster/oneshot.py`:235](https://github.com/asmaasamyhagag22-design/marketing-/blob/main/poster/oneshot.py#L235)
- **Kind:** system
- **Model:** Injected multimodal caller (Gemini vision), group_name="oneshot_ocr", structured output _SeenText, image passed in
- **Does:** OCR read-back gate: character-exact vision reader that copies rendered poster text pixel-for-pixel without correcting spelling/dialect (evidence for the fidelity gate).

> You are a CHARACTER-EXACT OCR reader. You copy text pixel-for-pixel; you NEVER correct spelling, dialect, or grammar.

### read_rendered_text (user)

- **Where:** [`poster/oneshot.py`:237](https://github.com/asmaasamyhagag22-design/marketing-/blob/main/poster/oneshot.py#L237)
- **Kind:** user
- **Model:** Injected multimodal caller (Gemini vision), group_name="oneshot_ocr"
- **Does:** Asks the vision model to list every visible text element top-to-bottom exactly as printed, preserving Arabic dialectal spellings and dot counts.

> List EVERY piece of text visible in this poster image... copied EXACTLY letter-for-letter as printed. CRITICAL for Arabic: preserve DIALECTAL spellings exactly (if the poster prints أكتر with ت, write أكتر — do NOT 'fix' it to أكثر); count the dots — ت has 2 dots, ث has 3; ة vs ه as printed.

### poster_vision_qa (system)

- **Where:** [`poster/vision_qa.py`:87](https://github.com/asmaasamyhagag22-design/marketing-/blob/main/poster/vision_qa.py#L87)
- **Kind:** system
- **Model:** Injected multimodal qa_caller (Gemini vision), group_name="poster_vision_qa", structured output _QAResponse, poster PNG passed in
- **Does:** Art-director vision QA of the FINAL rendered poster — structured verdict on Latin-in-copy, **script_wellformed** (Batch 2: Arabic must be correctly shaped/connected with correct dots, not merely "no Latin"), logo integrity, clipping, on-brand color, candid violation, focal/typography/CTA prominence, 1-10 score, overall pass. `overall_pass` re-ANDs `logo_ok` AND `script_wellformed` code-side (hard gates). Composes with the separate OCR character-fidelity gate (pipeline.py:512).

> You are a WORLD-CLASS advertising ART DIRECTOR reviewing a FINAL rendered poster. Your bar: 'Would a top brand actually run this?' Be strict... script_wellformed = true ONLY if every rendered copy line is WELL-FORMED for its script (Arabic connected/ligatures/dots)... overall_pass = true ONLY if logo_ok AND script_wellformed AND no Latin in the copy AND no clipping AND on_brand_color AND no candid violation AND score >= 7.

### poster_vision_qa (user)

- **Where:** [`poster/vision_qa.py`:108](https://github.com/asmaasamyhagag22-design/marketing-/blob/main/poster/vision_qa.py#L108)
- **Kind:** user
- **Model:** Injected multimodal qa_caller (Gemini vision), group_name="poster_vision_qa"
- **Does:** Trigger line asking the model to review the attached rendered poster as an art director and return the verdict.

> Review this rendered poster as an art director and return the verdict.

### build_art_direction (restaurant category_prompt)

- **Where:** [`poster/art_director.py`:296](https://github.com/asmaasamyhagag22-design/marketing-/blob/main/poster/art_director.py#L296)
- **Kind:** template fragment
- **Model:** Image model (provider_prompt) — VertexImagenProvider / OpenAI image; no LLM text-call here
- **Does:** Per-vertical image-BACKGROUND generation prompt for a restaurant brand (text-free scene; text/logo overlaid later).

> Create a premium vertical marketing poster BACKGROUND for a restaurant brand... Show an elegant dining atmosphere with authentic cuisine... Strict rules: No text. No words. No logo. No watermark. No readable menu. No price tags.

### build_art_direction (education category_prompt)

- **Where:** [`poster/art_director.py`:326](https://github.com/asmaasamyhagag22-design/marketing-/blob/main/poster/art_director.py#L326)
- **Kind:** template fragment
- **Model:** Image model (Imagen/OpenAI image)
- **Does:** Per-vertical image-BACKGROUND prompt for an education/training institute (ICT learning environment, credible, no fake certificate text).

> Create a professional vertical marketing poster BACKGROUND for an education and training institute... Include subtle cues of ICT, networking, digital learning, certifications... No text... No fake certificate text. No readable signage.

### build_art_direction (medical category_prompt)

- **Where:** [`poster/art_director.py`:357](https://github.com/asmaasamyhagag22-design/marketing-/blob/main/poster/art_director.py#L357)
- **Kind:** template fragment
- **Model:** Image model (Imagen/OpenAI image)
- **Does:** Per-vertical image-BACKGROUND prompt for a medical clinic, with compliance safety rules (no patients/procedures/before-after/guaranteed results).

> Create a clean vertical marketing poster BACKGROUND for a medical clinic brand... Safety: Do not show identifiable patients. Do not show before/after imagery. Do not show procedures, needles, blood... Do not imply guaranteed results.

### build_art_direction (skincare category_prompt)

- **Where:** [`poster/art_director.py`:392](https://github.com/asmaasamyhagag22-design/marketing-/blob/main/poster/art_director.py#L392)
- **Kind:** template fragment
- **Model:** Image model (Imagen/OpenAI image)
- **Does:** Per-vertical image-BACKGROUND prompt for a skincare/beauty brand with cosmetic-compliance rules.

> Create a premium vertical marketing poster BACKGROUND for a skincare or beauty brand... Compliance: Do not imply medical treatment or guaranteed results. Do not show skin disease, before/after comparisons, or clinical claims... No readable labels.

### build_art_direction (business/default category_prompt)

- **Where:** [`poster/art_director.py`:424](https://github.com/asmaasamyhagag22-design/marketing-/blob/main/poster/art_director.py#L424)
- **Kind:** template fragment
- **Model:** Image model (Imagen/OpenAI image)
- **Does:** Default per-vertical image-BACKGROUND prompt for a generic business brand.

> Create a premium vertical marketing poster BACKGROUND for a business brand... Create a modern, professional, brand-safe campaign background with clean visual hierarchy... No text... No readable signage. Do not generate typography.

### build_art_direction (negative_prompt)

- **Where:** [`poster/art_director.py`:444](https://github.com/asmaasamyhagag22-design/marketing-/blob/main/poster/art_director.py#L444)
- **Kind:** template fragment
- **Model:** Image model negative prompt (Imagen/OpenAI image)
- **Does:** Shared negative prompt for background image generation — bans text/logos/typography and low-quality artefacts.

> text, words, letters, typography, logo, watermark, readable signage, price tags, UI screens with readable words, fake certificates with text, distorted hands, distorted faces, low quality, blurry, cluttered layout, busy overlay zones...

### build_creative_prompt

- **Where:** [`poster/art_director.py`:485](https://github.com/asmaasamyhagag22-design/marketing-/blob/main/poster/art_director.py#L485)
- **Kind:** template fragment
- **Model:** Image model (Imagen/OpenAI image); used as fallback when no art-director LLM caller
- **Does:** Static (no-LLM) fallback image concept prompt: one ultra-minimal surreal hero concept from the category + hero offering; default variant forbids all baked text (overlay later), bake_text variant renders short Latin headline/CTA.

> A bold, ultra-minimal advertising poster for a {category} brand — ONE striking creative concept... Surreal forced-perspective with a single dramatic hero subject inspired by "{hero}"... ABSOLUTELY NO text, words, letters, numbers, logos, captions, or signage anywhere in the image.

### build_llm_concept_prompt (system — brandbook-style variant)

- **Where:** [`poster/art_director.py`:753](https://github.com/asmaasamyhagag22-design/marketing-/blob/main/poster/art_director.py#L753)
- **Kind:** system
- **Model:** Injected text caller (Gemini/OpenAI), group_name="poster_concept", structured output _CreativeConceptResponse
- **Does:** LLM art-director prompt used when a BrandBook visual identity exists: write a fresh photorealistic on-brand image-generation prompt in the brand's own learned visual world.

> You are an award-winning advertising art director. Using the brand's OWN visual world below (learned from its real photos), write a detailed image-generation prompt for a FRESH, photorealistic, on-brand scene... NOT a reused stock image, NOT an abstract metaphor... ABSOLUTELY NO text... in the image.

### build_llm_concept_prompt (system — persona/metaphor variant)

- **Where:** [`poster/art_director.py`:773](https://github.com/asmaasamyhagag22-design/marketing-/blob/main/poster/art_director.py#L773)
- **Kind:** system
- **Model:** Injected text caller (Gemini/OpenAI), group_name="poster_concept", structured output _CreativeConceptResponse
- **Does:** LLM art-director prompt used without a BrandBook: invent ONE striking surreal/metaphorical visual concept embodying the brand persona.

> You are an award-winning advertising art director. Invent ONE striking, surreal or metaphorical VISUAL concept for a premium vertical ad poster... EMBODY the brand persona below... The brand palette is the scene's DOMINANT color story... ABSOLUTELY NO text... Purely visual: no factual claims, prices, or guarantees.

### build_llm_concept_prompt (user)

- **Where:** [`poster/art_director.py`:791](https://github.com/asmaasamyhagag22-design/marketing-/blob/main/poster/art_director.py#L791)
- **Kind:** user
- **Model:** Injected text caller (Gemini/OpenAI), group_name="poster_concept"
- **Does:** Supplies business/category/offerings/tone/palette + brand visual world + verbatim persona to the art-director concept call.

> Business: {business_name}\nCategory: {category}\nReal offerings: {offerings}\nTone: ...\nBrand palette: ...\nThe brand's OWN visual world (from its real photos): ...\nBrand persona (all verbatim from the real website): ...\nReturn concept_title and a vivid, text-free image_prompt.

### build_llm_concept_prompt (grounded 'lead' image prompt assembled from the LLM output)

- **Where:** [`poster/art_director.py`:849](https://github.com/asmaasamyhagag22-design/marketing-/blob/main/poster/art_director.py#L849)
- **Kind:** template fragment
- **Model:** Image model (VertexImagenProvider / OpenAI image); built from _CreativeConceptResponse.image_prompt
- **Does:** Deterministically-assembled FINAL Imagen prompt that leads with grounded subject+region+palette (DNA-led or natural-light variant) and demotes the LLM's prose to a minor cue — the actual string sent to the background image model.

> A single photorealistic advertising photograph. Show {subject}. {region}. The brand palette ({palette}) appears naturally in the environment... NOT as colored lighting... ABSOLUTELY NO colored gel lighting, NO duotone... No text, letters, numbers, logos, or signage anywhere. Vertical 4:5 advertising poster.

### build_design_spec (system)

- **Where:** [`poster/art_director.py`:972](https://github.com/asmaasamyhagag22-design/marketing-/blob/main/poster/art_director.py#L972)
- **Kind:** system
- **Model:** Injected text caller (Gemini/OpenAI), group_name="poster_design", structured output _DesignSpecResponse
- **Does:** LLM art-director COMPOSITION brief: choose layout/headline-treatment/accent-word/text-align/scrim/show/accent-hex/marketing-archetype and free-form text_box & logo_xy coords for one poster (design only, no facts).

> You are an award-winning poster ART DIRECTOR. Decide the COMPOSITION for ONE premium vertical ad poster for THIS specific brand. You choose DESIGN ONLY... marketing_archetype: FIRST pick the ONE archetype (magazine_editorial | product_hero | typographic_anchor | proof_and_trust)... text_box: [x, y, w] as FRACTIONS of the canvas.

### build_design_spec (user)

- **Where:** [`poster/art_director.py`:1017](https://github.com/asmaasamyhagag22-design/marketing-/blob/main/poster/art_director.py#L1017)
- **Kind:** user
- **Model:** Injected text caller (Gemini/OpenAI), group_name="poster_design"
- **Does:** Supplies brand/category/verbatim-headline/offerings/palette/logo-flag/RTL + persona + brand design-language + variation cue to the composition call.

> Brand: {business_name}\nCategory: {category}\nHeadline (verbatim — do not change the words): {headline}\nOfferings: ...\nBrand palette (choose accent_hex from these EXACTLY): ...\nRTL / Arabic copy: {is_rtl}\nBrand persona (verbatim from the real site): ...

### copy_style_cue

- **Where:** [`poster/variation.py`:87](https://github.com/asmaasamyhagag22-design/marketing-/blob/main/poster/variation.py#L87)
- **Kind:** feedback / instructions
- **Model:** Fragment merged into concept caller prompt (Gemini/OpenAI)
- **Does:** Per-run COPYWRITING-style cue injected into build_creative_concept's system prompt — varies the rhetorical FORM/VOICE of the copy each run (design domain; facts stay Ledger-gated).

> THIS RUN'S COPYWRITING STYLE — the headline must be {copy_form}; the overall voice of ALL copy is {copy_voice}... do NOT fall back to the same hook-plus-proof formula every run. The FACTS may only come from the evidence; the FORM is yours.

### design_variation_cue

- **Where:** [`poster/variation.py`:111](https://github.com/asmaasamyhagag22-design/marketing-/blob/main/poster/variation.py#L111)
- **Kind:** feedback / instructions
- **Model:** Fragment merged into build_design_spec caller prompt (Gemini/OpenAI)
- **Does:** Per-run composition cue injected into build_design_spec's user prompt to steer layout/treatment/scrim mood.

> Creative direction for THIS render (make the composition feel {energy} and {mood}; choose a layout, headline treatment and scrim that express it — stay on-brand, never change the words).

### concept_variation_cue

- **Where:** [`poster/variation.py`:122](https://github.com/asmaasamyhagag22-design/marketing-/blob/main/poster/variation.py#L122)
- **Kind:** feedback / instructions
- **Model:** Fragment appended to background image prompt (Imagen/OpenAI image)
- **Does:** Per-run scene-look cue appended to the static build_creative_prompt image concept to vary mood/lighting/composition of the generated background.

> Overall feel: {mood}, {energy}. Lighting: {lighting}. Composition: {composition}.

---

## 3. Reel — Gemini creative director, Veo render, scene QA

Gemini 2.5 Pro (vision) designs an N-scene 9:16 ad from the brand's REAL photos on a HOOK → WHAT-IT-IS → BENEFIT → PROOF → CTA spine, writing a Veo 3.1 motion prompt + voice-over + caption per scene. A deterministic plan-eval catches a weak plan before the expensive render; a vision scene-QA rejects Veo hallucinations after it.

### _system_prompt

- **Where:** [`reel/creative_director.py`:201](https://github.com/asmaasamyhagag22-design/marketing-/blob/main/reel/creative_director.py#L201)
- **Kind:** system
- **Model:** Gemini 2.5 Pro (gemini-2.5-pro) via design_creative_reel -> the shared Caller (system_prompt + images)
- **Does:** Directs Gemini 2.5 Pro to design an N-scene vertical 9:16 ad reel from the identity + real photos, following the HOOK -> WHAT-IT-IS -> BENEFIT -> PROOF -> CTA spine, and return structured scenes (veo_prompt/voiceover/on_screen_text).

> You are a SENIOR CREATIVE DIRECTOR with 15 years in performance-marketing and TikTok/Reels advertising... Design a {n_scenes}-scene reel that ADVERTISES this brand with the proven AD SPINE... Return ONLY a JSON object, no prose, no markdown fences.

### _MOTION_GUIDANCE

- **Where:** [`reel/creative_director.py`:168](https://github.com/asmaasamyhagag22-design/marketing-/blob/main/reel/creative_director.py#L168)
- **Kind:** template fragment
- **Model:** Gemini 2.5 Pro (gemini-2.5-pro) — injected into _system_prompt by mode
- **Does:** Per-vertical (beauty/elegant/food/generic) veo_prompt motion-guidance fragment picked by _vertical_mode and spliced into the system prompt so each reel's motion vocabulary fits the brand.

> beauty: '- veo_prompt: a vivid, TikTok-native Veo 3.1 IMAGE-TO-VIDEO prompt where a real PERSON uses THIS exact product: a hand enters frame, picks it up and applies it... and the model REACTS'

### _MOTION_TAIL

- **Where:** [`reel/creative_director.py`:160](https://github.com/asmaasamyhagag22-design/marketing-/blob/main/reel/creative_director.py#L160)
- **Kind:** template fragment
- **Model:** Gemini 2.5 Pro (gemini-2.5-pro) — appended to every _MOTION_GUIDANCE mode
- **Does:** Shared motion tail appended to every mode's guidance: keep the product identity exact, add the person/action around it, only physically-logical use, real energetic movement (no slow zoom/static pan).

> The product's IDENTITY must stay exactly as shown — same shape, label, colour and proportions... Show only PHYSICALLY LOGICAL use... Real, energetic movement in every second — NOT a slow zoom, NOT a static pan. Add no fake text, logos, or signage.

### _DELIVERY_EG

- **Where:** [`reel/creative_director.py`:193](https://github.com/asmaasamyhagag22-design/marketing-/blob/main/reel/creative_director.py#L193)
- **Kind:** template fragment
- **Model:** Gemini 2.5 Pro (gemini-2.5-pro) — injected into _system_prompt's voiceover_delivery line
- **Does:** Per-vertical example voiceover_delivery phrases handed to Gemini 2.5 Pro as examples of the emotion/performance note to write for each scene's narration.

> beauty: 'fresh and upbeat', 'confident glow', 'delighted reaction', 'warm friendly invitation'; elegant: 'hushed reverence', 'quiet confidence'...

### _system_prompt (featured/whole-brand branch)

- **Where:** [`reel/creative_director.py`:205](https://github.com/asmaasamyhagag22-design/marketing-/blob/main/reel/creative_director.py#L205)
- **Kind:** template fragment
- **Model:** Gemini 2.5 Pro (gemini-2.5-pro)
- **Does:** Featured-vs-whole-brand variant text inside _system_prompt: when featured_product is set, forces every scene onto REAL PHOTO index 0 (same product, varied shots); otherwise anchors on one hero product across distinct photos.

> FEATURED PRODUCT: {featured}. This reel advertises ONLY this product — EVERY scene is the SAME product (REAL PHOTO index 0), varied by SHOT and ACTION... NEVER a different product.

### design_creative_reel

- **Where:** [`reel/creative_director.py`:372](https://github.com/asmaasamyhagag22-design/marketing-/blob/main/reel/creative_director.py#L372)
- **Kind:** user
- **Model:** Gemini 2.5 Pro (gemini-2.5-pro) — user message with image parts
- **Does:** Builds the user turn sent to Gemini 2.5 Pro: the business identity brief (_identity_block) + an optional FEATURED PRODUCT note + the downscaled real-photo image parts (passed via the Caller's images=), then asks it to design the reel.

> BUSINESS IDENTITY:\n{identity} ... You have {len(used)} real photos below. Design the reel.  / feat: 'FEATURED PRODUCT (advertise ONLY this): {featured_product}. Every scene is the SAME product below — vary the shot/action, not the item.'

### _instructions_for

- **Where:** [`reel/voiceover.py`:40](https://github.com/asmaasamyhagag22-design/marketing-/blob/main/reel/voiceover.py#L40)
- **Kind:** feedback / instructions
- **Model:** OpenAI gpt-audio-1.5 (chat.completions system msg) / gpt-4o-mini-tts (instructions param); also prepended to Gemini TTS content
- **Does:** Builds the TTS performance/delivery brief: one consistent human narrator, tone-adaptive (luxury=refined/unhurried, playful=bright/energetic, else warm), Egyptian-Arabic read when Arabic text is detected, plus a per-passage emotional direction appended from `delivery`.

> You are ONE consistent, professional human voice-over artist narrating a premium brand film. Keep the SAME voice, character and energy from the first word to the last... Perform it {mood}. Never flat, never robotic, never a news anchor.

### check_scene (system)

- **Where:** [`reel/scene_qa.py`:106](https://github.com/asmaasamyhagag22-design/marketing-/blob/main/reel/scene_qa.py#L106)
- **Kind:** system
- **Model:** Vision model via injected `caller(system, user, _QAResponse, group_name='reel_scene_qa', images=...)`
- **Does:** Strict vision-QA/judge prompt: inspects extracted clip frames against the real product reference photo and returns a structured verdict on product_faithful / product_persists / action_plausible / overall_pass with a reason.

> You are a STRICT quality reviewer for a short product ad clip... product_faithful: do the clip frames show the SAME product as the reference... product_persists: is the product clearly visible in EVERY clip frame... action_plausible: is every depicted action physically possible... overall_pass = product_faithful AND product_persists AND action_plausible.

### check_scene (user)

- **Where:** [`reel/scene_qa.py`:121](https://github.com/asmaasamyhagag22-design/marketing-/blob/main/reel/scene_qa.py#L121)
- **Kind:** user
- **Model:** Vision model via injected `caller`
- **Does:** The user turn paired with the QA system prompt, instructing the model to compare the clip to the reference and emit the structured verdict.

> Review the clip against the reference and return the structured verdict.

---

## 4. Competitor & Strategy — themes, TOWS, discovery, calendar

Real peer-review themes feed a cited SWOT; a TOWS matrix pairs labelled SWOT items into strategies with anchor ids; a reject-only web-discovery judge keeps only true direct competitors; a social strategist builds a grounded content calendar in the locked language.

### _PROMPT_TEMPLATE (module const; formatted by ReviewThemeExtractor._build_prompt, sent by _call_llm)

- **Where:** [`competitor/themes.py`:43](https://github.com/asmaasamyhagag22-design/marketing-/blob/main/competitor/themes.py#L43)
- **Kind:** user
- **Model:** Gemini 2.5 Pro (gemini-2.5-pro) via the shared Caller (default_caller(strong=True)), structured response
- **Does:** Extract recurring praise/complaint THEMES from real Google reviews of peer businesses, each citing the exact supporting review IDs, so themes feed the SWOT as Opportunities/Threats.

> You are analyzing real Google reviews of competing local businesses to find recurring customer themes... Only themes expressed in AT LEAST TWO reviews... list the exact review IDs... Classify each theme as "praise"... or "complaint"... Return ONLY a JSON array, no prose, no markdown fences.

### _llm_strategies (system arg)

- **Where:** [`competitor/tows.py`:169](https://github.com/asmaasamyhagag22-design/marketing-/blob/main/competitor/tows.py#L169)
- **Kind:** system
- **Model:** Shared Caller protocol (default_caller) — Gemini 2.5 Pro via default_caller(strong=True) in competitor/full_run.py; group_name="tows_matrix"
- **Does:** System prompt: act as a senior brand strategist building a TOWS matrix, pairing labelled SWOT items into SO/ST/WO/WT strategies with cited anchor ids and no invented facts.

> You are a senior brand strategist building a TOWS matrix. Pair the labelled SWOT items into concrete strategies: SO... ST... WO... WT... every strategy MUST cite the exact item ids it pairs in `anchors`... do NOT invent facts, numbers, rankings, awards or claims not present in the items; ... at most 3 SO, 2 ST, 2 WO, 2 WT.

### _llm_strategies (user arg)

- **Where:** [`competitor/tows.py`:177](https://github.com/asmaasamyhagag22-design/marketing-/blob/main/competitor/tows.py#L177)
- **Kind:** user
- **Model:** Shared Caller (Gemini 2.5 Pro via default_caller(strong=True)); group_name="tows_matrix"; validated into _TowsResponse
- **Does:** User prompt: supplies the labelled SWOT items block ([id] text + citations) and asks the model to return the TOWS strategies.

> "SWOT items:\n" + _items_block(ids) + "\n\nReturn the TOWS strategies."

### SerpWebDiscoveryEngine._plan_queries_llm (system arg)

- **Where:** [`competitor/web_discovery.py`:332](https://github.com/asmaasamyhagag22-design/marketing-/blob/main/competitor/web_discovery.py#L332)
- **Kind:** system
- **Model:** judge_caller = default_caller(strong=False) → Gemini 2.5 Flash (see default_web_engine); group_name="peer_query_plan"; output _Plan
- **Does:** System prompt for the LLM query planner: write 1-3 customer-language category search queries to find DIRECT competitors, determine the market, and return identity_terms — never the brand's own name, invent nothing.

> You plan web searches to find DIRECT COMPETITORS of a business. Return 1-3 'queries' a real CUSTOMER in the business's market would type to find businesses of the SAME KIND... NEVER include the brand's own name in a query. Also return 'identity_terms'... Base everything ONLY on the given facts; invent nothing.

### SerpWebDiscoveryEngine._plan_queries_llm (user arg)

- **Where:** [`competitor/web_discovery.py`:346](https://github.com/asmaasamyhagag22-design/marketing-/blob/main/competitor/web_discovery.py#L346)
- **Kind:** user
- **Model:** judge_caller → Gemini 2.5 Flash; group_name="peer_query_plan"
- **Does:** User prompt: hands the planner the subject's business facts (name, homepage, category, tagline, description, sample offerings, site languages).

> f"Business: {name}\nHomepage: {homepage}\nCategory: {category}\nTagline: {tagline}\nDescription: {desc}\nSample offerings: {offers}\nSite languages: {langs}"

### SerpWebDiscoveryEngine._judge_relevance (system arg)

- **Where:** [`competitor/web_discovery.py`:429](https://github.com/asmaasamyhagag22-design/marketing-/blob/main/competitor/web_discovery.py#L429)
- **Kind:** system
- **Model:** judge_caller = default_caller(strong=False) → Gemini 2.5 Flash; group_name="peer_relevance"; output _Kept(keep_indices)
- **Does:** System prompt for the reject-only relevance JUDGE: keep only candidates that are plausibly a DIRECT competitor of the same kind/market; drop listicles, directories, news, universities, industry software, marketplaces; when unsure drop; never add.

> You filter competitor candidates for a business-comparison report. Keep ONLY candidates that are plausibly a DIRECT COMPETITOR... DROP: listicles/rankings, directories, news/media, universities/courses, software or tools FOR that industry, marketplaces... When unsure, DROP... Never add anything.

### SerpWebDiscoveryEngine._judge_relevance (user arg)

- **Where:** [`competitor/web_discovery.py`:437](https://github.com/asmaasamyhagag22-design/marketing-/blob/main/competitor/web_discovery.py#L437)
- **Kind:** user
- **Model:** judge_caller → Gemini 2.5 Flash; group_name="peer_relevance"
- **Does:** User prompt: gives the judge the subject (name/category/description) and the numbered candidate list, asking for keep_indices (a subset).

> f"Subject: {name} — category: {category}.\n{desc}\n\nCandidates:\n{lines}\n\nReturn keep_indices (subset of the shown indices)."

### _llm_plan (system arg)

- **Where:** [`strategy/builder.py`:156](https://github.com/asmaasamyhagag22-design/marketing-/blob/main/strategy/builder.py#L156)
- **Kind:** system
- **Model:** Shared Caller (default_caller) — Gemini 2.5 Flash via default_caller(strong=False) in strategy/__main__.py; group_name="content_strategy"; output _PlanResponse
- **Does:** System prompt: act as a senior social-media strategist producing a concrete, varied N-day content calendar grounded in the brand's real persona/offerings, inventing no facts/prices/claims, in the locked language (Egyptian Arabic vs English).

> You are a senior social-media strategist. Produce a concrete, varied content calendar for the brand below... Ground every item in the brand's real persona/offerings; do NOT invent facts, prices, or claims. Vary content_type and angle... LANGUAGE: {lang}.

### _llm_plan (user arg)

- **Where:** [`strategy/builder.py`:163](https://github.com/asmaasamyhagag22-design/marketing-/blob/main/strategy/builder.py#L163)
- **Kind:** user
- **Model:** Shared Caller → Gemini 2.5 Flash (default_caller(strong=False)); group_name="content_strategy"
- **Does:** User prompt: supplies the persona block + optional current trends and asks for `target` calendar items evenly spread across the day window, each with day_offset/platform/content_type/topic/angle/hook in the locked language.

> f"{persona}\n{trend_block}\n\nPlan {target} items EVENLY spread across a {days}-day window... Platforms to use: {...}. content_type ∈ {reel, post, story, carousel}. Each item: day_offset, platform, content_type, topic, a one-line angle, and a scroll-stopping hook. Write the topic/angle/hook in {lang}."

---

## 5. Grounding — the same-subject judge

The Evidence Ledger's last-resort residual check: when a marketing claim and brand evidence share a token, a cheap Gemini judge decides only whether they describe the SAME thing (accept synonyms, reject unrelated subjects), biased to accept when unsure.

### _SYSTEM (module constant; consumed by make_subject_judge -> judge)

- **Where:** [`grounding/subject_judge.py`:19](https://github.com/asmaasamyhagag22-design/marketing-/blob/main/grounding/subject_judge.py#L19)
- **Kind:** system
- **Model:** Gemini cheap/Flash caller (default_caller(strong=False), e.g. gemini-2.5-flash); invoked as caller(_SYSTEM, user, _SameSubject, group_name="ledger_subject_judge") from the EvidenceLedger C2 ambiguous-case gate
- **Does:** System prompt for the Evidence Ledger's C2-residual 'same-subject' judge: given a value/word shared by BOTH a marketing claim and brand evidence, decide ONLY whether it describes the SAME thing (accept synonyms/inflections, reject unrelated subjects), biased to accept when unsure.

> You check a marketing CLAIM against a brand's REAL evidence. The shared value (a number, or a ranking/superlative word) already appears in BOTH — decide ONLY whether it describes the SAME THING. ... same_subject=false (reject): the value is about an UNRELATED thing ... When genuinely unsure, answer true — never block a plausibly-real claim.

### judge (nested closure inside make_subject_judge)

- **Where:** [`grounding/subject_judge.py`:47](https://github.com/asmaasamyhagag22-design/marketing-/blob/main/grounding/subject_judge.py#L47)
- **Kind:** user
- **Model:** Gemini cheap/Flash caller (same call as _SYSTEM), structured-output schema _SameSubject (BaseModel: same_subject: bool), group_name="ledger_subject_judge"
- **Does:** Per-call user prompt supplying the shared token, the claim copy, and the brand evidence (each truncated to 400 chars), asking whether claim and evidence use this value for the SAME thing; returns structured output via the _SameSubject Pydantic model (same_subject: bool) -> True/False/None.

> Shared value/word: {token!r}\nCLAIM copy: {(claim_text or '')[:400]!r}\nBRAND evidence: {(evidence_text or '')[:400]!r}\nDo the claim and the evidence use this value for the SAME thing?

---

_This catalog is generated. When you add or move a prompt, regenerate or hand-edit this file so it stays the single index of everything the models are told._
