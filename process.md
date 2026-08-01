# process.md — Universal AI Marketing Strategist

**The single source of truth for this project.** Replaces the historical
change-log. Read this before acting in this repo. Last full revision: 2026-07-04.
Test suite: **1635 passed, 2 skipped, 0 failed** (2026-07-15, re-derived live, 66s; grew from
880 as each audit fix below shipped with its hermetic regression tests).

**D-8 UNIVERSALITY — "it has to work for any country" (2026-07-15, IN PROGRESS).**
Volume Cafe (Doha, a real client deliverable) exposed it: `upcoming_season` returned **"موسم الصيف
والسواحل / Summer & coast season"** — Egypt's North-Coast BEACH season — for a Qatari café. Not
merely inapplicable: **inverted** (a 45 °C Gulf summer drives people INDOORS; Egypt's is the peak).
That violates **D-8, an OWNER DIRECTIVE** (`docs/INTERFACES.md:22`): *"the system is universal across
verticals/languages/**countries** … CONFIG keyed by the profile's universal signal, always with a
universal default … a vertical hack in core logic is forbidden."*

**THE FAILURE CLASS, now at four instances — "a true statement that answers a different question
than the one asked":** ① the 42 ("not checked" written as "verified") · ② trends ("Rising interest"
from an `else` branch) · ③ **seasons** — `trends/seasons.py:9` *"Universal by construction … no
**vertical** logic"*: true, and defending the WRONG AXIS while country logic sits hardcoded in the
same file · ④ **trends `gl`/`hl`** — the citation TRACES (real URL, real article) while the claim
("a signal for YOUR market") does not, because the search asked about a different market.
*Honest caveat on ③: seasons.py is not hiding it — `:1` says "Egyptian marketing-season calendar"
and the call site's own comment (`dashboard/build.py:182`) says "an Egyptian marketing season". The
code KNEW, wrote it down in both places, and shipped it universally anyway. The primary sin is the
CALL SITE, not the docstring.*

**SHIPPED so far (all zero-spend, RED-first per fix):**
- `trends/sources.py` — `SerperTrendSource(keywords, *, gl, hl)` now passes the brand's market to
  the SERP. Before, `prov.search(q, num=…)` sent **neither**, so Serper served its **US/English
  default** and the dashboard labelled the result *"إشارات السوق الحيّة · Live market signals"*.
  `hl` is **not** derived from `gl` — "the country is Arab ⇒ search in Arabic" is
  `strategy/builder.py:154`'s bug with the arrow reversed. Both default to `""` ⇒ omitted ⇒
  byte-identical to the old request. The false *"FREE, keyless public APIs"* docstring (`:1`) is
  fixed — Serper is paid, keyed and FIRST.
- `poster/locale.py` — `country_code()` ("Qatar" → `"qa"`) **derived from `CCTLD_COUNTRY` at import**
  (no second table — `poster/art_director.py:582` already is one, and two tables drift). `.uk`→`gb`
  is the only ccTLD≠ISO mismatch in all 62 keys. `"the USA/Canada"` (`:51`) resolves to `""` — a +1
  phone genuinely cannot say which country. Plus `search_locale(profile) -> (gl, hl)`.
- `competitor/full_run.py` + `api/routes/swot.py` — both resolve the market from the profile.

**MEASURED, and both refuted something we believed:**
1. **The SERP's noise floor is 66 %.** Identical query, nothing changed, minutes apart ⇒ 66 % of
   URLs differ. A first A/B "measured" 79 % churn from `gl`; the control showed that was ~all
   noise. **Any n=1 trend snapshot is mostly noise** — including every trend list this product has
   ever rendered. (`outputs/_gl_control.json`)
2. **`gl` is necessary but NOT sufficient.** Queries with market words in the TEXT went **0 → 4**
   Qatar/Gulf hosts when `gl=qa` was added; new angles with `gl=qa` and no market words returned
   **0**. `gl` re-ranks — it does not conjure local sources for a globally-written topic. The market
   must be in the query **and** in `gl`. (`outputs/_gl_ab.json`)

**A REAL SCRAPE CLOSED THE HONESTY GAP** (owner: *"اسكراب حقيقي علشان تبقا النتيج صح"*).
`scrapes/volumeqatar_com_20260715_112727/` (10 pages, robots-allowed, free) + a $0.019 profile build
⇒ `country_of` resolved **"Qatar"** from the brand's OWN `service_areas: ["Qanat Quartier", "Pearl
Qatar"]` (quote + page_url), and `languages` measured **en @ 100 %** ⇒ `search_locale = ("qa","en")`.
The market is no longer asserted by a human. *(`hl="ar"` would have been wrong — the site is English.)*

**FOUND, NOT YET FIXED (tickets, evidence-backed):**
- **T-KW-SALIENCE — the auto trend query is nonsense for this brand.** Left alone the pipeline
  searches **`"cafe baked salmon trends 2026"`**: `keywords_from_profile` splits OFFERING NAMES into
  words and `_query()` takes the **first three**, so a brand whose own title is *"Volume Cafe –
  Specialty Coffee"* never mentions coffee (it sits ~40th). Vertical-calibrated by accident: for a
  jeweller, offering names ARE the category (rings/earrings); for a café they are menu items.
- **T-PHONE-CIRCULAR — an assumed country code read back as evidence of the country.**
  `scraper/extractors/contact.py:47` `_HOME_MARKET_REGION = "EG"` is the fallback when
  `region_from_site` finds no signal (generic TLD, no `/xx-yy` path). `country_of` step 3 then reads
  that `+20` and concludes **"Egypt"** — *the evidence is the assumption*. Same shape as the circular
  trends gate deleted in P1. **PROVEN, not theoretical:** `scrapes/nahdionline_com_20260704_102759`
  (Al Nahdi, **Saudi**) stores `e164=+20920024673` for the Saudi unified number `920024673`, and
  `search_locale` on it returns **`("eg","ar")`** — a Saudi brand would search Egypt's SERP.
  Bounded: only numbers valid in BOTH countries collide (Qatari 8-digit locals are rejected, not
  fabricated), and `contact.py:44-46` already DOCUMENTS this exact residual. `PhoneRecord`
  (`scraper/schemas.py:162`) carries no signalled-vs-assumed flag, so nothing downstream can tell.
  Note the EG fallback is load-bearing for the corpus — most of the 14-URL grid is `.com`.
- **THE BENCHMARK NEVER TESTED THE COUNTRY AXIS.** All 14 graded URLs serve Egypt. D-8 says the grid
  stays *"multi-**vertical**"* — it says nothing about multi-country. So universality is measured on
  the vertical axis and **never on the country axis**, which is exactly how the Egypt coupling
  survived every gate. (Ad-hoc scrapes do include `aztech_sa`, `outletpharmacyonline_sa`, `ulta_com`
  — but nothing non-Egyptian is *graded*.)
- **The country-assumption sweep (~20 sites)** — `dashboard/build.py:464` renders **"كل مصر ·
  nationwide"** to a Doha brand · `media_plan/config.py:67` prices every KPI in **EGP** ·
  `reel/render3/director.py:269` `return loc or "Egypt"` · `strategy/builder.py:154` any-Arabic ⇒
  Egyptian (**the bug D5 already killed in the reel, still live in the calendar**) ·
  `reel/voiceover.py:202` `ar-EG-SalmaNeural` · `reel/render3/prompts.py:62,101` hardcoded "Egyptian"
  casting · no **QAR** in the currency regexes.
  ⚠ **`process.md:861`'s "no hardcoded Egypt [in the reel]" is an OVERSTATEMENT of a real, bounded
  fix** — D5 covered `_culture_line` in `reel/art_director` + `reel/generate`, exactly and only.
  ⚠ **Do NOT "clean" the ~20 hits for "Cairo" in `reel/textlayer.py` / `endcard.py` /
  `poster/template.py`** — that is the **Google Font named Cairo**; removing it breaks Arabic
  rendering everywhere.

**STILL OPEN:** the seasons country axis (config keyed by `country_of`, **universal default =
EMPTY**, Egypt the one ratified table — owner ruling: *the DATE is universal, the MARKETING MEANING
is not*) · the visibility edits (**two** are needed: `advisor/advisor.py:2` says it *"mirrors"*
`dashboard/build.py:_honesty_surface`, and the advisor's `Gap`s reach only the launch bundle — an
advisor-only gap would never reach the client) · the Volume Cafe market angle re-run
(`outputs/volume_cafe_trends.json` is marked **WIP / DO NOT SEND**).

**THE FAIL-OPEN SWEEP (P6, 2026-07-15) — the most valuable audit in this project's history.**
A repo-wide audit of every gate/validator asked one question: *what happens when it CANNOT
verify?* It found **42 unjustified fail-opens**, and the headline is not 42 — it is that **four
were PINNED BY TESTS**. Suite-green had been certifying the holes. **It is ONE bug in 42 costumes:
"cannot verify" encoded as "verified".** The fix was TYPE-LEVEL, not 42 patches.

| # | Hole | Status |
|---|---|---|
| — | **TRI-STATE**: `overall_pass: Optional[bool]` — True=PASS, False=FAIL, **None=NOT_CHECKED**, falsy so callers fail CLOSED. A verdict could read `overall_pass=True, reason='scene QA call failed'`. | **CLOSED** `1ca345b` |
| 1 | An LLM fact with **no source_url** was evidence → sheet printed *"Verified against a sourced web result"*, `source_url: None`. Model output proving model output. | **CLOSED** `de7337f` |
| 2 | **Empty audit → green PASS** (`all([])` vacuity) in the client deliverable. | **CLOSED** `de7337f` |
| 3 | Poster headline gate shipped the line it exists to stop (*"Egypt's #1 pharmacy"*) on both triggers. | **CLOSED** `de7337f` |
| 4 | U4/U6 re-gates returned CLEAN on any ledger error (inverse of the PD-3 they cite). | **CLOSED** `de7337f` |
| 5 | The **$50 cap's live default was unproven** — conftest raised it to 100000 for every test. | **CLOSED** `7ade8a6` |
| 6 | A **truncated spend line silently re-opened budget**; the test named for the guarantee corrupted a *non-spend* line. | **CLOSED** `7ade8a6` |
| 7 | An **unjudged seed animated** — spending Veo money — despite a "Fail-closed" docstring. | **CLOSED** `1ca345b` |
| 8 | Curator returned the **raw uncurated URL list** when every fetch failed; FIX-D1's "keep-all is dead" was false. | **CLOSED** `d50701a` |
| 9 | `invented_product` **never asked** on the classic path → the topshoes fabricated-hero-sneaker shipped on every classic poster. | **CLOSED** `d50701a` |
| 11 | The **PAID dashboard ignored the registry** (raw live pool); `if approved:` resurrected rejected peers; a corrupt registry auto-approved everyone. | **CLOSED** `d50701a` |
| P1 | The **circular trends gate** (indexed candidates as their own evidence; reject rate structurally zero) + the three false "Ledger-gated" claims it licensed. | **DELETED** `67196d5` |
| P0 | *"Rising interest"* was an `else` branch over data with **no time-series**. | **CLOSED** `a3c9aa6` |
| — | `DecisionKind.ABSTAIN_NO_TARGET` — refusing to judge ≠ "keep spending". KEEP was always an abstention wearing an endorsement's name. | **CLOSED** `a7cc698` |
| 10 | strategy `topic` ungated → becomes the poster headline. | **TICKETED** |
| 12 | Compositor ships a QA-failed clip if the retry can't be judged (now explicit `is not False`). | **ACCEPTED-WITH-REASON** |
| — | The C2 subject judge is wired at **1 of 8** ledger sites; the export says `passes: true` either way. | **TICKETED** |
| — | Remaining rows of the 42. | **TICKETED** |

**Two audit claims did NOT reproduce, and are reported rather than quietly "fixed":** (a) #4's
`profile=None` is not the trigger — `from_profile(None)` returns an EMPTY ledger, so every hard
claim is unsourced and it already failed closed (latent, not routine); (b) `gate_active: True` is
not a lie — `build_poster_audit` builds its ledger UNGUARDED, so a failure raises and no trail is
emitted. Discipline used throughout: **every pinned-permissive test was FLIPPED FIRST and watched
go RED** before any code changed. The **SSRF guard was the one artifact that was already
fail-CLOSED on all five branches** and proved so by defeating the conftest DNS seam — that is the
template every fix above now follows.

**⚠ CORRECTION — U1 GATE IS NOT CURRENTLY REPRODUCIBLE AT 93/92/92 (re-derived live 2026-07-15).**
A live `python -m benchmark.grade_u1` today returns **objective 86% (12/14) · destination 83%
(10/12) · combined 83% · GATE PASS** (bar = `U1_GATE = 0.80`, `benchmark/grade_u1.py:23`) — NOT the
"objective 93% / destination 92% / combined 92%" recorded below (entry "DIRECTOR ROUND (2026-07-11
late)", ~line 700). The delta is fully accounted for, and none of it is a builder error:
1. **Two objective "misses" are missing CACHE, not wrong deductions.** `benchmark/runner.py:
   find_latest_scrape_dir` returns the NEWEST scrape dir even when it holds no `business_profile.json`.
   `raw_african`'s newest dirs (`scrapes/rawafrican_net_20260713_*`) are scrape-only (manifest only);
   its profile lives in `…_20260711_141527`. `mumm` is bot-walled (only the 2026-05-30 dir has a
   profile). grade_u1 scores "no cached profile" as a MISS — so the metric conflates *no data* with
   *wrong answer*, which is none of the three taxonomy categories (builder-error / profile-weakness /
   label-uncertainty). **Objective over the 12 URLs that HAVE a cached profile: 12/12 = 100%.**
2. **`assalam` destination flipped match→miss on FROZEN inputs.** Its profile
   (`scrapes/assih_com_20260711_164437/business_profile.json`) has mtime 2026-07-11 19:58 and its
   label has been frozen since the 2026-07-12 amendment (`a6ffaed`, mtime 09:15). Same profile, same
   label, different answer. `build_campaign_objective` is a **live Gemini call** (`media_plan/
   builder.py`; `grade_u1.py:9`, and `_load_env()` ~line 148 loads the repo-root `.env`), and
   `temperature=0.0` is not a determinism guarantee — `gemini-2.5-pro` is also a moving alias. The
   deterministic layer (`conversion_signals`) correctly enumerates 4 evidence-backed candidates for
   assalam (whatsapp / phone_call / lead_form / website); the **LLM picks one**, and that pick is
   where the variance lives.
**Consequences to state plainly:** (a) the gate's run-to-run variance has NEVER been quantified — it
has only ever been run once per measurement, so 93 vs 86 cannot be attributed with confidence;
(b) `grade_u1` is a **PAID** gate (one live Gemini call per labelled URL) and must not be described as
offline; (c) fixes: prefer the newest scrape dir that actually CONTAINS a profile, exclude (don't
score) uncached URLs, pin a model snapshot, and report mean±range over N runs. Until then, quote the
**live** number (86/83/83 PASS), not 93/92/92.

**CLOSEOUT WAVE 3 (autonomous, additive; suite 1595).** **Reel brand-safety gate in CODE**
(`reel/coherence.py:coherence_lint`, PD-1): the director is *told* to keep third-party marks out
of frame — now a deterministic check *enforces* it. A scene whose Veo prompt renders a
logo/trademark that is NOT the brand's own (or a hard mark: swoosh / golden arches / ® / ™) is
flagged fail-closed, next to the existing SCREENS check. Precise by design — a mark must carry an
explicit logo/trademark word, so ordinary nouns ("orange", "apple") never trip it; the brand's own
and generic-self ("our/brand logo") marks pass. +4 tests. **Poster OCR verbatim gate — MOOT by
design (documented, no code):** the classic poster composites text as HTML/CSS rendered in a
browser (template.py), so its text is exact by construction; OCR verification only matters for
MODEL-drawn text, which is the oneshot engine — already gated by VERBATIM_RENDER_CONTRACT +
script_wellformed.

**CLOSEOUT WAVE 2 (autonomous, additive; suite 1591).** (a) **U6 Launch Bundle CLI**
(`launch_bundle/__main__.py`): `python -m launch_bundle <media_plan.json | run_dir | result.json>`
assembles the Ads-Manager-ready DRAFT bundle from a completed run — completing the chain
URL→scrape→profile→SWOT/TOWS→media_plan→**launch_bundle** as a usable deliverable (Meta campaign
plan + compliance + U4 lint + U5 advisor + content-bound approval token; PAUSED/DRAFT, PD-7; pure
reader/assembler, no network/spend). +4 tests. (b) **Adversarial SSRF pack**
(`tests/test_ssrf_coverage.py`): pins `is_safe_public_url` (the single fetch choke) against
IP-literal internals, non-http schemes, hostname→metadata-IP rebinding, mixed public+private
resolution (fail-closed), DNS-failure (fail-closed), and the reel reference-image sink — its
IP-classification had NO direct test before. +18 tests. (c) **LLM cost foot-gun guard**
(`tests/test_llm_extractor.py`): asserts the model `default_caller` actually resolves to is in
`_PRICING`, so a future default-model switch can't silently zero every cost report. Verified
already-wired (no code change): **T-3** text cost metering flows caller→`_cost_for`→
`telemetry.record(cost_usd=)`→`run_cost()`→dashboard; **T-TOWS-ACTION** ships as
`TowsResult.priority_actions` (ranked `PriorityAction`s, horizon now/next/later); SSRF guard is
already wired at every external-input fetch source.

**CLOSEOUT FOLLOW-UPS (autonomous, additive; suite 1568).** (a) **U4 rewrite re-gate** (`policy_linter/rewrite.py`, spec §6-W7): `suggest_and_regate` — an LLM `suggested_rewrite` can replace a violating line ONLY after it re-PASSES BOTH the Policy Linter AND the Evidence Ledger (a still-violating or claim-smuggling rewrite is rejected; PD-1/PD-3; injected caller → hermetic). +5 tests. (b) **U9 Tier-3 `snapshot_parser`** (`social_intel/providers/snapshot_parser.py`): parses HUMAN-SAVED page HTML (PD-10 — no login/automation; stdlib `html.parser`, no new dep), authors hashed at ingestion (PD-6); documented `data-author` snapshot format (real FB/IG selectors pending owner raw samples, A5) + a committed demo fixture. +4 tests. (c) **doc-drift**: fixed the stale `poster/oneshot.py` "NOT wired" docstring — oneshot is the DEFAULT poster engine (`poster/schemas.py:149`). (T-1 benchmark naming = not reproducible/already resolved.) (d) **U1→CLI** (`competitor/full_run.py`): the grounded `MediaPlan` now assembles as a post-TOWS stage of the main run (guarded try/except, sibling `media_plan.json`, `--no-media-plan` opt-out) — the media-buying loop is reachable from the real entrypoint, not just tests. (e) **ElevenLabs TTS backend** (`reel/voiceover.py`): EXPLICIT-only (`backend="elevenlabs"`/`REEL_TTS_BACKEND=elevenlabs`; the free-edge auto-default is byte-unchanged), staged for the owner's Creator upgrade to swap the Egyptian voice (per the render#3 deliverable). Degrades to None without `ELEVENLABS_API_KEY`. +4 tests. (f) **U2→SWOT bridge** (`competitor/brand_signals.py:threats_from_ad_presence` + `swot.synthesize_swot(ad_presence=...)`): an ad-library `AdPresence` becomes graded SWOT Threats (proven-winner = validated, active = directional), cited to "Meta Ad Library"; default `None` is regression-safe — flows the instant a `META_ADS_TOKEN` lands. +3 tests. (g) **test hermeticity + spend guard** (`tests/conftest.py`): an autouse two-seam network guard (getaddrinfo remaps hostnames to a fixed global IP so `is_safe_public_url` still resolves; connect refuses every non-loopback address) that ROOT-FIXED an intermittent multi-minute full-suite hang — `render_creative_reel`'s seed-gen reached a real Vertex image call (`generate_oneshot_poster`) with no timeout, which also leaked spend on an ADC machine. Suite is now deterministic (~61s, was masking as ~12min) and no test can spend. `PYTEST_ALLOW_NETWORK=1` opts out for live runs.

**CLOSED LOOP — governance closes the loop (media-buying loop; suite 1552).** The distinctive v2.2 §6-W8 flow: `launch_bundle/loop.py:run_closed_loop` chains performance → `decision_engine.evaluate` → on ROTATE_CREATIVE/KILL_AND_REPLACE a programmatic **variation** (injected `variation_fn` — decoupled from the creative engine, hermetic) → **RE-GATE the variant through BOTH the Evidence Ledger (grounding) AND the Policy Linter (U4)** → a fresh DRAFT `LaunchBundle` delta for a human. Nothing publishes and no budget moves (PD-7). Verified: fatigue→rotate→clean-variant bundled DRAFT; a "نتيجة مضمونة 100%" variant REJECTED by both gates; winner→no_change; loser→kill→variant. Also shipped `docs/feature_ledger.md` (the DoD honesty artifact — Shipped&Gated / Simulated / Roadmap / Rejected per unit). +4 hermetic tests.

**U6 — LAUNCH BUNDLE (media-buying loop, the deliverable; suite 1552).** The Ads-Manager-ready, human-gated handoff (v2.2 §5/§6-W8): new `launch_bundle/` package. `campaign_plan.py` flattens a grounded U1 `MediaPlan` into the Meta Campaign→AdSet→Ad hierarchy (one campaign per objective, objectives as the EXACT Meta API strings, `status="PAUSED"` by construction — never live, PD-7; geo/persona/KPI from the plan). `bundle.assemble_launch_bundle` reuses U1 + `grounding.compliance.build_compliance_sheet` + U4 `lint_campaign` (over the creative + shipped copy) + U5 `build_missing_info_report`, derives a `PrereqChecklist` (pixel/whatsapp/page-access/creative-pending from destinations), and stamps a DRAFT `ApprovalDraft` whose `approval_token=sha256(campaign_plan)` is CONTENT-BOUND (approving plan A never carries to a mutated B; mirrors render3's token). Writes `campaign_plan.json` + `compliance.json` + `approval.json` + an Ads-Manager import CSV (openpyxl not bundled → CSV interim, JSON load-bearing; xlsx = roadmap) into a dedicated `outputs/launch_bundle/<slug>/`. GATE MET: Meta hierarchy + exact objective strings, embedded compliance/lint/advisor, DRAFT+token that flips on mutation, all files written. +8 hermetic tests.

**U5 — MISSING-INFO & CONFIDENCE ADVISOR (media-buying loop; suite 1552).** New `advisor/` package (v2.2 §5/§6-W7): `build_missing_info_report` formalizes the inlined `dashboard/build.py:_honesty_surface` into typed `Gap{field,why_it_matters,fills_via,severity}` + risks + per-axis grounded/ungrounded lists + missing_elements — reading the REAL objects (`SWOT.mode`, `MediaPlan/Persona/CampaignObjective.is_grounded`, `KPITarget.target_value`, the empty-CompetitorSet flag). **Deliberately NO composite "confidence %"** (§10 forbids magic rolled-up scores) — honesty is per-signal. Deterministic, no LLM. GATE MET: a weak plan surfaces all 5 gap classes; a healthy plan zero; no fabricated number. +7 hermetic tests. `_honesty_surface` left untouched (dashboard de-dup = a later guarded commit).

**U4 — META POLICY LINTER (media-buying loop, Gate ②; suite 1552).** Deterministic ad-policy gate (v2.2 §5/§6-W7): new `policy_linter/` package (additive, PD-1 no LLM/network). `rules.py` = compiled bilingual (ar+en) detectors for the Meta policy classes — `personal_attributes` ("هل تعاني…؟"/"do you suffer from" — implying a personal condition) + `guaranteed_results` ("نتيجة مضمونة"/"guaranteed results"/"عائد مضمون") are UNIVERSAL; `before_after` ("قبل وبعد"/"before/after") + `medical_claims` ("علاج نهائي"/"permanent cure") are scoped to the medical+skincare category groups (reusing `poster/contracts.py:_COMPLIANCE_BY_CATEGORY`'s category keys; default = brand-safety only, D-8). `linter.lint_campaign(category, copy_surfaces)` returns a `PolicyLintReport{verdict, findings, surfaces_checked}` whose `verdict="VIOLATIONS"` iff any `block` finding — RECOMPUTED IN CODE (PD-1, never self-graded); `surfaces_from_compliance_sheet` pulls the shipped copy so U6 lints exactly what ships. GATE MET: seeded ar+en violations across all 4 classes caught with ZERO false negatives on block rules; clean copy PASSes; correct category scoping. +14 hermetic tests. The LLM `suggested_rewrite` re-gate loop (rewrite must re-pass Ledger+Linter) is a planned follow-up commit. Additive → zero regression; feeds U6 + the closed loop.

**U3 — DECISION ENGINE + SIMULATOR (media-buying loop; suite 1519).** The governance half of the media-buying system (v2.2 §5/§6-W6), buildable NOW because performance is SIMULATED or CSV-imported — no live ad account needed. New `decision_engine/` package (additive, PD-2): `schemas.py` (`PerformanceSnapshot` — one schema for sim AND csv; `CampaignPolicy` reusing U1's `KPITarget`; `DecisionKind` KEEP/WATCH/REDUCE_BUDGET/SCALE/ROTATE_CREATIVE/KILL_AND_REPLACE/NO_DECISION; `Decision`). `engine.evaluate(policy, snapshots)` is a PURE function (PD-1 — deterministic, no I/O, no LLM): guardrails (min hours/conversions/spend → NO_DECISION) → fatigue (freq > 3.5 AND CTR fell > 25% → ROTATE_CREATIVE) → **learning-phase guardrail T-4 (< ~50 conversions → WATCH, NEVER kill/scale)** → the ladder (CPL ≤ target → SCALE ≤20% step; CPL > 1.5× → KILL_AND_REPLACE; between → REDUCE_BUDGET). `simulator.simulate(winner|loser|fatigue)` = seeded deterministic daily series (no wall-clock); `csv_import` round-trips a documented client CSV. GATE MET: winner→SCALE, loser→KILL_AND_REPLACE, fatigue→ROTATE_CREATIVE, premature→NO_DECISION, <50-conv→WATCH, CSV round-trip. +18 hermetic tests. Nothing imports it into a wired path → zero regression; feeds U6 + the closed loop.

**REEL DIRECTOR OVERHAUL — real product-on-screen compositing (Phase D; suite 1501).** Closes the "product hidden" root cause: video models can't render legible product UI, so a software-product scene keeps its GENERATED screen abstract (the SCREENS rule) AND the brand's REAL screenshot/photo is composited ON TOP — the `product_overlay="real_screen"` route the Phase-A/B contracts+gate produce. New `reel/product_overlay.py:composite_product_screen` = one ffmpeg overlay pass (mirrors `text_overlay.overlay_timeline_on_video`): the real asset is scaled into a generic lower-two-thirds inset with a short alpha fade, source audio copied through; best-effort (returns the plain clip on any failure, so it never loses a render). `ReelScene` gains `product_asset_url`/`product_placement`; `build_creative_storyboard(profile=...)` resolves the asset per hero-product scene — `real_photo` → the scene's own real photo, `real_screen` → the brand's `visual.homepage_screenshot_path` (else the scene photo). `reel/compositor.py:render_reel` runs the overlay pass right after the text-free clip passes scene-QA and BEFORE the caption overlay (which rides on top unchanged); a URL asset is fetched via the SSRF-guarded `_load_reference_image`, a local path used directly; scenes without an asset are byte-identical to today. Feasibility (called out): compositing only fires when a real asset resolves — when none exists the honest outcome is "abstract screen, no composite", but the Phase-B gate still forces a NON-screen hero-product moment so product visibility never depends on an asset; perfect in-device screen registration is out of scope for v1 (generic inset). +6 hermetic tests (default box, overlay graph, missing-asset + ffmpeg-failure fallbacks, storyboard mapping real_screen/real_photo/none, compositor composites only asset-bearing scenes).

**REEL DIRECTOR OVERHAUL — render-level sound design (Phase C; suite 1501).** The creative reel was SILENT but for the VO (the owner's "sound is half the feel" was unaddressed on the live path; `music_mood` was authored but never consumed). Phase C makes the render play the plan's authored sound. `reel/sound.py:synth_sound_bed` gains `mood` (a universal `_MOOD_TABLE` tunes the ambient pad's tone — darker/sparser for tension, brighter/fuller for confidence — keyword-matched against the plan's `sound_design`) and `sfx_beats` (per-scene cues: whoosh on a hard motion, impact on a reveal, a new bright chime blip on the pay-off), all pure ffmpeg synth, positional-signature back-compatible. `reel/creative.py:render_creative_reel` now, when no real `--music` is supplied, synthesizes a bed sized to the storyboard duration (no dead-air tail), mood from `creative.sound_design`, and `sfx_beats` from each scene's `sfx` at cumulative scene-start times, and hands it to the compositor as `music_path` (which already ducks it under the VO). Real supplied music still wins. Best-effort (a synth failure just leaves today's silence). +6 hermetic tests (mood tunes the pad, sfx cues placed at their times, out-of-range skipped, positional back-compat, render synthesizes a bed when no music + total==duration, supplied music not overridden).

**VERTEX SPEND GUARD — meter + cap the expensive Google renders (suite 1489).** OWNER SCARE 2026-07-14: $228 of a $300 Vertex credit gone in ~4 days with ZERO local tracking. Diagnosed: no runaway process (2 idle python procs); the burn was **Veo reel renders** (render3/guardian alone rendered 3× — assembly/v3/v4, 6-8 Veo i2v clips each) + **image gen** (42 posters) — the genuinely expensive ops; text/analysis is cents. The old `spend.md` only tracked Apify + WaveSpeed, so renders burned credit **invisibly**. New `vertex_spend.py` (mirrors `social_intel/spend_guard.py`): meters every Veo render (by second) and every Nano-Banana/gemini image (per model tier — Pro vs flash) to a gitignored `runs/vertex_spend.jsonl`, enforces a monthly cap (`VERTEX_MONTHLY_CAP_USD`, default $50), and REFUSES a render (raises `SpendRefused`, BEFORE any client/network work) once the month's metered total would breach the cap. Wired into the 3 paid call sites: `reel/video_provider.py:VeoProvider.generate` (fail-fast pre-flight + record on success), `reel/render3/nano.py:generate_with_refs`, `poster/oneshot.py:generate_oneshot_poster`. Note: Imagen is being deprecated by Google (erroring) → image stack is Nano Banana (`gemini-3.1-flash-image-preview` fast + `gemini-3-pro-image-preview` legible-text). Rates are conservative estimates for the guard; Cloud Billing is authoritative. `python -m vertex_spend` prints the running monthly total + per-kind breakdown. Also complements the reel coherence gate (Phase B), which cuts Veo waste by catching bad plans at the cheap text stage BEFORE the expensive render. +8 hermetic tests (estimates, month-scoped sum, cap allow/refuse, guard raises over cap, summary, corrupt-line-never-opens-budget, VeoProvider refuses-before-network); conftest autouse fixture isolates the ledger so no test pollutes it.

**REEL DIRECTOR OVERHAUL — hybrid coherence gate + auto-correct loop (Phase B; suite 1481).** The code-enforced half of Phase A's contracts: new `reel/coherence.py`. `coherence_lint` deterministically re-checks all 8 classes on a `CreativeReel` (≥1 hero-product scene; no abstract/blur subject unless routed to `real_screen`; hook works-muted + not the cliché stressed-person-at-a-laptop regex; exactly one final CTA with matching caption/VO/`reel.cta`, no earlier competing CTA; locked dialect + no dead-air scene; captions non-verbatim with a role, ≤5 words — the CTA scene EXEMPT since its caption SHOULD equal its VO; a grounded `brand_unique_claim` actually used in the copy; no intra-shot cuts) — worst-first, each with a fix, scored, folding in the legacy `plan_eval` stranger/caption checks. `critic_review` is a cheap LLM "creative director" judging the fuzzy axes (hook-stops-scroll, works-muted, product-shown, dialect-consistent, one-voice, cta-unified, brand-specific, caption-strategy) with its **verdict RECOMPUTED IN CODE** (the render3 g2 invariant — the model can't self-pass); honest-degrade to deterministic-only with no caller. `plan_reel_gated` is the prevention loop (mirrors `prepare_render3`): author → gate → feed failures back as a cumulative `fix_note` → regenerate, keep the best-scoring plan, bounded (`max_rounds=2`), never raises (a zero-spend text layer → ship-with-surfaced-issues, not fail-closed). WIRED into `hitl_preview`/`hitl_refine` (the gate runs BEFORE the owner sees the plan; residual issues echoed into the preview next to the ⚠ grounding flags) and `render_creative_reel` (non-approved → gated author; APPROVED `plan_override` → coherence advisory-log ONLY, never regenerated — HITL sanctity preserved). VERIFIED LIVE end-to-end on the real Vodafone preview (the reel that triggered this overhaul): the gated plan fixed **6 of the 7 owner-flagged defects automatically** — product now SHOWN (S2/S3/S6 `hero_product` + `real_screen` composite), ONE unified CTA ("اكتشف المتجر الرقمي دلوقتي" in caption+VO+cta), dialect locked Egyptian colloquial across every line, full sound arc + per-scene sfx (typing→whoosh→chime→flourish), captions role-tagged & non-duplicating, grounded brand-unique claim in copy — and the 7th (a still-cliché "overwhelmed by apps" hook) was correctly REFUSED by the gate and surfaced in the preview rather than shipped silently. +21 hermetic tests (each defect class caught, clean plan passes, CTA-scene verbatim-exempt, critic verdict recomputed both directions, degrade w/o caller, cumulative-fix_note retry keeps best, never-infinite-loop, director-can't-plan → None, approved-plan advisory-only, non-approved routes through gate). Phase C (render sound) + D (product compositing) follow.

**REEL DIRECTOR OVERHAUL — creative contracts + schema + data-wiring (Phase A; suite 1471).** The LIVE reel path (`reel/creative_director.py` — NOT render3, which the dashboard never calls) produced a Vodafone reel with a class of creative-director defects (product never shown / all "abstract·blurred UI", cliché VO-dependent hook, split CTA, dialect jumping عامية↔فصحى, dead-air scenes, captions verbatim-repeating the VO, nothing brand-specific). Owner's ruling: these are SYSTEMATIC — the director + gates must PREVENT them every run, universally (any business, no vertical hacks). Phase A = the director layer: new `reel/reel_contracts.py` with 8 `[BRACKET-TAG]` craft contracts (PRODUCT_VISIBLE, ARRESTING_HOOK works-muted, ONE_CTA, UNIFIED_VOICE, SOUND_DESIGN, CAPTION_STRATEGY, BRAND_SPECIFIC, FEASIBILITY) composed into `_system_prompt` after CRAFT_CONTRACT; the SCREENS rule keeps its "abstract UI" bar but adds a hero-product `product_overlay="real_screen"` exception (the "product hidden" root cause — a software product is now SHOWN via post-composite, Phase D); the old "one or two scenes SHOULD be silent" line (which invited dead air) is replaced with "silent only if sound carries it". Additive schema (back-compatible — old `<slug>_reel_plan.json` still loads): scene `hero_product/product_overlay/caption_role/sfx/sound_mood`, reel `dialect/sound_design/brand_unique_claim`, threaded through `_ReelResponse` + the field-by-field reconstruction loop (the silent-drop guard). DATA now reaches the director: Ledger-gated brand-unique differentiators (`competitor/brand_signals.strengths_from_profile` + `other_unique_insights`) via a new `_brand_unique_block`, and per-photo depiction labels (`image_select.select_brand_photos(return_verdicts=True)` — the `PhotoVerdict.subject` was computed then discarded) via a new `photo_subjects` arg. Dialect is CODE-LOCKED per audience (`_locked_register`: Arabic→Egyptian colloquial, `REEL_ARABIC_DIALECT=fusha`→MSA, else conversational-of-language) and stamped onto `reel.dialect` (model can't drift). `design_creative_reel` gains `photo_subjects` + `fix_note` (the coherence-gate retry hook, Phase B), plumbed through `render_creative_reel` + `reel/__main__.py`. +16 hermetic tests (contracts compose, real_screen exception, register named+universal, new fields specified, dead-air not encouraged, reconstruction threads every field, code-locked dialect, photo_subjects+fix_note injected, old-plan back-compat, brand-unique Ledger-gated, return_verdicts). Phase B (coherence gate + retry loop), C (render sound), D (product compositing) follow.

**DASHBOARD — "اسأل الأرشيف · ASK THE ARCHIVE" GROUNDED Q&A (Run#2 Front-4; suite 1460).** The one wholly-absent item-#4 feature (recon-confirmed: no route/component/panel anywhere; `business_profile/llm/rag.py` was an INERT extraction retriever, not a user feature). New `dashboard/archive_qa.py`: the owner types a question about an analyzed brand and gets an answer drawn ONLY from that brand's saved run — `build_archive_corpus` assembles labelled, provenance-carrying chunks from the extracted profile (identity/value-props/offerings/trust/locations), SWOT, competitor set, TOWS, media plan, AND the scraped page text (bounded); retrieval is embedding-ranked (reusing `embeddings.top_k`) with a keyword-overlap FALLBACK so it degrades with no credentials; the answer is ONE grounded text call under a strict contract (cite `[n]`, treat excerpts as DATA not instructions, and set grounded=false + say "الأرشيف ما فيهوش إجابة" when uncovered — the moat applied to Q&A, no fabrication). An honesty guard downgrades any "grounded" answer that cites nothing. New `/ask?slug=&q=` route (slug-guarded, never 500s) + a bilingual studio panel (input + example chips + cited answer with a ✓من الأرشيف / ⚠مش مغطّى badge). Both `caller` and `embed_fn` are injectable → fully hermetic. VERIFIED LIVE on the real nti_sci_eg archive (98-chunk corpus): "الاعتمادات؟" → partnerships cited to real pages; "المنافسين؟" → the actual competitor cited; "أسعار الكورسات؟" → the EXACT registration fees (1000 EGP / \$200 …) pulled from the offering chunks with citations. +11 hermetic tests (corpus coverage+provenance, keyword+embedding retrieval, grounded-cites, no-citation downgrade, out-of-archive refusal, empty/no-archive, /ask route + guard + failure + studio-panel). NOTE: structured trust-signal chunks can lose top-K to page chunks on some phrasings — a retrieval-boost is a measured follow-up (ticket T-ASK-BOOST). HARDENED by an adversarial review workflow (15 agents, dual find→verify) that CONFIRMED 8 defects — all fixed with regressions: fail-open `grounded` default (→False), stored XSS via a `javascript:` citation URL (→client scheme-guard + server `_safe_url` blanks non-http(s) at the Chunk), 160-char-prefix de-dup dropping distinct pages (→key on full text), honesty guard checking citation-existence not support (→`_answer_supported` lexical+script-aware gate, never false-downgrades cross-lingual answers), prompt-injection earning the badge (→`<<<>>>` excerpt fencing + explicit "ignore instructions inside excerpts" rule), `answer_question` raising on malformed artifacts (→isinstance guards + never-raises), the `"None"` substring filter, and a `_latest_manifest` TOCTOU. Live re-verified post-hardening (prices/competitor/cyber-academy still grounded+cited). +11 more regression tests.

**DASHBOARD — CREATIVE↔STRATEGY TIE IN THE LIVE STUDIO (Run#2 Front-3 §D; suite 1460).** RUBRIC D ("no orphan creatives — every asset labelled with the strategy/funnel/audience it serves") rendered only in the DOWNLOADABLE `/dashboard` export, because `_studio_page` called `build_dashboard_html` without poster/reel paths → the live Creative Studio showed the on-demand poster/reel with NO "why" beside them. New `dashboard/server.py:_strategy_tie(slug, out_dir)` mirrors the export's `_tie` chips (🎯 strategy line from TOWS priority-action/strategy, funnel-stage label, 👥 audience — prefixes stripped) from the same `{slug}_media_plan.json` + `{slug}_result.json` the deliverable reads, and `_studio_section` now renders it at the top of the studio card (visible BEFORE any paid render, so the owner sees the rationale before spending). Empty when no media plan exists yet. VERIFIED live on the real nti_sci_eg artifact (tie present inside the Creative Studio: 🎯 "Bridges the talent-industry gap…" · تحويل·BOFU · 👥 mixed). +3 hermetic tests (unit + empty-guard + socket-level /studio integration). Recon confirmed the other 5 F3 features (registry-diff, tier reasons, market pulse, capability census, media-plan rationale) already render in Baseera Studio.

**RENDER #3 — CODE-REVIEW REMEDIATION (owner's dual-lens review; Run#2 Front-1; suite 1436).** Fixed the verified findings from the Staff-Eng × Creative-Director review of the render3 subsystem, each with a hermetic regression test: **C1 (critical)** — `seed_prompt` + `veo_prompt` still hardcoded 'her'/'the woman' (the gender refactor was only half-applied to `character_block`), so a male protagonist's per-shot seed AND Veo prompts still said "the woman's face / her outfit"; both now route through `_gender_words`. **C2 (high, the moat)** — the VO↔EvidenceLedger trace used a silent `except: pass`, so a future typo would quietly disable brand-safety grounding and pass an ungrounded VO as clean; it now FAILS VISIBLE (logs, sets `G1Report.moat_skipped`, and adds a G1 issue → surfaced at HITL #1). **H1** — G1 now checks shot durations sum to the target runtime (30±5s); **L1** — and that shot ids are unique (seed/clip filenames are id-named → collision guard). **H2 (spend-safety)** — the HITL law was comment-only; `continue_render3` could fire seeds+Veo with no code gate. Added a content-bound `approval_token` (sha256 of the exact approved treatment): `prepare_render3` emits it, `continue_render3` refuses all spend unless the caller passes it back — so approval is EXPLICIT (a silent/automated path is refused) and CONTENT-BOUND (approving concept A can't authorize a mutated concept B). **M4** — `run_g2_loop` blind-regenerated ALL seeds on an arc/grade failure with no named frame; re-rolling identical shots+locked-blocks can't fix a story-ordering problem, so it now fails closed immediately (zero wasted image spend) pointing at a treatment-level fix. **L2** — already satisfied (`.gitignore` has `__pycache__/` + `*.pyc`; zero tracked pyc). Deferred: **M3** (pro-lane still uses substring cues; an explicit `Shot.legible_text` field is a separate measured change — ticketed). +8 hermetic tests.

**SCRAPER TWO-PHASE FOOTPRINT (owner ruling; Run#2 Front-2; suite 1431).** FULL homepage now loads ABOVE-fold images (logo/header/hero -> real logo dims + clean palette) then blocks BELOW-fold product-image BYTES during the scroll — URLs still collected from the DOM; logo-like URLs whitelisted (`_is_logo_like`). `TWO_PHASE_IMAGE_FETCH=True` default; `--thorough` restores load-every-image. REGRESSION PROOF (controlled deterministic e2e): thorough 21 imgs vs two-phase 1 (logo) + 20 blocked = ~95% image-byte cut with IDENTICAL text+html+logo (zero content regression). All 429 fixes kept. +7 tests.

**REEL render#2 -> render#3 (the motion-restructure's measured artifact).** #2: protagonist DRIFT (4+ different people across frames), junk sci-fi screens, no visual thread — G2 calibration FAILs it (junk_screens=[5]). #3 (nti_render3_final.mp4): ONE locked protagonist (Karim, G2 same_person=T across all seeds, zero regens), one evolving motif (employee badge->keycard), 3-act colour thread, REAL NTI building+tunnel grounding, testimonial to-camera with REAL VEED lip-sync, blue-logo watermark + creative end-card, clean single masri VO. Voice = temp openai (Egyptian ElevenLabs pending Creator upgrade). Front-1 of Run#2.

**RENDER #3 — PRO LANE FOR ANY LEGIBLE IN-FRAME TEXT (suite 1425).** The seed pro-lane trigger only fired on REAL_CONTENT screens, so a legible non-screen element (an approved keycard title) would have used the fast model and risked garbling. `_needs_pro_lane` now also fires when the action/camera calls for readable text (legible / clearly see / readable / the title). LIVE (guardian EXECUTE): shots 4 (terminal) + 6 (keycard 'KARIM' + title) used the pro model; all 6 seeds generated with the card attached and G2 PASSED on the FIRST attempt (same_person, same_world_grade, junk_screens=[], arc_readable — zero regens). +1 hermetic test.

**RENDER #3 — GENDER-AWARE CHARACTER (male protagonists now render correctly; suite 1424).** `character_block` + `character_sheet_prompt` HARDCODED 'Egyptian woman' / she / her — the whole render3 character system was female-only (built for Layla), so the male pharmacist/guardian protagonist rendered as a WOMAN. Added `CharacterSheet.presenting_gender` ('man'|'woman', R7 now requires it) and made both prompt builders gender-aware (noun + pronouns + the lock sentence; a man has hijab null, lock on hair not hijab). Empty default preserves the original female phrasing for pre-field treatments. LIVE: Karim's card now renders a consistent man across all 3 views (C2-verified: same person, scar his-left, watch his-left). +1 hermetic test.

**CREATIVE VARIETY ENGINE (4/N) — DOMAIN-TRUTH GROUNDING (suite 1423).** Owner caught a real grounding failure: the sampler cast a pharmacist for NTI (an ICT/cybersecurity/software/data-science TRAINING institute). Two fixes: (a) the Director now receives the brand's REAL audience_signals + a DOMAIN TRUTH rule (R10) — the protagonist must be a real audience-signal person ENTERING/ADVANCING in the brand's ACTUAL field, never an invented off-domain use case; each concept names `audience_signal_served`. (b) the sampler bug: `small-business-owner`/`parent`/`craftsman` were mis-tagged `universal`, making the audience filter a no-op — removed, so a tech-training brand no longer casts a shopkeeper. +2 hermetic regression tests (education excludes business-owner slot; domain-truth reaches the prompt).

**CREATIVE VARIETY ENGINE (3/N) — CONCEPT-LOCK + TEXT-ONLY APPROVAL STEP (suite 1421).** §5 HITL: after the owner picks one of the 3 candidates, `direct_reel(concept_lock=...)` realizes the FULL treatment for EXACTLY that concept (never re-inventing), and the G1 feedback loop cleans it via `fix_note` corrective retries. `prepare_render3(concept_lock=, generate_card=False)` returns the CLEAN treatment TEXT-ONLY for concept approval BEFORE any character card is spent (card follows on approval, per pack §0). LIVE: the owner-picked pharmacist concept regenerated G1-clean in 2 retries (smile-in-shot-5 + unsourced-VO fixed). +4 hermetic tests.

**CREATIVE VARIETY ENGINE (2/N) — D1 3-CONCEPT OUTPUT + LOCALE (suite 1417).** The Director gains a variety mode (`direct_reel_concepts`): the sampled creative direction (archetype/motif-domain/protagonist-slot) + an AVOID list are injected as strong defaults; R9 NOVELTY added; the output is a `DirectorOutput` = 3 mutually-distinct candidate stubs + the ranked pick + the FULL treatment for the pick + a direction_note. LOCALE is now a parameter (Egypt the DEFAULT value, not a hardcoded rule) — R1-R8 unchanged (R8 keeps 'Egyptian Arabic' per the owner's unchanged list). `concepts_distinct` code-checks mutual distinctness (Jaccard). +6 hermetic tests.

**CREATIVE VARIETY ENGINE (1/N) — DIRECTION SAMPLER (suite 1412).** Owner directive: lock WITHIN a run, vary ACROSS runs — variety is a REQUIREMENT with a mechanism, not chance. New `creative/` package: three libraries (narrative_archetypes / motif_domains / protagonist_slots YAML, each entry tagged audience_fit) + `sampler.py` drawing one (archetype, motif-domain, protagonist-slot) triple FILTERED by the grounded profile's audience. Reproducible (seed + `forced=` re-injection of a logged triple), `avoid=` de-prioritises a client's recent runs, and `sample_distinct_directions` yields N candidates with distinct archetypes. +6 hermetic tests.

**RENDER #3 — G2 JUNK-SCREEN DETECTION STRENGTHENED (owner C1; suite 1405).** The G2 gate now flags sci-fi glow (neon/cyan LED strips, holographic panels) even when it could pass as ambient architectural light — closing the D-R3.5 gap where render#2's glowing frame 5 slipped junk_screens. Live-recalibrated x2: stable junk_screens=[5], verdict=FAIL, same_person=false; the calibration test pins `5 in junk_screens`. Blocking conjunction / locked blocks / spend order UNCHANGED — only junk-detection sensitivity. (D-R3.7)

**SCRAPER — HONEST 429 MESSAGING END-TO-END (suite 1405).** A rate-limit is not a block: telling
the user "the site may block scraping or be unreachable" on an HTTP 429 is misleading (the site
is fine and works again after a short cooldown). Now `competitor/full_run.py` (via the testable
`rate_limited_failure`) prints "rate-limited (HTTP 429) — wait about a minute and analyze again"
instead of "could not read this site", and the studio's final banner (`_stream_analyze`) watches
the streamed log and shows the same honest, actionable message rather than the generic
block/outage line. +3 hermetic tests (banner both ways + the 429 detector).

**SCRAPER — PATIENT HOMEPAGE 429 RETRY (the make-or-break fetch; suite 1402).** A homepage
HTTP 429 loses the ENTIRE scrape (0 pages -> "could not read this site"), yet a rate-limit
cooldown clears in tens of seconds — the old budget gave up in ~18s. `fetch_page` now takes a
`rate_limit_retries` arg granting a LARGER, 429-ONLY patience budget; the crawler passes
`HOMEPAGE_RATE_LIMIT_RETRIES=4` for the homepage only, so it waits out a cooldown
(6s,12s,18s,24s ~= 60s) while EVERY other failure and EVERY subpage still fails fast on
RETRY_ATTEMPTS. Verified hermetically: a 429-only budget (not network errors), correct backoff
schedule, default callers unchanged. +4 tests.

**SCRAPER FOOTPRINT — DROP TRACKING/ADS BEACONS ON EVERY FETCH (suite 1399).** MEASURED: the
topshoes.store homepage fires ~300 requests; ~14 are third-party analytics/ads/telemetry beacons
(GTM, TikTok, Facebook pixel, DoubleClick, Google Ads, Converted.in, Shopify telemetry) that
carry NO brand content and are never screenshotted. A blocklist (`_is_tracking_request`) now
aborts them on BOTH routers — LIGHT subpages (`_block_heavy_route`, already dropping
image/media/font) and the FULL homepage (`_block_tracking_route`, trackers ONLY so first-party
images/CSS still load for the screenshot). ~14 fewer requests/page x 41 pages ~= 570 per crawl,
lowering the chance of tripping a per-IP rate limit (the 429 cause) while speeding the crawl.
Never matches first-party content or the site CDN. +5 hermetic tests.

**STUDIO ANALYZE — CONCURRENCY GUARD (the REAL 429 root cause; suite 1396).** MEASURED
live: analyzing topshoes.store from the studio 429'd after 3 retries — yet the site serves
200 to sequential requests (it is behind Cloudflare, NOT blocking us). The studio log showed
TWO "analyzing" blocks: `/analyze` had NO concurrency guard (unlike `/generate`), so a
double-click / SSE-reconnect / second tab spawned TWO concurrent crawls of the same host —
and two crawls hammering the site at once is exactly what trips the rate limit. Fix
(`dashboard/server.py::_stream_analyze`): the existing `_INFLIGHT` `(slug, kind)` guard now
covers analyze too — a duplicate is rejected cleanly ("already being analyzed") instead of
racing. +1 hermetic test mirroring the generate guard (two threads -> one scrape).

**SCRAPER 429 ROOT FIX — HTTP 429/5xx ARE TRANSIENT, NOT "UNREADABLE" (suite 1395).**
MEASURED: bobana-eg.com returned HTTP 429 (Too-Many-Requests) on one burst and the whole
scrape died in 19s with 0 pages + "could not read this site" — but the same URL served 200
moments later (the site is NOT blocked). Root cause: `fetch_page` only retried
{NETWORK,RENDER,TIMEOUT}; a 429 fell to HTTP_ERROR and was treated as permanent. Fix
(`scraper/fetcher.py`): 429/500/502/503/504 now retry via `_is_transient`; a 429 backs off
FAR longer than a network blip (6s x attempt) and HONORS the server's `Retry-After` header
(new `FetchResult.retry_after_s`, capped at 20s so a run can't stall). A hard 4xx (404) still
fails immediately — no hammering. +5 hermetic tests (scripted _fetch_page_once, no network).

**RENDER #3 · COMMIT 4 — G1 CALIBRATED AGAINST THE LIVE DIRECTOR + HITL #1 REACHED (suite
1390).** Three measured drifts from live NTI runs fixed at the root (D-R3.6): R6 now
names `NONE` (the model was inventing `N/A`); safe screen tokens accept a descriptive
`:detail` suffix with the safety clause always riding along; G1 corrective retries raised
1 -> 3 with cumulative deduped feedback (text-only spend). **Live result: NTI treatment
passed G1 on the first corrective retry (VO 59 words), character sheet generated — the
HITL #1 package (big idea / logline / motif / VO / 6 shots / protagonist card) is now
AWAITING THE OWNER. Zero seed/Veo spend, exactly per the pack.**

**RENDER #3 · COMMIT 3 — THE ORCHESTRATOR: TWO HITL HARD STOPS + THE VEO INVARIANT (suite
1390).** `orchestrate.py`: `prepare_render3` = Director -> G1 (one corrective retry with
the lint issues fed back, then loud error) -> character sheet -> HITL #1 package
(treatment.json + locked_hashes.json + character_sheet.png). ZERO seed/Veo spend before the
owner approves the concept + protagonist card. `continue_render3` (post-approval only) =
seeds with the ref sheet ATTACHED to every request (+ optional real location photo /
screenshot, pro lane for legible screens) -> `run_g2_loop` BLOCKING -> explicit re-check
`if verdict != PASS: RuntimeError` -> Veo i2v per shot FROM the verified seed paths
(1080x1920). Hermetic proof: the Veo recorder shows ZERO calls when G2 fails, and
exactly-one-image-call (charsheet, no refs) during prepare. +4 tests.

**RENDER #3 · COMMIT 2 — LOCKED BLOCKS + NANO GENERATOR + G2 GATE, CALIBRATED LIVE (suite
1386).** `prompts.py`: STYLE_BLOCK/CHARACTER_BLOCK as pure functions with the §3 sha256
lock (`assert_locked` raises on a single drifted byte), §2 character-sheet prompt, §4 seed
prompt (identity anchor FIRST -> scene -> negatives LAST), §6 screen-rule expansion incl. the
attached-screenshot strongest form, §7 Veo prompt (ambient-only audio, match-cut end), §8
global negative as the single source of truth. `nano.py`: reference-conditioned generation
with lane fallback (3.1-flash-image-preview@global -> 2.5-flash-image@us-central1; pro lane
3-pro-image-preview), resolved model id logged per request, quota-aware backoff. `g2.py`:
numbered contact sheet + the BLOCKING verdict — PASS computed IN CODE from the booleans (the
model never self-passes), targeted regen <=3 then RuntimeError (Veo never fires).
**MANDATORY CALIBRATION RUN LIVE (D-R3.5): G2 correctly FAILED render #2's sheet** — 4
identity offenders, grade+arc false; CI pin behind RUN_LIVE_GATES=1 (hermetic CI policy).
+11 hermetic tests (+1 live-gated).

**RENDER #3 · COMMIT 1 — DIRECTOR + G1 (owner prompt pack adopted verbatim, suite 1375).**
New `reel/render3/` package. `director.py`: the §1 Director prompt VERBATIM (D1 concept + D2
Arabic VO + D3 storyboard in ONE strict-JSON call; hard rules R1-R8: one protagonist, 3-act
arc, one motif, color script, match cuts, screen rules, frozen character sheet, VO
evidence-traced) -> a validated ReelTreatment (retry once, then None — fail loud).
`g1_lint`: every checkable rule re-checked IN CODE (exactly-2 distinctive features, ≤4
locations, act order, smile-ban outside the final payoff, match_cut_out chain, screen-rule
whitelist, 55-75-word VO) + the VO<->EVIDENCE trace via ledger.audit_text (D-R3.4 — the moat,
not an LLM opinion). Capability probe LOGGED (D-R3.1): gemini-3.1-flash-image-preview +
gemini-3-pro-image-preview LIVE at location=global; the Imagen blocker is dissolved per §10.
Calibration fixture BUILT from render #2 (D-R3.2, tests/fixtures/render2_sheet.png — visibly
carries ≥4 different protagonists + the banned smile-at-laptop + a glowing sci-fi UI panel).
+5 hermetic tests.

**CAPABILITY CENSUS SHIPPED (Round-2 D, suite 1370).** (1) _brand_assets surfaces the real
selling lines the census found dropped (shown only as KPI counts before): the 8 trust signals /
accreditations (credibility spine), value propositions, and offerings list, as actual content
under "ما وجدناه في موقعك · What we found on your site". (2) _capability_census — a strip where
every capability has a visible home (profile/SWOT/competitors/plan/creative/run log) with honest
checkmarks (never red), an optional per-run telemetry cost line, and the discreet logs link.
+4 hermetic tests. Round-2 D done; scorecard v2 holds 8/9 (only E, live customer voice, needs a
review pull).

**MARKET PULSE + T-SEASON SHIPPED (Round-2 A, suite 1366).** The trends the engine already
computes were folded ONLY into SWOT; now full_run persists them into result.json and the
dashboard renders a "نبض السوق · Market pulse" section — live trend chips (linked) + the upcoming
Egyptian marketing-season window. `trends/seasons.py` is the one allowed new computation (data
genuinely absent): a deterministic static calendar of Egypt's real seasons (Ramadan/Eid/
back-to-school/White Friday/…), with Hijri dates honestly flagged "تقريبي · approximate" since
they shift ~11 days/year. `upcoming_season` (nearest within 120 days, active season wins) +
`season_for_date` (per-calendar-item attribution: a post dated in a season gets a "قرب رمضان ·
near Ramadan" tag). Renders only when there is a real trend or season. +8 hermetic tests (4
seasons + 4 dashboard). Round-2 A done.

**T-TIER — TIER MATCHING AS DECISIVE CRITERIA SHIPPED (suite 1358).** Two pieces. (1) The
dashboard competitor card now shows WHY each rival matched — plain bilingual chips from the
already-computed PeerFitBreakdown (نفس المجال · نفس الشريحة السعرية · حجم مماثل · قريب منك);
the web-search path honestly shows no tier chips (its sub-scores are null) and falls back to the
search reason; media-buyer jargon (type=doctor tier=mid dist=1.2km) is hidden. (2) A DECISIVE
tier-mismatch cap in peer_match._aggregate (owner: "a burger joint must not surface
McDonald's"): a SEVERE size mismatch (≈100x reviews) or the opposite price band caps the total
below PEER_FIT_FLOOR so a strong category score can't carry a wrong-tier business in —
transparent, not a magic weight. Conservative: fires only on extreme mismatch, so the near-tier
peers the U1 gate selects are untouched (D-3.4 + ticket T-TIER-GATE for the owner-side live
re-gate confirmation). +8 hermetic tests (4 card reasons + 4 cap).

**T-REGISTRY — COMPETITOR REGISTRY SHIPPED (the one real build, suite 1351).** The
owner-caught non-determinism (every scrape rebuilt the competitor set from live search + LLM
selection, so "who are my competitors" drifted run-to-run). `competitor/registry.py`: a stable
identity key (domain, name fallback), JSON persistence (registry/<slug>.json, gitignored), and
`establish_or_diff` — the FIRST discovery persists as the approved baseline; every later run
DIFFs (still-present ✓ / disappeared ⚠ / new-pending) WITHOUT mutating the baseline. SWOT now
computes against the APPROVED set (full_run wires matrix_competitors = approved), not the raw
pool. Newcomers are PENDING owner approval (HITL for identity) — never auto-added; disappeared
peers are flagged, never deleted. The diff persists into result.json and renders as a dashboard
panel ("سجل المنافسين — ثابت لا يتغيّر بلا إذنك"). Best-effort: any failure degrades to today's
raw-pool behaviour, never blocks a run. +8 hermetic tests (5 registry logic + 3 dashboard panel).

**PHASE 3 · SECTION H — FIVE-MINUTE ORDERED READ SHIPPED (suite 1341).** The body is
reordered to the mandated arc — Market (SWOT + competitors + voice) -> Strategy (TOWS + priority
actions) -> Plan (explained plan) -> Creative -> Calendar -> Gaps (honesty) — and a numbered
bilingual "start here" arc strip sits right under the executive summary so one scroll reads
top-to-bottom in order. Verified: the six section anchors render in monotonic arc order on the
real NTI page. +1 hermetic test. Section H: 2 -> 4.

**SCORECARD v1 (after Phase-3 sections A,B/I,C,F,G,H — surface-first pass):** A 1->4 · B/I 1->4 ·
C 2->4 · F 1->4 · G 2->4 · H 2->4. Still open: D (creative tied to strategy — needs the
poster/reel labelled with strategy/audience/funnel) · E (voice renders only when a review file
matches the slug — needs a live pull, P2/P5). Round-2 builds still queued: Market Pulse (trends),
Competitor Registry (the one real build), tier-match reasons, capability census.

**PHASE 3 · SECTION G+ — RTL + REAL FONTS + ZERO RAW INTERNALS SHIPPED (suite 1340).** The
standalone deliverable is now `<html lang="ar" dir="rtl">` (Arabic-primary; Latin runs render
LTR via bidi, layout mirrors via the logical CSS properties used throughout) with an
Arabic-capable font stack (Segoe UI / Sakkal Majalla / Traditional Arabic + Tahoma/Noto fallbacks
— the previously-named Fraunces/Inter never loaded and silently fell back). `_clean_citation()`
strips the raw internals the census flagged: snake_case field names (value_propositions ->
"قيمك المعلنة · your value props"), dotted flag paths (readiness.swot_quality_signals.tagline=false),
and raw URLs (-> "web: <domain>"). Verified: 0 raw-internal leaks in the rendered NTI page.
Visual RTL screenshot verification deferred to an owner interactive pass (D-3.1/D-3.2 — browser
preview unavailable this run). +2 hermetic tests. Section G: 2 -> 4 (structural).

**PHASE 3 · SECTION F — HONESTY SURFACE SHIPPED (suite 1335).** `_honesty_surface()` — the
"ما لا نعرفه بعد · What we don't know yet" panel (rubric F, was 1). Detects real unknowns from
the artifacts and DISPLAYS them, each naming the step that closes it (the advisor-gap channel):
standalone/0-competitor mode, no customer voice collected, competitor running-ads unavailable
(blocked on the Meta identity confirmation), null KPI target (calibrate-later, never invented),
ungrounded persona axes (provisional assumptions). Pure detection, zero fabrication, ZERO red
banners — failures are relocated to honest disclosure per the dashboard failure policy (D-0.2).
Test asserts no error/FAIL/traceback language ever reaches the client. +3 hermetic tests.
Section F: 1 -> 4.

**PHASE 3 · SECTION I/B — THE EXPLAINED PLAN SHIPPED (suite 1332).** The owner's headline
pain ("cards I don't even understand") — the media-plan grid of raw enums is replaced by
`_explained_plan()`, a narrated plan a non-marketer reads and can explain back: (1) OBJECTIVE as
a plain bilingual sentence + why + the evidence RECEIPT inline (the real proof quote, not a bare
tick) + confidence; (2) FUNNEL stage with what it does in words; (3) BUDGET SPLIT — surfaces the
category-tuned channel_weights (config.channel_weights — computed, never shown before) as human
bars (IG 40 / FB 30 / …); (4) AUDIENCE — persona axes as sentences, each tagged بالأدلة/افتراض;
(5) KPI — metric explained + the HONEST "يُعاير بعد أول أسبوعين" line, never a fabricated
target (a real target shows verbatim if one ever exists); (6) KILL-SWITCH / learning floor in
plain words; (7) WHAT HAPPENS NEXT — 3 numbered steps; (?) education tooltips on every term.
Rendered on both real brands. Old grid tests re-pinned to the narrated copy. +6 hermetic tests.
Sections B/I: 2/1 -> 4.

**PHASE 3 · SECTION A — EXECUTIVE SUMMARY (first-screen story) SHIPPED (suite 1326).** The
STEP-0 census (logs/mission_inventory.md, logs/_census_raw.json) confirmed the owner's verdict:
52 of 79 computed-intelligence items are hidden or shown as raw enums; scorecard v0 = 0/9
sections pass. Rubric A was joint-lowest (score 1: no executive summary — the first screen was a
product pitch). `_executive_summary()`: in 10 seconds it tells WHO the brand is (name + category
+ first sentence of the real description), its MARKET POSITION (TOWS posture translated to plain
bilingual words + strength/weakness/competitor counts), and THE one recommended move (rank-1
priority action, prefix stripped, + horizon + media-plan objective). Pure composition, zero new
computation, zero fabrication; degrades cleanly. Rendered on BOTH real brands. +3 hermetic tests.
Section A: 1 -> 4.

**CLIENT-DELIVERABLE MISSION — PHASE 0 (logs as a product) SHIPPED (2026-07-12, suite 1323).**
The dashboard is now framed as a PAID deliverable, not a debug view. New `logs/` product:
`telemetry/run_log.py` renders plain-language BILINGUAL step lines ON TOP of the existing U7
telemetry JSONL (zero new instrumentation) -> logs/<run_id>/<brand>.log; `run_cost()` exposes
per-run spend/tokens/latency (the census gap: cost was tracked, never shown — the dashboard
will surface it); `update_index()` maintains logs/INDEX.md. Committed scaffolding:
logs/decisions.md (this run's autonomous choices + FLAGs), logs/spend.md (metered caps +
ledger), logs/INDEX.md. DASHBOARD FAILURE POLICY recorded (D-0.2): client UI shows only
achievements + the honest "what we don't know yet" panel; every failure degrades to logs/ +
advisor gaps behind a discreet "التفاصيل في سجل التشغيل" link — never a red banner. Per-run
subdirs gitignored; the markdown product is tracked. +4 hermetic tests. Mission runs
autonomous ~12h, never-halt: ambiguities take the safest evidence-honest default logged in
decisions.md; blocked subtasks are ticketed+skipped. Priority P0->P3->P1->P4->P2->P5->P6->P7.

**CALIBRATION #2 (owner's audio + pacing verdicts, 2026-07-12) — measured by TRANSCRIBING her
actual VO track and diffing against the approved plan.** Findings: (1) "HireReady" was spoken
as "وهاي ريدي" — Latin program names inside Arabic TTS get butchered → PRONUNCIATION rule in
the narrator brief (Latin brand/program names = one fluent English word, never letter-by-letter
or Arabized syllables). (2) The line "كن الخيار الأول لكبرى الشركات" was BLANKED by the
grounding gate ('first choice' = unsourced hard claim) — correct behavior; the preview flags it
now, so approval sees it coming. (3) Speech ≈ 9s inside a 17.5s video → DEAD-TAIL FIT: after
the grounding gate, the video total now tracks the SURVIVING speech (estimate at the same
2.2-wps pace; scenes shrink proportionally, 2.0s floor) — no more silent tails. (4) Her "جاية
من بعيد شكلها قطع" = the two real-photo fallback scenes (the silent seedgen failures) — the
ruling's own verdict confirmed: raw photos break the generated world. Now: a 3rd seed attempt
WITHOUT photo conditioning (clean on-brief beats world-breaking), and a scene whose seed still
fails is DROPPED (gate veto, logged; >=3-scene guard keeps the last-resort real photo only when
the reel would otherwise die). (5) "مش سيناريو" — honest answer: this render used the OLD
approved plan, authored BEFORE the one-scenario rule; render #2 needs a FRESH plan (her
Generate) to show the new director. +2 hermetic tests; 2 amended to the 3-attempt contract.

**CALIBRATION RUN #1 (fully-generated mode, NTI, 2026-07-12 19:21) + three surgical fixes.**
The reel rendered end-to-end on the owner's APPROVED plan (17.5s, audio ON, audit trail
exported). VISUAL VERDICT (frames inspected): the generated world is exactly the ruling's
target — cinematic, coherent, authentically Egyptian, native 9:16, zero crop. THE BOTTLENECK
(measured from the run log): Veo drew GARBLED TEXT onto text-bearing surfaces in 4/5 failed
clips (equipment labels, the building's real Arabic sign) + 1 place-drift (high-rise vs the
institutional reference — place fidelity CAUGHT it, working as designed); the motion QA
rightly killed all five, each fell fail-closed to the KenBurns seed-frame fallback — nothing
bad shipped, but the reel moves less than it should and every kill wasted a Veo render.
FIXES (before the owner's resemblance tuning cycle): (1) the SEED GATE gained a `no_text`
criterion — text-bearing seeds die at cents, never reach Veo dollars; (2) a standing NO-TEXT
clause is appended to EVERY Veo motion prompt at the compositor choke-point (covers old plans
and HITL-edited ones); (3) [seedgen] stats now print to the CLI (logger.info was swallowed —
that's also why 2 scenes silently fell back to real-photo seeds mid-run). Stale log wording
fixed ('real-photo' -> 'seed-frame' fallback). COST DELTA measured: +~8 gemini-image calls
(seeds+retries, cents) per reel; the wasted-Veo retries are what fixes 1-2 remove. AWAITING
OWNER at HITL: the resemblance verdict on the calibration frames + EXECUTE for render #2.

**OWNER CREATIVE RULING — REELS GO FULLY GENERATED (2026-07-12).** Real photos EXIT the reel's
display path (no raw site photo appears inside a reel — the aesthetic break she kept flagging);
their role EXPANDS to (a) GENERATION CONDITIONING — every scene seed is now generated 9:16 by
the same gemini-image call the poster one-shot uses, conditioned on the brand's real
place/product photo (the C2 REAL-PLACE mechanism), DNA style lines and the evidence-derived
locale (reel/seed_gen.py) — and (b) JUDGING REFERENCES — ReelScene.place_ref_url carries the
real photo and motion-QA's place-fidelity verdict judges the generated world AGAINST it (the
reference stopped being visible, not load-bearing). **SCOPE BOUNDARY (log this — defense
line):** the ruling covers REEL SCENES, an EXPRESSIVE surface (generated-but-grounded). It does
NOT touch evidence surfaces — real product photos in commerce posters, composited real logos,
verbatim copy: "real stays real" remains the law there. Anti-hallucination stays fully armed
(seed gate -> regenerate-once -> REAL-photo fallback; motion QA + place fidelity) — that stack
is what makes generated ≠ invented. SIDE EFFECT: the 37%-crop problem dissolves (generated
seeds are native 9:16); the auto/blur-contain path remains as the legacy fallback for
user-supplied media and failed generations (REEL_SEED_MODE=real restores it wholesale).
**STANDING STYLE (the reel reference example is superseded accordingly): "one continuous
cinematic world, brand-grounded, fully generated."** Next: the NTI calibration render in the
new mode -> frames at HITL -> ONE owner tuning cycle on place-resemblance strength + the cost
delta (~6-8 gemini-image generations added per reel ≈ cents; crop processing removed).
+3 hermetic tests.

**U9 INSTAGRAM (Tier-1, OFFICIAL) SHIPPED + LIVE-VALIDATED (2026-07-12):** the owner created
her Meta app; `scripts/refresh_meta_token.py` exchanged her short-lived token for a 60-DAY
one (auto-written to .env). Probed live: identity OK; **IG Business Discovery WORKS** (her
page has a linked IG business account); ads_archive still needs the one-time identity
confirmation at facebook.com/ID (code=10 until then — ads_intel live waits on that).
`scripts/pull_instagram.py` (the only network path): --own = her media + customer COMMENTS
(ABSA own-brand voice), --peer = a competitor's public captions/engagement via
business_discovery (marketing voice; the API exposes no peer comments — honest limit).
`social_intel/providers/ig_business.py` parses snapshots only: comment authors hashed AT
INGESTION, usernames never survive serialization; peer captions flagged is_own_brand=False
(O/T side, never S/W). LIVE SMOKE: business_discovery pull on a public brand (3 media,
291.8M followers) → provider parsed 3 caption signals. The rejected Tier-4 scraper is now
fully replaced by the official door. +3 hermetic tests.

**ADS INTEL (Phase-2 #8) — FIRST SLICE SHIPPED, fixtures-first (2026-07-12, suite 1311):**
new `ads_intel/` package for competitor live-ads intelligence via the OFFICIAL Meta Ad
Library API. Strict `CompetitorAd` (page names are public business identities — verbatim, no
PII issue) + `AdsProvider` protocol + fixture provider + `MetaAdLibraryProvider` (parse-only
over saved snapshots, PD-4) + `ad_presence()` — the DETERMINISTIC aggregation: longevity =
the market's own split-test verdict (active >=60d = proven winner), format/platform counts;
all math in code, no model. `scripts/pull_meta_ads.py` is the only network path — **BLOCKED
on an owner credential: META_ADS_TOKEN** (a free Meta app token with Ad Library API access —
facebook.com/ads/library/api); the script says so and exits cleanly without it. NEXT SLICE
(after her token + one live snapshot): wire ad-presence into the SWOT (ad-presence dim per
the roadmap) + platform/format priors into the media plan. +3 hermetic tests.

**VEO-AUDIO GATE + REEL AUDIT TRAIL SHIPPED (roadmap Phase-1 #1 — the last brand-safety
sliver, 2026-07-12, suite 1308).** Measured first: the CREATIVE/CLI path's Veo audio is
STRUCTURALLY STRIPPED (every compositor mux maps video-only; spoken audio = the Ledger-gated
TTS VO + instrumental music) — nothing to transcribe there, the guarantee is structural. The
REAL ungated slice was the GENERATED path (web API): `_voiceover_clause` makes Veo SPEAK
natively and `build_animated_reel(keep_audio=True)` keeps it — the intended TEXT was gated but
what Veo actually SAYS was not. New `reel/audit.py`: `transcribe_clip` (Gemini native audio via
the existing caller protocol — media parts are mime-agnostic), `audit_spoken_audio`
(ledger.audit_text on the transcript), `gate_clip_audio` (unsourced spoken hard claim → the
clip is MUTED in place — fail-closed: visuals + gated overlay audio survive, rogue speech never
ships), wired per-clip right after each Veo render. Plus the poster-parity per-asset trail:
`build_reel_audit`/`write_reel_audit` export `<reel>.audit.json` (coverage scoping + the copy
audit claim→source + per-scene motion-QA verdicts — the compositor now returns them on
ReelRenderResult.scene_qa — + the audio-surface statement), wired on BOTH paths. +4 hermetic
tests. Phase 1 of the roadmap is now COMPLETE on the brand-safety axis; next per the ratified
order: Meta Ad Library competitor-ads intelligence (Phase 2 #8).

**SEED FRAMING — "الفريم كله كأنه زوم مقصقص" fixed at the root (owner, 2026-07-12, suite
1304).** MEASURED: the seed normalizer's `cover` default center-cropped EVERY landscape real
photo to a 37-42% vertical sliver (all six NTI photos measured: 750x500 ratio 1.5 → 37%
survives) before Veo ever saw it — and Ken Burns cover-cropped the same way then zoomed on
top. Every frame inherited a cropped, zoomed-in world. Fix in `_to_vertical_seed` (ONE
choke-point — Veo seeds AND Ken Burns both route through it): new `auto` default —
portrait-ish sources (ratio<=0.8, loss<=~30%) keep the sharp cover; landscape sources try
REAL OUTPAINT to 9:16 (imagen edit — photo preserved, border generated, cached per seed),
degrading to blur-contain (whole photo visible over its own blurred copy). Explicit
REEL_SEED_FILL still wins (cover/pad/blur/outpaint/none; --product-image keeps pad).
**HONEST LIVE STATE:** outpaint 404s on radiant-octane — NO imagen-3.0/4.0-capability model
is enabled on the new project (it was verified on the old image-498715); today the fallback
blur-contain is what runs (still: 100% of the scene visible vs 37%). Owner action to unlock
the premium path: enable an Imagen capability/edit model on radiant-octane — the code
auto-uses it the moment it exists. T-10 (candidate): gemini-image-based extension instead —
needs an owner ruling first because a generative model may REDRAW the original pixels (the
real-photos-stay-real doctrine). +4 hermetic tests (landscape whole-scene, outpaint-preferred,
portrait-sharp, explicit-override).

**U8b DISCOVERY v2 — FIRST SLICE SHIPPED (2026-07-12, suite 1300):** MarketDefinition now
DRIVES retrieval. The measured v1 gap: candidates came from ONE offerings-built (usually
English) text query — Arabic-named local peers never entered the pool at all. Additive fix in
discover_competitors: the definition's bilingual query_seeds (<=3) join the v1 query
(v1 FIRST — its behavior is the floor), the union dedupes by place_id, the pool cap holds,
and the tuned scoring/hard-filter pipeline is UNCHANGED; an ungrounded definition degrades to
EXACT v1 (pinned by test). Cost: <=4 Places text searches per discovery instead of 1
(~$0.03). Remaining v2 scope (roadmap): web-discovery merge under the same seeds + geo-mode
awareness (radius vs national bias). +2 hermetic tests.

**REFERENCE REEL EXAMPLE (per the owner's directive — pairs with the poster reference):** the
NTI 6-scene plan (outputs/nti_sci_eg_reel_plan.json, 2026-07-12 11:09) + its three pre-EXECUTE
verdicts: (1) GROUNDING — "خريجينا بيشتغلوا في أكبر الشركات" and "٤ شهور" both PASS
ledger.audit_text against NTI's evidence; the SURFACE was ungated → now blocking
(drop-to-grounded) on VO/hook/cta incl. HITL plans, with the verdicts shown at preview time.
(2) SCENE-5 UI TEXT — generalized into the director's SCREENS & MARKS rule + the motion-QA
no_junk_generated_text criterion; Cisco/FortiGate style tech mentions stay legitimate ONLY as
curriculum-evidence copy, never as on-screen logos (third-party marks banned at source and at
QA). (3) VO-DURATION FIT — verdict: the compositor STRETCHES the scene to the words (0.3s +
words/2.2, cap 8s) and never compresses beyond atempo 1.12; scene 2's 13-words/4s would now
render at ~6.2s. **QUEUE STATUS:** the NTI re-render awaits the owner's EXECUTE (HITL law — no
auto-render). op_shoes motion restructure: its roots (motion QA, scenario arc, featured
seeding) shipped this round; the before/after case runs at her next Generate. NEXT build unit:
U8b Discovery v2 (consumes MarketDefinition: query_seeds + geo → candidate pool → peer-fit
rank; replaces the current discovery's ad-hoc seeds; fixtures-first per PD-3).

**DASHBOARD ROUND — the visual mess fixed + "حط فيها كل حاجة" (owner 2026-07-12, screenshot).**
(1) The scraper-quality panel's 10 stacked pink URL rows: the per-category ajax_modal_details
notes now AGGREGATE server-side (dashboard/products.py) into ONE human line ("JS-modal detail
pages resolved: +N URLs across M page(s)"); raw lines kept under ajax_detail_lines. (2) The
plan mess: the exported media-plan card renders BILINGUAL LABELS (عملاء محتملون · Leads /
نموذج تواصل / تحويل · BOFU / قومي · National) — never raw enums — plus the EVIDENCED persona
axes from base_persona; and the STUDIO no longer shows the plan TWICE (its embed passes
include_media_plan=False; the richer Arabic card is the single source there). (3) Calendar
polish: pretty dates (Sun 05 Jul), snake_case types spaced, no empty “” hooks. (4) NEW on the
export: "Priority actions — أولويات التنفيذ" (tows.priority_actions were silently dropped;
ranked chips — rank is a REAL sequence) and "Data coverage — تغطية البيانات" (the scrape-QA
summary strip, best-effort). Studio-side changes land on the next server restart (owner's
call). +1 test; test_products re-pinned to the aggregate. (Commit 63c9d9a's message says
+2/1299 — miscounted; the truth is +1/1298.)

**POSTER PRODUCT AUTHENTICITY — the topshoes invented-sneaker chain CLOSED (owner-caught,
2026-07-12).** The mapped 3-link chain, each link fixed: **(1) starved photos:** topshoes'
profile stored ONLY Shopify `?width=100/330` thumbnails → filter_usable_photos measured 0/10
usable → n_products=0. Fix: `original_variant(url)` strips CDN size params and a too-small
thumbnail retries at the ORIGINAL (measured: same URL without the param = 1024px real product
photo) — the UPGRADED url is kept. This is the T-7 thumbnail-site fix, universal. **(2) the
license to invent:** the n_products=0 prompt explicitly PERMITTED 'generic unlabeled products'
— for a retail brand that licensed a fabricated hero sneaker (a false product claim, not
design freedom). Fix: with no real product attached the prompt now BANS inventing a hero
product presented as merchandise (compose with the brand's real WORLD: hands/environment/
texture) and BANS all third-party logos/trademarks (the swoosh was a LOGO, invisible to every
text gate) — the marks ban is unconditional. **(3) blind QA:** poster_vision_qa gained
ADDITIVE hard gates `invented_product` (asked only when expect_real_products=False — the
caller knows the ground truth) + `third_party_mark` (always asked), both re-ANDed IN CODE and
included in _qa_image_fixable so a flagged render retries. +3 hermetic tests.

**SEED GATE (Stage A) SHIPPED (2026-07-12):** `check_seed_frame` in reel/scene_qa.py —
generalizes the poster art-critic criteria (anatomy_ok / lighting_real / composition_clean /
ad_grade / score 1-10, conjunction + score>=6 COMPUTED IN CODE, permissive degrade) and wires
into the Imagen-seed pipeline (reel/generate.py, the web-API 'generated' mode): a still that
fails is REGENERATED once (cents); still bad -> the scene is DROPPED — a bad seed is never
animated into Veo dollars. Scope honesty: the CLI/creative path seeds with REAL photos (mapped:
no Imagen step there) — those are covered by the director's image bans + motion-QA
setting_faithful, so Stage A applies exactly where generated seeds exist. +1 hermetic test.

**MOTION QA (Stage B) SHIPPED + CALIBRATED (owner-triggered priority, 2026-07-12):**
check_scene's verdict EXTENDED additively (locked wording untouched; permissive defaults):
faces_intact_across_motion, no_morphing_artifacts, no_junk_generated_text, ad_grade,
setting_faithful — the compound gate is COMPUTED IN CODE (the model never self-passes) and now
runs for ALL creative reels (no featured product required): with no product reference the
scene's OWN real-photo seed is the judge's reference. Fail → one retry → faithful per-scene
KenBurns (machinery already existed; fail-closed). **CALIBRATION (live Gemini vision, frames
from real clips):** owner-labeled-BAD NTI reel — morphed-hands scene FAILS
(no_morphing_artifacts) and the owner's exact 'old school' scene FAILS once judged against its
REAL seed photo (ad_grade: "dated and somewhat dilapidated — poor fit for a modern digital
training program"); earlier topshoes-era clip 0/4 false-positives; iti's uncanny AI faces
caught. One calibration sample (tarshouby) was INVALID — my product hint mislabeled the brand;
excluded honestly. Cost note: QA ≈ 1-2 vision calls/scene (cents) vs a wasted Veo scene render
(dollars) and a full 10-20-min re-run when a bad reel ships. The NTI reel re-renders only
after Stage A (seed gate) also lands — next. +2 hermetic tests (code-conjunction; service-brand
QA with seed reference).

**DIRECTOR ROUND 2 (owner 2026-07-12: "مش سيناريو" / "الكلام أوفر وبيقولوه غلط" + her
pre-EXECUTE scene-5 check):** three prompt-layer rules in _system_prompt (prompt-layer per the
honesty ruling — deterministic guards are HITL + the motion QA landing next): (1) ONE
CONTINUOUS SCENARIO — same protagonist/world hook→CTA, each veo_prompt continues the thread
(montage = failed reel); (2) SCREENS & MARKS — in-scene displays are abstract with NO readable
text (the junk pseudo-text class), NO logos of ANY company incl. third-party marks (the
topshoes swoosh class) and the brand's own (end-card carries the real one); (3) VO discipline —
<=8 words/line, silent scenes encouraged, numbers written as dialect words (TTS-friendly:
أربع شهور not ٤). +1 hermetic test.

**CAPTIONS OFF BY DEFAULT (owner reversal 2026-07-12: "الغي الكلام اللي ع الريل خالص خليه
صور").** build_creative_storyboard(captions=False default; REEL_CAPTIONS=on or captions=True
re-enables — the designed routing is preserved, not deleted). The reel is pure footage +
voice-over; the deterministic branded END-CARD stays. VO listenable-pacing stretch unaffected.
Caption tests re-pinned behind the flag; +1 default-off test.

**REEL GROUNDING GATE — the LAST ungated copy surface CLOSED (owner pre-EXECUTE check,
2026-07-12).** Her check was right structurally, wrong on the content: the two flagged NTI VO
lines ("خريجينا بيشتغلوا في أكبر الشركات", "٤ شهور") both PASS ledger.audit_text against NTI's
real evidence — but the surface itself was AUDIT-ONLY (reel/grounding.py said so; the
creative.py comment claiming grounding runs was aspirational). Now BLOCKING at the plan_eval →
storyboard seam in render_creative_reel: voiceover + hook + cta + captions pass the shared
drop-to-grounded policy (unsourced hard claim → BLANKED, ambience instead of speech, never
rewritten); covers the fresh-director path AND plan_override (HITL law: blanking is a veto,
not a plan swap). hitl_preview now shows the same verdicts inline ("⚠ غير مدعوم — سيُحذف عند
التنفيذ") so approval never diverges from render. grounding/audit.py: reel moved to
GATED_SURFACES; honest imagery limitation stays named. +1 hermetic test.

**POSTER 429 RESILIENCE + STALE-CREATIVE RESET (owner round, 2026-07-12, suite 1288).**
Her failed studio run: poster died after 239s with a raw google.genai ClientError traceback
fragment — WHILE the Veo reel rendered concurrently on the same Dynamic-Shared-Quota project
(the reel finished OK at 404s). Three fixes: **(1)** `generate_oneshot_poster` now WAITS OUT
transient bursts — capped backoff (0/20/45/90s) on 429/RESOURCE_EXHAUSTED/5xx via
`_is_retryable`; a real 400 still fails fast. **(2)** poster CLI catches the failure and
prints ONE readable line (`_fail_reason`): the 429 case names the cause ("a reel rendering on
the same project") and the action ("Regenerate in a minute") — the studio log tail is now for
humans, not a stack fragment. **(3)** Studio `pick()`: choosing/scraping a NEW product resets
BOTH creative stages to the honest placeholder (never showing the LAST product's poster/reel
as if they were this one's) — guarded so a render in flight is never clobbered; files on disk
untouched. +5 hermetic tests. NOTE: (1)+(2) are subprocess-side (live on her next Generate,
no restart); (3) is studio-page JS — it lands on the NEXT server restart, owner's call when.

**CUSTOMER VOICE + MEDIA PLAN ON THE DASHBOARD (owner: "عايزة الشغل كله يترند... بعرض على
الدكتور", 2026-07-12, suite 1283).** Constraint honored: the studio server was NOT restarted
(owner mid-demo) — both wirings live on SUBPROCESS paths, so they take effect on her next
Analyze/Dashboard click with the running server untouched. **(1) competitor/full_run.py:**
own-brand customer voice — `_voice_files_for` matches saved review data to the subject by
normalized-slug prefix (>=5 chars: alameda_hc ↔ alameda-hc.com; demo/gold fixtures can't
false-hit) across reviews/fixtures + runs/review_snapshots + social_intel/fixtures;
`_own_voice_themes` feeds the rows through ABSA (verbatim-quote gate + >=2 threshold intact)
and appends to `synthesize_swot(themes=)` — own praise lands in Strengths, own complaints in
Weaknesses, each citation carrying its «quote»; independent of --no-themes (that flag governs
PEER Places themes); any failure degrades silently. **(2) dashboard/build.py (exported HTML —
what the client sees):** a dedicated bilingual "Customer voice — صوت العملاء" section
(filters SWOT items whose evidence is own-brand customer voice; polarity chips + verbatim
quotes) + the U1 "Media plan — خطة الشراء الإعلاني" card (objective/destination/funnel/KPI/
geo/evidence badge + rationale), AUTO-DISCOVERED from the sibling <slug>_media_plan.json so
no caller changes were needed. Smoke on real NTI artifacts: card renders (OUTCOME_LEADS /
cost_per_lead / مؤكد بالأدلة). +4 hermetic tests. Demo path for the doctor: pull the clinic's
reviews with brand-ref = its slug → Analyze → the dashboard shows SWOT + voice + plan +
poster + reel in one page.

**LIVE REVIEW PULLS (Google Maps) SHIPPED — code complete, live pull BLOCKED on one owner
click (2026-07-12, suite 1279).** Mirrors the ratified Facebook-wrap governance exactly:
`scripts/pull_reviews.py` (the ONLY network path; resolves a place via the existing
competitor PlacesClient, snapshots the RAW payload with author names under gitignored
runs/review_snapshots/), `scripts/convert_reviews_snapshot.py` (loud PII gate — refuses to
write if any raw author name survives), `reviews/providers/google_maps.py` (parse-only
ReviewProvider: hash-at-ingestion, relative dates -> honest None, ~5-reviews/place cap
documented, reads raw snapshots AND sanitized fixtures). Reviews feed ABSA rows directly
(own/peer tagged from the snapshot). +3 hermetic tests. **LIVE-VALIDATED END-TO-END same day:**
first attempt hit 403 (Places API (New) disabled on project 627765796315) → owner enabled it →
`pull_reviews "Alameda ... As Salam International Hospital Cairo"` resolved the REAL hospital
(مستشفى السلام الدولى), pulled 5 Arabic reviews, converter passed the PII gate →
`reviews/fixtures/alameda_hc.json`, and ABSA on Gemini produced **3 grounded themes with
verbatim quotes** (nursing praise 5/5 supports, doctor_competence 3, service_quality 2 — all
own-brand praise → Strengths via R-5). The full customer-voice chain (pull → sanitize →
provider → ABSA → SWOT door) is now proven on live data. Note: person names appearing INSIDE
public review text (praised doctors/staff) are review content, not reviewer identity — the
PII rule covers AUTHORS, and no author name survives. Vezeeta remains a separate future
source (needs a governance tier ruling before any code).

**REEL REGRESSION — OWNER ROUND (2026-07-12 "الريل البشع", suite 1276): four roots found by
forensics on HER actual run (outputs/nti_sci_eg_reel_plan.json + reel/), each fixed measured.**
**(1) HITL BETRAYAL (the "prompt never executed"):** reel/creative.py's pre-render eval ran on
the EXECUTE-approved plan and, on a weak (or even TIED — `>=`) score, silently swapped in a
freshly regenerated plan the user never saw. Now: on plan_override the eval is ADVISORY-ONLY
(logged); the approved plan renders verbatim — gates may veto/log, never substitute. **(2) SPEED
("منلحقش نفهم"):** her plan carried 3.2-3.3 words/sec scenes (13 Arabic words in 4.0s) and the
whole track was atempo-squeezed up to 1.35x. Three-layer fix: director PACE BUDGET hard rule
(<=2 words/sec of duration_s), storyboard LISTENABLE PACING (a narrated scene stretches to
0.3s + words/2.2, cap 8s — the VIDEO stretches, never the audio squeezed), atempo cap 1.35 ->
1.12 (extracted `_fit_filter`, unit-tested). **(3) EMOTION LOST:** voiceover.py used only the
FIRST scene's voiceover_delivery for the whole film — her reel was narrated entirely as
"slightly frustrated". Now the full per-line ARC goes into the single-call performance brief
(order-preserved; works for gpt-audio system prompt + Gemini directive; edge stays direction-
less, documented), plus an anti-rush pace line in every mood. **(4) ONE PHOTO x6:** with a
featured product the director rule forced image_index ALWAYS 0 — valid only when the product's
real photo anchors the pool (CLI substitutes selected=[product_image]); her photo-less course
got a generic about-photo seeded into all 6 scenes. Now ALWAYS-0 requires
product_photo_anchored (pool==1); a photo-less offering locks the MESSAGE on the product while
visuals move through DIFFERENT real photos. +5 hermetic tests (test_reel_owner_round_0712) +
the featured-product test amended to the new truthful spec; all 151 reel/voice/creative tests
green. NOTE: investigation ran in-loop — the 4-agent workflow died on the session subagent
limit (resets 13:40); findings were reproduced first-hand.

**ABSA SHIPPED (owner-APPROVED design, fixtures-first, 2026-07-12, suite 1271):** `reviews/
absa.py` — the customer-voice layer, grounded by CODE not model trust: every mention must cite
an `evidence_quote` that is a VERBATIM substring of its row (whitespace-insensitive; paraphrase
= rejected), the aspect must exist in the category taxonomy (universal default for unknown
categories), and a theme survives only with >= 2 DISTINCT supporting rows. Routing is
deterministic R-5 through the single door `synthesize_swot(themes=)`: `ReviewTheme` gained
`is_own_brand` — own praise -> Strengths, own complaint -> Weaknesses (evidence: "own-brand
customer voice", claim ladder unchanged: >=2 citations = validated, each citation carries its
verbatim quote «...»); peer complaint -> Opportunity when the aspect is a controllable service
gap (waiting/booking/communication/staff/delivery/shipping/returns/emergency/nursing) else
Threat; peer praise stays a category note. Gold fixture `reviews/fixtures/absa_gold_ar.json`
(masri clinic voice, pilot vertical; _gold expectations embedded, no author fields by design).
Inputs are provider-agnostic (SocialSignal / Review / dicts) — the fiveguys Facebook fixture
and the U8a review fixtures feed it as-is. +5 hermetic tests (end-to-end with every rejection
gate, fabricated-quotes-in-numbers, threshold, no-caller/empty valid, universal-taxonomy
fallback). **LIVE SMOKE (Gemini 2.5 Pro, same day): 13/13 raw mentions passed the verbatim
substring gate and the surviving themes match _gold EXACTLY** (own complaint waiting_time
{R2,R3,R6}, own praise staff {R4,R7}, peer complaint waiting_time {P1,P3}; all single-support
mentions correctly held under the threshold). Caveat for entrypoint authors: default_caller
needs .env loaded — a bare script without it gets the extractor's honest-empty []. Live wiring
(real pulls -> ABSA -> SWOT v2 in the studio) = the queue's next measured step.

**FACEBOOK VALIDATION PULL EXECUTED (the ONE pre-authorized pull, 2026-07-12):** scripts/
pull_facebook.py live end-to-end on the team's same public test page (fiveguys): cap check
passed (est $0.07 + $0.00 <= $20.00), team pipeline ran unmodified via Apify (2 posts + 10
comments), ledger row recorded (runs/apify_spend.jsonl), raw snapshot under runs/
social_snapshots/ (PII, gitignored), converter re-ran with the PII gate and refreshed
social_intel/fixtures/fiveguys.json. apify-client installed into the conda env. No recurring
pulls scheduled.

**U9 FACEBOOK WRAP SHIPPED (2026-07-12, suite 1266) — the $20/month cap unblocked it.**
Recon verdict on the team's uploaded `facebook_collector/` (untracked, per governance): Tier-2
CLEAN — Apify Actors only (pages/posts/comments scrapers), APIFY_TOKEN from its own gitignored
.env, no direct-scraping libs; defensive `pick()` normalization is good. Caveats: `collector.py`
is dead code, `test_profile.py` is broken (imports a function that doesn't exist), and every
post/comment carries the FULL raw actor payload + raw author name/url/picture — which is exactly
why the repo boundary hashes. Integration (WRAP, not rewrite): `social_intel/providers/
apify_facebook.py` parses saved snapshots ONLY (no network; PD-4) — authors hashed at ingestion
via the one blessed fingerprint, actor `raw` payloads dropped, malformed rows skip, empty=VALID;
`scripts/pull_facebook.py` is the single network path (drives the team pipeline UNMODIFIED,
refuses before the pull if the worst-case results estimate would cross the owner's $20/month —
`social_intel/spend_guard.py` JSONL ledger under runs/, conservative $5/1k-results estimate,
APIFY_MONTHLY_CAP_USD/APIFY_COST_PER_1K_RESULTS overridable); `scripts/
convert_facebook_snapshot.py` makes committable fixtures with a LOUD PII gate (refuses to write
if any raw author name/profile URL/avatar survives serialization). The team's real fiveguys
sample converted: `social_intel/fixtures/fiveguys.json` (12 sanitized signals, 2 posts + 10
comments) — ABSA now has REAL Facebook data to develop against, hermetically. +4 tests
(ingestion-hashing/PII-drop, provider raw+sanitized round-trip, real-fixture sanitation, cap
refusal). NO recurring pulls scheduled — one-shot script only, per the standing ruling.

**OWNER ROUND LOCKED (2026-07-12, suite 1262):** three rulings executed. **(1) RAG-to-creative
regression PINNED — with a measured fix first:** on the real NTI manifest the owner's popup course
text never actually reached generation — `_product_raw_facts` any-word matching let campus pages
that merely say "fiber" eat the whole 1800-char budget (measured pre-fix: `(72 Hours)` and the
description ABSENT, Smart Village noise present). Fix: PRECISION-FIRST page ranking (URL match 4 /
full-name-OPENS-page 2 / full-name-anywhere 1; a scored page is the product's OWN page and is
taken WHOLE; any-word matching is now only the honest fallback when nothing names the product;
nested-container duplicate blocks deduped so the budget buys unique text). Measured after: the
modal detail page leads — hours + description verbatim in, noise out. One fixture, two guarantees
(the owner's design): `tests/fixtures/nti_popup_manifest.json` is a VERBATIM trim of the real
coursesev.php?catID=205 popup-family manifest — it documents the modal-resolved modules/2631.html
page shape AND drives the single regression test (manifest → `_product_raw_facts` → concept
prompt: popup verbatim asserted present, 'fiber'-only noise asserted absent) so no refactor
silently re-orphans the manifest. **(2) BRAND-COHERENCE honestly classified: PROMPT-LAYER**
(same class as pre-U4 compliance) — the model usually obeys; the deterministic guard is the HITL
gate, NOT this rule; never read it as a hard gate. **T-9 (candidate criterion, NOT a build
item):** if a coherence-violating hook ever passes HITL unnoticed, that is the trigger to
consider a cheap LLM-judge coherence check at U4 time. **(3) Apify monthly cap ON RECORD:
$20/month** (owner message 2026-07-12) — the last outstanding §10 condition; facebook_collector
integration unblocked (recon → hashed fixtures → provider wrap, in that order).

**POPUP BUG CLOSED (owner URL dey/coursesev.php?catID=205, 2026-07-12) — three measured loops:**
the ajax resolver was INNOCENT (run directly on the page it reconstructed all 6 modal URLs).
Loop 1: `coursesev` failed is_leaf_detail's EXACT filename match -> PREFIX match for compound CMS
names (a09d01a; regression fixture saved from the live page). Measure: still 0 fetched. Loop 2:
BFS leaves were appended at the frontier TAIL and every run ended before reaching them -> leaf
candidates now SPLICE right after the current page, the ajax philosophy (ca75895). Measure:
coursesev 7/7 + 40 modals, but the owner's exact modal (2631) died on _AJAX_MAX=40 with 140s
budget unused. Loop 3: cap 40->120 (2170814; modal pages are tiny — time is the real bound).
FINAL MEASURE: 105 pages / 66 modals / 544s and **"Fiber Networks Essentials (72 Hours)" +
its full description are in the text blocks** — the exact popup from the owner's screenshot.
Deliverables complete: root cause + universal fix + regression fixture + measured re-scrape.

**DIRECTOR ROUND (2026-07-11 late, holds lifted) — RATIFIED GATE: objective 93% / destination
92% / combined 92% PASS.** ⚠ **SUPERSEDED 2026-07-15 — do not quote these three numbers; see the
CORRECTION at the top of this file.** Live today: objective **86%** (raw_african's newest scrape
dir has no profile → `find_latest_scrape_dir` picks it → scored a miss); destination **83–92%,
UNSTABLE** (≥4 live runs on frozen inputs returned 83% three times and 92% once — `assalam`'s pick
oscillates; the deduction is a live Gemini call). The "92%" below was one draw from a distribution
whose variance was never measured, not a wrong number. Note also this file contradicted itself on
destination even before the correction: 92% here vs "93%/85%" at the R-SERIES/U7 entries below.
assalam label owner-ratified phone_call→lead_form (truth-tracking,
dated note in ground_truth _doc, a6ffaed) → destination 85%→92%; the only miss anywhere is mumm
(bot wall). TTS listen-test verdict WIRED (951ce39): fusha→Gemini Kore directed, masri→OpenAI
gpt-audio (REEL_TTS_BACKEND still overrides; dialect+language = user's UI choice). U8c labeling
kit shipped (reviews/taxonomy.py ABSA aspects config-by-category+default per D-8; labeling_kit
100-row JSONL with embedded aspect sheet + evidence_quote substring contract; Arabic one-page
guide). U9 skeleton per governance (fixtures-only: strict SocialSignal + Protocol + fixture
provider; Apify=Tier-2 behind §10 sign-off; direct IG scraper REJECTED, salvage=snapshot_parser
Tier-3; schema pending owner raw samples). IN PROGRESS: HITL prompt gate (preview AR/EN + edit +
EXECUTE lock before any paid render), studio language/dialect selects, MediaPlan dashboard card +
process map, popup-extraction bug hunt. ABSA R-5 one-paragraph design DELIVERED — awaiting
owner sign-off before implementation.

**U8a SHIPPED (fixtures-first, 2026-07-11, suite 1254):** new `reviews/` package — strict
`Review` schema (privacy rule: `author_hash` NEVER a raw name; the one blessed fingerprint is
telemetry.payload_hash per D-3), `ReviewProvider` protocol (a provider-per-source is universal,
D-8; empty result = VALID -> advisor flag), and the FIXTURE provider every downstream unit
(ABSA -> SWOT v2) develops against (PD-3). Real Google-Maps/Vezeeta pulls land later behind
scripts/ (PD-4, the only network paths) as separate measured steps. +3 hermetic tests (privacy,
empty-is-valid, malformed-row skip). **THE OWNER'S FULL QUEUE IS NOW COMPLETE**: baseline v2 ->
U1 gate (93%/85% PASS) -> U7 telemetry (skeleton + runner stitching) -> stale-gate sweep ->
R1b/R2/R2b -> re-gate -> MarketDefinition -> U1 assembly CLOSED -> U8a. Next in line per owner:
the deferred art/motion restructure (queue #3 post-U1, op_shoes as the before/after case) +
owner-review flags: assalam destination label; TTS dialect listen test verdict.

**U1 CLOSED — config + full MediaPlan assembly SHIPPED (2026-07-11 evening, suite 1251):** the
two pieces the owner held "until the gate number exists" (gate: 93% PASS, twice). `media_plan/
config.py`: channel weights CONFIG by category with a universal default (always sums to 1.0) +
`default_kpi(objective, category)` planning priors whose metric names align with the schema's
cost-metric set so the 3xCPL learning floor engages automatically; NO invented target values.
`build_media_plan(profile, caller, manifest, run_id)`: deduced objective (+KPI prior) +
`build_persona` (ONLY evidenced axes; location from evidenced service_areas or the REAL address
as a structural fact — never a URL dressed as a location) + `build_geo` (real address -> RADIUS
anchored with a resolved address_ref + category-class radius; else honest NATIONAL) +
MarketDefinition ref; run_id adopts TELEMETRY_RUN_ID when present (U7). Hardened `_offerings`
against EvidencedField-shaped names (dict name crashed the deduction join). LIVE: rawafrican ->
SALES/online_store/bofu, cost_per_purchase, national, plan honestly ungrounded on the persona
axis (advisor flag) — outputs/rawafrican_media_plan.json. Also U8b slice: `competitor/
market_definition.py` (pure grounded projection: geo from evidenced addresses + locale ccTLD,
category-class radius config, bilingual query seeds; 4 hermetic tests + live smoke).

**R-SERIES CLOSED + U1 RE-GATE 93%/85% PASS (2026-07-11 evening, autonomous mode):** the owner's
three report-back items, each measured. **R1b** (6d322f0): the real root was UPSTREAM of the
frontier — _process_fetched_page classified links against the REQUESTED url, so a cross-domain
homepage redirect made every link born-external; links now anchor to the FINAL host. LIVE: assih
1 page -> **41 pages / 20 phones / 8 forms** (a proper Alameda-group portal profile, 21 medical
departments). **R2 chain**: pack leaf-boost (1ebbd62; product blocks 29->60, priced 53->67) was
NOT sufficient — the binding constraint was the PROMPT: both store shapes hard-ruled
"DEPARTMENTS FIRST", collapsing boutiques; R2b (3d6cb5d) adds rule 0 SIZE CHECK (a ~<=40-product
boutique lists INDIVIDUAL products with verbatim price_text; departments-first is for
marketplaces). LIVE: rawafrican offerings 8-categories/0-priced -> **15 products / 15 priced** and
the online_store signal returned. **U1 RE-GATE on fresh profiles: objective 93% (13/14),
destination 85% (best ever) — PASS.** 3-way taxonomy: builder-errors ZERO; profile-weakness = mumm
only (bot wall, known ceiling); label-uncertainty = assalam destination (pre-tagged; FLAGGED for
owner review — post-migration the phone_call label likely becomes lead_form, exactly as the owner
predicted). **U7 full wiring** (c6634df): the benchmark runner mints one RunTelemetry and exports
TELEMETRY_RUN_ID to every scrape/extract subprocess — a whole benchmark = ONE stitched
runs/<run_id>/telemetry.jsonl. Also shipped: REEL_ARABIC_DIALECT=masri|fusha owner option; logo
legibility bundle (reel white-knockout watermark, poster subtle-panel gate 48->30, prompt tone
ban). Queue continues: MarketDefinition -> U1 assembly close -> U8a.

**BASELINE v2 (2026-07-11, benchmark/runs/20260711_132224 — the new-crawler reference point):**
full 14-URL fresh run per owner directive 1, judged by the PRE-REGISTERED criteria. (a) REGRESSION:
6/7 of the 1.0 URLs held; assalam fell 1.00→0.50 — diagnosed NOT-FIX-A: assih.com now REDIRECTS to
alameda-hc.com (As-Salam merged into the Alameda group portal), the crawler anchors internality to
the ORIGINAL host so every link classified external → 1-page scrape (persistent across a re-scrape;
universal gap: re-anchor site_url on a cross-domain homepage redirect — proposed fix). (b)
HYPOTHESIS CONFIRMED: norshek 0.50→0.75 ready, almentor 0.50→0.75 ready — the shallow-crawl class
healed exactly as predicted. (c) Bonus: mcdonalds 0.75→1.00, alameda 0.75→1.00; zero other moves.
AGGREGATE: avg SWOT-critical **0.846→0.885** (first time over the 0.85 gate), ready 11/13→12/13.
Console "THRESHOLD CHECK: FAIL" = the fail-closed completeness rule (mumm errored as BOT_PROTECTION
instead of clean exclusion); results.json quality gates: passed=true, failures=[].

**U1 GATE on the FRESH v2 profiles (owner directive 2): objective 79% (11/14), destination 83% —
FAIL by exactly one URL, with the 3-way miss taxonomy showing ZERO builder-errors.** assalam =
profile-weakness (+ the owner's pre-registered label-uncertainty tag vindicated: the site itself
migrated); mumm = profile-weakness (bot wall, permanent); raw_african = profile-weakness on the
EXTRACTION side — the NEW crawl captured 11 /products/ leaf pages (old crawl: 0) yet the offerings
extraction returned 8 category-level unpriced items, so conversion_signals saw no store and the
builder HONESTLY deduced Leads. On healthy-signal profiles the builder went 11/11. NEXT MEASURED
FIX (proposed): make offerings extraction exploit the leaf product pages the crawler now delivers;
then re-gate (projected ≥86%). assalam benchmark entry needs an owner review (entity merged). one grep sweep for OpenAI-era gates after
the third relic (full_run gate, curator, and now): REMOVED dead `api/jobs/runner.has_openai_key()`
(no callers); api/schemas.py stopped lying (`model` default "gpt-4o-mini" → advisory-empty,
"no OpenAI calls" → "no LLM calls"); `business_profile/__main__._make_caller` auto-mode now
honors ANY Google credential (was: GOOGLE_CLOUD_PROJECT or bust → OpenAI); build.py docstring
example → default_caller. OpenAICaller class itself KEPT (documented legacy fallback, not a gate).
Runner `run_scrape` timeout 300→720s (post-PSL/BFS stores measured 592s — 300s killed them).
**OPERATIONAL FINDING (owner decision pending): reel/voiceover.py is a REAL OpenAI dependency
(gpt-audio-1.5 TTS) and the new .env has no OPENAI_API_KEY → reels currently render WITHOUT
voice-over.** Options: re-add the key for TTS only, or migrate to Google Cloud TTS (~a day,
unifies billing). Suite 1233 green.

**FIX-D SHIPPED + verified live (reel, suite 1233) — the 5-front campaign is code-complete.**
(D1) `reel/image_select` runs on the SHARED GEMINI CALLER (was: hard-required the empty
OPENAI_API_KEY since the all-Gemini migration → a silent NO-OP that kept every banner/logo-wall —
the mechanism of the broken reels); no-caller/failure now fails CLOSED through (D2)
`image_quality.is_collage_bytes` (white-gutter band detection — logo grids never animate; wired
into filter_usable_photos too, protecting the poster path as well). (D3) crop safety: >1.6:1
landscape sources CONTAIN over their own edge color instead of center-cropping (no more 'TRAING'
mid-word truncation), both in `_to_vertical_seed` and the Ken Burns path (seed normalized first).
(D4) creative director: evidence-derived Country/market line in the identity brief; hook rule
fixed ("the macro must come from a REAL photograph… a screwdriver-on-circuit-board for a TRAINING
institute is a failed hook") + absolute image bans (banner/poster/screenshot/diagram/logo-collage);
caption language = the DOMINANT site language by proportion (was languages[0] → 'en' beat 'ar').
(D5) hardcoded-Egypt hacks replaced by `_culture_line` via poster.locale (a Saudi brand renders
Saudi; unknown stays honest) in reel/art_director + reel/generate. LIVE VERDICT (nti, $0.0014
Flash call): curator kept 0/6 — CORRECTLY: the four 'about' photos are program LOGOS (Digital
Egypt Youth, Wazeefa-Tech…), sprogram1/3 are the holographic-TRAINING stock banners, and the real
campus photos are 300px thumbnails (tech-gate honest reject) → nti's reel now routes to generated
b-roll WITH Egyptian casting instead of zooming a logo wall; photo-rich brands keep their real
photos junk-free. Residual (ticketed, not bundled): nti thumbnails need T-7-style larger-original
discovery; plan_eval image-suitability check + storyboard quality-ordered seeds = small follow-ups.

**CREATIVE-QUALITY CAMPAIGN (2026-07-11, owner's 2-month pain, 5 diagnosed fronts):** evidence run
nti.sci.eg. Root causes (5 parallel investigators, all file:line-verified): (1) logo white plate =
the oneshot MODEL paints a placeholder slot (prompt says "logo placed there afterward"); (2) vague
posters = oneshot (forced for all Arabic+logo brands) bans real-photo attachment for service
categories + no locale line + imagen-edit conditioning dead on that path; (3) leaf hierarchy =
depth-1 frontier (built once; sub-page links never re-seed) + Shopify-only leaf notion + picker
ignoring profile offerings (NTI picker = 0 items); (4) reel broken = image_select curator NO-OP
since the Gemini migration (hard-requires the now-empty OPENAI_API_KEY, fail-OPEN keeps junk) +
from_visual discards real photos into logo_candidates + any-alt→rank-0 + cover-crop truncates text
banners + "punchy MACRO" hook rule picked a screwdriver; (5) culture = locale primer wired only to
the unused classic path; reel hardcodes ANY-Arabic→"Egyptian" (anti-universal) + languages[0] is
ALPHABETICAL so 'en' beat 'ar' (English captions on an Egyptian reel). Fix order: A scraper/picker →
B from_visual projection → C poster → D reel; every fix measured; owner authorized larger crawls.

**FIX-A SHIPPED (leaf discovery + picker, 2026-07-11): NTI picker 0 → 10 real named+priced items
instantly.** (a) `is_leaf_detail` (page_type.py) — universal leaf shapes: offering segment + slug
(/courses/x, /services/x, /menu/x) OR offering-named CMS file + id query (courses.php?catID=601;
?page= excluded); Shopify rule untouched. (b) Multi-depth crawl (crawler.py): `_bfs_new_candidates`
(pure, hermetic) re-seeds the frontier from every fetched page's links — leaf-first, HIGH/MEDIUM
only, deduped, depth≤3 (config MAX_CRAWL_DEPTH), ≤120 new candidates, same page-cap/time budget
(spends what depth-1 left on the table: NTI wasted 370s/13 slots); `_reserve_product_quota` now
uses is_leaf_detail so course/service/menu leaves get the diversified budget share. The EXISTING
ajax-modal resolver (built for NTI's url-less course popovers — learns the JS `.load(prefix +
data-attr + suffix)` template and reconstructs real detail URLs) now actually fires: BFS finally
reaches the category pages that host the popovers. (c) Picker (dashboard/products.py) priority
sources: profile offerings (names+prices, any vertical) → leaf pages named by their own h1/h2 →
Shopify slug fallback (byte-compatible for stores); server passes out_dir. +9 hermetic tests; suite
1221. Re-scrape measurement of NTI (BFS reach + modal pages) recorded below when complete.

**FIX-A MEASURED (nti re-scrape with the multi-depth crawler): the owner's url-less popups are now
scraped pages.** OLD: 26 pages / 169s / 0 bfs-ajax events. NEW: **45 pages / 302s / 17 bfs+ajax
notes** — BFS reached `eta/courses.php?catID=601` (the course-category leaf) and the EXISTING ajax
resolver then reconstructed the six JS-modal course URLs (`eta/pages/modules/10001-10006.html`),
each scraped WITH full content (Huawei Big Data Associate 80h, Cloud Computing 70h, HCIA-Security,
5G, AI, Datacom — 22-59 text blocks each). Plus the Tracks pages (dey/cat4m.php, catcr.php...).
Budget still not exceeded (302s < 540s). **Picker: 0 → 14 real items** — profile offerings with
prices (incl. Arabic: مبادرة تمويل اللاب توب) + the six modal courses named by their own h1
(picker refinement: an 'other'-typed page qualifies when its URL is offering-shaped
(is_leaf_detail) AND it announces itself with a heading — the JS-modal course pages classify
as 'other').

**FIX-C SHIPPED (poster: white plate + structural vagueness + locale, suite 1231):** three fixes in
one front. **C1 white plate**: the killer was the prompt's own sentence "the brand's real logo is
placed into that space afterward" → the model painted a literal white placeholder slot (the plate
the owner fought for 2 months). New corner clause = "plain, low-detail CONTINUATION of the SAME
background artwork" + explicit ban ("no white or blank box, panel, rectangle…"); plus a
DETERMINISTIC tile-based corner gate (`_corner_placeholder_detected`, 12×2 luminance tiles, a
near-uniform tile far from the band median on the logo side ⇒ retry BEFORE compositing —
legitimately light full-width headers don't trip it, verified by synthetic tests). **C2 place
assets**: `_gather_product_props` now returns (props, allowed, ROLE) — service categories attach
their REAL premises photos as role='place' (post-FIX-B these are the real campus/facility shots)
with a REAL PLACE prompt block ("set THE SCENE inside this real place"), instead of the old hard
bail-out that attached NOTHING and condemned every service poster to stock; product brands keep
role='prop' unchanged; a picked product is always a prop. **C3 locale**: new shared
`poster/locale.py` — `brand_locale(profile)` derives the country from EVIDENCE (ccTLD table
worldwide → service_areas → phone e164 → language cue → honest ('','')) and the proven anti-drift
primer wording; injected into `build_oneshot_prompt(locale_line=)` AND the concept builder
(Country/market in the user block + "scene is set in {country}" in the visual_idea spec). Nothing
hardcoded: a .sa Arabic brand renders Saudi (regression test), unknown locale injects nothing.
+10 hermetic tests. **LIVE VISUAL VERDICT (nti, outputs/posters/nti_fixc_after.png vs
nti_sci_eg_poster.png):** white plate GONE (logo direct on the navy header — the 2-month defect is
dead; the corner gate never even fired, the prompt fix alone prevented the slot); person reads
authentically Egyptian (was Western stock); copy concrete ("خليك مدير نفسك" + freelance-career
programs + "سجّل دلوقتي في برامجنا"); log confirms "2 real place photo(s) attached" and the
art-critic REJECTED attempt 1 as "generic stock photography" then passed attempt 2 (fidelity 1.0).
HONEST remainder: the scene is a bright plausible office, not yet unmistakably the NTI campus —
the model draws on the attached place photos loosely; that refinement belongs to the HELD
art-direction restructure, no longer a structural defect.

**FIX-B SHIPPED + measured (from_visual projection — feeds poster AND reel):** two silent killers
in `business_profile/rules/from_visual.py`: (1) `_logo_basenames` swallowed the ENTIRE
logo_candidates bucket ("a real photo is never a logo candidate" — FALSE: the scraper scores every
prominent image, so nti's real facility photos sat there as score-20 'unknown_candidate' entries
and were dropped from content_images); only candidates whose classification says logo count now.
(2) `_content_rank`'s "ANY non-empty alt → rank 0" let help.png ('Create User Profile'), partner
logo-walls ('Industry Partners') and the screwdriver macro ('eme') LEAD; rank-0 now requires a
product-like alt (≥2 words, ≥8 chars, no UI/partner/logo token). MEASURED on the fresh nti
extract: content_images went from junk-led to **the real facility photos leading** (about/eme2,
dey2, acadmy, wazefa2 + nasr_city 01-03 + smart campus). +2 hermetic tests; suite 1223.

**Studio: product-only scrape snapshot (owner feature, 2026-07-11):** the moment a PRODUCT-specific
poster/reel generation starts in the studio (picker → Generate), `dashboard.products.save_product_scrape`
writes `scrapes/<brand>_<ts>/product_scrape_<product>.json` — a grounded snapshot of how far the crawl
reached for THAT product: `coverage` at a glance (reached_product_page / text_blocks_mentioning_product /
images_matched / priced_offering_found) + the matched pages/images/offerings themselves (from the
existing manifest + profile — nothing invented). Whole-brand generations write nothing; failures never
block generation (try/except + progress line shows the file path in the studio stream). Suite 1212
(+2 hermetic). Also: session launch.json now pins the studio's cwd to the MAIN repo (the worktree has
no .env/scrapes/outputs).

**U1 GATE — first measured number (2026-07-11): objective 57% / destination 46% — FAIL vs ≥80%.**
Owner ratified all 14 `expected_objective`/`expected_destination` labels in `ground_truth.json`;
`python -m benchmark.grade_u1` now prints a real number. (First run read 0% — a wiring bug, not
deduction: `grade_u1` was the ONE entrypoint in the repo not loading `.env`, so `default_caller`
saw no creds and the builder honest-degraded to None on all 13. Fixed: `_load_env()` at
entrypoint-only + hermetic never-overrides test.) **Miss decomposition (offline signal dump, no
LLM):** 8/14 objective-correct. The 6 misses are ~all SIGNAL COVERAGE, not reasoning: (a) the
`online_store` signal requires ecom-category OR cart-ish URL hints, so **buffalo_burger** (22 priced
offerings on `/menu`) and **norshek** (12 priced on homepage, category='') expose NO store surface →
the LLM obeys the hard rule and picks Leads; (b) **mcdonalds_eg** (11 offerings, 0 priced) +
**zooba** (2 offerings, 0 priced) — prices never extracted → Traffic; (c) **alameda** profile exposes
ONLY `website` (no phone/form for a hospital — extraction gap) → Awareness; (d) **mumm** = no profile
(bot protection, permanent unless re-scraped) → ceiling is 13/14 = 93%. **andalusia** is a label
nuance (deduced LEADS ✓ via phone/form surface; owner label says dest=website). Proposed fix order
(ONE at a time, measured): FIX-1 broaden `online_store` signal universally (N≥3 priced offerings on
own domain OR order-ish URL hints) → predicted +2 = 71%; FIX-2 extraction gaps (mcdonalds/zooba
prices, alameda contact channels, norshek category — overlaps the norshek/almentor 0.50 outliers) →
predicted ceiling ~93%. Awaiting owner approval.

**FIX-1 SHIPPED + measured (2026-07-11): 57% → 71% objective — prediction exact (+2: buffalo,
norshek).** Universal change in `media_plan/builder.conversion_signals`: (a) order-ish URL hints
added to the cart patterns (`order`, `/menu`, `/store`, `/buy`); (b) NEW priced-catalog rule —
`_PRICED_CATALOG_MIN=3` priced offerings on the brand's OWN domain (`_same_site`, www-insensitive;
marketplace URLs don't count; a lone/duo price like a delivery fee stays below threshold) emits the
`online_store` surface even with no cart URL and no ecom category. Offline signal dump confirmed
zero false surfaces (all 4 clinics unchanged). +4 hermetic edge tests (catalog≥3 / menu-hint /
below-threshold / offsite). Destination 46% → 54%. NOTE: assalam's destination flipped phone→(other
lead surface) across runs — objective is stable, but the destination choice among a brand's several
equivalent LEAD surfaces (whatsapp/phone/form) is degenerate + nondeterministic; the U1 gate is
objective-accuracy by design, destination stays secondary. Remaining 4 misses = extraction-side
(mcdonalds/zooba unpriced offerings, alameda missing contact surfaces) + mumm (no profile). FIX-2
predicted: 12/14 = 86% > gate.

**FIX-2 diagnosis (3 parallel read-only investigators, 2026-07-11) — evidence overturned two
assumptions:** (a) mcdonalds/zooba prices are NOT an extraction bug and NOT image-menus (screenshots
verified price-free): the first-party domains genuinely publish no prices — mcdonalds.eg's menu is
names+calories (prices live on the never-followed mcdelivery.eg), zoobaeats.com is a brand-story
site delegating orders to Talabat/InstaShop/Mrsool/elmenus. OCR would recover nothing HERE (the
image-menu class — e.g. Kababgy-style photographed menus — remains a real future ticket, separate).
(b) alameda's hotlines/booking forms live on *.alameda-hc.com SUBDOMAINS that the 2026-05-30 scrape
classified EXTERNAL under the old pre-PSL host check — the PSL fix already landed since, so a
RE-SCRAPE alone should recover them (FIX-2b, no code). (c) norshek category was silently nulled by
the validator (enum-coercion miss / evidence gate drop with ZERO rejection records — the raw LLM
string is discarded untraceably); its OTHER empties share a scraper budget kill (2/165 pages in
62s) — separate ticketed defect.

**FIX-2c SHIPPED + measured (2026-07-11): 71% → 93% objective — U1 GATE PASS (≥80%).** Universal
`_ordering_links(manifest)` in the builder: order-intent OUTBOUND links on the brand's own pages =
online-ordering surface (anchor whole-word EN+AR / host substring / delimited path tokens;
`workshop`/`borderline`/App-Store traps excluded; evidence cites the BRAND's page, never the third
party — no third-party scraping). Manifest enters as an OPTIONAL param through
`conversion_signals(profile, manifest=None)` + `build_campaign_objective(..., manifest=None)` —
zero touch on the frozen profile schema (PD-1). mcdonalds ✓✓ + zooba ✓✓ exactly as predicted; the
only miss is mumm (no profile — 13/14 is the ceiling). Destination 54% → 69%. HONESTY CAVEAT:
alameda's LEADS this run is LLM VARIANCE, not a fix (prior run deduced AWARENESS on the same
[website]-only signals) — FIX-2b re-scrape is what stabilizes it. +4 hermetic tests; suite 1208.

**FIX-2a SHIPPED + verified live (2026-07-11): the validator's silent category drop is closed.**
Two paths fixed in `business_profile/llm/validator._validate_identity`: (1) an enum-coercion miss
now appends a `RejectionRecord(code=enum_coercion_miss)` carrying the RAW LLM string — 'LLM said
null' vs 'non-enum string' vs 'no evidence' are finally distinguishable in diagnostics (norshek's
drop was untraceable: rejections=0, raw string discarded); (2) category validates with
`require_evidence=False` — it is a whole-pack enum-constrained CLASSIFICATION (inferred=True), so a
valid member no longer dies for missing/invalid block refs; tagline/description keep the strict
gate; evidence is still validated + attached when present. Live verification: norshek re-extracted
(Gemini) → `category="ecommerce"` WITH grounded evidence ("Shop Now" blocks), zero fields_dropped.
+2 hermetic tests (classification survives empty evidence; enum miss leaves a raw-string trace);
suite 1210. NOTE: norshek's remaining thinness (2/165 pages, 62s budget kill) is a separate
ticketed scraper defect (crawl budget vs JS-render latency), NOT bundled here.

**FIX-2b DONE + measured (2026-07-11) — U1 journey COMPLETE: 57% → 93% PASS at the theoretical
ceiling.** alameda re-scraped with the current PSL-aware crawler (zero new code — the
`same_registrable_host` fix had landed after the stale 2026-05-30 scrape): the crawler finally
entered the *.alameda-hc.com hospital subdomains → **7 forms** (was 0) + hotlines **19885/16370** +
+201099569334 in the new profile → signals [phone, contact_form, website]. Final gate: **objective
93% (13/14) STABLE** — alameda LEADS ✓ + lead_form ✓ on real surfaces (no more LLM variance),
**destination 77%** (69%→77%). The only objective miss is mumm (bot protection, no profile) —
13/14 IS the ceiling. Remaining dest misses are non-defects: assalam (lead-surface degeneracy),
andalusia (owner label=website vs deduced lead surface — label nuance), almentor (no offerings
extracted; part of the thin-crawl follow-up). Operational notes: (a) `run_scrape`'s 300s subprocess
timeout is too tight post-PSL for subdomain-rich sites (alameda took ~6min direct) — bump when next
touching the runner; (b) alameda's new profile flags `low_evidence_coverage` (3020 blocks dwarf the
extracted evidence) — structural-score follow-up, not a U1 concern; (c) a partial
`alameda-hc_com_20260711_071618/` dir (killed scrape, no manifest) is inert — never deleted per
policy, superseded by `_072238`.

**BASELINE (post-audit, new project, 2026-07-10):** first full 14-URL benchmark on the fresh GCP
project (`radiant-octane-501919-v1`, Vertex ADC — old `image-498715` was dunning-suspended). This is
the REFERENCE POINT for every measured change from here. `benchmark/runs/20260711_055349/`:
**13 graded** (mumm excluded — bot protection, expected), **11/13 ready (85%)** (> 0.786 gate ✅),
**avg SWOT-critical 0.846** (JUST under the 0.85 gate — marginal FAIL by rounding). Per-vertical: clinic
0.92 · ecommerce 1.00 · restaurant 0.83 · skincare 0.81 · education 0.75 (all > 0.70 floor). Two
outliers drag it: **norshek 0.50 + almentor 0.50** (both not-ready — investigate as a quality follow-up,
NOT the U1 gate). CAVEAT: this is a **STRUCTURAL-only** average — the 3 fuzzy SWOT-critical fields
(audience/value_propositions/tone_of_voice) are UNGRADED until `ground_truth.json` is labelled; the real
SWOT-critical number will differ once they are. Headline: the prompt audit + Gemini migration did NOT
regress extraction (11/13 ready, most URLs 0.75–1.0). **Quota diagnosis: per-MINUTE** — the first run
429'd on rapid cached re-extraction; this run (fresh scrapes pacing it, quota reset between runs)
completed with 0 backoff events. The `run_extract` backoff is the safety net for fast re-extraction.
**Quota resolution (2026-07-11):** no manual increase — owner enabled the Vertex **Quota adjuster**
(Quotas → Configurations → Enable; auto-raises adjustable quotas gradually from usage). Two nets now:
runner backoff (code) + adjuster (console). Future-429 map: the "…**with audio input**…" rows are System
limits (`Adjustable=No`, dim `base_model_id_and_resolution`) — NOT the throttle; the tokens quota (10^10)
is never it. **CONFIRMED it's DSQ:** filtering dim `base_model:gemini-2.5-flash-ga` returns only two token
rows — per-**minute** input tokens = "Unlimited", per-**day** = 10^10 (~0% used); there is **no
requests/min quota row**. So the 429 was transient Dynamic-Shared-Quota contention, not exhaustion —
nothing to raise, the adjuster is a no-op here, and the only levers are backoff (have it) + paid
Provisioned Throughput if a 0-429 SLA is ever needed.

**U1 Media Plan — schemas + builder (the real U1 gate) shipped (2026-07-07):** the priority v2.2 unit
(the defense asks about the objective, not prompt hygiene). New additive `media_plan/` package
(INTERFACES.md §F), profile pipeline untouched. **Schemas** (`schemas.py`, strict/PD-5): the 6
`MetaObjective` (ODAX `.value` = Ads-API string, imports to Ads Manager untranslated) + `FunnelStage`
/`GeoMode`/`Destination` enums, `KPITarget` (positivity-bounded), `EvidenceRef` (resolution-wrapper — a
claim is a fact only once Ledger-resolved), `Persona` (every axis an `EvidenceRef` → grounded, not a
guess), `GeoTargeting` (mode-consistent + mutually-exclusive), `CampaignObjective`
(funnel_stage/budget_allocation_pct/num_ad_sets/test_budget + `_learning_floor` 3×-CPL gated to
cost-metrics), `MediaPlan` (allocations sum==100, single-objective auto-100, weakest-link `is_grounded`
folding in objectives+persona+geo). A 15-agent adversarial review (owner: overkill — 3 lenses next time)
found+verified 4 real "silent-math" bugs, all fixed (is_grounded ignored persona/geo; negative target
silently disabled the floor; floor misfired on non-cost metrics; geo modes not mutually exclusive).
**Builder** (`builder.py`, the gate): `build_campaign_objective(profile, caller)` deduces objective +
destination TOGETHER from real facts, GROUNDS the destination in actual conversion surfaces
(`conversion_signals`: WhatsApp/checkout/phone/form/address/site → resolved EvidenceRef); a deduced
destination with no signal → honest `is_grounded=False` (advisor flags, never fabricates a store); no
caller/thin profile → None. +19 hermetic tests (MockCaller, offline). Owner decisions: kept 3× (budget
floor) + ticketed ~50-conversion learning-phase exit as a U3 guardrail (T-4); declined global
`strict=True` (breaks U6 json-dict round-trip); objective↔destination compat is the builder's job (inferential).
**Blocker:** Gemini billing still 403 dunning on `image-498715` (106225033713) — the 14-URL baseline
benchmark waits on the owner's console reactivation; U1 is offline and did not.

**Batch 3 (poster image) — COMPLIANCE_CONTRACT + shared image negative; Batch 5 calendar (2026-07-07):**
owner's Batches 3–5. Shipped the SAFE, high-leverage core this pass; the vertical→shape restructures +
persona strip are the next unit (heavy test coupling — the reel `_system_prompt` alone has 50+ substring
assertions). **(1) `COMPLIANCE_CONTRACT`** (owner's #1 fix — real ad-safety, was buried in 5 aesthetic
prompts and MISSING on the LLM + reel paths): `poster/contracts.compliance_for(category)` is now the ONE
source (medical → no patients/before-after/procedures/guaranteed-results; skincare/beauty → no
before-after/clinical-claims/skin-disease; finance/legal → no guaranteed returns; else empty). Wired into
`build_llm_concept_prompt` (the LLM image path that had NO compliance) and the reel `_system_prompt` (also
none) — additive + empty for non-medical, so the 50+ reel assertions + all image tests stay green. It also
feeds the future Meta Policy Linter (one compliance truth). **(2) `IMAGE_NEGATIVE_CONTRACT` +
`IMAGE_NEGATIVE_TERMS`** — the shared text-free/artefact-free image negative, promoted verbatim from
art_director's per-call `negative_prompt` (zero-change consolidation). **(3) Batch 5 calendar** — explicit
trend-weighting in the content-calendar prompt (trends shape timing/angle of GROUNDED content, never invent;
most stays evergreen). +4 tests. **Batch 4 keystone — reel persona stripped (2026-07-07):** the owner's "strongest single anti-convergence
move". The reel `_system_prompt` opened with "SENIOR CREATIVE DIRECTOR with 15 years in performance-
marketing and TikTok/Reels" — the most homogenizing primer in the catalog (it made a heritage jeweller and
a discount grocer sound identical). Replaced with `CRAFT_CONTRACT` + a brand-DERIVED register ("a luxury
house is unhurried; a youth brand is fast; a clinic is calm — do NOT default every brand to high-energy
TikTok pacing"). The ad-SPINE (HOOK→WHAT-IT-IS→BENEFIT→PROOF→CTA), the SUBJECT+STYLE+CAMERA+LIGHTING+MOTION
agency formula, BANNED WORDS, FRAMING, and the "OBSESSED with realism" emphasis are ALL kept verbatim.
+de-primed "must feel premium"→"polished". 1 test updated (persona→craft-bar+register).

**DEFERRED (next unit, need config-map decision + heavy test rework):** collapse
`build_art_direction` 5 vertical prompts → ~3 scene shapes on the Batch-1 category→shape map (reusable —
`_shape_for` is importable); restructure reel `_MOTION_GUIDANCE` 4 keys → motion shapes + re-key
`_DELIVERY_EG`; promote `_MOTION_TAIL` → `MOTION_CONTRACT`; strip "award-winning" primers in
`build_design_spec`/`build_llm_concept_prompt`; the `build_creative_prompt` surreal-default alignment.
**Measure-first tickets (blocked by Gemini billing 403):** oneshot negative→positive reframe (B2), scene-QA
compliance criterion (B4), `bake_text` reachability (B3), cross-lingual RAG recall (B1).

**Batch 2 prompt refactor — poster concept/copy/fidelity chain (2026-07-07):** owner's expert 5-lens
critique of the 11 poster prompts. Two systemic weaknesses: copy-side aesthetic PRIMING that made every
brand's copy converge on the same imagined award-show ad (finding 3), and judge-side Arabic blindness
(the QA gate only checked "no Latin", never whether the Arabic was well-formed). Fixes: **(1) two shared
contracts** in new `poster/contracts.py` (the Batch-1 pattern applied to poster): `CRAFT_CONTRACT` — a
BAR not a brag ("would this brand actually ship it"), earn attention through the ONE specific real thing,
bans the empty primers (premium/world-class/award-winning/cutting-edge/…), states the philosophy "facts
gated, form free"; `VERBATIM_RENDER_CONTRACT` — the frozen-strings rule shared by the one-shot renderer
AND the OCR read-back reader so both speak one language (Arabic dot-counts ت=2/ث=3, ة vs ه). **(2) concept
de-primed** — stripped "senior advertising CREATIVE DIRECTOR", injected `CRAFT_CONTRACT`, kept the
excellent STRANGER TEST verbatim as the positive engine, added an anti-convergence line ("two businesses
must never produce interchangeable concepts") + a jargon→customer-language instruction; the regenerate
loop now re-asserts the Stranger Test so a fix doesn't break the glance. **(3) oneshot** — de-primed +
injected `VERBATIM_RENDER_CONTRACT` (the full negative→positive restructure is TICKETED measure-first:
confirm OCR pass-rate on the paid benchmark before locking — blocked now by the Gemini billing 403).
**(4) vision-QA Arabic gate** — new `script_wellformed` criterion: for non-Latin copy the rendered text
must be correctly SHAPED/CONNECTED with correct dots, not merely "no Latin"; a no-Latin-but-garbled-Arabic
poster now FAILS (code-side hard gate, re-ANDed like logo_ok). This composes with the existing OCR
character-fidelity gate (pipeline.py:512, catches wrong CHARACTERS) — together they cover both failure
modes (wrong chars + visual malformation). KEPT untouched (owner: exemplary): `_grounding_problems`, the
Arabic OCR dot-counting user prompt, `copy_style_cue`, the variation cues. +6 tests.

**All text/reasoning migrated OFF Anthropic → Gemini 2.5 Pro (2026-07-07):** owner directive —
"the whole project uses Gemini 2.5 Pro" + "no Claude, don't write the word in any file". Three code
paths still called the Anthropic SDK directly (and the ANTHROPIC_API_KEY was set, so they ran):
`business_profile/domain_schema.py` (domain-adaptive schema), `competitor/themes.py` (review themes),
`reel/creative_director.py` (the reel director — WITH images). All three now route through the shared
`default_caller(strong=True)` = `GeminiCaller(gemini-2.5-pro)` with a Pydantic response_schema
(structured output, no manual JSON strip): new `_RawDomainResponse`, `_ThemesResponse`, `_ReelResponse`
models; the reel's real photos go to Gemini as `images=[(bytes,mime)]` (it's natively multimodal) via
the renamed `_image_parts`. Every grounding/validation stayed byte-for-byte (the `_is_grounded` field
filter, the review-citation support floor, the scene clamping) — the moat is CODE, not the model.
Honest-degrade preserved: no Gemini caller (creds/SDK) → None/[]. `AnthropicThemeExtractor` renamed to
`ReviewThemeExtractor` (callers + exports updated, no alias). `anthropic==0.106.0` dropped from
requirements. Result: **zero `anthropic`/`claude` anywhere in code** (only the required nothing — the
API is Gemini now). Also fixed a real bug the migration exposed: `grounding/measure_reel.py` gated the
creative path on `ANTHROPIC_API_KEY` → now checks Gemini creds. +13 tests (each path: injected-caller
happy path + honest-degrade + grounding-drops-fabrication). ⚠️ Gemini billing on the GCP project was
DENIED (403 dunning) when measured — these paths (and all other Gemini calls) only run once billing is
restored; until then the reel director that used to run on Opus returns None (expected, per the
all-Gemini directive). **Model stack now:** text/reasoning = Gemini 2.5 Pro/Flash; poster image =
gemini-3-pro-image-preview (oneshot, for exact-text fidelity) + Imagen 4 Ultra (classic); reel video =
Veo 3.1 (highest Veo 3). See the Model map below.

**Discovery routing — REACH businesses compete by category, not proximity (2026-07-07):** owner's ITI
follow-up ("who should a specialized institute's competitors be? IT institutes / bootcamps / online
platforms?"). Root cause: `business_type.classify_business_type` had `education` in `_LOCAL_CATEGORIES`,
so ITI → LOCAL → Places-proximity ONLY, which returns broad-category NEIGHBOURS (universities, the
faculty of education, the brand's own branch) — proximity is the WRONG relevance model for a business
that competes by category across a city/country/online. Fix: new `BusinessType.REACH` +
`_REACH_CATEGORIES = {education, professional_services, services_b2b, agency}`; education removed from
`_LOCAL_CATEGORIES`. A reach business with NO local footprint → REACH (web-discovery only); WITH a
campus address → HYBRID (Places local peers + web category peers), never pure proximity-LOCAL. The
router routes REACH → the SERP web engine, whose LLM relevance judge finds true category peers and drops
universities/directories (`topuniversities.com` etc. already block-listed). The production path
(`full_run.py`, `api/routes/swot.py`) already wires `default_web_engine()`, and `SERPER_API_KEY` is set,
so this is LIVE — ITI now routes to category web-discovery instead of Places noise. Universal (no
IT-institute hack): the signal is "competes by reach/category", not the specific vertical. +5 tests.
Pairs with the earlier cross-script self-match fix (ITI was listing its own branch as a peer).

**Batch 1 prompt refactor — one GROUNDING_CONTRACT + catalog shapes (2026-07-07):** owner ran an
expert 5-lens critique of the 10 business_profile prompts and asked to "fix these first prompts." The
root problem was DUPLICATION, not bad instructions: nine prompts each re-stated the grounding rules,
and the copies had drifted (one call stricter than another). Mapped exhaustively first (a 5-agent
workflow: exact source + the ONLY consumer `extractor.py` + every test-asserted substring + the
category→guidance mechanism), then adversarially reviewed the diff before committing. Changes:
**(1) `GROUNDING_CONTRACT`** — the canonical block (5 non-negotiable rules + a positive coverage line +
the lifted anti-cliché "specificity is the filter" rule), stated ONCE and injected into the shared
`SYSTEM_PROMPT`; the 4 group USER prompts inherit it instead of re-litigating (SYSTEM_PROMPT is sent as
the system role for all groups). **(2) identity** — tagline is COPY-verbatim, description is
COMPOSE-but-every-claim-cites-a-block_id (the implicit mode-switch made explicit); category enum
hardened ("'other' only when nothing fits, never a shortcut for 'unsure'" — it routes downstream
specialization). **(3) offerings** — dropped the prose FORBIDDEN-CLAIMS block (redundant with contract
rule 3 + the `UNSUBSTANTIATED_CLAIM_TOKENS` validator); kept the own-name ban + honest-empty + the
1-2-broad-category cap. **(4) `_OFFERINGS_GUIDANCE` (18 vertical keys) → `_CATALOG_SHAPES` (4 universal
shapes: broad_catalog / named_menu / programs / default) + `_CATEGORY_TO_SHAPE` map** — the central
fix: config keyed by a universal signal with a universal default (rule 5), so `_shape_for(None/unknown)
→ default` BY CONSTRUCTION and the documented fail-open None-key bug is impossible; every hard-won
specific (DEPARTMENTS-FIRST, SKU≠offering, breadth, named-menu discipline) preserved inside the shapes.
**(5) trust** — deleted "Skip vague self-praise" (the contract's specificity rule covers it).
**(6) same-subject judge** — its 400-char truncation is now WINDOWED on the shared token (a head-slice
clipped the proving sentence on long blocks); `_window` coerces inputs so it can't break the judge's
never-raise→None guarantee; `_SYSTEM` (measurement-locked 98%) untouched, short claims byte-identical.
**(7) domain_schema** — attributes must be marketer-ACTIONABLE (targeting axis / message angle / proof
point), not trivia. NOT touched (measure-first): `_GROUP_QUERIES` cross-lingual recall (ticketed).
+7 tests; docs/PROMPTS.md updated (§1: `_CATALOG_SHAPES`, GROUNDING_CONTRACT).

**Poster primary color must DOMINATE + never self-list a competitor (2026-07-07):** two more
"continue on the best solutions" fixes. **(1) on-brand color dominance** (owner: the ITI poster read
navy when the brand is red — "the brand color should DOMINATE, not just the CTA"): the palette DATA was
already fixed (logo-SVG extraction leads with ITI's red `#9c3c3c`; navy is 5th and never reaches the
top-4 `_palette_names`), but the one-shot COLOR SYSTEM mandate let the model satisfy "make it present"
with only the CTA button (the old "or a dominant color field" was optional). Strengthened: the first
palette color is the PRIMARY and must occupy a LARGE color field (background zone / major panel / hero
graphic area) so the poster reads as this brand at a glance; neutrals may support but never out-weigh
it, and the model may not drift to a generic default navy. Universal; the render is still measured by
the paid benchmark. +1 test. **(2) no self-competitor across scripts** (owner: irrelevant "competitors"
for ITI — the saved run in fact listed ITI as its OWN peer): the Places result «معهد تكنولوجيا
المعلومات - ITI» (an Alexandria branch, no website) beat both `_is_self` checks — the profile carried
its URL only in `source_url` (so the domain check never ran) and the exact-name check compared English
"Information Technology Institute" to the Arabic listing. `_is_self` now also reads `source_url` and
adds a cross-script brand-label check: the subject's registrable-domain label ("iti" from iti.gov.eg,
PSL-correct) matched as a distinct token in the candidate name catches a different-script branch of the
same brand; guarded by a generic-label stoplist + ≥3-char rule so it never drops a real peer sharing a
common word. +4 tests.

**Reel pre-render plan-eval + poster service-prop gate + PROMPTS.md catalog (2026-07-07):** three
landed under the owner's "continue on the best solutions" authority. **(1) evaluate the reel BEFORE it
comes out** (owner's explicit ask): new `reel/plan_eval.evaluate_reel_plan(reel, profile, featured)`
scores a plan against the stranger test + caption craft (names the brand −30 / names an
offering-or-category −20 / CTA on the last scene −15 / caption-driven −15 / captions ≤6 words −10 /
scene variety for non-featured −10) and returns `ReelPlanVerdict(ok, score, issues)`. Deterministic —
no LLM/network — so it's a cheap pre-flight: `render_creative_reel` runs it after the Opus design, and
regenerates the plan ONCE (a cheap call) when it fails, keeping the higher-scoring plan, all BEFORE the
10-15 min Veo render (the compositor's per-clip scene_qa still runs after). +3 tests. **(2) poster
service-prop gate** (fixes ITI clutter/framed-face): a whole-brand poster for a SERVICE brand
(education/clinic/agency/government/…) was compositing its scraped content images as one-shot "product
props", but those are BUILDINGS and PEOPLE → clutter + a hallucinated framed face. `_gather_product_props`
now attaches NO whole-brand props for `_SERVICE_NO_PROP_CATS`; product/food brands (and any unknown
category) keep the prop path; a user-PICKED product always attaches regardless of category. +1 test.
**(3) `docs/PROMPTS.md`** (owner's explicit ask, "a file with all the project's prompts"): the single
catalog of all 53 LLM/image/vision prompts across the 5 subsystems, each indexed by file:line
(clickable), receiving model, kind, purpose, and a representative excerpt, opening with the
two-truth-domains map (LLM designs, code renders + validates).

**Reel captions DESIGNED + paced + voice not flat (2026-07-07):** owner: "the reel text is written with
no design, too fast, boring, unlistenable — study how big brands do it." A 3-agent workflow (research
top-brand short-form craft + audit our rendering + synthesize a spec) found the root cause: the CREATIVE
reel put every caption into `sublines` with `kind="gallery"`, so they rendered with the plainest
template rule (`.item`: Inter 600, ~50px, flat white, one soft fade) — the rich designed lockup /
accent-CTA-chip / logo only fired for intro/outro. Fixes: **(1) designed captions** —
`build_creative_storyboard` routes scene 0's caption → `headline` (kind=intro → hero lockup + logo), the
last → `cta_text` (kind=outro → accent CTA chip + logo), middle captions → `headline` (the big display
`.headline`, not the tiny flat `.item`); `.headline` gains readability ARMOR (`-webkit-text-stroke:2px`
+ `paint-order:stroke fill`). **(2) readable pacing** — a captioned scene holds `max(3, words*0.4+1.5)`s
(was ~2s → the "too fast" flash). **(3) caption-driven** — the director prompt wants a SHORT 2-4 word
caption on MOST scenes (benefit/proof too), with the reading-speed duration + split-if-over-7-words rule.
**(4) voice not flat** — the free edge-tts path derives rate/pitch from tone (playful +9%/+4Hz, default
+4%/+2Hz, luxury -4%) instead of a flat +0%/+0Hz monotone. +6 tests. Deferred (needs timing infra):
word-synced karaoke active-word highlighting. Grounded in research (OpusClip/Blitzcut caption specs,
3-7 words @ 2-3s, 12-17 CPS, white+black-stroke, 1s hook).

**Reel director — say what the brand DOES, vary the scene, pace the text (2026-07-07):** owner's reel
feedback (same STRANGER-TEST point as the poster): (1) "in every reel I need to understand what the
brand does; if it's a service, name the service" — the prompt was entirely product-centric ("the
product", "Shop the mist"), useless for a service brand (ITI). Fixed: WHAT-IT-IS beat is now the
STRANGER TEST (voiceover must convey WHO + WHAT IT DOES/category + the SPECIFIC named product OR
service); BENEFIT generalised to "a PRODUCT mid-use OR a SERVICE delivered in its real setting (a
learner coding in the lab, a graduate hired)"; HARD RULE + SELF-CHECK now demand product-OR-service.
(2) "the background must NOT stay behind me the whole reel" — new SCENE VARIETY rule: every scene is a
DIFFERENT setting/angle/moment; never one backdrop held the whole reel; even with one photo, change
framing/distance/action each scene (no frozen wallpaper). (3) "the text goes by too fast, and no
feeling (but not over-the-top)" — captions ≤4-5 words and must stay on screen LONG ENOUGH TO READ (a
captioned scene needs ≥3s); voiceover delivery is REAL/warm but MEASURED (never flat/robotic, never
melodramatic/over-acted). +1 test; existing 17 preserved. Pairs with the poster stranger-test prompt —
both assets now anchored in the concrete offering, not a vibe.

**SPA logo capture — the "poster with no logo" bug (ITI, 2026-07-07):** owner: the ITI poster shipped
with NO logo. Root cause found in `visual.py`: ITI's real mark IS captured (`<img
src=.../ColoredLogo.svg class="header__image">`, scored 42) but the custom Angular header
(`<app-header>`, not a `<header>`) never fired the +30 in-header bonus, so it landed as a sub-threshold
`unknown_candidate` and `logo_url` came out NONE. Fix: a **NAMED-LOGO rescue** in
`_choose_primary_logo` — a last resort (only when every other path found nothing) that promotes an
**SVG** whose filename is a **PURE brand-mark** (`ColoredLogo`/`WhiteLogo`/`logo`/`brand-logo`, via
`_is_pure_logo_filename`), excluding partner/authority/sponsor/favicon marks, preferring the coloured
variant over the white/footer inverse. Restricted to SVG + pure-name so it can NOT reopen the
floor-rescue's deliberate refusals (all of which are PNG/data-uri third-party walls / low-repetition
tiles). VERIFIED LIVE: ITI now `Logo found: True`, `primary_logo=ColoredLogo.svg` (was None). +2 tests.
Combined with the SPA XHR-capture + the poster stranger-test prompt, a re-generated ITI poster now has
a real logo + 12 grounded programs to anchor on.

**API-driven SPA scraping — recover content from same-site JSON (ITI, 2026-07-07):** owner (via the new
Scraper-QA panel) caught ITI (iti.gov.eg) finishing in 53s with only 4 pages + 0 offerings. DIAGNOSED
live: ITI is a full **Angular SPA** — its homepage exposes only 4 static `<a href>` links, and ALL real
content (the programme catalogue, contacts, partners) loads at runtime from **same-site JSON APIs**
(`pgateway.iti.gov.eg/OldApi/ProgramCategory/…`, navigated by the Angular router with NO href). A
link-following crawler sees a news-heavy shell → 0 offerings. Fix (universal): new
`scraper/xhr_capture.py::extract_json_text` flattens captured JSON bodies to their human-readable
strings (names/titles/descriptions; drops guids/urls/filenames/dates/hashes). `fetcher._fetch_page_once`
attaches a `page.on("response")` listener that captures **same-registrable-domain** JSON during the
render (`same_registrable_host`, so `pgateway.iti.gov.eg` ≡ `iti.gov.eg`; bounded 30×400KB), stores it
on `FetchResult.api_text` (+ appends to rendered_text). `crawler._process_fetched_page` turns
`api_text` into **CITABLE `tag="api"` text_blocks** (real block_ids, Section.MAIN) so offerings/contacts/
partners extracted from it survive the evidence validator. VERIFIED LIVE end-to-end: ITI homepage
text-blocks 370→684 (82 API blocks: Post Graduates / Under Graduates / Tech-Business / Vodafone / Fawry /
Work Phone …), and a full profile build now extracts **12 offerings (was 0)**. Non-SPA sites are
unchanged (no same-site JSON → empty capture). +5 tests.

**URL-less 'modal' details (NTI) — resolve the URLs JS builds from a data-attribute (2026-07-06, slice
1):** owner: NTI's course catalogue shows each course as a card; clicking opens a modal with the real
details (Overview/Prerequisites/Hours) but the modal has NO URL, so the crawler (which follows hrefs)
can't reach it ("شال الزايدات واسكراب nti نفسها"). DIAGNOSED live: `text_blocks.py` skips
`display:none` content, AND — the real cause — NTI's `js/popupCourse.js` does
`$('#popover-cont').load("pages/modules/" + link.getAttribute('data-target') + ".html")`, so each
course's detail page lives at `pages/modules/<data-target>.html` — a URL ASSEMBLED IN JS that never
appears in the HTML. New `scraper/ajax_details.py::extract_ajax_detail_urls(html, base_url, js_texts)`:
learns the `.load(PREFIX + el.attr + SUFFIX)` template from the page's JS (getAttribute / .attr / .data
/ dataset.x variants; drops the cache-buster query; skips `data-target="0"` sentinels) and applies it to
every trigger element → real absolute detail URLs. VALIDATED live on NTI: resolved all 6 course module
URLs (pages/modules/2631.html …) from the actual rendered page. Universal for the very common jQuery
`.load()` reveal pattern. +4 tests. **Slice 2 (DONE): wired into the crawler.**
`ajax_details.discover_ajax_details(html, base_url, fetch=…)` gathers a page's inline + SAME-ORIGIN
external JS (skips CDN jQuery; caches the shared script body per crawl) and resolves the detail URLs.
`crawler.py` calls it on the homepage AND every fetched sub-page (`_add_ajax_details`), SPLICING the
resolved URLs into the frontier right after the current page (so they're fetched next, not lost in the
tail) and bumping `page_cap` by the count added so they get their OWN slots (bounded by `_AJAX_MAX=40`;
time budget still applies; light/competitor mode skips). VERIFIED LIVE end-to-end: scraping NTI's
`coursesev.php?catID=205` now fetches all 6 `pages/modules/<id>.html` course pages with full content
(Module Name/Duration/Prerequisites/Hours/Description) — previously 0. +2 tests (discover gathers
same-origin external + inline JS). So a url-less-modal catalogue (the exact NTI case) is now fully
scraped, and each course flows into the profile/products like any page.

**Scrape a product by its URL — for an item the crawl never reached (2026-07-06, slice 1):** owner:
"لو اليوزر عايز يعمل [إعلان/بوستر] لمنتج مش موجود في الاسكرابر، يدّي لينك المنتج وأروح أسكرابه — صورته
وبياناته." The full crawler deliberately re-anchors a deep URL to the brand homepage
(`url_utils.site_root_if_deep`, so one deep page can't hijack the brand profile) — the OPPOSITE of this
need. New `scraper/product_page.py`: `parse_product(html, url)` (pure, hermetic) pulls ONE product's
name / image / price / description — JSON-LD schema.org `Product` FIRST (parsed directly, since
`extract_structured_data` intentionally skips JSON-LD; walks `@graph`), then og:/meta/`<h1>` fallback,
relative image URLs resolved. `scrape_product_page(url)` fetches the page via Playwright then parses.
VALIDATED on a real rawafrican page (extracted name+image live). +5 tests. **Slice 2 (DONE): wired into
the studio** — the product picker now has a "paste a product link not in the list" input; JS `addByUrl()`
→ `GET /product_by_url?url=` → `_serve_product_by_url` (public-URL guarded via `is_safe_public_url`) →
`scrape_product_page` → returns {name,image,price} → a new picker chip is added AND auto-selected, so it
flows straight into the reel/poster via the existing `pname/pimg` path. +4 tests (route ok/reject-non-
public/no-product + the studio UI). So a product the crawl never reached is one paste away from an ad.

**Reel SCENE QA gate — reject Veo hallucinations, don't just prompt against them (2026-07-06):**
owner watched a real render and caught three failures the prompt CANNOT stop (Veo i2v drifts from the
seed over a few seconds): the product is not faithful to the real photo, it 'magically' VANISHES
mid-scene, and it presses a SEALED pump ("هو سحر؟"). The system prompt asks Veo not to; it does anyway.
Honest fix = inspect the actual clip, not trust the prompt. New `reel/scene_qa.py::check_scene`:
extracts 3 frames (start/mid/end) from the generated clip, hands them + the REAL product photo (as the
reference) + the product name to a vision caller, and returns a structured verdict
(product_faithful / product_persists / action_plausible / overall_pass). Wired into
`compositor.render_reel(qa_caller=…, qa_product_hint=…, qa_reference_image=…)`: a clip that fails QA is
regenerated ONCE, then falls back to the FAITHFUL real-photo KenBurns — so a hallucination NEVER ships
(worst case = the true product with simple motion, not a vanished/redrawn one). `render_creative_reel`
turns the gate on for a FEATURED product (builds a Gemini caller, fetches the seed bytes via
`_load_reference_image`). QA is OFF (identical old behaviour, all prior tests green) when qa_caller is
None. This complements the creative-director playbook (prompt-level realism) with an OUTPUT-level gate —
the prompt reduces bad scenes, the gate removes the ones that slip through. +8 hermetic tests (verdict
logic + compositor reject→regen→faithful-fallback + pass-keeps + qa-off back-compat). Live proof render
pending owner go. NOTE the deferred `--product-image`/reel already seeds every scene from the real
photo; the remaining drift is a Veo-model limit the gate now catches.

**Picked product now drives the POSTER's IMAGE too, not just its name (2026-07-06):** owner: "عملت حوار
الراج وظبطت أنا أختار المنتج اللي عايزة أعمله الإعلان أو البوستر" — the studio RAG product-picker feeds
BOTH the ad (reel) and the poster. AUDIT of the poster path found a gap: `poster/__main__.py`'s
`--product-image` was declared but "reserved" — never wired — so picking a product set it as the hero
OFFERING (name → concept) but the poster's IMAGE-conditioning still composited whatever the first two
`content_images` were, NOT the picked product's real photo (the reel used the real image; the poster
didn't). Fixed by threading `product_image` through `generate_poster → _try_oneshot →
_gather_product_props`, where the picked photo is now the PRIMARY composited prop — kept even if the
quality gate would drop it (the user chose it), second slot filled from the gated rest, its real label
OCR'd into the fidelity allow-list. The studio poster uses `--engine oneshot` (dashboard/run.py), which
is exactly the composited-prop path, so the picker now makes the poster SHOW the exact chosen product
with its correct label (parallel to the reel's featured seed). +1 test. (Classic engine, CLI-only,
still conditions on content_images generically — a measured follow-up if ever needed.)

**Reel creative-director upgraded to the agency PERFORMANCE-AD playbook (2026-07-06):** owner supplied a
detailed Senior-Creative-Director brief (Hook→Body→CTA spine; the SUBJECT+STYLE+CAMERA+LIGHTING+MOTION
prompt formula; explicit camera/lighting terms; a banned-words list "slow zoom"/"refined"/"dreamy"; a
physics Logic-Check). `reel/creative_director.py::_system_prompt` now encodes all of it: a 15-yr
performance-marketer persona obsessed with realism; a HOOK that opens on a proven hook TYPE (the mistake
/ 3 reasons / before-after / provocative question) with a reacting FACE + a DELIBERATE macro detail; a
BENEFIT shown mid-use (never at-rest on a shelf/in a bag); a PROOF beat that uses real social proof only
if grounded (never fabricated); a CTA whose on-screen caption and voiceover say the SAME written action
line; the per-`veo_prompt` formula with explicit CAMERA (macro/tracking/hand-held/dolly-in) + LIGHTING
(soft natural/diffused/high-contrast); a BANNED-WORDS list; and a SELF/LOGIC-CHECK for impossible
physics. The draft was then ADVERSARIALLY AUDITED by a 3-critic + synthesis workflow (coverage /
contradiction / craft) against the owner's spec → 10 findings, all applied: chief among them the
close-up-vs-"nothing cropped" contradiction (now: intentional macro on the hook, product WHOLE on the
establishing/CTA scenes), the at-rest-context collision, the "CTA verbatim from brand" vs "invent no
facts" bind (CTA is written COPY, only its claims are grounded), and product-identity-stays-while-pose-
changes. TWO findings ("Shop Now button", "render the logo") were applied Veo-SAFELY — on-screen text
and the brand logo are composited in POST (caption overlay + auto end-card), so the footage renders
NEITHER (Veo would garble fake UI/wordmarks). A re-verify pass confirmed all 10 resolved + caught one
residual (non-featured image_index still said "a DIFFERENT photo per scene" → reworded to "a different
SHOT/angle of the ONE hero"). +5 tests. Fully live on the FEATURED single-product reel (see below); a
final proof render on the upgraded director is pending owner go.

**Single-product REEL — one item, realistic, framed-for-9:16 (2026-07-06):** owner: "أنا هعملّ ريل
على منتج بعينه مش ميت منتج ... يكون منطقي مش يضغط والغطا مقفول ... الصورة كأنها مقصوصة ... صمم الفريم
في الأصل على ريل". Three faults fixed together in the creative path (studio picks the product; §5.G):
**(1) ONE product, not a montage** — `reel/__main__.py` `--product-image` is now EXCLUSIVE (`selected =
[img]`, was "picked + 2 supporting"), and `featured_product` threads picked-name → `render_creative_reel`
→ `design_creative_reel` → `_system_prompt(featured=…)`, so EVERY scene is the SAME product (real photo
index 0), varied only by SHOT/ACTION (macro → in-hand → in-use → result), never a different item.
**(2) REALISM** — `_MOTION_TAIL` now forbids impossible actions: "if the product has a cap/lid/pump/
dropper, the person REMOVES or FLIPS it BEFORE dispensing … NEVER pressing a sealed pump / pouring from
a closed bottle" (owner: "مش يضغط والغطا مقفول"). **(3) FRAMING** — a FRAMING block makes the director
compose vertical-first for the 1080x1920 frame ("product FULLY visible … NEVER a wide/landscape shot
that gets cropped … NOTHING is cut off"), and when a product is featured the seed defaults to
`REEL_SEED_FILL=pad` — CONTAIN the whole photo over its OWN background colour (`_edge_bg_color` samples
the four border strips), so a studio product shot pads SEAMLESSLY (no crop AND no band). VERIFIED live
(Hair Growth, Veo 3.1): 1080x1920, 21s + branded end-card, all 5 scenes the SAME product, a real model
spraying the mist onto glossy hair (physically-logical use, not a static zoom), ad-arc HOOK→use→result→
CTA "Get The Raw Experience". First pass used the earlier `blur` seed and left a faint band on the two
seed-hugging scenes → switched the default to the seamless `pad` seed (`_to_vertical_seed` gained a
'pad' mode; +3 seed tests). Re-render on `pad` pending owner go.

**Crawl reaches INDIVIDUAL products, not just categories (2026-07-06):** owner: azza/rawafrican came
out with category-level offerings only (RINGS/EARRINGS; Face/Hair Care). A multi-agent analysis found
the crawl discovers 200+ pages but fetches ~12-30, AND (worse) `/collections/rings` and
`/products/<slug>` classify IDENTICALLY (PageType.PRODUCTS), so the stable frontier sort let
collections (discovered first) fill every slot while individual product pages sat in the discarded
tail. Slice 3: `page_type.is_product_detail(url)` distinguishes a PDP (`/products|/product|/pdp/<slug>`)
from a list — a pure boolean, no PageType enum change (zero ripple). Slice 4:
`crawler._reserve_product_quota` gives ~65% of the frontier to individual products, DIVERSIFIED across
parent collections (`_diversify_by_parent` round-robin), interleaved with the top landing/collection
pages — so the SAME page budget now captures a spread of real products (name/image/price) that the
product picker + reel need, item-by-item. Non-store frontiers are unchanged. +3 tests. **Slice 5
(DONE): store cap 30 -> 50 + budget 330 -> 560s** (config.py) + `MAX_SITEMAP_URLS` 200 -> 300 — the
crawl goes DEEPER, and because the frontier reserves the budget for product-DETAIL pages, the extra
slots buy ~50 real PRODUCTS not more collections (store scrape ~8-9 min). Remaining follow-ups: Slice 1
cut per-page overhead on light sub-pages (the ~11s/page floor is hard-coded homepage-footer waits, not
page weight) to bring the time back down; Slice 2 a per-page wall-clock timeout for safety.

**Reel/poster coherence — content_images are now PRODUCTS, not shop-fronts (2026-07-06, root cause):**
the engineer: "I can't tell what the reel advertises." A multi-agent analysis found the true cause in
ONE line — `business_profile/rules/from_visual.py::_content_images` sorted role=content images by file
extension (`.jpg/.webp` first, `.png` last), so a brand whose STORE-LOCATION photos are webp/jpg (Mall
of Arabia, City Stars, pharmacy) and whose real PRODUCT mockups are `.png` got content_images = 8-9
shop-front photos. The reel animated kiosks while the grounded voice-over sold beauty products =
incoherent. Fixed by `_content_rank`: demote location/stockist/banner tokens (mall/pharmacy/branch/
kiosk/banner/…), promote real products (from `/products|/collections` URLs or a product alt), stable
sort. MEASURED: rawafrican content_images flipped from 8-9 store photos → all 12 real products (Floral
Blast Hair Mist, Follicle Booster Oil, Face Cleanser…). One-file fix, benefits reel default + `--real`
+ poster. +3 tests. (Reel-coherence slices since DONE: the picked product NAME+details now thread into
the creative director as a single-product ad-arc system prompt — see the single-product REEL entry at
the top. Remaining: an identifying caption + the product on the end-card.)

**Studio product-picker (2026-07-06, engineer suggestion #1):** the studio now lets the user CHOOSE
which product to advertise, grounded in the RAW scrape. `dashboard/products.py::products_for_slug`
extracts pickable products from the freshest manifest (names from real `/collections|/products/<slug>`
pages, images from role-tagged images_of_interest; banners/logos excluded) — NOT the profile's
`content_images` (which for rawafrican were store-location photos). `GET /products?slug=` feeds a
thumbnail picker in the Creative Studio; the chosen product flows `pname/pimg` → `_stream_generate`
→ `generate_poster/reel(product_name, product_image)` → `--product-name`/`--product-image` on both
CLIs: the poster makes it the HERO offering, the reel uses its real image as the footage. Whole-brand
stays the default. Also FIXED: `append_endcard_to_reel` (branded logo end-card) was defined but NEVER
called by either reel path — every reel shipped with no logo; now wired in (owner: "الريل مفيهوش
لوجو"). MEASURED: rawafrican → 6 real products (Hair Growth, Face/Lip/Nail Care, …); reel end-card
verified (real logo on brand green). Live product-featured OUTPUT verification pending (paid run).

**Active work — adversarial audit (2026-07-05):** a deep verified audit found 1 CRITICAL
+ 8 HIGH + a medium/low tail + finder-flagged leads (full list in the session +
`memory/audit-2026-07-05-findings`). Fixed in priority order, one at a time, suite green
between fixes. **C2 (2nd CRITICAL, from a finder lead): the Evidence Ledger falsely
'verified' fabricated numbers** — bare-digit resolution certified "Save 50%" against
"50 years" in the client-facing Compliance Sheet (and passed it through every gate). Fixed
with unit-class resolution (§5.C + §8). Residual: bare-number-vs-bare-number and superlative
subject context are still context-blind (documented follow-up). **ALL CRITICAL+HIGH DONE
(suite 880 → 926):** C1 (phone-region fabrication), H1 (mid-crawl e-commerce budget
re-evaluation), H2 (redirect/duplicate-final dedup), H3 (frontier scheme/slash dedup),
H4 (social-domain exact/subdomain match, no substring), H5 (multilingual CTA verbs),
H6 (poster external-headline grounding gate), H7 (`--trend` now feeds the concept copy),
H8 (salvaged-homepage readiness) — details in §5 + §8. **Medium/low tail — landed:** cookie
false-drop (banner-specific patterns, not `\bcookie`), RSC/code-payload gate, validator
wrong-block_id quote-recovery + word-boundary blacklist, strategy variety + Arabic filler +
even day-distribution, trends stopwords/numeric filter, campaign dispatcher robustness, and
**grounding reputable-web now enforced at ledger-BUILD** (`is_reputable_web_source` in
`from_profile.add` — a web claim sourced only by an aggregator/junk host is dropped for
EVERY gate, not just `pick_angle`; poster keeps a thin alias). **Remaining tail:** the
finder-flagged leads needing re-verification and the un-audited logo/palette pipeline.

---

## 1. Vision (from the concept PDF)

From **one business URL**, produce a complete, on-brand marketing campaign pack —
**poster, short-form reel, competitor-grounded SWOT, multi-day content calendar** —
governed by one non-negotiable principle:

> **Provable zero-hallucination.** Every factual claim in any customer-facing output
> traces to a real, sourced piece of evidence — exportable as an audit trail
> (the Ad Compliance Sheet). Not "AI that makes posters": AI that produces
> agency-grade creative you can PROVE is brand-safe.

**The two truth domains** (the core architectural idea):
- **FACTS** (offers, figures, claims) — extracted deterministically + validated;
  the LLM is an extraction/verification tool, never a source of truth.
- **DESIGN** (words' form, image, layout, story) — creative and free, but bounded by
  the brand's identity and by the verified evidence.

**Universality rule:** works for any vertical, any language, any country. Never add
vertical-specific hacks; category-specific *guidance* selected by a universal signal
is fine (e.g. the restaurant gate, the marketplace offering rules).

The PDF also promises an **Intelligent Media Buying** layer (objective deduction,
competitor live-ads intelligence via the Meta Ad Library API, kill-switch campaign
governance) and enterprise layers (Compliance Sheet ✅ built, A/B/C/D testing packs).
Their status is tracked honestly in §6 (roadmap) — Section-04 media buying is NOT
built yet.

---

## 2. Standing working rules (do NOT violate)

1. **One fix at a time. Measure between fixes.** Never bundle — it obscures causality.
2. **Patterns over single observations.** Measure with numbers before changing the
   scraper or any extraction behavior.
3. No new `BusinessProfile` fields without benchmark evidence.
4. **UNKNOWN is honest.** Never infer a value to make output look complete. A wrong
   competitor/logo/claim is worse than an empty slot.
5. Keep the scraper universal — no vertical-specific logic.
6. Don't build architecture before knowing real failure modes (benchmark first).
7. Be honest about limitations; never over-claim (in code, docs, or to clients).
8. **Keep this file updated** as part of every landed change — feature state, gaps,
   and measured lessons. (This file replaced the old per-change log; keep entries
   CONDENSED here: state + the lesson, not the full narrative.)
9. Every bug the owner catches becomes a **gate + a test**. The gates are the product.

---

## 3. Runtime & operations

- Windows, Anaconda, Python 3.11 — conda env `marketing_scraper`.
  - This machine (Admin): repo at `C:\Users\Admin\Desktop\dev\scraper_v01`,
    env at `C:\Users\Admin\.conda\envs\marketing_scraper`.
  - Other machine (asmaa): repo is the Google-Drive copy (slow — scope searches).
- **Keys (.env at repo root):** `GOOGLE_CLOUD_PROJECT=image-498715` + gcloud ADC
  (Gemini / Imagen / Veo — the ~$300 GCP credit pool), `GOOGLE_MAPS_API_KEY` (Places),
  `SERPER_API_KEY` (SERP; free tier is flaky under bursts),
  `OPENAI_API_KEY` (legacy fallback only). `.env` is gitignored.
- **Model map (deliberate, measured; owner directive 2026-07-07 — ALL text/reasoning on Gemini,
  NO Anthropic):** extraction = Gemini 2.5 **Flash** (evidence-bounded — Pro adds cost, not
  quality); concept/design/research/story + **reel director + domain-schema + review-themes** =
  Gemini 2.5 **Pro** (`default_caller(strong=True)` — the last three migrated OFF Anthropic/Opus
  this day, see log); one-shot poster image =
  `ONESHOT_IMAGE_MODEL=gemini-3-pro-image-preview` (measured quality ceiling;
  3.1-flash passes but follows palette/layout mandates worse); judges/OCR/planner =
  Flash; reel video = **Veo 3.1** (`veo-3.1-generate-001`, Vertex; renders NATIVE
  speech, highest Veo 3); classic poster image = **Imagen 4 Ultra**
  (`imagen-4.0-ultra-generate-001`); image edit/upscale = imagen-3.0-capability-001 /
  imagen-3.0-generate-002 (no Imagen-4 edit variant confirmed yet).
  **Probe model availability before assuming** (documented 404 lessons).
- **Run:**
  - API: `python -m uvicorn api.main:app --port 8000` — **restart after ANY code
    change** (`--reload` misses new files on Windows; stale servers produced
    "already fixed" bugs twice).
  - Frontend: `cd frontend && npm run dev` (:3000). **Never `npm audit fix --force`**
    (downgrades Next 15 and breaks the App Router).
  - CLI: `python -m scraper <url>` · `python -m business_profile <manifest> -o p.json`
    · `python -m competitor.full_run <url>` · `python -m poster <profile>` ·
    `python -m reel <profile>` · `python -m strategy <profile>` ·
    `python -m campaign <profile> --from-plan plan.json` · `python -m trends "<kws>"`.
- Tests: `python -m pytest tests/ -q` — hermetic (no network/LLM); live paths are
  verified manually and cost money (out-of-CI by policy).

---

## 4. Architecture

```
URL
 └─ scraper (Playwright crawl, universal)          → ScrapeManifest
     └─ business_profile (rules + Gemini + RAG + validator) → BusinessProfile (all fields evidence-cited)
         ├─ competitor (router → Places/SERP → matrix → SWOT → TOWS)
         ├─ grounding  (Evidence Ledger — the central gate + audit + Compliance Sheet)
         ├─ brand      (BrandCreativeDNA / BrandBook — vision understanding of identity)
         ├─ poster     (concept → variation → one-shot OR classic render → QA gates)
         ├─ reel       (story → Imagen stills → Veo i2v + native VO → captions → end-card)
         ├─ strategy   (content calendar) → campaign (fan-out to creatives)
         └─ trends     (free keyless trend sources)
api/ (FastAPI, :8000) + frontend/ (Next 15, :3000)
```

Packages: `scraper/`, `business_profile/`, `grounding/`, `competitor/`, `brand/`,
`poster/`, `reel/`, `strategy/`, `campaign/`, `trends/`, `api/`, `frontend/`,
`benchmark/` (measurement harnesses), `tests/`.

---

## 5. Feature inventory — BUILT ✅ (by subsystem, with the load-bearing details)

### 5.A Scraper (universal ingestion)
- **Tolerates invalid TLS certs (2026-07-06)**: `make_browser_context` sets
  `ignore_https_errors=True`. A real brand site with an expired/misconfigured cert
  (`net::ERR_CERT_AUTHORITY_INVALID`, MEASURED: marasimltd.com) made Chromium refuse it → the whole
  scrape returned 0 pages in ~23s ("finished in 23 seconds and produced nothing"). We only READ
  public marketing content (no credentials submitted), so a cert-authority error must not block the
  crawl. (Common on MENA small-business sites.) +1 test. NOTE: a site returning HTTP 403 (active
  bot-block / IP rate-limit after repeated runs) is a SEPARATE, transient condition — not fixed by
  this and not evaded (out of bounds); the realistic Chrome-131 UA is already set.
- **Adaptive crawl budget**: default 12 pages/150s; e-commerce detected by product-URL
  density (incl. `/plp/`,`/pdp/`) → 30 pages/330s. Light sub-page fetches (block
  images/fonts/media, skip screenshots) ≈20% faster.
  - **H1 mid-crawl re-evaluation**: the store signal is no longer a one-shot read of the
    homepage snapshot (a JS store can under-render it — MEASURED: nahdi flipped 6-vs-24
    pages on the same store, same morning, with a dead sitemap). The frontier is built at
    the ecommerce max cap and the signal is re-run over ACCUMULATED links after each
    subpage; crossing the product-URL threshold upgrades page-cap + time budget once
    (`E-commerce detected mid-crawl` note). Offline-validated: nahdi's failing 6-page run
    had 15 product URLs across the pages it did fetch. ⚠ live re-scrape confirmation of
    the end-to-end page-count recovery is still pending (a paid run).
- **Never homepage-only**: `MIN_SUBPAGE_ATTEMPTS=5` — the budget guard stops the tail,
  never prevents the start (nahdi lesson: dead sitemaps ate the whole budget → 1-page
  scrape of a 300-link store → "no prices").
- **Sitemap stage guards**: per-fetch 5s timeout + consecutive-unreachable breaker
  (2 fails → stop) + 20s stage cap.
- **Deep-seed re-anchor**: a deep content URL re-anchors identity to the site root
  (locale homepages like `/ar-sa` are respected, not stripped).
- **eTLD+1 everywhere** (bundled PSL): subdomain-heavy enterprises (web./eshop.)
  crawl correctly; `.com.eg`-class hosts don't collapse into one key.
- **Links**: per-page dedup by `(href, page, anchor)` — a CTA sharing a nav href is
  never swallowed (daturial "Book a Meeting" lesson; measured corpus-wide, zero junk).
  CTA verbs cover ecommerce/restaurant/Arabic + `explore/استكشف`, and **(H5)** the major
  expansion-market languages (FR/ES/DE/IT/PT/TR) — a French store scored 0 CTAs on 310
  links before; MEASURED +CTAs only on the French site, zero EN/AR inflation (Italian
  "registrati" dropped: it prefixes English "Registration"). CTA detection is still
  verb-list-bound (a truly language-agnostic structural/LLM detector is a follow-up).
  **(H4)** social-platform classification matches a host EXACTLY or as a subdomain, never
  as a substring — the old `dom in host` mislabeled xerox/box/netflix/fedex.com (contain
  `x.com`) as twitter and could strip a subject's own links from the frontier.
- **Frontier dedup (H2/H3 gates)**: the crawl budget is the binding constraint on ~45%
  of scrapes, so a page fetched twice is a real page lost. **H3** collapses http/https +
  trailing-slash variants PRE-fetch via a scheme/slash-insensitive `url_utils.dedup_key`
  (the fetch url stays the faithful `normalize_url` form — forcing https would break
  http-only sites). **H2** dedups POST-fetch on the normalized FINAL (post-redirect) url
  (`_register_fetched_or_skip`), catching different URLs that redirect to one page
  (WordPress internals, empty collections). MEASURED: 9/110 manifests had duplicate
  pages (daturial fetched its homepage 5×; orange.eg http+https+slash) = 22 wasted
  fetches now skipped, with a `redirect_duplicate_skipped` note.
- **Visual identity**: brand palette from **logo pixels + header + footer** (owner's
  rule — page screenshots alone vote photo colors; measured: Orange #000000→#fc6c0c).
  Logo pipeline: host-stripped keyword/brand matching (a CDN named after the brand
  granted 432 promo tiles `brand_name_match` — nahdi lesson), "shop by brands"
  listing penalty, footer-logo rescue (signature = logo_keyword + brand_name_match +
  score≥45), structural-independent floor (a UNIQUE, hosted, site-wide-repeated content-only
  mark for opaque-DOM SaaS-builder sites — §6 elkbabgi), inline-SVG rasterization with
  visibility gate + dark-chip for white logos, contact-icon penalty, raster-over-wordmark
  preference.
- **Content images**: real photos from `<img>`/lazy/CSS-bg collected (`CONTENT` role);
  homepage full screenshot saved + surfaced on the profile
  (`visual.homepage_screenshot_path`).
- **RSC/JS-payload gate** (`content_quality._looks_like_code_payload`): a Next.js React
  Server Components 'flight' payload (`1:"$Sreact.fragment" 2:I[56700,[],"default"]…`) is
  NOT prose. trafilatura's density gate passed it, so pages with ZERO real text blocks were
  flagged `has_meaningful_content=True` and their junk stored as `cleaned_main_text` —
  inflating the pages-with-text diagnostic and feeding the readiness/pricing signals
  (MEASURED: 31 nahdi product pages; detector flags exactly those, 0 false positives on the
  other 514 meaningful pages). Now returns `(None, False)`.
- **Phone region (C1 gate)**: a bare national number (no `+`) is parsed against the
  SITE's own region from a RELIABLE universal signal — URL locale path (`/ar-sa`→SA) then
  ccTLD (`.com.eg`→EG) via `url_utils.region_from_site` — before falling back to the
  home market (`_HOME_MARKET_REGION=EG`). A blanket "EG" had turned the Saudi hotline
  920024673 into a valid-looking WRONG +20920024673 on the flagship demo. `html lang` is
  deliberately NOT a signal (`en-US` boilerplate mislabels EG sites). Measured: nahdi ×3
  → +966…, zero corpus regression on the tel: path.
- **Resilience**: real-UA + transient retry, HTTP/2 fallback, robots respected,
  malformed hrefs never crash, Deep-Search fallback (Serper `site:` recovery) for
  blocked sites, `scraper/net.py` breaker+retry for keyless JSON fetchers.
- **Screenshot failure is non-fatal**: the full-page screenshot runs AFTER html/text/links
  are captured, so its failure must not discard the page. It no longer sets an error_code
  (`_capture_screenshots` flags `screenshot_failed` + a manifest note); visual identity
  degrades gracefully to logo pixels + header/footer colors. MEASURED: azzafahmy.com was
  getting 0 pages (all text/offerings lost) on a SCREENSHOT_FAILED — the same class as H8.
- **Readiness (H8)**: a timeout-SALVAGED homepage that carries real content counts as a
  homepage — `compute_readiness` gates on content (`_homepage_is_usable`), not `not
  p.failures`. The salvage attaches a benign TIMEOUT failure; the old test discarded the
  whole scrape (MEASURED: nahdi 226 blocks + te.eg 87 blocks were marked not-ready). A
  hard-failed homepage never becomes a HOMEPAGE record (early return), so requiring
  content still correctly rejects an empty salvage (buffaloburger 0 blocks).
- **SSRF guard** (`is_safe_public_url`) at API entry + every remote image fetch.

### 5.B Business Profile (grounded extraction)
- Rules layer (deterministic) + 4 grouped Gemini Flash calls; **every field carries
  verbatim evidence** (`block_id` + `page_url` + quote); the validator rejects any
  citation not literally present (quote-glyph folding for Arabic).
- **Validator quote-recovery**: a citation whose `block_id` is wrong (or points at the
  wrong block) but whose quote is VERBATIM in another real block is RECOVERED with the
  corrected block_id, not discarded — a mis-attribution used to throw away real, citable
  evidence. Same substring strictness across all blocks + a length floor so a stop-word
  can't be laundered; a quote in NO block is still rejected (fabrication). (Real recovery
  rate needs a live extraction run — rejected citations aren't persisted to measure offline.)
- **Evidence-pack cookie filter is banner-specific** (2026-07-05): the boilerplate filter's
  `\bcookie` pattern deleted the FOOD "cookie" as if it were a consent banner — losing real
  dessert menu blocks from the pack (MEASURED: 186 blocks over 8 sites — buffaloburger "Cookie
  Dough"/"SPOON ME COOKIE", mcdonalds "Chocolate Chip Cookie"). Now only cookie-CONSENT
  phrasing (accept/reject/manage/use/policy/settings/…) is boilerplate; the food is kept.
- **RAG (Pillar 2)**: full uncapped evidence pack + per-group semantic retrieval
  (Gemini embeddings, cosine top-K in-process — **no vector DB by design** at this
  scale); validator sees the full pack. Measured: te.eg 153→265 blocks seen.
- **Offerings**: category-aware cap (30 default / 12 ecommerce); **marketplace HARD
  RULES** (departments-first from the store's own nav evidence; pack-size/count names
  are SKUs → family once; breadth over depth, one family ≤2 entries) — measured:
  nahdi flipped from "22 coffee-dominated SKUs" to 24 pharmacy departments.
  ⚠ Guidance is keyed by the **rules** category which can be None → the hard rules
  ALSO live in `_generic` as a conditional block (the fix that actually landed).
- **Unsubstantiated-claim blacklist is WORD-BOUNDARY** (2026-07-05): the tokens (halal/iso/
  organic/certified/…) are matched with `\b…\b`, not raw substring. Substring both
  false-rejected real offerings ("iso" inside poison/comparison, "healthy" inside unhealthy)
  and LAUNDERED a claim through a quote that merely contained the substring ("comparison"
  grounding "iso"). Multi-word/hyphenated tokens ("gluten-free", "fda approved") still match.
- Name chrome stripping at the source (Website/E-Shop/الموقع الرسمي), restaurant-gate
  (2+ identity tokens or a menu page), `other_unique_insights` catch-all (consumed by
  SWOT strengths + poster headline pool).
- **Brand name = the BRAND, not a marketing sentence (2026-07-06)**: og:site_name / `<title>`
  is often "Brand - Tagline" or "Brand | categories"; taking it verbatim made the NAME a whole
  sentence (MEASURED: rawafrican.net = "Raw African's Beauty Hub - Get the Raw Experience!"). The
  name is the identity anchor on the poster/reel/dashboard/SWOT, so `_pick_brand_segment` now
  splits on separators and keeps the segment that ECHOES the DOMAIN (rawafrican -> "Raw African"),
  falling back to first-segment (og, brand-first) / shortest (title, legacy). A possessive
  descriptor is trimmed only when a real descriptor trails it AND the head reconstructs the domain
  exactly, so possessive brands that END at "'s" (Levi's, McDonald's) and qualifiers ("Orange
  Egypt") are never touched. Verbatim source stays the cited quote. (`from_metadata.py`; +2 tests.)

### 5.C Evidence Ledger — the moat (`grounding/`)
- **Claim extraction** (AR-aware normalize; tashkeel/alef/ya/digits): significant
  numbers/years, superlatives incl. the `-est` class + `#1/no.1`, first/only,
  credentials **split** award/certification/guarantee (evidence of one never sources
  another), free-offers. Canary test suite guards coverage regressions.
- **Number resolution is unit-class-aware (C2)**: a %/price/duration/scale number is
  'verified' only by evidence carrying the SAME kind of number. The old bare-digit match
  certified fabrications — "Save 50%" resolved against "50 years", "99% pure" against a
  "99 EGP" price — producing FALSE 'verified' rows in the client-facing Compliance Sheet
  AND passing the poster/reel/strategy/TOWS gates. Safe by default: only a positively-
  identified strong unit tightens, so no legitimate claim regressed.
- **Subject-context judge (C2 residual)**: the remaining hole was bare-number-vs-bare-number
  ("100 gifts" ← "100 stores") and superlative SUBJECT ("best coffee" ← "best regards"). A
  token/stem heuristic can't fix this — it can't tell an acceptable synonym/inflection
  ("5000 experts"↔"5000 youth", رقمي↔رقميون) from a fabrication (both are token-disjoint;
  MEASURED: a heuristic broke 4 legit tests). So the ledger takes an OPTIONAL `subject_judge`
  (`grounding.make_subject_judge(caller)`): the deterministic scaffold detects the ambiguous
  token-disjoint case, a CHEAP Flash call decides same-subject (cached, fires only on
  ambiguous claims), and any error/absence → LENIENT (the project's number-only grounding),
  so nothing regresses without a judge. Wired into the poster concept gate. Mechanism is
  hermetically tested (13 tests, mock judge + mock caller). **Live ACCURACY VERIFIED
  2026-07-05** (owner-authorized Flash run, `grounding/measure_subject_judge.py`, 45 labeled
  bilingual EN+AR pairs): **98% (44/45); 25/25 fabrications CAUGHT (0 missed — incl. subtle
  adversarial ones: years-vs-awards, Michelin-stars-vs-locations, purity%-vs-count); 19/20 real
  claims kept** (the 1 error is a SAFE over-block on a borderline AR "largest pharmacy" vs
  "biggest pharmacy chain"); 0 abstentions. The lenient-when-unsure bias did NOT wave through any
  fabrication — the moat's strict mode holds. Re-run: `python -m grounding.measure_subject_judge`.
- **Source tiers**: brand site > web snippet (web must be reputable; media outlets are
  reputable EVIDENCE but are NOT competitors — two separate host-lists). The reputability
  check runs at ledger-BUILD (`is_reputable_web_source` in `from_profile.add`), so a claim
  sourced only by an aggregator/junk host is unsourced for EVERY gate — not just
  `pick_angle`, which was the only enforcer before. `poster._is_reputable_source` is now a
  thin alias delegating to the shared `grounding` predicate (single source of policy).
- Language-aware resolution (Arabic claim cites Arabic quote; mismatch labeled).
- **Blocking gates live on**: poster concept copy (soften/drop/regenerate + remediation
  log), research angles (`pick_angle`), calendar hooks/angles (blank-to-topic), reel
  captions + voiceover lines + hook (drop-to-grounded).
- **Per-asset audit trail** sidecars (`.audit.json`) + **Ad Compliance Sheet**
  (`grounding/compliance.py`): claim → verified(source URL+quote) / softened / dropped
  / no-checkable-claims; verdict PASS/NEEDS_REVIEW; shown & downloadable in the web UI
  and written as `.compliance.json`. Coverage block states honestly what is NOT yet
  gated (see §6).

### 5.D Competitor intelligence & SWOT
- **Router**: LOCAL→Places · ECOMMERCE→SERP · HYBRID→both · UNKNOWN→SERP (never skip).
- **Places path**: adaptive peer discovery + reviews; **subject's own listing** found
  by domain / exact name / cross-script brand-unanimity (Arabic listings for a Latin
  profile name) → fills the rating/volume THREAT quadrant.
- **SERP path (globally robust — the layered design)**:
  1. **LLM query planner** (Flash, ~$0.001): writes the query a real customer would
     type — right language + market (market inferred from any evidence incl. URL
     locale `/ar-sa`); brand-name leakage dropped. (Deterministic identity-terms +
     market-hint remain as the no-caller fallback.)
  2. Aggregator/marketplace/directory denylist + media/listicle host list.
  3. Relevance gate (identity-term overlap on title/snippet/URL).
  4. **Reject-only LLM judge** ("when unsure, DROP — a wrong competitor is worse than
     a missing one"); judge failure keeps all (never breaks discovery). Its keep-list is
     REQUIRED (2026-07-05) so an OMITTED judgment can't be read as pydantic's default `[]`
     and silently drop every real peer; an explicit `[]` is still a genuine "keep none",
     and a drop-all is logged for traceability.
  Measured: nahdi 0 → 4 real Saudi pharmacies; El Ezaby math-test peers eliminated.
- **Lite peer scrape** on the sync web path: plain-HTTP homepage → cheap comparable
  dims (social/CTA/WhatsApp/booking); unknown stays UNKNOWN. A JS-only shell (no anchors in
  the server HTML) now returns None (UNKNOWN) instead of "0 social / no WhatsApp" — that
  absence is JS-hidden, and evidence-of-absence must not read as knowledge (rule 4). A REAL
  page with genuine zeros still reports them.
- **SWOT**: mechanical synthesis from the gap matrix (every item cited);
  `claim_strength` ladder (validated / directional_not_validated /
  internally_supported); own-site S/W floor in competitive mode; review-theme
  complaints → Threats, unmet needs → Opportunities; standalone degrade never empty.
- **Brand-level Strengths (2026-07-06, SWOT-quality Slice 1)**: owner called the SWOT "stupid —
  all page attributes, not brand-level" (Strengths read "Number of CTAs: 3", "Social links: 20"
  for a heritage jewelry house). The rich profile was thrown away — `value_propositions` /
  `domain_schema` appeared ZERO times in swot.py/matrix.py; only offering/trust COUNTS leaked in.
  Now `competitor/brand_signals.py::strengths_from_profile(profile, ledger)` mines the grounded
  profile — distinctive `value_propositions` + proof-carrying `trust_signals` (generic checkout
  boilerplate filtered) — into Strengths, each **Ledger-gated** (same `all(v.sourced for v in
  ledger.audit_text(text))` gate as TOWS/strategy; fail-closed) and cited to its profile field,
  `internally_supported`. `synthesize_swot(..., profile=None)` gains the param and **prepends**
  them so the SWOT LEADS with brand strategy; the mechanical gaps stay as a secondary signal.
  `profile=None` is byte-identical (regression-safe); added after the standalone check so it can't
  suppress the 0-peer fallback. MEASURED: Azza Fahmy Strengths 4 page-attribute → 9 (5 brand-level
  first: heritage, 18kt gold, craftsmanship, service, shipping); Raw African +7 (incl. "Loved by
  17k+ Customers", "Cruelty-Free"). Universal (jewelry + skincare, no vertical hacks). Wired at
  full_run.py + api/routes/swot.py. +4 hermetic tests.
- **Strategist phrasing (2026-07-06, SWOT-quality Slice 2)**: the mechanical own-site lines still
  read as a raw attribute dump ("Number of CTAs: 3", "Online booking: not detected on site").
  `brand_signals.phrase_dimension(dim_key,label,kind,value,positive)` re-phrases the SAME
  scrape-grounded cell into strategist language ("Clear conversion paths (3 calls-to-action)",
  "No WhatsApp contact channel", "Single-language site (missing Arabic/English)") — TEXT only,
  citation/evidence/claim_strength unchanged — in `_standalone_from_subject`. Keyed off dim.key
  with a clean label fallback (a new dimension never regresses to a dump). +2 tests (2 legacy
  standalone assertions updated).
- **Trend-driven Opportunities/Threats (2026-07-06, SWOT-quality Slice 3)**: the SWOT had no
  market-shift signal — an online brand with no Places peers and no reachable reviews got EMPTY
  O/T. `brand_signals.opportunities_threats_from_trends(profile, trends)` maps on-topic
  `TrendItem`s to Opportunities ("Rising interest in {terms}: {title}") or Threats (decline/
  regulation/ban keywords → "Category headwind on {terms}: {title}"), each citing the trend URL,
  `directional_not_validated`. GROUNDED: junk/aggregator hosts dropped via `is_reputable_web_source`
  then every line **Ledger-gated** (candidates indexed as web evidence, `from_profile(..., swot=)`).
  `synthesize_swot(..., trends=None)` gains the param and PREPENDS them (market signals lead);
  `None`/`[]` is byte-identical (regression-safe). Callers (`full_run.py`, `api/routes/swot.py`)
  fetch `top_trends(keywords_from_profile(profile), require_match=True, top_k=6)` best-effort
  (any failure → [], never blocks). MEASURED: Raw African O/T 0/0 → 2 opps + 1 threat from
  synthetic trends (regulation → threat; junk host + off-topic dropped). +3 hermetic tests.
- **Readiness-gap Weaknesses (2026-07-06, SWOT-quality Slice 4 — DONE, all 4 slices shipped)**:
  `brand_signals.weaknesses_from_readiness(profile, ledger)` adds brand-foundation Weaknesses from
  the grounded `readiness.swot_quality_signals`, but ONLY for a WHITELISTED signal explicitly False
  (tagline / value_propositions_3plus / trust_signals_2plus / offerings_3plus / pricing_posture_known
  / multi_page_evidence). `locations_with_geo` / `hours_known` are deliberately EXCLUDED — False for
  every online brand and not a weakness there (rule #4: never a weakness for something irrelevant).
  Phrased without numbers (pure paraphrase → passes the gate), cites the readiness audit,
  `internally_supported`, APPENDED after the concrete weaknesses (`_append_unique`). MEASURED:
  well-scraped Azza/Raw African add 0 (correct); a thin profile surfaces 3 real gaps (no tagline,
  thin value prop, single-page) and NOT hours/geo. +3 hermetic tests. **SWOT-quality program
  COMPLETE** — the SWOT now LEADS with brand-level strengths (S1), reads like a strategist (S2),
  has market-shift O/T (S3), and flags real foundation gaps (S4); the mechanical matrix is a
  cited secondary signal, all Ledger-gated. profile=None/trends=None stay byte-identical.
- **TOWS grounding gate fails CLOSED** (2026-07-05): a ledger error on a strategy's text
  now returns not-grounded → the pairing keeps its GROUNDED deterministic template instead
  of shipping the LLM's unverified text (was fail-open — an error let unverified strategy
  copy through the moat; the pairing is kept either way, so failing closed loses only LLM
  phrasing, never a fabricated claim).
- **TOWS layer** (adopted from teammate Rawda's BI platform — HER synthesis thinking,
  OUR grounding; her prompt-only trust model was explicitly not taken): SO/ST/WO/WT
  strategies with anchors validated against real SWOT ids, Ledger-gated text
  (fabrication → grounded template), ranked priority actions + posture. In result.json
  and the web UI. Competitors are LISTED in the UI (name/site/rating/why-selected).

### 5.E Poster Studio
- **One pipeline** (CLI = web): DNA → research → concept → variation → engine → gates.
- **BrandCreativeDNA**: Gemini-Pro vision over the brand's real ads (tiered ownership
  filter: own site/social, or reputable portfolio WITH the brand named) **+ the
  homepage screenshot as always-available identity evidence** (the page AS DESIGNED is
  identity by construction; embedded photos may be stock/supplier — the prompt reads
  design SYSTEM, not photo subjects). Cached per brand in `outputs/brandbooks/`.
- **Concept** (Pro): one campaign idea → copy derived from it. Language lock
  (AR = zero-Latin, validated + regenerated), دليل/proof gate, copy critic,
  **Ledger enforce** (+ live web research facts feed both the prompt AND the ledger so
  researched numbers survive), remediation log.
- **Copy variation engine**: per-run rhetorical FORM (question/statement/command/
  benefit/contrast/number-lead/story) × VOICE (direct/playful/premium/urgent/warm/
  expert) — kills the fixed hook+proof formula; facts stay gated regardless of form.
- **External-headline grounding (H6)**: an override headline — `--headline`, a content
  calendar hook OR **topic** (the calendar `topic` is ungated in the strategy layer), or a
  trend — is verified against the Evidence Ledger before it replaces the gated concept
  headline (`_verified_external_headline`); an unsourced claim falls back to the grounded
  concept headline. Previously a calendar topic like "Egypt's #1 pharmacy" rendered
  verbatim, bypassing the moat.
- **Trend-aware concept (H7)**: `--trend` fetches on-topic trends and threads them into the
  concept prompt as INSPIRATION (`trend_context`), so a trend shapes the copy's angle while
  the grounding gate still blocks any fabricated claim. Was a silent no-op on every
  campaign-dispatched poster (trends were read only inside the `--research and no headline`
  branch).
- **Design variation**: mood/lighting/composition/energy + per-run font pairing.
- **ONE-SHOT engine (default web engine)** — Gemini image model composes the whole
  creative; hard gates:
  - concept scene mandate (ONE idea, execute THIS scene; ≤3 props; calm text zone;
    TEXT CONTRAST non-negotiable),
  - **brand integrity**: logo only as the attached real asset; NEVER paint the brand
    onto objects (painted-brand OCR gate: >2 brand lines → reject),
  - **REAL PRODUCT PROPS**: up to 2 quality-gated real product photos attached; their
    OCR'd real labels become ALLOWED text; invented labels (≥3 junk lines) → reject
    ("real medicine with its real name" — the owner's rule),
  - character-exact OCR **verbatim-copy gate** (Arabic-aware; dialect-preserving OCR),
  - **art-critic vision QA** (clutter/contrast/focal) with retry + best-attempt.
  Falls back to the **classic engine** (adaptive background STYLE-first→outpaint→t2i,
  letterbox trim, 2x upscale, free-form LLM layout + marketing archetypes, lockup
  typography, emerging scrim, adaptive logo plate, brand fonts) — never an error.
- **Language option** (auto/ar/en) end-to-end; audit + compliance sheet on every
  poster; engine + model surfaced in the UI.

### 5.F Reel Studio
- **Pipeline**: brand story (Pro) → Imagen stills (DNA imagery themes, natural skin,
  no colour dye — the "purple people" lesson) → **Veo 3.1 image-to-video** per scene
  (real motion) → xfade assembly → brand end-card → kinetic caption overlay.
- **Video provider = Veo 3.1, the ONLY i2v engine** (`veo-3.1-generate-001` on our Vertex
  project). `default_video_provider()` AUTO-selects our provisioned Veo. **Runway was REMOVED
  2026-07-05** (owner directive): a stale len-132 `RUNWAY_API_KEY` in `.env` had silently
  overridden Veo (the factory preferred Runway whenever the key was present) and Runway returned
  HTTP 400 on every scene → the reel degraded to Ken-Burns stills, no real motion (seen in the
  end-to-end demo; the Veo re-run produced real cinematic motion). The `RunwayProvider` class and
  all `RUNWAY_API_KEY` branches are gone; AIML remains an opt-in Veo-3.1 gateway via
  `REEL_VIDEO_BACKEND=aiml`. NOTE: the leftover `RUNWAY_API_KEY` in `.env` is now inert — safe to
  delete (owner action; `.env` is never touched by code). 6 hermetic precedence tests.
- **Story**: IDENTITY ANCHOR (the world must be unmistakably what the business IS;
  first/last scenes especially; offerings are a SAMPLE = props only — the "pharmacy
  became a coffee ad" lesson) + **diverse offering sample** (dedup by family, from the
  full profile) + description as the world anchor + recurring protagonist +
  DNA themes + fresh Ledger-gated hook (not the verbatim tagline).
- **Native voiceover**: Veo speaks the story's lines — ONE consistent **narrator**
  (gender/age/tone, repeated in every scene clause; lines are one continuous
  narration, grammatical gender locked incl. the hook, neutral viewer address).
  Captions + voiceovers + hook all pass `grounded_captions` (drop-to-grounded).
- **Audio chain correctness** (measured 0ms A/V gap): per-clip `apad=whole_dur`
  (never `apad`+`-shortest` — muxer overshoot), acrossfade mirrors xfade, endcard
  apad, overlay without `-shortest`.
- Kinetic typography overlay (hero/support lockup, RTL-correct via Chromium, pill CTA,
  safe margins), persistent corner logo fading into the end-card, calm pacing
  (~4s shots), image-quality gate on any real photos, Ken-Burns per-scene fallback,
  free edge-tts backend for the CLI voiceover path, language option (auto/ar/en).
- **Caption TYPE is tone-aware (2026-07-05 polish)**: a luxury/fashion/beauty brand
  (`_reel_theme` off tone_of_voice + category) renders the hero in a refined display SERIF
  (Fraunces / Amiri for Arabic), title-case, ivory-warm, italic accent word — instead of the
  chunky Oswald uppercase that read "سخيف" on a jewelry reel. Hero size now caps by the LONGEST
  word so a wide serif word never overflows the frame. Non-elegant brands keep the punchy bold.
- **Voice-over rebuilt (2026-07-05 polish)**: was per-scene (a SEPARATE synth call each scene →
  the voice/energy CHANGED every scene) and hard-trimmed to the scene length (chopped long lines
  mid-word → "بيقطع"). Now ONE continuous script → ONE call → ONE voice/performance, TIME-FIT to
  the footage (gentle `atempo` only if it runs long, else pad — never a mid-word cut). The
  delivery brief is tone-aware and no longer hardcodes an "appetizing food ad" (a vertical leak).
- **End-card redesigned — logo always visible, elegant, no fake button (2026-07-06)**: the brand
  end-card put the LOGO on the brand's OWN primary colour, so a GOLD jewelry logo VANISHED on a
  gold ground (owner: "انت بظت اللوجو"); the name was chunky sans ("الكلام بشع"); and a solid
  white "Catalog" BUTTON sat in a non-interactive video ("الزار دا إيه لازمته"). Now: the
  background is chosen by the LOGO's luminance — a light/gold/white logo gets a DEEP brand-tinted
  charcoal card (the real luxury gold-on-black look), a dark logo gets a brand-tinted IVORY card —
  so the logo ALWAYS has contrast, plus a brand-colour halo behind it. Typography is tone-aware
  (refined **Fraunces/Amiri serif** for luxury via `_reel_theme`, clean Space Grotesk otherwise).
  The fake button is gone: elegant cards show the brand URL as the real "where to go"; non-elegant
  cards use a hairline outlined chip. VISUALLY VERIFIED across 3 archetypes (Azza Fahmy gold→dark,
  As-Salam navy→light, Orange orange→dark). (`endcard.py`; +5 hermetic tests.)
- **Creative-director is tone/vertical-aware (2026-07-06)**: the Opus reel director's system
  prompt hard-coded FOOD dynamics into EVERY reel ("steam/smoke, flames, mouth-watering") — wrong
  for a jewelry house. `_vertical_mode(profile)` now swaps the motion + delivery vocabulary:
  **elegant** (luxury tone OR jewelry/fashion/accessories) directs the product WORN / admired on
  skin, light glancing off metal & stones, refined slow moves, grace — no food dynamics; **food**
  keeps the appetising motion; **generic** stays neutral. Every mode now asks for ONE coherent
  STORY arc (heritage-aware: tell the REAL founding story, never invent) and enforces a
  MINIMAL-TEXT policy — a caption on AT MOST the hook + CTA scene, empty for all others (owner:
  "الكلام مكتوب بشوع… يبقا قليل"). LIVE-VERIFIED on Azza Fahmy (luxury→elegant): a celestial
  heritage story, jewelry "resting on skin / worn on an elegant hand", captions on only 2/5 scenes.
  (`creative_director.py`; +4 hermetic tests.)

### 5.G Strategy, Campaign, Trends
- `strategy/`: N-day content calendar (Gemini or deterministic), hooks/angles
  Ledger-gated (blank-to-topic), `.audit.json` sidecar. CLI-only for now.
  - **Fallback quality (2026-07-05)**: items spread EVENLY over the whole window (was
    front-loaded — a 30-day/17-item plan filled only days 0-16, tail empty); the last-resort
    filler is LANGUAGE-matched (an Arabic brand no longer gets hardcoded English topics that
    render as English poster headlines — the language lock); the real `tagline` is used as
    one more grounded topic; the LLM prompt carries a language directive + "spread evenly".
    (True topical VARIETY on a single-offering brand still needs the LLM path — the
    deterministic degrade cycles format/platform.)
- `campaign/`: calendar → poster/reel jobs fan-out (`--from-plan`, `--dry-run`).
  - **Robustness (2026-07-05)**: `plan_creatives` tolerates a hand-edited/malformed plan
    (non-dict calendar / missing `items` / non-dict item → skipped, no traceback); `run_all`
    catches a per-job launch failure so ONE bad job no longer aborts the whole fan-out
    (records it rc=1, continues); the CLI now returns NON-ZERO when any creative failed (a
    silent exit 0 hid failed renders from automation) and rejects an out-of-range `--only`.
- `trends/`: free keyless sources (HN/Reddit/Dev.to), normalized ranking, profile
  keyword matching; feeds poster `--trend` and strategy `--trends`.
  - **Relevance + resilience (2026-07-05)**: the sources are TECH-skewed, so keyword matching
    dropped bare years/numbers + a broader generic/tech-ambiguous stopword set — a non-tech
    brand no longer false-matches an irrelevant tech trend into its copy (MEASURED: 24/45
    corpus profiles false-matched a sample tech title before, 0/45 after). `fetch_trends`
    now LOGS a down/schema-drifted source instead of silently swallowing it. (Deeper
    relevance — the "apple" ambiguity — still wants an LLM judge or better sources: follow-up.)

### 5.H Web app
- Scrape pipeline with SSE live progress; tabs: Profile / Evidence (citations) /
  SWOT (competitors list, quadrants with strength badges, TOWS, notes incl. discovery
  diagnostics) / Diagnostics / **Poster Studio** (engine toggle, language, versions,
  compliance sheet + download, CTA button) / **Reel Studio** (language, versions,
  mode label, download).
- API: `/api/run` (async job + SSE), `/api/swot/from-profile`,
  `/api/poster/from-profile`, `/api/reel/from-profile`, `/api/health`, `/` hint page.
- **Baseera dashboard** (`dashboard/build.py`, `python -m dashboard <competitor.json> --profile
  --plan --poster --out`): ONE self-contained HTML page (inline CSS + base64 images + system
  fonts, NO external CDN/web-fonts — opens & publishes anywhere) that "prints everything" from a
  run in the Baseera design language — brand + KPIs, the CITED SWOT (citations + claim-strength
  badges on screen = the moat visible), discovered competitors (peer-fit + why-selected), the
  TOWS strategies + posture, the content calendar, and the embedded poster. 3 hermetic tests.
- **One-command demo** (`dashboard/run.py`, `python -m dashboard.run <url> [--fast] [--open]`):
  a URL in, a finished dashboard out — drives the real pipeline as subprocesses
  (competitor.full_run → strategy → poster(one-shot) → dashboard), each stage best-effort with a
  clean stage-by-stage progress log for a LIVE demo. `--fast` skips the poster for speed.
  `run_pipeline(..., on_progress=(event,label,msg)→None)` streams stage boundaries to a live UI.
- **Everything in one dashboard (2026-07-06)**: the dashboard now embeds the **reel** (an inline
  `<video>`, base64 so the page stays self-contained) alongside the poster in the Creative section
  (`build_dashboard(..., reel_path=)`, `python -m dashboard --reel`). `dashboard.run` grew a reel
  stage (Opus-directed, Veo 3.1, best-effort, `--fast` skips it) and the live server's progress
  chips include it, so a full run produces a dashboard showing profile → SWOT → competitors → TOWS
  → calendar → poster → **reel** in one place (owner: "كل حاجة تظهر في الداش بورد"). VISUALLY
  VERIFIED (poster + 9:16 reel player + evidence note render side by side). +2 tests.
- **Local INTERACTIVE studio** (`dashboard/server.py`, `python -m dashboard.server [--port 8770]
  [--no-open]`): the owner wanted to sit IN the dashboard and DRIVE it, not get a one-shot report
  ("عايزة أبقى في قلب الداش بورد… أجنريت صورة… أطلّع الفيديو… أشوف السوات"). Flow: paste a URL →
  **Analyze** streams the FAST core over SSE (`GET /analyze?url=` → scrape/profile/competitors/SWOT
  /calendar, NO heavy generation) → redirect to `GET /studio?slug=`, an interactive page that
  embeds the report (hero/KPIs/cited SWOT/competitors/TOWS/calendar via `build_dashboard_html`,
  standalone=False) PLUS a **Creative Studio** with on-demand **Generate/Regenerate** buttons:
  `GET /generate/poster?slug=` and `/generate/reel?slug=` stream progress over SSE, then the asset
  loads in-panel from `GET /asset?slug=&kind=` (poster PNG / reel MP4, Range-served, slug-guarded).
  `GET /dashboard?slug=` exports the full self-contained dashboard. `run.py` was split into
  `analyze` / `generate_poster` / `generate_reel` / `build_dashboard_file` (composed by the CLI
  `run_pipeline`). Stdlib-only (`http.server` + threads). 8 hermetic tests (pipeline FAKED —
  analyze→studio→generate→asset flow, slug guard, SSE framing). VISUALLY VERIFIED (studio renders
  report + poster/reel panels with Regenerate). The studio **auto-runs** whatever isn't generated
  yet on load (missing poster/reel → auto-start, streaming into its panel; existing assets stay put
  with Regenerate) so they don't sit idle after Analyze (owner: "شغّلهم"). **Light generating state
  (2026-07-06)**: the empty/generating asset stage was a big near-black box (owner: "طلع سواد" while
  the poster/reel were still generating) — now a light blush-tinted placeholder with the brand icon,
  a "Generating your poster/reel…" label + shimmer, switching to a dark frame only once a real asset
  is present; a failed generation shows a light "Couldn't generate — press Regenerate" state, never
  a black void.

---

## 6. Honest gaps & known limitations (current)

- **Logo threshold calibration (RESOLVED 2026-07-05 — structural-independent floor)**:
  elkbabgi.com (= its diplomatic-lark Strikingly subdomain; ONE site captured 8×) has a real
  header logo that scored **54** — `logo_keyword`+raster+`suitable_logo_shape`+`repeated` — and
  missed the 55 gate by 1 because a SaaS site-builder's opaque DOM denies EVERY structural
  signal (in_header/near_nav/links_to_home). Visually confirmed genuine (gold crowned-lion mark).
  Fix: a last-resort `_floor_ok` conjunction in `_choose_primary_logo` (`scraper/extractors/
  visual.py`) — promote a content-only mark ONLY when it's a HOSTED raster (not `data:`),
  logo-named, logo-shaped, repeated site-wide (≥4), penalty-free, AND the UNIQUE such image on
  the page. Threshold/weights UNCHANGED (a conjunction adds no points — cannot regress a passer
  or shift a score). Two guards, both adversarially found & closed: (1) the uniqueness check
  refuses a third-party logo-WALL (press/client/payment tiles named `*-logo.png` — the multi-
  tile ambiguity we can't disambiguate without structure → honest UNKNOWN); (2) the floor runs
  AFTER footer-rescue so a real footer logo is never suppressed. The `data:` guard refuses the
  measured marasim "Client Five logo" placeholder twin (byte-identical reason set to elkbabgi).
  MEASURED (real patched selector, floor on-vs-off over 110 manifests): **+8 gained / -0 lost /
  0 changed** — the elkbabgi cluster and nothing else; re-score harness validated 8080/8080
  candidate scores. 7 hermetic tests in `tests/test_visual_identity_v02.py`. Chosen over a
  threshold-nudge (promotes a bare near-nav wordmark at 54) and a repeated-cap bump (promotes
  the marasim twin) via a design panel + 3 adversarial verifiers. The 16 no-logo corpus sites
  are FAILED/BLOCKED scrapes (bot wall, robots, HTTP error, timeout), not logo-pipeline bugs.
  WATCH-ITEM: the floor rests on the ambient `logo_keyword`; if a HOSTED client-strip FP is ever
  seen, add "client"/"clients"/"our-clients" to `PARTNER_KEYWORDS` (auto-voids via
  `classified_partner_logo`) — NOT by lowering `_FLOOR_MIN_REPEAT`.
- **Palette calibration (INVESTIGATED 2026-07-05 — NOT a current bug; measure-first)**: the
  audit flagged pale-blue primaries on iti.gov.eg (#c5d6e9) and te.eg (#91a8bc) beating the
  real maroon/purple. An OWNER-AUTHORIZED live re-scrape (8 sites; the palette's `color_signals`
  input is NOT persisted, so offline re-score was impossible) proved these DON'T reproduce on
  current code + homepage: te→#54249c purple ✓, orange→#fc6c0c ✓ (stale said #000000), iti
  homepage→#203947 navy (maroon also in-palette) — a defensible two-color brand — and
  vodafone/kfc/mcdonalds unchanged-correct. The stale misses were ARTIFACTS: te = old-code
  staleness (same URL now correct); iti = identity taken from a DEEP track page whose only strong
  signal was a pale button (`button_bg rgb(197,214,233)`, `palette_dominated_by_background`).
  Current crawler already anchors deep seeds to the site root for identity
  (`url_utils.site_root_if_deep`: iti deep→`iti.gov.eg/`), so the homepage drives identity and
  the miss can't recur. NO scoring change made (don't change what measured-correct). Pinned with
  a regression test (`test_saturated_brand_color_beats_a_pale_high_dominance_button`).
- **End-to-end live demo (2026-07-05, eg.azzafahmy.com) — remaining problems surfaced** (the
  pipeline ran all 7 stages; the moat + QA gates behaved honestly; these are the open weaknesses):
  1. **Poster garbled Arabic logo — FIXED (one-shot) + routed 2026-07-05/06.** The image model
     re-draws/bakes the brand logo and garbles Arabic ("عزة فهمي"→"ةفهمص"). **One-shot engine:**
     the prompt now RESERVES a clean corner (draws no logo), the logo is not attached to
     generation, and `poster.pipeline._overlay_real_logo` COMPOSITES the real logo asset there
     deterministically (PIL, opaque plate + soft shadow) — pixel-exact, never garbled. **Classic
     engine (default):** its Imagen STYLE background bakes a garbled logo at an UNPREDICTABLE
     location (measured on Azza Fahmy: bottom-right ~96%h) — trying to "cover" it is unwinnable
     whack-a-mole (it just adds a competing logo), so we do NOT patch the classic path. Instead:
     (a) an ARABIC-copy brand with a logo now auto-prefers the one-shot engine; (b) a new
     `python -m poster --engine oneshot` flag lets ANY brand use it (needed for an Arabic-LOGO
     but English-COPY brand like Azza Fahmy, where the copy-language signal is absent). The
     classic garble that remains is caught by the vision-QA gate (pass=False, flagged not shipped).
     3 hermetic tests (reserve-corner prompt; deterministic composite; unchanged).
  2. **`creative_dna` vision 400 `Provided image is not valid` — FIXED 2026-07-06.** A single
     undecodable brand-ad reference (an SVG, a truncated download, an HTML error page returned as
     bytes) 400'd the WHOLE vision call → the brand lost its DNA signal. Fix:
     `brand.creative_dna._sanitize_vision_images` decodes + re-encodes every reference to a clean
     JPEG the model accepts and DROPS the undecodable, before the call; if none survive, vision is
     skipped honestly (`used_vision=False`). Tests feed valid tiny images now.
  3. **Trends sources tech-centric — FIXED 2026-07-06.** HN/Dev.to/generic-Reddit gave a jeweller
     "buttons/AI/compilers". Now sources are VERTICAL-AWARE (`trends/sources.py`): a new
     `SerperTrendSource` searches the brand's own salient keywords + "trends" via the configured
     SERP provider (the reliable consumer source — measured: Azza Fahmy → Forbes/Elle/Marie
     Claire/Net-a-Porter "Jewelry Trends 2026"); Reddit picks subreddits by vertical (jewelry →
     r/jewelry,femalefashionadvice; food → r/food …, best-effort since Reddit's public JSON is
     largely blocked now); the TECH feeds (HN, Dev.to) are added ONLY for a brand with a WHOLE
     tech keyword (word-level match, so "techniques"/"keychains" don't drag them in — the measured
     bug). `python -m trends` now loads `.env` for the SERP key. 3 hermetic tests.
  4. **Social links absurdly over-counted — FIXED 2026-07-06.** `social_presence` counted SHARE
     buttons as accounts (Azza Fahmy: 20 "social links" = facebook/sharer.php, twitter/intent/tweet,
     pinterest/pin/create/button on every product page — only 5 real accounts). `_social_platform`
     now returns None for a share/intent URL (`scraper.extractors.links._is_share_url`: path markers
     + a `?u=/url=/text=http` query), so a share button is never a social account. Fixes the bogus
     "23 social links above peer average" SWOT strength. 1 hermetic test.
  5. **Poster logo had an ugly white PLATE — FIXED 2026-07-06.** `_overlay_real_logo` drew an opaque
     box behind the mark (needed once for the reverted classic-cover attempt). The one-shot reserves
     a clean corner, so the real logo is now placed DIRECTLY with only a soft drop-shadow — no plate.
- **Reel is still officially UNGATED in the coverage block**: captions/voiceover/hook
  ARE gated, but Veo extends speech beyond the provided lines (ungated model speech)
  and there is no per-reel audit trail yet. → closing items in §7 Phase 1.
- **Classic poster path** can still bake garbled labels in STYLE backgrounds (the
  junk-label OCR gate covers one-shot only).
- Offerings family names can come out in English on an Arabic site (cosmetic; output
  copy is language-locked anyway).
- Deterministic (no-LLM) reel scene fallback still uses the brief's capped offerings.
- Media buying (PDF §04) does not exist in code. A/B/C/D packs: variation engine
  exists, no batch endpoint. Funnel/TOFU-MOFU-BOFU/media-plan: not built.
- Productization: no auth / rate limiting / cost caps or tracking / CI / Docker;
  jobs are in-memory (restart loses them); poster/reel routes are sync-blocking
  (~1-3 min / 10-15 min) with a spinner-only UI; Windows-only tested.
- SSRF: DNS-resolve-time check (TOCTOU/rebinding + crawler-followed links deeper gate
  pending). Serper free tier throttles bursts.
- Old scrapes/profiles don't carry the new fixes — **re-Analyze before judging**.
- **Phone region residual (C1)**: a non-Egyptian business on a GENERIC TLD (.com/.net)
  with NO locale path can still fall back to EG and mis-parse a bare national number —
  the honest edge of what a universal signal can prove. Sites with a ccTLD or `/xx-yy`
  locale are correct. (Reliable-signal-else-EG chosen over drop-if-unknown to avoid
  regressing the many Egyptian .com sites; drop-if-unknown is a one-line change if the
  zero-fabrication bar must be absolute.)

---

## 7. Roadmap — in order

### Phase 1 — finish the PDF's shipped story (highest value / lowest effort first)
1. **Reel brand-safety completion**: transcription gate on the full Veo audio
   (transcribe → Ledger-audit → mute/regenerate on unsourced hard claims) +
   `reel/audit.py` per-asset trail → move reel to GATED_SURFACES.
2. **Compliance Sheet for the calendar** in the web UI (transform exists; wire like
   the poster's).
3. **A/B/C/D testing pack**: one endpoint/button → N variants (seeded variation:
   different form/voice/layout/scene per variant) + a pack manifest; each variant
   carries its own compliance sheet. (PDF enterprise feature; engine already exists.)
4. **Calendar + campaign in the web UI** (strategy is CLI-only today).
5. **Async jobs for poster/reel** (reuse the scrape job/SSE pattern) — progress
   stages instead of a 15-minute spinner.
6. Classic-path junk-label OCR gate (parity with one-shot).

### Phase 2 — PDF Section 04: Intelligent Media Buying (the unbuilt half of the pitch)
7. **Objective deduction** from profile+SWOT (lead-gen vs conversion vs awareness) —
   rules + one Gemini call over data we already have; output = a cited objective memo.
8. **Competitor live-ads intelligence**: Meta Ad Library API (official, free) —
   competitors' running ads → longevity = proven winners; platform/format inference.
   Feeds the SWOT (ad-presence dim) and the media plan.
9. **Media plan report**: budget split, platform per segment, target CPA — consumes
   profile + SWOT + objective (report only; no spend execution — human stays in
   control, per the PDF).
10. **Funnel-aware pack** (TOFU awareness reel / MOFU proof poster / BOFU conversion
    ad) — a consumption layer over existing generators.
11. Campaign governance (kill-switch rules, fatigue prediction) — needs live campaign
    data; design the schema first.

### Phase 3 — productization (before any external/paid exposure)
12. Auth + per-key rate limiting; cost tracking + per-run caps (a reel ≈ 10 paid
    calls); job persistence; Docker/Linux; CI (GitHub Actions: pytest + tsc).

### 💡 Suggested additions (NOT previously requested — clearly marked)
- **(💡) Brand Kit export**: one JSON/PDF per brand (logo assets, palette, fonts, DNA,
  voice) — reusable across campaigns and a nice client deliverable.
- **(💡) Multi-format renders**: same creative → 1:1 post, 9:16 story, 16:9 banner
  (the one-shot prompt is ratio-parametric; cheap win for the testing packs).
- **(💡) Approval workflow**: a "draft → approved" state on each asset in the UI with
  a reviewer note — makes the PDF's human-in-the-loop explicit and sellable.
- **(💡) Performance feedback loop**: paste/import post metrics per published asset →
  feed winning form/voice/layout back into the variation engine (real "learning" media
  buyer over time; pairs with Phase 2).
- **(💡) Cross-run brand memory (lightweight vector store)**: only when multi-client
  SaaS starts — cache embeddings + DNA + research per brand across sessions.
- **(💡) Watermark/draft mode** for unapproved renders (protects against accidental
  publishing of ungated drafts).
- **(💡) Competitor visual benchmarking**: run the DNA vision step on competitors' ads
  (once Ad Library lands) → "their visual language vs yours" slide in the SWOT.

---

## 8. Measured lessons (never re-learn these — condensed from the old change-log)

**Generation:**
- Imagen/image models BAKE raw hex codes and any quoted text into pixels → palettes by
  NAME only in image prompts; verbatim copy only in the one-shot (whose OCR gate
  verifies it).
- STYLE-conditioning on text-heavy brand ads reproduces GARBLED text — attach real
  assets (logo/products) for fidelity; use DNA as TEXT for style.
- Veo/Imagen/Gemini model availability differs per project/region — **probe before
  building** (veo-3.1-preview 404'd for weeks; image models resolve on
  location="global").
- Coloured "brand" lighting on people = alien skin; brand colours belong to
  environment/props, never skin (reel + ITI de-RGB lessons).
- ffmpeg: `apad`/infinite-src + `-shortest` OVERSHOOTS (muxer interleave race) — pad
  to explicit durations; `-shortest` only with finite streams. libass can't shape
  Arabic — all text via Chromium capture.
- Vision OCR readers silently "correct" Arabic dialect (أكتر→أكثر) — demand
  character-exact, dot-counting OCR in gates.

**Extraction/scraping:**
- Serialized (dict) vs object profiles: EVERY adapter must unwrap
  `{"value":...}` — this class of bug appeared 3 times (discovery query, matrix dims,
  web engine).
- The offerings guidance is keyed by the RULES category (often None) — universal
  instructions must live in the generic branch too.
- A CDN host that contains the brand name poisons ANY substring brand-match — always
  host-strip before keyword matching.
- Deterministic heuristics for language/market/identity hit ceilings fast — the
  pattern that works: deterministic scaffolding + ONE cheap LLM call at the point of
  infinite variety (query planning, relevance judging), with reject-only/fallback
  contracts so the LLM can never fabricate.
- `same_registrable_host` must be real eTLD+1; naive last-two-labels breaks .com.eg.
- `normalize_url`'s output is the FETCH url, not just a dedup key — forcing a scheme
  there would break http-only sites. For dedup that must fold http/https + trailing
  slash, use a SEPARATE key (`dedup_key`) and keep fetching the faithful form. And a
  page can be fetched twice via a redirect the frontier can't see pre-fetch — dedup
  again POST-fetch on the normalized final url.
- A one-shot signal read from the homepage SNAPSHOT is nondeterministic on JS sites (the
  same store rendered 2 vs 164 product links across runs). A signal that gates crawl
  depth (or any budget) must be RE-EVALUATED over accumulated evidence during the crawl,
  not frozen at page 1 — build the frontier wide and raise the cap live when it flips.
- A token/stem heuristic cannot judge SUBJECT identity — it can't separate an acceptable
  synonym/inflection ("experts"↔"youth", رقمي↔رقميون) from a fabrication ("gifts"↔"stores");
  both are token-disjoint (MEASURED: a subject-overlap heuristic broke 4 legit grounding
  tests + the Arabic-inflection match). This is the "heuristics hit ceilings" lesson — the
  fix is deterministic scaffold + ONE cheap LLM judge on the ambiguous case, with a LENIENT
  fallback so it never regresses without the judge. (Measure-first caught the bad heuristic
  before it shipped.)
- Selecting ONE element from a `set` via `next()` is PYTHONHASHSEED-dependent — it made the
  C2 number-class verdict nondeterministic across processes (a pre-commit review caught it).
  When a gate decision depends on a set, use the WHOLE set (accept if evidence matches ANY
  class) or a deterministic order, never an arbitrary pick.
- A verifier that matches a claim's VALUE without its CONTEXT certifies fabrications: a
  bare-digit ledger match "sourced" "Save 50%" from "50 years" and "best coffee" from
  "best regards", printing a FALSE 'verified' row in the compliance sheet (the moat's
  proof) — worse than an ungated surface. Number resolution now requires unit-class
  agreement (%/price/duration/scale); tighten only on a POSITIVE strong-unit signal so no
  real claim regresses. (Superlative + bare-number SUBJECT context remain open.)
- A bare phone number (no `+`) is a DIFFERENT real number in every country — parsing it
  under a hardcoded region fabricates a valid-looking WRONG number (nahdi Saudi
  920024673 → +20920024673 under blanket "EG"). Derive region from a RELIABLE per-site
  signal (locale path, ccTLD) first; `html lang` is NOT reliable (`en-US` is a common
  template default on non-US sites). Home-market fallback only when no signal exists.

**Process:**
- Restart uvicorn after every backend change; restart cleanly when adding route files.
- Byte-identical LLM outputs across runs = your change never reached the prompt.
- The owner's live testing is the best QA in the project: each caught failure became
  a permanent gate + test (912→913 and counting). Keep that loop.

---

## 8.5 — 2026-07-18/19: الداشبورد الموحّدة + الوكالة الآلية (uncommitted mega-session)

**U1 — War Room ثم الداشبورد الموحّدة اللافندر:** `dashboard/war_room.py` — 10 صفحات full-viewport
(قيادة+بطاقة براند+posture · سوق · SWOT/TOWS/voice · ذكاء · خلايا · خزنة · خدمات تفاعلية HITL ·
استوديو · تقويم · ذمة+honesty)، هوية لافندر `#CDB4E4`/`#A98BD0`، RTL. Routing: `/dashboard`=الموحّدة،
`/classic`=القديم، `/war_room` alias. اللاندنج بنفس الهوية + السلوجن الرسمي (ع/EN toggle):
"ذكاءٌ يُؤسَّس على الحقيقة.. لِيَخلُقَ يقيناً." / "Grounded in truth. Engineered for certainty."
**قاعدة مالكة:** ممنوع "صفر هلوسة"/إطلاقات في أي سطح client-facing — النظام يعالج ويستبعد، لا يمنع.

**U2–U5 — الشاحنات الأربعة (ads_intel/marketing_intel/media_plan_v2):**
- ش2 حقائق حتمية: `ads_intel/facts.py`+`config.py` — Winner=نشط≥30ي (env)، إجهاد=3 نسخ في 7ي
  (collation 120-char NFKC)، CTA سلم حتمي (لينك>نص، ""=مجهول). AdPresence+winners_30d/fatigue/top_*.
- ش3 وضع المنافس: `marketing_intel` mode="competitor" + kind=message_angle + `from_ads.py`؛
  نفس موات الاقتباس الحرفي (كود). CLI: `scripts/extract_competitor_intel.py`.
- ش4 الهجوم المضاد: `media_plan_v2/engine.py` — `_counter_cell` (منافس صورة→Reel، فيديو→ثابت،
  سعر→جودتنا)، `counter_basis` "أب القرار"، لا CPL منافس مخترع (rationale فقط).
- ش1 السحب التكيفي: `ads_intel/live.py` (transport+pagination fail-closed) + `pull_ledger.py`
  (META_MONTHLY_CALL_CAP=500) + `orchestrator.py` (batch 20، ثقة 3عروض|5آلام|1فائز، سقف 200،
  autopull_enabled=token∧¬NO_SPEND∧¬DISABLED∧¬pytest) موصول في `full_run` + `competitor_intel` بالنتيجة.

**2026-07-19 مساءً — إصلاحات استخدام المالكة (6) + كل المفتوح:**
- صلة الإعلانات: `search_type=KEYWORD_EXACT_PHRASE` + بوابة صلة متعددة الإشارات في
  `ads_intel/relevance.py` (chokepoint واحد: `ads_from_snapshot(only_competitor=True)`) + عدّاد
  `filtered_out` يظهر chip بالرادار.
- منافسو التعليم: `web_discovery._judge_system_prompt(category)` — قائمة DROP ديناميكية تستثني نوع
  البراند نفسه (تعليم يحتفظ بالمعاهد، برمجيات بالأدوات)؛ باقي الفئات بايت-ثابتة. بوابة identity
  تتخفف لـREACH بهوية رفيعة.
- برومبت البوستر: العقد الحرفي خرج من برومبت الصورة (OCR فقط)؛ سياج BEGIN TEXT/END TEXT يحمل
  الكوبي فقط؛ compliance_for وصلت للـoneshot (CS-02 residual مقفول).
- bidi: `unicode-bidi:plaintext`+`dir=auto` (داشبورد+لوج اللاندنج). أدلة: لينك دائماً — fallback
  لموقع البراند للادّعاءات الموثّقة فقط + default source_url في `grounding/ledger.py` brand-tier.
- كروت الخدمات: unquote عند المنبع + dedup/merge بمفتاح مطبَّع (نانوكير 10 نص مكسور→6 نظيفة) +
  grid auto-fill. بطاقة البراند في p1.
- التسليمات: `launch_bundle` يشحن media_plan_v2 (+test_cells.csv) — LB-MPV2-GAP مقفول؛ CS-121
  الكالندر بقى ياخد compliance sheet (شكل items مقبول + موصّل للحزمة)؛ CS-14 تحييد P-72 في
  concept؛ CS-26/29/30 صياغات بلا إطلاق.
- الترند: CS-63 dedup URL مطبَّع، CS-44 salience بالتكرار، إفصاح ضوضاء الـSERP (قياس 07-15) على p2؛
  Serper بقى له عدّاد+سقف (SERPER_MONTHLY_CALL_CAP=500، fail-closed) في المزوّد نفسه.
- سلامة اختبارات: `two_phase_route_decision` دالة إنتاج مختبَرة (CS-54/70)؛ اختبار سلوكي للريجستري
  (CS-91)؛ رسالة مفيدة لـmeasure_oneshot_poster (CS-116).
- CS-158: ACCEPT_LANGUAGE بقى env-overridable (SCRAPER_ACCEPT_LANGUAGE) + إفصاح — القياس تذكرة.
- print_pack: الأكسنت وردي→لافندر (خلفية الطباعة البيضا مقصودة).

---

## 8.6 — 2026-07-20: البرفكت من الجذور (ذوق الريل · منطق البوستر · عزل اللوجو · أرقامها)

توجيه المالكة: "مفيش ذوق ولا وجهة نظر [في الريل] · البوستر بيحط علبة الدوا في أي مكان بلا منطق ·
اللوجو اعمله isolation صح · اهتم بالتفاصيل ومبدأ إننا شغالين على أي URL".

**حُكم المالكة الحاكم (غيّر التصميم):** *"الذوق ليس له Ground Truth — مستحيل تختبره. لكن
Strategy يمكن."* + *"لجنة 3 خبراء = 4 نداءات LLM؛ هل ده أحسن ولا بس أغلى؟ ناقد واحد يكفي."*
⇒ اتلغت لجنة الـWorkflow المخططة، والبوابة بقت **بنيوية تُقاس بالكود**، مش حكم ذوق.

- **استراتيجية الريل (`reel/creative_director.py` + `reel/coherence.py`):** ثلاث التزامات تُقرَّر
  **قبل** أي مشهد: `creative_pov` (الإحساس الواحد) · `narrative_arc` (≥3 مراحل مرتّبة — القصة اللي
  الـPOV ما يقولهاش، بحكمها) · `visual_motif` (خيط بصري). كل مشهد يعلن `arc_beat`.
  `coherence_lint` يرفض بالكود: POV فاضي/عام (فلتر كلمات تسويقية عامة ع/EN) · قوس <3 مراحل أو مكرر
  · مشهد بلا beat معلن · مشاهد ما بتمشيش القوس بالترتيب من أوله لآخره · **موتيف ما بيتكررش فعلاً**
  (لازم توكناته في ≥2 برومبت مشهد — ده اللي بيفرّق بين موتيف وأمنية). `require_strategy=True` وقت
  التأليف فقط، فالخطط المعتمدة قبل العقد تفضل تعدي (استشاري).
- **منطق وضع المنتج (`poster/oneshot._props_rules` + `poster/concept._placement_rule` +
  `poster/vision_qa`):** خمس قواعد فيزياء + قرار مكان يتاخد مع الفكرة (نفس النداء = صفر تكلفة زيادة)
  ويتنقل حرفياً للرندر + معيار `placement_logical` في الناقد البصري = فشل قابل لإعادة الرندر.
  **تصحيح المالكة (D-8):** القاعدة مش "على سطح" — ده صح لعلبة وغلط للجزمة (الأرض) والعربية (الطريق)
  والكنبة (الأوضة) والمبنى؛ الصيغة العالمية = **دعامة طبيعية تناسب فئة المنتج نفسها**.
- **عزل اللوجو (`reel/image_quality.white_key_logo`):** flood-fill من الحواف بدل الـsoft ramp —
  الخلفية المتصلة بالحواف alpha 0 **كامل** ("شيل خالص مش تطفية")، والأبيض الداخلي محفوظ (غير متصل).
  **مقيس على لوجو نانوكير الحقيقي: 84.5% شفاف تمام، 0.00% نص-شفاف، العلامة سليمة.**
  + تفضيل الفيكتور (`scraper/inline_svg_logo`): لوجو أساسي JPEG (شفافية مستحيلة) + مرشح inline-svg
  بثقة ≥0.3 ⇒ يُجرَّب الفيكتور أولاً بنفس بوابة الحبر. **نانوكير: مرشحاه بلوكات مصمتة 100% ⇒ اترفضوا
  صح، فضل الـJPEG + الـflood-fill.** كمان: `resolve_raw_homepage` بيقرا `home.html` زي
  `00_homepage.html` — الاسم الجديد كان بيعطّل الإثراء بصمت في كل سكرايب حديث.
- **أرقامها تُحسب (`media_plan/builder._apply_owner_budget`):** ميزانيتها الشهرية بقت `test_budget`
  فعلياً، متقسّمة بحصص الأهداف (المجموع = رقمها بالظبط، بدون كسر مخترع)، ومعاها `budget_note` بيقول
  الحساب. أقل من **أرضية التعلّم** (3×التكلفة المستهدفة×المجموعات) = رفض معلن مش قبول صامت ولا كراش.
  **بق مسكه الاختبار:** `model_copy` ما بيعيدش التحقق ⇒ الميزانية تحت الأرضية كانت هتعدي بصمت؛
  الحل `model_validate`. الديك بيقرا `_plan_test_budget` (مجموع كل الأهداف) و p1 بيقول "من رقمك ✓".
- **قاعدة "كل داتا = ملف" (`trends/artifact.py`):** `outputs/<slug>_trends.json` = العناصر +
  `_meta` (البذور، gl/hl، العدد، إفصاح الضوضاء المقيس، وسبب الفراغ لو فاضي). الملفات المعاد بناؤها
  من تحليل قديم بتتوسم `backfilled_from` وبتقول السوق **غير مسجّل** — مش "غير محدد" (نفس فخ D-8:
  جملة صحيحة بتجاوب على سؤال تاني).
- **الكاش:** `Cache-Control: no-store` كان **موجود أصلاً** على كل رد ديناميكي ⇒ سبب "الميزات مش
  بتظهر" كان **عملية السيرفر الشغالة أقدم من الكود**، مش الهيدر. اتضاف اختبار حماية للهيدر.

**فحص الربط بالداشبورد (سؤال المالكة "انت ربط كل التعديلات بالداشبورد صح؟") — لقى 3 فجوات حقيقية:**
1. **إثراء اللوجو ما كانش شغال خالص على مسار الداشبورد**: `enrich_profile_logo` بيشتغل على ملف،
   وبيتنده من CLI الـ`business_profile` بس؛ لكن الداشبورد بيحلل عبر `competitor/full_run.py` اللي
   بيسيب البروفايل جوه الـresult ⇒ كل براند من الداشبورد كان بيتخطى الإثراء بصمت. الحل:
   `enrich_logo_in_profile_dict` (نسخة in-memory، والملف بقى بيفوّض ليها) متندهة من full_run.
2. **بريف الـHITL ما كانش بيعرض الاستراتيجية**: البوابة بتفرض POV/قوس/موتيف، لكن الشاشة اللي
   بتوافق منها ما كانتش بتعرضهم ⇒ موافقة على ~$11 من غير ما تشوفي الحاجة الوحيدة اللي اتغيّرت.
   الحل: `_reel_brief_lines` بيبدأ بوجهة النظر/القوس/الموتيف وكل مشهد بيتوسم بمرحلته.
3. **ملف الترندات ما كانش موصول من الصفحة**: اتضاف سطر في صفحة السوق بالاسم والعدد الكامل.

**الاستوديو بيعرض شغل قديم (owner، NTI 2026-07-20):** التحليل اتعاد النهاردة 12:24 والبوستر/الريل
من 12 يوليو — إعادة التحليل بتبني الاستراتيجية وما بتلمسش الإبداع، فشغل مُتجاوَز فضل يقدّم نفسه
كأنه الحالي. + "مش سامحلي أجنيريت": مفيش أي قفل في الكود — بانل الـHITL بيفتح **تحت حدود الشاشة**
فالضغط يبان كأنه ما عملش حاجة. الحل (٣ حتت):
- `_staleness_note`: أي إبداع أقدم من `<slug>_result.json` بيتوسم على الصفحة نفسها بعمره بالأيام
  ("قديم — اتعمل قبل التحليل الحالي بـ8 يوم"). قاعدة عالمية، نفس فئة الخطأ بتاعة الادعاء بلا مصدر.
- `archive_creative` + `POST /studio/reset` + زر "🧹 ابدأ من جديد": بينقل البوستر/الريل/الخطط لـ
  `outputs/_archive/<slug>/<timestamp>/` — **أرشفة مش مسح** (رندر مدفوع ~$11 لازم يفضل استرجاعه
  ممكن)، وملفات التحليل ما بتتلمسش.
- البانل بقى `scrollIntoView` عند الفتح.

**إعلانات ميتا اشتغلت (2026-07-20) — وكشفت بقّين صامتين:**
- **`.env` مش متحمّل في نقاط الدخول**: المالكة أكّدت هويتها، ولّدت توكن شغّال، حطته في `.env` —
  والسحب قال "META_ADS_TOKEN is not set". `.env` كان بيتحمّل في 3 أماكن مالهاش علاقة بالتحليل، فـ
  `autopull_enabled()` بيقرا بيئة فاضية ⇒ ذكاء إعلانات المنافسين كان هيفضل فاضي **للأبد بلا أي خطأ**
  وبشكل مطابق للحقيقة ("المنافس مش بيعلن") — أخطر نوع فشل في المشروع. الحل: `config_env.load_env`
  مشترك، متندَه من كل نقطة دخول بتقرا اعتماد. **الاختبار سلوكي مش نصي** (جربت grep الأول وعدّى غلط
  لأن `full_run` عنده helper اسمه `_load_env` — نفس المصادفة اللي هترجّع البق).
- **البحث بالعبارة الحرفية بيصفّر الأسماء المركّبة**: مقيس حي (EG/ACTIVE): "Vodafone" حرفي 5 /
  غير مرتّب 5، لكن **"Vodafone Egypt" حرفي 0 وغير مرتّب 1** — البراند بيختفي بالكامل. الحل: صفحة
  أولى فاضية ⇒ توسيع **مرة واحدة** لـUNORDERED، وبوابة الصلة الموجودة بترمي الغرباء. كلمة واحدة =
  مفيش إعادة (نفس البحث). **إثبات حي: السحب اتحول من 0 لـ1.**
- أداة `scripts/verify_meta_token.py`: بتترجم رفض ميتا لحاجة واحدة تتصلح، وبتقيس عمر التوكن
  (توكن الـExplorer = ساعتين) و`--long-lived` بيبدّله بـ60 يوم **ويكتبه في .env** (مش في الشاشة).

السويت: 1957 → **2039** أخضر، صفر إنفاق (مكتبة إعلانات ميتا مجانية)، صفر commit.

## 8.7 — 2026-07-21: تشغيلة فوليوم قطر (أول براند غير مصري) — سجل المشاكل بالتفصيل

**الطلب:** سكراب `volumeqatar.com/food menu/` + تشغيل المشروع كله **ما عدا الريل** + فولدر لكل موقع.
**النتيجة:** كل حاجة اتسلّمت (سويت 2080 خضراء)، والتشغيلة دي لوحدها كشفت **٧ باجات** — ستة منها
كانت هتخرّب أي تسليم لعميل، وواحد كان بيخفي نفسه بشكل مطابق للحقيقة.

### المشكلة ١ — كل موديلات الصور ميتة في المشروع (البوستر فشل كلياً)
- **العَرَض:** `[X] poster failed: ClientError 404 NOT_FOUND ... imagen-4.0-generate-001`.
- **التشخيص:** فحص حي لسبع موديلات على `radiant-octane-501919-v1`: **كلها 404** — كل الـImagen
  و`gemini-3-pro-image-preview` (اللي `.env` مثبّتها في `ONESHOT_IMAGE_MODEL`). الشغّالة الوحيدة:
  **`gemini-2.5-flash-image` (نانو بنانا)**. ملاحظة تشخيصية: أول فحص عملته كان على
  `us-central1` وده كان غلط مني — موديلات الـoneshot بتتحل على `location="global"` بس.
- **الجذر:** موديل واحد ثابت = نقطة فشل مفردة؛ الـ404 (موديل اتشال) كان بيتعامل زي أي خطأ.
- **الحل:** `poster/oneshot._MODEL_CHAIN` + `_is_missing_model` — الموديل المضبوط يتجرب **أولاً**،
  و404 **ما يتعادش على نفس الموديل** (فرق جوهري عن 429 اللي هو نوبة مؤقتة وبيتعاد فعلاً).

### المشكلة ٢ — اللوجو بيتحط فوق العنوان (البوستر غير صالح)
- **العَرَض:** "Feed your mind" اتقرت "eed your mind"؛ والناقد البصري نفسه كتب: *"the brand logo is
  garbled and overlaps with the headline, making it completely unusable"*.
- **الجذر:** الركن كان بيتحدد من **اتجاه القراءة** بس، من غير ما يبص الركن فيه إيه.
- **محاولة أولى ناقصة:** خليته يختار بين الركنين الفوقانيين — وفضلت تقع، لأن **عنوان بعرض الصفحة
  بياخد الاتنين**. الاختيار بين ركنين وحشين برضه بيقع على النص.
- **الحل النهائي:** قياس **الأربع أركان** بطاقة الحواف. **معايرة على الرندر الحقيقي:**
  عنوان = 22.9 · خلفية صورة عادية = 7.3 · نسيج خفيف = 1.8 ⇒ عتبة نص مطلقة 12.0 + نسبة 1.6،
  فالعتبة لوحدها ما بتحركش اللوجو على صورة مزدحمة، والنسبة لوحدها بتقلب اللاي-آوت على نسيج عادي.

### المشكلة ٣ — البوستر **المرفوض** بيدهس البوستر الكويس (الأخطر بصرياً)
- **العَرَض:** الملف المسلَّم فيه "speciaity coffee coffee" و"Mall of Maqur".
- **المفارقة:** بوابة النص **رفضت** الرندر ده فعلاً (fidelity 0.667 < 1.0) — ومع ذلك اتكتب على
  الملف النهائي، لأن الـcanonical sink (اللي اتعمل أصلاً لعلاج "فشل وهو موجود") كان بيتحدّث من
  **كل** محاولة ساعة ما تتولد، قبل أي بوابة.
- **الحل:** `_refresh_canonical(..., passed=)`. رندر ما عدّاش البوابات يُنشر **فقط** لو مفيش أي
  تسليم قبله (وعد البند ١ محفوظ: التايم-آوت يسيب حاجة معروضة)، لكنه **أبداً** ما يستبدل تسليم قائم.

### المشكلة ٤ — الناقد بيرسّب أي براند إنجليزي (D-8، محور اللغة)
- **العَرَض:** بوستر نضيف (دقة 1.0، تقييم 8/10) مرفوض بـ*"The poster uses Latin script, which
  violates the pass criteria"*.
- **الجذر:** تعليمة الحقل كانت لغوية-واعية، لكن سطر `overall_pass` كان بيطلب "no Latin in the copy"
  **لكل براند في الدنيا**. قاعدة صح لبراند عربي، متطبّقة على سؤال مش بتاعها.
- **الحل:** `_pass_criteria(arabic)` — النص العربي يحتفظ بالقاعدة، وغير العربي بيتقاله صراحة إن
  الحروف اللاتينية **صحيحة** وما تتحسبش ضده.

### المشكلة ٥ — الناقد بيرفض لوجو العميل نفسه
- **العَرَض:** ٣ رندرات متتالية مرفوضة بـ"the brand logo is completely garbled and illegible".
- **التشخيص:** نزّلت `wp-logo-BOOK.png` من موقع البراند وعرضته على خلفية داكنة: **لوجو مقصود
  حروفه متداخلة** (V من ورق كتاب + OLUME متشابكة). التركيب عندنا **بكسل-مطابق** (الملف RGBA شفاف
  أصلاً ⇒ `white_key_logo` سابه بايت-ثابت، 77.5% شفافية زي ما هي).
- **الجذر:** `logo_ok` اتعمل عشان يمسك **الموديل** وهو بيعيد رسم لوجو ويبوّظه. لما بنركّب الأصل
  الحقيقي حتمياً، الفشل ده **مستحيل بالبناء** — والحكم على شكل الحروف بقى بيرفض تصميم العميل.
- **الحل:** `logo_composited=True` ⇒ المعيار يبقى **الوضوح** (تباين/قص/تغطية) مش شكل الحروف، وبيتشال
  من ضرب `overall_pass` (بـ`_recompute_pass`، الحكم لسه بيتحسب في الكود مش من الموديل).

### المشكلة ٦ — العملة بترجع "جنيه" لما البلد مش متعرفة (D-8، محور البلد)
- **العَرَض:** براند قطري ممكن ياخد أرقامه بالجنيه المصري.
- **الجذر:** `currency or "جنيه"` في `reality_check` (نص الإفصاح) و`war_room` (مرتين).
- **ملاحظة على الاختبار:** أول اختبار كتبته فحص حقل `currency` بس و**عدّى** — التسريب كان في
  **نص الإفصاح**. وسّعت الفحص للنص والملاحظات فطلع أحمر.
- **الحل:** `"بعملتك المحلية"` + `_deck_currency` — بلد مجهولة = **مفيش كلمة عملة**، مش عملة تانية.

### المشكلة ٧ — ملف الفولدر بيتسلّم قديم (باج في اللي لسه كاتبه)
- **العَرَض:** اختبار السويت الكاملة رسب بشكل متقطع.
- **الجذر:** `shutil.copy2` بينسخ الـmtime، فملف اتعدّل في **نفس جزء الثانية** بيتقارن كأنه "محدَّث"
  ⇒ العميل ياخد النسخة القديمة. تحسين شكله سليم وبيشحن بيانات قديمة بصمت.
- **الحل:** الملفات الصغيرة (<5MB) تتنسخ **دايماً**؛ المقارنة للفيديو الكبير بس.

### ما اشتغل صح من أول مرة (محور البلد — الاختبار الحقيقي)
`country_of` = Qatar · `search_locale` = `qa/en` · الترندات قطرية فعلاً ("Qatar Catering Services
Market Size") · زر الطوارئ **بالريال القطري** · الكاستينج خليجي · 14 صنف من المنيو.
**الأسعار:** غير موجودة على الموقع أصلاً (صفر ذكر لـQAR في 85 ألف حرف) — استخراج صح مش فقدان.

### فجوات مفتوحة في التسليمة (مش باجات — نواقص معلنة)
- **منافس واحد بس** (LEE-TOP Catering) لمنطقة فيها مقاهي كتير. غالباً لأن الدخول كان من **صفحة
  المنيو** ⇒ التصنيف "catering" وبذور البحث بقت أصناف أكل ("tomato","basil","soup") مش سوق.
  **مقترح:** التشغيل على الدومين الجذر بدل صفحة داخلية.
- **التهديدات في SWOT = صفر** — تحليل المخاطر فاضي في حزمة هتتنافس بيها.

## 8.8 — 2026-07-21: منافس واحد وصفر تهديدات — الحل من الجذر (٦ بنود)

سلسلة سبب **واحدة** ورا العرضين، اتكشفت بتلات تحقيقات متوازية. الأرقام قياسات مش تقديرات.

### ١) الكود كان بيقرا حقول غير موجودة (أخطر واحد)
`competitor/discovery.py` كان بيقرا `physical_address`/`lat`/`lng` — **مش في الاسكيما أصلاً**.
**مقيس على كل الكوربوس: صفر من ٢٢ بروفايل فيه الحقول دي.** يعني `is_online_only=True` في **١٠٠٪**
من التشغيلات، وفرع الجيوكود المكتمل (`:274-300`) **ما اشتغلش ولا مرة في الإنتاج** — كل براند محلي
اتبحثله بلا إحداثيات ولا تحيّز مكاني. `_subject_address` بيقرا `locations` ⟶
`contact_channels.physical_addresses` ⟶ `service_areas`. **النتيجة: صفر ⟶ ١٤ من ٢٢.**

### ٢) السوق قبل المنيو
`market_definition.py` كان `for svc in subs[:5] or ([category] ...)` — التصنيف يُستخدم **فقط لو
مفيش عروض**. المقهى دخل من صفحة المنيو فـ"العروض" بقت أطباق، والاستعلام بقى
`'Volume Catering Qanat Quartier'` (اتنين من تلاتة باسم البراند نفسه). نانوكير كانت بتدوّر على
**SKU بتاعها هي**. الحل: ترتيب ثلاث طبقات (تصنيف ⟶ متكرر-عبر-العروض/مُعزَّز-عبر-حقلين ⟶ نسخة منتج)
+ حارس اسم البراند + مكان قابل للبحث. **رتّبنا ما حذفناش، فكل الاختبارات القايمة عدّت بلا تعديل.**
وصلّحنا إن `cafe` كانت **بتتشال** كـ"كلمة براند" لأن الاسم "Volume Cafe" — كسر فئة كاملة
("X Pharmacy"، "Y Jewelry").

### ٣) LOCAL كان محبوس على محرك واحد
`router.py` كان بيرجّع Places لوحده و`thin_peer_set` محسوب ومهمَل. الدليل: **منافسي نانوكير الأربعة
كلهم من الويب** (Places رجّع صفر). دلوقتي البركة الرفيعة بتوسّع بنفس دمج HYBRID الموجود؛ النتيجة
الكافية ما بتتغيّرش.

### ٤) البركة الصغيرة كانت بتضخّم الدرجة
بمرشح واحد وسيط المراجعات = مراجعاته هو ⟹ `size_similarity=1.0` مجاناً ⟹ شركة كاترينج عدّت كقرين
مقهى بـ0.73. (وهنا **رابع حقل وهمي**: `review_count` — فالاحتياطي هو المسار الوحيد اللي بيشتغل.)
بركة أقل من ٣ ⟹ الدرجة تُسقَط بدل ما تتحوّل لثقة.

### ٥) تهديدات مشتقة وموسومة — **والتصميم اتصحّح بواسطة اختبار قايم**
أول تنفيذ حوّل **ضعف في موقعك** لتهديد، فوقع `test_scraped_behind_standalone_emits_no_phantom_threat`
فوراً. الاختبار كان صح: **التهديد خارجي والضعف داخلي — ده تعريف SWOT مش تفضيل**. شِلت المولّد ده
تماماً؛ الاشتقاق بقى من أدلة **خارجية** بس (إشارات سوق + نضج البراند)، وكل بند بياخد
`claim_strength="directional_not_validated"` (السلّم الموجود، مش حقل جديد) وبيقول في نصه إنه استنتاج.
كمان: `_TREND_THREAT_KEYS` ١٦ كلمة إنجليزي، فترند "Qatar Catering Market Size" بقى **فرصة** تلقائياً.

### ٦) اللي النظام بيعترف بيه لنفسه بقى يوصل للصفحة
`swot.notes` و`discovery_notes` **مكانوش معروضين في أي مكان** في `dashboard/` — رغم إن سجل الاكتشاف
بيقول حرفياً "مش هنحشي بمرشحين تحت المستوى" و"جرّبي نطاق أوسع". و`swot.py:190` كان بيحسب الفراغ على
الـSWOT **كله**، فنقطة قوة واحدة عرضية سكّتت الإفصاح. دلوقتي: إفصاح **لكل خانة**، الخانة الفاضية
بتقول سببها بدل شرطة، وسم `ready_for_swot=false` بحاجبه، و`discovery_audit` بيتحفظ.

### التحقق: إعادة تشغيل فوليوم قطر فعلياً (المعايير مسجّلة مقدماً)

| | قبل | بعد |
|---|---|---|
| المنافسون | **1** (شركة كاترينج) | **4** — Joe's cafe · Pure Eats · Suisse Chalet Café · The Kitchen Doha |
| نوعهم | كاترينج عدّى على type="food" | مقاهي/مطاعم بمسافات 0.2 · 0.5 · 2.7 كم |
| المرشحون المسحوبون | 2 | **20** |
| أقران بموقع قابل للفحص | 0 | **3** |
| سجل الاكتشاف | `proximity dropped` | `resolved center via Places -> (25.37599, 51.54502)` · `biased ... r=15000m` |
| ملاحظات SWOT | 1 | 2 (منها الإفصاح الجديد لكل خانة) |

**الجيوكود اشتغل لأول مرة في تاريخ النظام.**

### صدق: حاجتان ما اتحسنوش
1. **التهديدات لسه 0.** الإفصاح الجديد بيقول السبب على الصفحة: "التهديدات محتاجة قرين له موقع قابل
   للفحص، أو شكاوى، أو إشارة سوق. المتاح: 4 قرين، منهم 3 بموقع قابل للفحص." الترندات اللي رجعت
   (`Fresh mood 🥗😍 #tchetche`) مالهاش إشارة نمو، فالاشتقاق ما اشتغلش — **صح إنه ما اخترعش**.
2. **استعلام v1 كان لسه بأسماء الأطباق** بعد إعادة التشغيل (`'tomato basil soup baked salmon with
   cafe'`) — الترتيب وصل لـ`query_seeds` ووقف قبل `discovery.build_text_query` بدالة واحدة.
   اتصلّح بعدها: `'cafe catering tomato basil soup'`، واسم البراند بيتشال من `offering_keywords`
   في المصدر. **التشغيلة اللي فوق كسبت من البذور بس — الرقم هيتحسن أكتر في التشغيلة الجاية.**

**السويت: 2080 ⟶ 2129 خضراء. صفر commit.**

## 8.9 — 2026-07-21: نانوكير + بيبسي — مشاكل ظهرت من تشغيل براندات مختلفة

### نانوكير (إيكومرس مصري بالعربي)
- **أسماء المنافسين كانت عناوين SEO.** التسليمة كانت بتقول "أفضل مكمل غذائي في مصر 2026"
  و"متجر مكملات غذائية في مصر" كأنهم براندات — بينما دول inshapesupplements.com و beseha.com.
  `_clean_title` كان بياخد أول جزء قبل الفاصل، واللي على صفحة SEO هو عبارة الكلمات المفتاحية.
  الحل: الجزء اللي **يطابق الدومين** يكسب، وإلا الدومين نفسه يتجمّل. القاعدة بالدومين مش بقايمة
  كلمات ⇒ بتشتغل بأي لغة. **النتيجة: Drdiamond · iFIT · Iherb** بدل عناوين البحث.
- **تسريب اسم البراند عبر الكتابتين.** الاسم إنجليزي ("Nano Care Therapeutics") والمنتجات عربي،
  فحاجز `tokenize(name)` مالحقش والاستعلام فضل `'ecommerce نانو كير ليبوسومال فيتامين'`. وصّلنا
  `brand_identity_tokens` (بتستنتج كلمات البراند من **تكرارها عبر المنتجات** بأي أبجدية).
  **مقايضة مسجّلة:** لبراند اسمه "Nano Care" بيبيع "Nano Care Liposomal X"، كلمة `ليبوسومال`
  بتركب كل المنتجات زي اسم البراند — والتكرار لوحده مش قادر يفرّق. **نرمي**: اسم مسرّب = صفر
  مضمون، فئة ضايعة = استرجاع أقل بس.
- **أهم إشارة كانت في الخانة الغلط.** نانوكير كلها قايمة على الليبوسومال، والسوق بيقول
  *"Why Liposomal Technology Is Becoming the Standard"* — النظام حسبها **فرصة**. دي تآكل خندقها.
  ضفنا فئة تهديد **التسليع** (غير النمو: النمو = منافسون أكتر، التسليع = ميزة أقل).
  **صفر ⟶ 1 تهديد حقيقي.** وخبر فئة عادي ("A Breakthrough in Nutrient Delivery") **مش** تهديد.
- **صدق:** التهديدات كانت صفر لسبب سليم — نانوكير **مش متأخرة** في أي بُعد مقيس أمام 3 أقران
  اتسحبوا فعلاً. المشكلة كانت التصنيف، مش نقص الأدلة.

### بيبسي (براند عالمي محجوب)
- **الموقع بيرجّع 403 — حتى على `robots.txt`.** أكدناه بفحص مستقل: `pepsi.com` و`pepsico.com`
  الاتنين 403 لمتصفح عادي، ومتصفح Chromium كامل بياخد نفس الرد. **مش مشكلة اسكرابر ضعيف** —
  الرفض عند حافة الـCDN قبل أي تفاوض.
- **الرسالة كانت كلام مبرمجين.** كانت بتطبع `<ErrorCode.BOT_PROTECTION: ...> datetime.datetime(...)`
  وتعرض تلات أسباب مختلفة مع بعض — رغم إن كائن الفشل عارف أنهي واحدة. بقى
  `scrape_failure_message` بيفرّق: حجب متعمد / 429 مؤقت / موقع مش موجود / شهادة — وكل واحدة
  بتقول **إيه العمل**. الـrepr الخام بيروح لسطر `[debug]` منفصل.
- **قرار المالكة: fallback بـApify.** `scraper/apify_fallback.py` — بيشتغل **بعد** زحف بصفر صفحات
  بس (موقع شغّال عمره ما يتحاسب مرتين)، سقف صارم 50 صفحة (Apify بيحاسب بالنتيجة)، وكل ناتج موسوم
  `source="apify"` فمحدش يقدر يعرض فيتش مدفوع على إنه زحفنا إحنا. أي فشل بيرجّع للرسالة الصادقة.
  **مقيس حي: 6 صفحات من بيبسي بمحتوى حقيقي (منتجات وحملات) في 162 ثانية.**
- **حد أخلاقي مسجّل مرة واحدة:** موقع بيرفض نشر `robots.txt` أصلاً — استخدام فيتشر خارجي هناك
  قرار بخصوص شروط الموقع، وهو قرار المالكة لكل براند. الكود ما بياخدوش بالنيابة عنها بصمت؛
  عشان كده كل تشغيلة fallback موسومة ومسجّلة. ومفيش أدوات تحايل (بروكسي دوّار/بصمة TLS/حل كابتشا)
  اتبنت ولا هتتبني.

### fallback بيبسي — أربع طبقات، كل واحدة كانت مخبّية اللي تحتها

الـfallback نجح من أول لحظة (12 صفحة بيبسي حقيقية). اللي فشل هو **استهلاك** نتيجته:

1. **قواميس Apify اتحطت في `manifest.pages` مباشرة.** لكن `PageRecord` مابيحملش نص — بيحمل
   `html_path`/`clean_text_path` بيشاوروا على ملفات. التعيين وقع في التحقق، والـ`except` الواسع
   بلعه ⇒ **فيتش ناجح بان كأنه موقع ميت.** الحل: نكتب نفس تخطيط ملفات الزاحف ونبني PageRecords فوقه.
2. **بروفايل فاضي اتشحن كتحليل.** بعد التصليح، التشغيلة كمّلت وطلّعت SWOT فيه ٨ نقاط ضعف عن بيبسي:
   "مفيش تاجلاين"، "قيمة مقترحة ضعيفة"، "مدى منتجات ضيق". **كلها كذب** — أعراض فشل استخراجنا
   مكتوبة كتدقيق للبراند. الكود كان بيحرس حالة "صفر صفحات" بس، مش الخطوة اللي بعدها.
   `profile_is_hollow` بيوقف التشغيلة دلوقتي ويقول السبب.
3. **HTML بلا رأس.** Apify بيرجّع HTML منظّف بالـreadability: `<div id="readability-content">`
   بدون `<head>`/`<title>`/`og:` — ومستخرجاتنا القاعدية بتقرا الحاجات دي بالظبط. اسم البراند كان
   في الـ`h1` والنظام أعمى عنه. بنعيد بناء رأس من ميتاداتا Apify نفسها؛ الحقل الغايب **يتشال**
   (`<title></title>` فاضية بتتقري "البراند مالوش عنوان" مش "إحنا مشفناش").
4. **`site_metadata` فضل فاضي.** الهوية بتتقري من `manifest.site_metadata`
   (`business_profile/rules/from_metadata.py:165`)، مش من HTML الصفحات — والاسترداد كان بيبني
   الصفحات ويسيب الكائن ده فاضي. بيتملّي دلوقتي من نفس الميتاداتا، **وبس لو الزاحف نفسه مقراش حاجة**.

**نظافة اختبارات (باج من عندي):** اختبار كان بيكتب نص فكسچر (`"real body"`) في مجلد `scrapes/`
الحقيقي لأني نسيت `scrape_dir`. اتنضّف، وكل الاختبارات بقت تكتب في `tmp_path`.

**السويت: 2145 ⟶ 2184 خضراء.**

## 8.10 — 2026-07-21: تقوية الاسكرابر — الإجابة مكانتش مكتبة

سؤال المالكة: "الجت هب فيه lib حلوة نقوي بيها الاسكرابر عموماً علشان ميبلوكش غير للشديد القوي".
القياس ودّى لحتة تانية: **مكانش عندنا مشكلة قوة، كان عندنا مشكلتين من صنعنا.**

### ١) كنا بنناقض نفسنا (والتناقض هو أرخص فحص بوت في الدنيا)
مقيس على متصفح المشروع نفسه:

| الإشارة | كنا بنقول | الحقيقة |
|---|---|---|
| نسخة المتصفح | Chrome **131** | **147** |
| `Sec-CH-UA` | **فاضية** | كروم بيبعتها دايماً |
| `navigator.languages` | `['en-US']` | وهيدرنا `en-US,en;q=0.9,ar;q=0.8` |

UA **مكتوب بالإيد** بيتقادم لوحده — شحنا كذبة ١٦ نسخة. `scraper/browser_identity.py` بيشتق الهوية
من المحرك الفعلي: نسخة صح + تلميحات عميل متطابقة + locale بيوافق الهيدر. **مقيس: 147 = 147 ✓**

**اللي رفضناه عمداً:** ترقيع `navigator.webdriver`. ده موجود لغرض واحد: إخفاء الأتمتة عن موقع سأل
عنها بالتحديد. بنشيل تناقضاتنا إحنا، مش بنكسر قفل حطه حد عن قصد.

### ٢) كنا بنرمي مواقع شغالة (الأخطر)
**مقيس (بيبسي):** الموقع رد **200 بصفحة كاملة 35KB وعنوان "Pepsi Official Site"** — وإحنا رمينا
الموقع كله لأن `_incapsula_` موجودة في الـHTML. لكن `_incapsula_` هو **سكريبت الحماية نفسه**،
بيشحن على كل صفحة محمية بما فيها اللي بتتقدّم لزوار عاديين. كنا بنقرا "الموقع بيستخدم حماية" على
إنه "الموقع رفضنا". العلامات اتفصلت:
- **قاطعة** (`challenge-platform`, `__cf_chl_`, `px-captcha`): بتتحمّل **فقط** على صفحة تحدي ⇒ حجب.
- **حضور بس** (`_incapsula_`, `_pxhd`, `ak_bmsc`): حجب **فقط** لو الصفحة كمان فاضية/بلا عنوان.

اختبار التحدي العربي من كلاودفلير (`test_bot_detection_lang_agnostic`) رجع **أخضر بلا تعديل** —
التفرقة صح مش ترخية.

### ٣) عن مكتبات GitHub — قياس مش رأي
- **أدوات التحايل** (`curl_cffi`, `undetected-chromedriver`, `camoufox`, `cloudscraper`): بتكسر
  الخط اللي فوق. مرفوضة.
- **أدوات الاستخراج** (`trafilatura`, `extruct`, `BeautifulSoup`): المحتوى المسترد فيه **صفر
  JSON-LD، صفر microdata، صفر أسعار** — مفيش مكتبة هتطلّع حاجة مش موجودة.
- المكسب الحقيقي كان **٤ سطور إعداد**.

### ٤) غلطة تشغيلية مسجّلة — كررت درس ١٣ يوليو
ضربت بيبسي ~١٠ مرات في التشخيص. نفس الكود بالظبط: **200 (35KB) من ساعة ⟶ 403 (908 بايت) دلوقتي**.
جدران الحماية بتسجّل سمعة IP. **مش عارف دلوقتي لو بيبسي أصلاً محجوب ولا أنا اللي حجبته علينا** —
والدليل الوحيد إنه رد 200 كامل مرتين قبل ما أكتّر. التأكد الوحيد: نسيبه يومين ونجرب **مرة واحدة**.
**قاعدة:** التشخيص على موقع خارجي = طلب واحد لكل فرضية، والتشغيلة الحقيقية هي الاختبار.

**السويت: 2184 ⟶ 2202 خضراء.**

## 8.11 — 2026-07-21: التاكسونومي بيخلط سؤالين (تحليل عميق، حل معماري)

**بدأ من:** ليه كوكاكولا مصر طلعت `services_b2c`؟ القياس عبر **كل** البروفايلات كشف إن ده عرض
لمرض في `BusinessCategory` نفسه، مش حالة واحدة.

**الجذر:** الـ19 تصنيف بيخلطوا **محورين متعامدين**:
- **السوق** (إيه بتبيع): restaurant · cafe · clinic · education · beauty · fitness · automotive …
- **نموذج العمل** (إزاي بتبيع): ecommerce · retail · services_b2c · services_b2b · agency …

حقل واحد مايقدرش يجاوب الاتنين، فبيتأرجح حسب اللي الموقع بيركّز عليه.

**الدليل المقيس (`outputs/*_profile.json`):**
- تحت `ecommerce` واحدة: **صيدلية أونلاين + بيت مجوهرات + براند مكملات + براند تجميل** — أربع أسواق
  مالهاش علاقة، فـ"مين منافسيّا" ممكن يقارن صيدلية بجواهرجي.
- **Raw African**: `beauty` في تشغيلات و`ecommerce` في غيرها — نفس البراند، نفس الموقع (**نتيجة غير
  ثابتة**).
- **كوكاكولا** = `services_b2c` (وهي FMCG مشروبات)؛ فودافون/أورانج نفس الشيء، وفودافون مرة
  `services_b2b`.

**ليه بيكسر كل حاجة:** المحورين **مطلوبين فعلاً** لمستهلكين مختلفين:
- السوق يحرّك: بذور الترند، `market_definition.query_seeds`، صلة الأقران، التموضع.
- النموذج يحرّك: `CATEGORY_TYPE_MAP` (أنواع Places)، توجيه `business_type.py`، نصف قطر البحث.
فأي إجابة يختارها الحقل، **المستهلك التاني بياخد مدخل غلط بصمت**. **12 موديول، 200+ استخدام.**

**حكم المالكة: "ممنوع الحلول المؤقتة"** — نفصل المحورين، مانرقّعش المصنِّف. + **السوق نص حر موثّق**
مش قايمة مغلقة (أي قايمة هتقصر عن أسواق حقيقية — D-8). + **إعادة تحليل الكل** بعد اكتمال التحويل.

**اتنفّذ (خطوة 1 من 4):** `business_profile/schemas.py`:
- `BusinessModel` enum مغلق صغير (ecommerce/local/service/hybrid/unknown) — المحور ده فعلاً محدود.
- `market_category` = `EvidencedField[str]` نص حر.
- `category` القديم **باقٍ** حتى يتحوّل كل مستهلك — مفيش حاجة تقع في النص.

**اتنفّذ (خطوة 2 من 4):** الاستخراج بيملا المحورين — برومبت الهوية بيسأل عن `market_category`
(نص حر: "handmade jewellery"/"liposomal supplements") و`business_model` (مغلق:
ecommerce/local/service/hybrid/unknown)؛ `IdentityResponse` + `ValidatedIdentity` بيحملوهم؛
`_merge_identity_axes` بيكتبهم على البروفايل (السوق EvidencedField، النموذج enum). موديل غير معروف
يُسقَط مايكسرش. **بق مسكه اختبار قايم:** الـ`default_factory` بتاعي بنى `ValidatedScalarField`
بدون `inferred` المطلوب — اتصلّح.

**اتنفّذ (خطوة 3 من 4):** المستهلكين اتحوّلوا للمحور الصح + الداشبورد:
- **النموذج**: `business_type.classify_business_type` بيقرا `business_model` صريح (ecommerce→ship،
  local→proximity، service→reach، hybrid→both)؛ غائب/unknown ⟶ قوايم `category` القديمة (توافق).
- **السوق**: `trends.keywords_from_profile` و`market_definition.query_seeds` يفضّلوا
  `market_category` النص الحر؛ `category` احتياطي. (نصف قطر البحث فضل على `category` — ده سؤال
  نموذج: geography-free ecommerce مقابل walk-in.)
- **الداشبورد** (`war_room._brand_chips`): شيب السوق (النص الحر) + شيب النموذج بكلمة بشرية
  ("متجر أونلاين"/"نشاط محلي")؛ unknown = مفيش شيب نموذج. برضه سطر brand-sub يفضّل السوق.

**متبقّي:** إعادة تحليل الـ22 بروفايل (حكم المالكة) عشان يتصنّفوا على المحورين.
**مسجّل في الذاكرة:** `category-conflates-market-and-model`.

**السويت: 2202 ⟶ 2207 خضراء.**

## 8.12 — 2026-07-25: تدقيق عدائي على الـSWOT — «يُنتَج · يُسلسَل · لا يُعرض»

المالكة طلبت «ظبط السوات والتأكد إن كل شيء بيرفكت» بعد إعادة بنائه على ورقة Pickton & Wright.
التدقيق العدائي طلّع **18 بند، منهم 3 حرجة، وأغلبها ارتدادات دخلت في نفس اليوم**.

**النمط الجامع، وهو نفس اللي اتصلّح للصور الصبح:**
> الحاجة بتتحسب صح · بتتخزّن صح · **وبتموت خطوة واحدة قبل الشاشة.**

`grep` على `frontend/index.html` رجّع `notes` = **0** و`site_readiness` = **0**. يعني كل نص
الإفصاح المكتوب بعناية بيتحط في حقل مالوش عارض، وبنود «جاهزية الموقع» اللي **اتنقلت** بحكم المالكة
كانت **بتختفي** عند العميل — النقل بقى حذف. `api/webapp_analysis._items` (نسخة تانية غير اللي في
`api/routes/swot.py`) كانت بتشحن ٥ حقول بلا `priority` وبلا `id`، فكل شغل الترتيب والأولوية
ماكانش بيوصل أصلاً.

**عيوب الدرجة نفسها (مقيسة، مش مستنتجة):**

| العيب | القياس |
|---|---|
| دليل أرفع = حكم أعلى | قرين واحد ⟶ `rank=1.00 high focus=True` · تلات أقران ⟶ `0.29 low False` |
| التفوّق العددي بيتحذف | `50` مقابل `{1,2}` ⟶ `drop=True` بقاعدة Kotler |
| `whitespace` بتتحذف **حتمياً** | انتشار 0 + وسيط 0 ⟹ مصفوفة فرص Kotler عمرها ما ولعت |
| `probability = importance` | محورين معلَنين، واحد مقيس |
| الأثر بيقصّ الفارق عند صفر | تقدّم 25 ضعف وتعادل تام بيدّوا `low` الاتنين |

**قرارات معمارية:**
- **الازدواج اتشال (حكم المالكة «واحد بس»)** — فجوة الموقع كانت بتشحن ضعف داخلي **و**تهديد خارجي
  بنفس الدرجة، وTOWS بيجوّز البند بنفسه (`WT [W1,T1]`). التهديد المشتق اتشال، **ومدح المنافس في
  تعليقات العملاء بقى يروح خانة التهديدات** بدل `notes` — دليل خارجي حقيقي بدل حقيقة داخلية بلافتة
  تانية. المبرر القديم («وإلا خانة التهديدات هتفضل فاضية للإيكومرس») بقى غير صحيح بعد شغل التعليقات.
- **الإفصاح بدل الإنفاذ لبوابة الليدجر (P-24)** — `strict_subject_gate` بتعلن المود، والـSWOT
  بيكتب إن بوابة التفضيل الدلالية غير مُفعَّلة. **شراء حَكَم لكل تشغيلة قرار صرف للمالكة، مش قرار
  يتاخد بالسكوت.**
- **«غايب ≠ خطأ»** — بروفايل قديم بلا `readiness` مابيتّهمش بعدم الجاهزية (P-11).

**درسان جديدان يتضافوا للقسم 8:**
1. **اختبار على محطة وسيطة بيعدّي والثغرة موجودة.** ١٣ اختبار كانوا بينادوا `score_gap` مباشرة،
   وكلهم أخضر، والأولوية عمرها ما وصلت عميل. الاختبار الجديد بيبدأ من `synthesize_swot` وبيـassert
   على `_swot_to_frontend`.
2. **الاختبار الثابت على الملف ما بيغنيش عن تشغيل الكود الحقيقي.** اختبارات الفرونت (فحص نصّي على
   `index.html`) كلها عدّت — وتشغيل دوال العرض نفسها على بايلود nanocare الحقيقي قبض إن البنود
   الأربعة بتتعرض بأساس «ادعاء البراند عن نفسه» تحت عنوان *"No clear call-to-action on the page"*.
   **ولا براند بيعلن عن الـCTA الناقص بتاعه.** فئة الفشل #6 بعينها.

**السويت: 2531 ⟶ 2582 خضراء** (51 اختبار جديد). صفر صرف، صفر ريل.

## 8.13 — 2026-07-26: الملف المتسلَّم نفسه (مظروف المنصة · الجهارة · حكم واحد · أودت الويب)

البوستر اتصلّح من الجذر في نفس الجلسة (مراجع الستايل = إعلانات جاهزة، تركيب المنتج، الحبر
المقاس، بوابات النص). فضل **الملف اللي بيوصل العميل**.

### القياس على آخر ريل اتسلّم (`abuauf_agency_v2.mp4`)

| الحقل | المطلوب | اللي اتسلّم |
|---|---|---|
| الأبعاد | 1080×1920 | **772×1372** |
| pix_fmt | yuv420p | **yuvj420p** (JPEG full-range) |
| color_range / space | tv / bt709 | **pc / bt470bg** (PAL متقاعد) |
| primaries / trc | bt709 | **unknown** |
| بت-ريت الفيديو | ≥ 6 Mbps | **1.65 Mbps** |
| الصوت | 48 kHz | **44.1 kHz** |
| الجهارة | −14 LUFS | **−28.3** (هيدروم فاضي 14.7 dB) |

ملف موسوم full-range بـtransfer غير معرّف **المنصة بتقراه limited** ⟶ الأسود بيتهرس على ريل
متوسط إضاءته 72.5/255. بحث في الريبو كله: **ولا موقع ترميز من الستة** بيحط وسم لون أو `-b:v`.

### الحل (٤ بنود، كل واحد اتربط بالداشبورد قبل اللي بعده)

1. **`reel/encode_profile.py` — تمريرة تسليم واحدة، مش ٦ مواقع مرقّعة.** `finalize_delivery()`
   بتعيد الترميز مرة واحدة بعد الاند كارد **وبعدين بتقيس الملف اللي كتبته** — مش الأعلام اللي
   بعتتها. فشل الترميز بيسيب الأصل مكانه ويقول.
2. **`check_audio()` — جهارة حقيقية بـ`ebur128`** (integrated / true peak / LRA) + صمت البداية
   والنهاية. `loudnorm=I=-14:TP=-1.5:LRA=11` راكب نفس التمريرة (سطر واحد، صفر تكلفة).
3. **`reel/verdicts.py` — `gate()` بترفض النجاح الفاضي.** `value=True` بلا `observed` بيبقى
   **`null`**، ورأي VLM لوحده مايعديش بوابة أمان. ده بالظبط شكل الحقل اللي كان بيقول
   `faces_intact_across_motion: true` على لقطات فيها **0/65 فريم فيهم وش**.
   `artifact_verdict()` بتلمّ كل البوابات في **قرار واحد حاجب**؛ كلهم `null` = **unverified**.
4. **`api/routes/reel.py` بقى ينادي `build_reel_audit`/`write_reel_audit`** زي الـCLI — مسار
   الويب **عمره ما ناداهم**. ريلز الداشبورد بتطلّع `.audit.json`، وسطر الميتا بيعرض الحكم
   **بالاسم** (`blocked` / `ماتقاسش`) بدل `screening` اللي بيبص للشرائط السودا والصمت بس.

**الدرس:** *«الغياب بيتقري نجاح»* تاني — بس المرة دي في الحاوية: كل موقع ترميز كان بيسيب وسم
اللون فاضي، وffmpeg بيملاه بافتراض، والافتراض بيتسلّم كأنه قرار.

**السويت: 2960 ⟶ 2981 خضراء** (21 اختبار، RED-first). صفر صرف.

## 8.14 — 2026-07-26: الور روم اتشالت من الوجود (بروداكت موازي كامل)

**أمر المالكة:** «احنا شغالين ع داش بورد webapp … localhost:8000 / ور روم دا الغيها من الوجود تماما».

### اللي اتقاس قبل المسح

| السؤال | الإجابة المقاسة |
|---|---|
| مين بيستورد `dashboard/*` من الكود الإنتاجي؟ | **صفر.** ولا ملف بره الحزمة نفسها — لا `api/` ولا `reel/` ولا `poster/` |
| حجم الحزمة | 10 موديولات، ~490 KB (`war_room.py` وحدها 113 KB، `build.py` 87 KB، `server.py` 81 KB) |
| اختبارات مربوطة بيها | **38 ملف كامل** (199 اختبار) + **15 اختبار** متفرقين في 9 ملفات مختلطة |
| بورت | 8770 (stdlib `http.server`)، مستقل تمامًا عن FastAPI بتاع الويب آب |

يعني كان **بروداكت تاني كامل**: سيرفره وHTML بتاعه وSWOT بتاعه ومنطق منتجاته — بيتبني وبيتصان
جنب الويب آب من غير ما يشوفه، والمالكة بتفتح الويب آب. **نص الشغل كان بيتعمل مرتين.**

### اللي اتشال واللي اتحافظ عليه

- **اتشال:** `dashboard/` بالكامل + 38 ملف اختبار.
- **اتحافظ عليه بإعادة توجيه مش بالمسح:** `test_env_is_loaded_on_every_entrypoint` كان بيتأكد إن
  `dashboard.server` بيقرا `.env`؛ اتحوّل لـ`api.main` — نفس الحارس على السيرفر اللي فعلاً بيشتغل.
- **اتراجع واتأكد إنه مش خسارة:** `_deck_currency` (حارس D-8 للعملة) كان دالة عرض في الديك نفسه؛
  الحارس الحقيقي فوق في `media_plan_v2` ولسه موجود (`test_media_plan_v2_reality_check`).

**السويت: 2981 ⟶ 2761 خضراء** (220 اختبار اتشالوا مع الكود اللي كانوا بيختبروه، صفر فشل متبقّي).

## 8.15 — 2026-07-28: المشهد بيتصوّر عشان يستقبل المنتج (قرار وضع واحد بدل ثابت)

**الشكوى:** «انا بستخدم نانو بانانا اللي هو شاطر في كدا ولما بستخدمه من جوجل بطلع نتايج تحفه يبقا
المشكله من عندك» — ثم، بعد اعتراض البوستر: «لو الموديل اللي رسم بيبوّظه، وفي نفس الوقت اللزق
بيبوّظ الباك جراوند اللي اتعمل ليها جينيريشن».

### اللي اتقاس (رن حي، ٣ محاولات، اعتراض ٣ من ٣)

| المحاولة | حكم الناقد |
|---|---|
| 1 | *"The brand logo is garbled and the **product boxes are floating illogically**"* |
| 2 | *"The **product boxes are floating illogically** in the scene"* |
| best | *"a **fabricated product**"* + `2 products in an ad for one named product` |

واتشحن مع `invented_product=True` متسجّل.

### السبب الجذري: نُصّين أعميين

اتقاس باعتراض كل نداءات الصور (`calls.json`): التلاتة كلهم `has_logo: False, n_product_images: 0`.
والمقارنة بين الأصل والمشهد كشفت تناقض هندسي:

| | الباكشوت `_nano_probe/11.jpg` | المشهد المتولّد |
|---|---|---|
| الكاميرا | من فوق لتحت ~45° | مستوى العين |
| الإضاءة | ستوديو مسطحة بلا ظل | نافذة اتجاهية من الشمال |
| القاعدة | علبتين مايلين، بلا سطح تلامس | أرضية في التلت السفلي |
| العدد | **٢** | إعلان لمنتج **واحد** |

باكشوت متصوّر من فوق **مستحيل هندسياً** يقف على أرضية متصوّرة من مستوى العين. الموديول نفسه كان
عارف المبدأ («ظل تحت قاعدة في الهوا هو ظل في الهوا») بس عالجه بتحريك الزون بدل قياس الزاوية.

### العلاج: القرار قبل التوليد

`DEFAULT_ZONE = (0.54, 0.50, 0.34)` — ثابت اتاخد مرة لبراند واحد واتطبّق على كل منتج بأي شكل في
أي لايوت — **اتشال**. محله:

1. **`poster/product_geometry.py`** (جديد) — نداء رؤية واحد مكاش بـsha1: زاوية الكاميرا · اتجاه
   الضوء · عدد الوحدات · وجود قاعدة · شكل العبوة. `checked=False` = **مش متقاس** (الحالة التالتة).
2. **`poster/product_placement.py`** (جديد) — القرار: `mode` (flat-lay / standing / held / hero)
   + zone + حجم، مشتقين من هندسة الأصل **ومن الكوبي** (ماينفعش يتقاطع مع `text_box`). سقف
   `MAX_HEIGHT_FRAC = 0.34` بيمنع عيب الـ٤٤% المقاس.
3. **`_product_scene_guard`** — البرومبت بقى يطلب الكاميرا والضوء من القرار بدل «سطح في التلت
   السفلي» دايماً. لو مفيش قياس، القاعدة العامة بتفضل (أضعف من مشتقة، أحسن بكتير من ولا حاجة).
4. **`placement.confirm_product_zone`** — بيقيس السطح اللي اترندر فعلاً (تدرّج أفقي) ويزحلق
   القاعدة عليه؛ ملقاش ⟵ يرجّع القرار كما هو.
5. **`product_composite`** — ظل إسقاط اتجاهي (`_estimate_light` من تدرّج سطوع الوجهة) + مجانسة
   لمعان/حرارة **مسقوفة** (12% / 6%) بترفض وتسجّل لو التصحيح هيطلّع أحمر البراند بره الصح.
6. **`rank_candidates`** — الباكشوت وحيد الوحدة بيسبق (بيقتل `2 products in an ad`).

**الليبل لسه بيتلزق مش بيترسم.** الـA/B (`test_the_product_label_is_not_banned.py`) قاس إن الموديل
حتى مع حماية الليبل غلّط حرف عربي («جديد» بدل «حديد») — لمكمل غذائي دي مشكلة علامة تجارية.

**+ إصلاح جانبي:** `_composite_after` / `_primary_product_img` كانوا بيتعرّفوا جوّه فرع `else` بس
وبيتقروا بره — `no_image=True` كان بيرمي `NameError` من أول محاولة. اتربطوا قبل الفرع.

**السويت: 2931 ⟶ 2969 خضراء.**

## 8.16 — 2026-07-28: الهجين — إضاءة الموديل + حروف البراند (الاستخدام بيحدد الطريقة)

**قاعدة المالكة الإنتاجية:** «بنخلي الـ AI يولد المشهد والعلبة عشان ناخُد منه الإضاءة، الظلال،
والزاوية الـ 3D التحفة. في المونتاج بنجيب التصميم الأصلي عالي الجودة للعلبة ونعمله إسقاط
(Perspective Warp) فوق العلبة اللي الـ AI رسمها».

### الموقفين القدام كانوا مقيسين، وكل واحد قاتل لوحده

| الطريقة | اللي اتقاس |
|---|---|
| الموديل يرسم | `test_the_product_label_is_not_banned` — العلبة مظبوطة والحرف العربي غلط («جديد» بدل «حديد»). لمكمل مسجّل دي مشكلة علامة تجارية |
| اللزق | ٣ رنّات ورا بعض: *"floating illogically"* ثم *"a flat graphic floating illogically over the scene, not placed within it"* |

### `poster/label_reprojection.py` (جديد)

الموديل بيرسم المنتج (الباكشوت **بيتبعتله**)، وبعدين الفن الحقيقي بيتسقط على العلبة اللي رسمها:

1. **إيجاد الوش بالدليل مش بالتخمين** — SIFT + RANSAC بين الباكشوت والعلبة المرسومة، بيرجّع
   **عدد نقاط متفقة** = ثقة مقاسة. أول قياس: `B_label_protected` (ليبل محمي) **33/97 = 34% اتلقت**
   مقابل `A_current_rules` (ليبل ممنوع) **25% اترفضت**.
2. **وحدة واحدة** — القناع = سيلويت المنتج ∩ مستطيل النقاط المتطابقة موسّع ١.٤٥×.
3. **مفيش حاجة بتطلع بره العلبة** — GrabCut بيقص على السيلويت اللي الموديل رسمه.
4. **الإضاءة من المشهد** — حقل ظلال من لمعان الوش (blur 22%)، السقف ±26%.
5. **جيتين إجباريين قبل أي وارب** (اتضافوا بعد عطل مقاس، تحت).
6. **بيتدهور مابيقعش** — مالقاش ⟵ المحاولة اللي بعدها ترجع للّزق. و`POSTER_PRODUCT_MODE=composite`
   بيرجّع اللين القديمة بالكامل.

**تبعيات جديدة (بإذن المالكة):** `numpy==2.4.6` · `opencv-python-headless==5.0.0`.

### الأعطال اللي ظهرت في التشغيل الحي — وكلها كانت «يُنتَج ولا يُفحَص»

| العطل | السبب الجذري | العلاج |
|---|---|---|
| البوستر اتحوّل لانفجار شعاعي على الكادر كله | homography شبه منحلّة **عدّت** لأن ٢٣/٧٣ نقطة اتفقوا. عدّاد النقاط مابيقولش إن التحويل ممكن فيزيائياً | `_homography_is_sane` — تجانس إشارة w (خط التلاشي)، نسبة المساحة، + `validate_quad` على الوش المسقط. **الاتنين كانوا مكتوبين ومااتنادوش** |
| الموديل رسم علبة زرقاء مخترعة رغم «الباكشوت مرفق» | `_image_bytes_from_url` بتقرا URLs بس؛ المسار المحلي بيرجّع None بصمت ⟵ **مفيش مرفق أصلاً** | الدالة بقت تقرا الملفات المحلية. السطر `[bg] nano-banana REAL PRODUCT (1)` مظهرش ولا مرة قبل الإصلاح |
| الفن الحقيقي اتلزق على **جسم تاني** | فولباك الرؤية «لقى» وش ليبل ٢.٣% على حاجة تانية — إجابة إحداثيات من غير error bar | الفولباك اتقفل افتراضياً (`POSTER_LABEL_VISION_FALLBACK=1` لتشغيله) |
| استنتاج غلط: «البرو هو الحل» | `gemini-3-pro-image-preview` بيرجّع **404** على المشروع ده (قياس مباشر ٢٠٢٦-٠٧-٢٨)، والسلسلة كانت بتنزل للـflash **بصمت** — فالـA/B اللي نسبته للبرو كان flash | التثبيت على البرو **اتشال**؛ `resolve_oneshot_model()` بيحترم `IMAGE_LANE`؛ والفيلأوفر بقى **بيعلن** أنهي موديل رسم فعلاً. الـdocstring اللي كان بيقول «pro SERVES» اتصحّح |

**ملحوظة على الخطأ ٤:** نص جوجل الكامل بيقول *"was not found or your project does not have
access to it… valid model name… available in the specified region"* — مفيش علاقة بخلاص الليمت
(ده بيرجع 429 RESOURCE_EXHAUSTED). الوحيد المقاس إنه بيخدم: `gemini-2.5-flash-image`.

### فجوة تانية اتقفلت (نفس الفئة)

مسار الـoneshot كان بيبلّغ الناقد البصري بـ`expect_real_products` و`logo_composited`؛ المسار
**الكلاسيكي** — اللي بيرندر أغلب البوسترات — مكانش بيبلّغه بأي منهم. فالناقد كان بيتسأل «هل ده منتج
مفبرك؟» عن صورة المنتج الحقيقي، و«هل حروف اللوجو سليمة؟» عن ملف اللوجو نفسه — والاتنين **حواجز
صارمة**، فبوستر سليم كان بيسقط.

### الحالة الأمينة دلوقتي

- **شغّال ومقاس:** معمار الوضع (الكاميرا تطابق الباكشوت · الحجم مشتق · مفيش تعويم)، الوارب نفسه
  ركن-بركن، الجيتات بترفض الفاسد، والتدهور للّزق.
- **حي على نانوكير:** الموديل بقى يرسم العلبة الحقيقية واقفة بظل تلامس ومنظور سليم ولون البراند صح.
- **لسه هامشي:** مطابقة SIFT بين **صورة** و**إعادة رسم** بالـAI — أحسن حالة ٣٤%، وعلى مشاهد
  الإنتاج ١٨–٣١%. آخر تحسين: قص الباكشوت على المنتج قبل المطابقة (`_crop_to_product`) عشان
  الوصفات ماتتصرفش على الخلفية والعلبة التانية. **لسه محتاج قياس.**

**السويت: 2969 ⟶ 3004+ خضراء.**

## 8.17 — 2026-07-28: الفيديو بقى Veo وحده (i2v فقط) — وكلينج اتشال

**أمر المالكة:** «اشتغل بـVeo مش كلينج، شيل أي أثر لكلينج» + «عايزه VEO من غير صور في الخلفية بس،
مجرد إنه بيحرك الصور اللي نانو بانانا بيديهاله».

**التكلفة اتقالت قدام القرار:** Veo بيحاسب **$0.40/ثانية** ⟵ ريل ٢٠ ثانية ≈ **$8 للرندر**، مقابل
سعر كلينج بالكليب. القرار اتاخد والرقم مذكور.

### اللي اتشال فعلاً

`KlingProvider` + `_kling_jwt` + الترانسبورت + الثوابت + كاتب الـledger (**٢٥٠ سطر**)، ملف اختباره
بالكامل، مفتاح `.env`، حارس الـconftest، وكل ذكر في الواجهة والاختبارات. الوحيد الباقي: **نص أمرك**
في docstring المُحلِّل كسجل.

### حاجتين كانوا موصولين على كلينج **بس** — لو اتشال من غيرهم كان هيبقى تراجع

| اللي كان معلّق عليه | لو اتشال كده |
|---|---|
| **عقد الحركة** — `apply_motion_contract` و`MOTION_NEGATIVE` كانوا متطبّقين في **مكان واحد**: جوّه كلاس كلينج | Veo كان بيتولّد **من غير أي عقد** — لا طلب كاميرا محمولة ولا منع للفريم الثابت. ودي قاعدة رقم ١ في البريف |
| **رفض توليد الفيديو من نص** | كلينج كان رافضه بالتصميم؛ Veo كان بيعمل العكس — **بينزل t2v تلقائي** لو الصورة ناقصة أو اترفضت |

الموديول `motion_contract.py` مكتوب في أول سطوره إنه موديول مش سترنج جوّه كلاس عشان **«العقد اللي
جوّه كلاس واحد هو عقد الموديل اللي بعده هيشحن من غيره»** — وبعدين وقع في نفس الحاجة بالظبط. حذّر من
نفسه وكان صح.

**العلاج:** النصف السالب راح لـ`_videos_config` (`negative_prompt` — حقل حقيقي في
`GenerateVideosConfig`، **متأكَّد منه بالفحص مش بالافتراض**)، والنصف الموجب راح لـ`VeoProvider.generate`.
و`MOTION_CFG_SCALE` **اتشال** — كان `cfg_scale` بتاع كلينج ومفيش مقابل ليه في Veo؛ ثابت محدش بيقراه
أوحش من عدم وجوده لأنه بيتقري كأنه تحكّم موجود.

**i2v فقط:** مفيش seed ⟵ **يرفض** برسالة واضحة. الـi2v اترفض ⟵ **يرفض**، مايرجعش t2v. الكولر عنده
أصلاً الصورة الأمينة ومسار Ken Burns، والاتنين مايتختاروش لو ده رجّع حاجة شكلها رندر ناجح.

### القياس

| الموديل | النتيجة (٢٠٢٦-٠٧-٢٨) |
|---|---|
| `veo-3.1-generate-001` | **بيخدم** — بدأ operation فعلاً |
| `veo-3.0-generate-001` | 404 NOT_FOUND |

**اختبارات جديدة:** `tests/test_the_video_stack_is_veo_only.py` — الكلاس اتشال، المُحلِّل مايوصلش له،
مفتاح بايت مايقدرش يخطف المسار (زي ما رانواي عمل ٢٠٢٦-٠٧-٠٥)، الرفض بدون seed، اختفاء الـt2v
fallback، وصول نصفَي العقد، والتدهور الآمن على SDK قديم.

**السويت: 3010 ⟶ 3006 خضراء** (٢٥ اختبار كلينج اتشالوا مع الكود، وضماناتهم اتنقلت على Veo).

**ملحوظة:** `runs/kling_spend.jsonl` (٥٠ صف) اتساب — ده سجل مالي تاريخي مش كود. قوليلي لو تحبي يتمسح.

## 8.18 — 2026-07-28: الاستمرارية بين المشاهد — بالبكسل مش بالوصف

**سؤال المالكة:** «هو بما إنه علشان يكون سيناريو مترابط، الأحسن i2v ولا t2v؟»

**الإجابة: i2v، ومش كتفضيل.** كل أدوات الترابط في السيستم بتشتغل على **الصورة الثابتة**: `look_lock`
(كاميرا وجريد واحدين للبراند) · `identity_ref` (نفس الوش المختار مع كل seed) · `place_ref_url`
(المكان الحقيقي) · الكمبوزيت والـQA قبل أي حركة. الـt2v بيرمي ده كله ويقول للموديل «تخيّل» — و«prose
loses to pixels» هو الدرس المقاس في الريبو ده أربع مرات.

**بس السؤال بيلمس فجوة حقيقية مش هي اللي بيسمّيها:** الترابط **جوّه** الكليب ≠ الاستمرارية **بين**
الكليبات. المشاهد كانت بتتبعت seeds مستقلة، فمشهد ١ ومشهد ٣ لقطتين شبه بعض مش لحظة واحدة مستمرة.

**اتقاس قبل الشغل:** `grep -rn last_frame reel/` رجّع **صفر**. ولا حاجة في الخط ده كانت بتنقل فريم
من مشهد للّي بعده.

### `reel/continuity.py` (جديد)

آخر فريم من مشهد N بيبقى seed لمشهد N+1 ⟵ اللحظة تكمل بالبكسل. وترتيب الأولوية:

1. **صورة المشهد الحقيقية** — مابتتزاحش أبداً
2. فريم المشهد السابق — لما المشهد ده يكمّل نفس اللحظة
3. الـstill المتولّد بتاع المشهد
4. فريم المشهد السابق — لما المشهد مالوش seed خالص

### حاجتين الموديول رافض يعملهم

| الرفض | ليه |
|---|---|
| **مابيزاحش صورة حقيقية** | وعد التأريض إن كل مشهد من مكان البراند الحقيقي. فريم المشهد السابق صورة **متولّدة**؛ لو زاحت الحقيقية بنكون اشترينا النعومة بالحقيقة |
| **مابيسلسلش من غير حد** (`MAX_CHAIN = 2`) | كل حلقة بتبعد جيل عن التأريض، فالانحراف **بيتراكم**. في الحلقة الخامسة الريل بيبقى ٤ أجيال من المكان الحقيقي ومحدش قال. ودي كمان طريقة تصوير الاستمرارية الفعلية: تمسك لقطة، وبعدين ترجع للكادر المؤسِّس |

**قطع مقصود مابيتسلسلش:** الـintro والـoutro (bookends)، ومشهد المنتج (إكمال حركة إيد على باكشوت
بيتقري كغلط مش كانسياب).

**مشتق مش معلَن:** «نفس اللحظة» = نفس `place_ref_url` + مش bookend + مش مشهد منتج. الحقل اللي
المخرج بيفتكر يظبطه اتترفض عمداً — الدرس متسجّل في
`test_the_reel_product_is_pasted_not_drawn`: «العلاج اللي بيعتمد على إن الموديل يفتكر يشغّله مش علاج».

**الفريم بيتاخد من الكليب الخالي من النص** — لو اتاخد بعد الكابشنز، هيدخل الهيدلاين بتاع المشهد
اللي فات جوّه **عالم** المشهد الجديد.

**السويت: 3006 ⟶ 3022 خضراء.**

**لسه متشغّلش:** `last_frame` في Veo (هدف نهاية المشهد، بيتحكم في القوس جوّه الكليب الواحد)
و`reference_images` و`seed` — تلاتتهم متاحين في `GenerateVideosConfig` ومستخدمين صفر مرة.

## 8.19 — 2026-07-28: خمس خصائص بتفرّق السيناريو عن الـspec sheet

**الطلب:** نقد مخرج إبداعي لريلاتنا. النتيجة المزعجة: **كل فحص البوابة بتعمله كان ناجح، والريل
لسه بيتقري كتسلسل آلي**.

### تصحيحان — مسجَّلان لأن التفكير كان غلط الأول

| الادّعاء | الحقيقة |
|---|---|
| «القوس السردي غايب من الإنتاج» | الخطتين اللي فحصتهم **أقدم من الحقول نفسها** (خطط ١٢ و٢٠ يوليو · الحقول اتضافت ٢٤ يوليو) |
| «البوابة بتتخطى نفسها لو الخطة فاضية» | `plan_reel_gated` بينادي `require_strategy=True` — الاستراتيجية **إجبارية** على أي خطة جديدة. والشرط مقصود: الخطط المعتمدة قبل العقد تتلَنت استشارياً بدل ما تتدان بأثر رجعي |

### الفجوات الحقيقية — مقيسة على خطط إنتاج

```
أطوال المشاهد   2.5 · 3.5 · 3.0 · 3.0 · 2.5 · 3.0
                2.0 · 3.0 · 3.0 · 3.0 · 3.0 · 3.0     ← مترونوم
المشهد الأخير    الكابشن والصوت **نفس النص حرفياً**
الكابشنز        ٥ من ٦، و٦ من ٦ — الصورة عمرها ما بتتكلم لوحدها
بريف مشهد ٦     "a clean, static hero shot" — والـmotion contract بيمنعه عند الرندر
```

### `_craft_checks` في `reel/coherence.py`

| الفحص | القاعدة |
|---|---|
| **رسالة واحدة للحظة** | كابشن = صوت ⟵ قناة محروقة. **الـCTA مستثنى** — على البيت الختامي التكرار مقصود، والقاعدة دي أقدم من الفحص (`test_cta_scene_caption_may_match_its_voiceover`) والفحص كان غلط لما تجاهلها |
| **مساحة تنفّس** | كل المشاهد عليها كابشن ⟵ الصورة مالهاش لحظة |
| **البيت الأول مشكلة** | القوس يفتح على الحل ⟵ مفيش تاني فصل يستحق. **قائمة عربي/إنجليزي** — قائمة إنجليزي بس كانت هتوقع كل خطة عربية |
| **الإيقاع** | أقصر مشهد > ٦٠٪ من أطول واحد ⟵ مترونوم. القطع الميكرو جوّه المشهد مابينقذش رتم مسطّح |
| **البريف مايحاربش الرندرر** | مشهد مكتوب static/locked-off ⟵ رفض عند التخطيط مش عند الرندر |

### باج غبي مني، وله حارس دايم دلوقتي

كتبت الريجيكس من خلال heredoc، فالـ`` اتحوّل لحرف **backspace حقيقي (0x08)** جوّه النمط. النتيجة:
الفحصين محتاجين حرف تحكّم قبل أول كلمة، **فمستحيل يطابقوا أي حاجة** — وكل فحص كان بيعدّي بصمت.
أسوأ فشل ممكن لبوابة: حيّة وشغّالة وعمياء. `test_no_pattern_carries_a_literal_control_character`
بيفحص **كل** نمط في الموديول دلوقتي.

وفجوتين تانيتين الاختبارات كشفتهم: `static hero shot` (الصفة والاسم مش ملزوقين) وكلمات التوتر
العربية.

**السويت: 3022 ⟶ 3046 خضراء.**

## 8.20 — 2026-07-28: الـDNA ليه جمهورين واتكتب لواحد

**الطلب:** مراجعة ذاتية لكل البرومبتات.

### اتقاس على برومبت حقيقي متلقّط (مش قالب)

`outputs/_nano_handoff/calls.json` — برومبت بوستر واحد، **٦٤٩٨ حرف / ١٠٢٩ كلمة**:

- **٣٩ جملة قاعدة** ⟵ قاعدة كل **٢٦ كلمة**
- تلات أسماء **مطلوبة وممنوعة في نفس البرومبت**:

| بيقوله | وبعدين بيقوله |
|---|---|
| «Place the 'NanoCare' logo top-center» | «Do NOT draw, letter or imply the mark» |
| «Assign one bold accent color to each product» | «**DO NOT DRAW THE PRODUCT ITSELF**» |
| «a large circular graphic icon for the pack» | «leave its place EMPTY» |
| «the 'nano' dot pattern on the packaging» | «ABSOLUTELY NO logos anywhere» |

### السبب الجذري

البلوك اللي جايب العمود الشمال عنوانه `BRAND DESIGN LANGUAGE (**follow it faithfully**)`. ده **بريف
تصميم عبوة** — متعلّم صح من إبداعات البراند اللي فيها العبوات — و`_brand_dna_lines` كان بيلزقه
**حرفياً** في برومبت شغلته خلفية بلا نص والمنتج **غايب عمداً**.

الموديل اتداله شغلتين واتقاله الاتنين «faithful»، فحلّها بالطريقة الوحيدة اللي يقدر عليها: **رسم
عبوة**. وده نفس سلسلة الأعطال اللي شفناها طول اليوم — علبة زرقاء بأيقونة ذرّة مخترعة («create a
large, stylized, circular graphic icon for the center of the pack»)، ولافتات مشوّهة («place the logo
top-center»)، وانحراف لون («assign one single, bold accent color»).

### `brand/dna_scope.py` (جديد)

**الفصل بالجملة مش بالحقل**، لأن حقل واحد بيخلط الجمهورين:

> *"Composition: **Packaging follows a strict, symmetrical, grid-based composition. The focal point
> is the large, central circular icon.** Lifestyle product shots use a simple tabletop setup with
> the product in sharp focus."*

الجملتين الأولانيين لمصمم عبوة. التالتة بالظبط اللي مولّد المشهد محتاجه. رمي الحقل بيضيّع التالتة،
وإبقاؤه بيشحن الأولانيتين.

**على الـDNA الحقيقي: ١٤ جملة عبوة اتحجزت، والعالم المصوَّر فضل.**

**تشديدتان اتعلمتهم بالقياس:**
1. قائمة الإنقاذ الأولى كان فيها colour/tone/mood ⟵ أنقذت **٧ جمل تخطيط عبوة**. نسخ التصميم مشبعة
   بكلمات اللون؛ الكاميرا أو الشخص المصوَّر بس هما اللي بيعنوا صورة.
2. الجملة اللي **موضوعها** سطح تصميم بتسقط مهما قالت — «Packaging... uses no photography» كانت
   بتنجو بالكلمة اللي بتستثني بيها نفسها.

**D-8:** المفردات عن **أسطح التصميم والتخطيط**، مش عن سوق أو لغة أو فئة. أي براند اتعلّم DNA من
عبواته عنده المشكلة دي. نانوكير مكان الاكتشاف مش مكان السكن.

**مراجعة ذاتية للبرومبتات اللي كتبتها النهاردة:** `product_geometry` (قاعدة كل ٧٣ كلمة) ·
`label_reprojection` (كل ٤٠) · `scene_instruction` (كل ٤٨) — صفر أسماء متنازع عليها، صفر لوكيل مشفّر.

**السويت: 3046 ⟶ 3061 خضراء.**

## 8.21 — 2026-07-28: اللوجو طول الفيديو · الخاتمة من عالم الفيلم · واسمين مش معرَّفين

**أمر المالكة:** «عايزة لوجو البراند يبقى موجود طول الفيديو» + «الخاتمة تبقى مميزة بالجو بردو
بالألوان الطبيعية، متخليهوش أبيض لأنها خلصت كتير قبل كده».

### اللي اتقاس قبل أي تغيير

- `textlayer._scene_html:316` — `show_logo = ... and scene.kind in ("intro","outro","contact")`.
  يعني **نص الريل** — الجزء اللي الناس بتتفرج عليه فعلاً واللي بيتقص ويتعاد نشره — من غير براند خالص.
- `endcard._endcard_html` — أرضيتين بس، **الاتنين صناعيتين**: تدرّج فحمي أو **عاجي/أبيض**. العاجي هو
  النهاية البيضا اللي اتمنعت، ولا واحدة فيهم شافت فريم من الفيلم اللي بتقفله.

### العلاج

**اللوجو:** موجود على **كل** مشهد. اللي بيتغيّر بين البوكيند وجسم الريل هو **البروز مش الوجود** —
الجسم بيلبسه بهدوء (أصغر · شفافية ٧٢٪ · **من غير أنيميشن**)، لأن علامة بتظهر بحركة في كل قطع بتتقري
**ست لوجوهات** مش براند واحد.

**الخاتمة:** أرضيتها بقت **آخر فريم من الريل نفسه** — مضبّب لدرجة عدم التعرّف، مغمّق، ومصبوغ ناحية
لون البراند. التضبيب مقصود: الهدف لون الفيلم وضوءه، مش إعادة عرض مشهد المتفرج لسه شايفه — أي حاجة
مقروءة هتتقري كفريم متجمد الميكساج نسي يقصه. الفريم متاح أصلاً من سلسلة الاستمرارية (قسم 8.18).

### واسمين مستخدمين ومش معرَّفين — نفس الفئة، اتلقوا بمسح AST على `reel/` كله

| المكان | الأثر |
|---|---|
| `reel/endcard.py` | `logger` مستخدم **مرتين** ومش معرَّف ⟵ **الكود المكتوب عشان يشرح ضياع الخاتمة كان هو اللي بيخفي السبب**: `NameError` من جوّه المعالج |
| `reel/__main__.py:372,459` | `os.environ` على **قرار الشحن** و`os` مستورد محلياً باسم `_os` بس ⟵ **`REEL_ALLOW_BELOW_GRADE=1` عمره ما اشتغل**، والمسار بيقع بدل ما يقف بنظافة |

المسح شغّال على ٤٤ موديول، وكلهم بيستوردوا. ومعاه اختبار حارس بيتأكد إن أي `logger.x(` في الموديول
ليه `logger` فعلاً.

**السويت: 3061 ⟶ 3073 خضراء.**

## 8.22 — 2026-08-01: الريل بيولّد منتجه · البوستر بيلزقه (رن حي فرض القرار)

**أول رن Veo حي بعد إصلاح المصادقة.** النتيجة: الريل خلص وعدّى الفحص النهائي، بس **أغلبه Ken Burns
مش حركة Veo** — والسبب واحد ومقيس.

### اللي اتقاس

| | |
|---|---|
| صرف Vertex النهاردة | **$17.16** (منهم **$16 Veo** = ١٠ رندرات) |
| مشاهد نزلت Ken Burns | **٥** |
| مشاهد اتشالت | **٣** (1, 6, 7 — seed مش متاح) |

**حكم الـQA على المشهد ٠:**

> *"The product is not generated within the video scene; it is a **STATIC IMAGE CRUDELY
> OVERLAID** on top of unrelated footage."*

المشهد ده لوحده حرق **٣ رندرات Veo × $1.60 = $4.80** وهو بيفشل على نفس السبب، وبعدين نزل Ken Burns
برضه. والصرف الضايع ده **فضّى ميزانية الرن ($8) قبل ما المشاهد ٣ و٤ و٥ ياخدوا كليب أصلاً** — يعني
عيب في الدمج كلّف الريل معظم حركته.

وفي نفس الرن، **أحسن الكادرات كانت اللي الموديل رسمها**: علبة نانوكير على كاونتر مطبخ، أحمر البراند،
و«Nanocare Liposomal E» مقروء.

### القرار — قاعدة المالكة نفسها

> «لو بتعملي ريل فيديو، اعتمدي على التوليد بدون أي قلق، لأن حركة الإيد والإضاءة هتبطّل العجب…
> لكن لو بتطلعي بوستر ثابت عالي الدقة، ارجعي لطريقة القص واللزق»

**البوستر بيتكبّر ويتقرا؛ كليب ثانيتين لأ.** فـ`_product_asset` بقى يرجّع `None` لأي منتج مادي —
الموديل يرسمه. **الشاشة لسه بتتلزق**، ودي مش استثناء من القاعدة بل تطبيق ليها: واجهة تطبيق **حبر
دقيق هو نفسه المنتج**، الحركة مابتخفيهوش، ومفيش موديل فيديو بيرسم UI مقروء.

وخوف الليبل المشوّه اللي الملف ده اتكتب عشانه بقى **بيتمسك أبكر وأرخص**: بوابة الـseed بترفض العبوة
غير المقروءة **قبل أي صرف Veo** (اتقاس في نفس الرن أكتر من مرة).

### اللوجو — اتقاس على الفيديو مش على الكود

التغيير وصل فعلاً، بس التنفيذ كان غلط: `scale(.62)` طلع **بكسلات رمادية مش مقروءة**، و`top:64px`
وقّعه **جوّه الشريط الأسود المخبوز في الكليب**. بقى `scale(.82)` · `opacity:.9` · `top:150px`.
«موجود» لازم تعني **مقروء**، وإلا فهي زينة بتدّعي إنها براندينج.

**اختباران اتحدّثوا مش اتشالوا** — `test_the_reel_product_is_pasted_not_drawn` كان بيرمّز القاعدة
العكسية، والسبب مسجّل فيه دلوقتي.

**ملحوظة مفتوحة:** فيه **letterbox** أسود فوق وتحت في الريل. الكمبوزيتور بيعمل cover-fit صح
(`force_original_aspect_ratio=increase,crop`)، يبقى الشريط **مخبوز في الكليب نفسه** — لسه محتاج تشخيص.

**السويت: 3073 ⟶ 3078 خضراء.**

## 9. Where things live (quick map)

| Concern | Files |
|---|---|
| Crawl budget/links/sitemap | `scraper/config.py`, `scraper/crawler.py`, `scraper/sitemap.py`, `scraper/extractors/links.py` |
| Logo + palette | `scraper/extractors/visual.py`, `scraper/inline_svg_logo.py` |
| Profile extraction | `business_profile/llm/{prompts,extractor,validator,rag,embeddings}.py`, `business_profile/rules/*` |
| Ledger + compliance | `grounding/{ledger,audit,compliance}.py`, `poster/audit.py`, `strategy/audit.py`, `reel/grounding.py` |
| Discovery + SWOT + TOWS | `competitor/{router,discovery,web_discovery,lite_scrape,matrix,swot,tows,themes}.py` |
| نطاق الـDNA حسب المستهلك | `brand/dna_scope.py` — الفصل بالجملة: العالم المصوَّر يعدّي، تخطيط العبوة يتحجز (قسم 8.20) |
| Brand identity (vision) | `brand/{creative_dna,brand_book}.py` |
| Poster | `poster/{pipeline,concept,oneshot,art_director,variation,from_profile,template,vision_qa,brand_research}.py` |
| وضع المنتج في المشهد | `poster/{product_geometry,product_placement,product_composite,placement}.py` — قياس الأصل ⟵ القرار ⟵ برومبت المشهد + اللزق (قسم 8.15) |
| الهجين: حروف البراند على علبة الموديل | `poster/label_reprojection.py` — SIFT+RANSAC ⟵ homography ⟵ قص GrabCut ⟵ حقل إضاءة (قسم 8.16) |
| Reel | `reel/{generate,art_director,motion,text_overlay,textlayer,endcard,video_provider,image_quality,grounding}.py` |
| تسليم الريل + الأحكام | `reel/{encode_profile,verdicts,audit,final_screening}.py` (مظروف واحد · جهارة مقاسة · حكم واحد حاجب) |
| استمرارية المشاهد | `reel/continuity.py` — آخر فريم يسيب seed اللي بعده، محدود ومشتق (قسم 8.18) |
| فيديو الريل | `reel/video_provider.py` — **Veo 3.1 i2v فقط**، بعقد الحركة (قسم 8.17) |
| صوت الريل | `reel/{voices,sfx,voiceover}.py` (كتالوج ElevenLabs محقَّق + SFX متمتَّر) |
| Web | `api/routes/*`, `frontend/components/*` |
| ~~الداشبورد الموحّدة~~ | **اتمسحت 2026-07-26** — شوف قسم 8.14. الواجهة الوحيدة = `api/main.py` + `frontend/{landing,index}.html` |
| ذكاء الإعلانات | `ads_intel/{facts,config,relevance,live,pull_ledger,orchestrator,consumer}.py`, `ads_intel/providers/meta_ad_library.py` |
| الذكاء التسويقي | `marketing_intel/{extractor,from_ads,schemas}.py` (الموات الحرفي) |
| الخطة الاستراتيجية v2 | `media_plan_v2/{engine,reality_check,schemas}.py` (خلايا/خزنة/هجوم مضاد) |
| حزمة الإطلاق | `launch_bundle/*` (الآن تشحن v2 + test_cells.csv + شيت الكالندر) |

---

*This file is the single source of truth for the project — read it before acting here.*
