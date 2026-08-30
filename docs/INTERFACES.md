# INTERFACES.md — Phase 0 (v2.2 execution plan)

**Status: GATE 0 approved-with-amendments (2026-07-06). Presented for FINAL sign-off before Week 1.**
**Governing rule:** *the plan bends to the repo, never the reverse* (§9 — no speculative refactors of working code). **Stay read-only until this file is signed.**
**Companion:** `AGENT_EXECUTION_PLAN_v2.2.md` + `pipeline_v2_architecture.html` (owner's Downloads).

This document is the single reconciliation point between the plan's *assumptions* and the *actual* repo. Where they conflict, the **Decision Log (§D)** is authoritative and the plan text is treated as amended.

---

## §D — Decision Log (GATE 0 rulings)

| # | Flag (my Phase-0 finding) | RULING (owner) → implementation contract |
|---|---|---|
| **D-0/1 Layout** | plan's `src/schemas\|providers\|stages` does not exist; repo is flat per-domain | **`src/` is DEAD.** New units = new **top-level packages** on the existing convention (§F). Where a package already owns a concern, **EXTEND in place** — Discovery v2 evolves `competitor/`, no parallel discovery system. |
| **D-2 "Frozen" schema** | `BusinessProfile` is `schema_version 0.2.0`, plain BaseModel, not `frozen/strict` | **PD-2 = PROCESS freeze** (do not edit existing schema files; no new `BusinessProfile` fields per standing rule 3). **Do NOT** retrofit `frozen=True`/`ConfigDict(strict)` onto `BusinessProfile`. Plan label corrected: the contract is **`schema_version 0.2.0` as-is**. **PD-5 (`strict=True, extra="forbid"`) applies to NEW models only.** |
| **D-3 Evidence verification** | Ledger uses verbatim + unit-class + subject-judge; **never** SHA-256. Plan's `EvidenceRef{…sha256…}` doesn't match | The **Ledger's mechanism IS the verification standard.** New units **NEVER reimplement verification** — they call `EvidenceLedger.from_profile(...)` → `resolve_claim` / `audit_text` / `audit_fields` and **store the returned `Resolution` as the evidence ref.** Plan §5 amended: **`EvidenceRef` → wraps `Resolution{source_url, matched_quote, confidence, tier, source_type, …}`.** SHA-256 language moves to telemetry fingerprinting only (D-7). |
| **D-4 Closed loop** | variation is a param, not a gate-reentry function | **Confirmed as an OUTER orchestration loop:** `Decision → build_variation(new_seed) → generate_poster(profile, variation=…)/render_creative_reel(…) → in-pipeline gates → Launch-Bundle delta → human approval`. **No new re-entry API.** Documented as the loop contract (§I.4). |
| **D-5 LLM client** | reusable, but `extractor._GROUPS` is 4-group coupled | **REUSE the `Caller` protocol** — it is `response_model`-generic (the 4 groups are extractor-internal, not a `Caller` limit). New units define **their own response models** on the same `Caller`; **`MockCaller` powers hermetic tests.** No new client abstraction. |
| **D-6 Benchmark** | `urls.json`/`ground_truth.json` don't exist (runner expects them); no `expected_objective` | **Week 1 Day-1 verification task:** dry-run the runner; if the JSONs are truly absent, **generate both mechanically from `urls.md`**. Add the **`expected_objective` field to the GT schema now**; VALUES come from the human at W4. The `contact`/`contact_phone` naming mismatch is **logged as ticket T-1 — not fixed in Phase 0.** |
| **D-7 Telemetry** | no JSONL run-records today; cost lives only in `extraction_meta`; `job_id` is in-memory | **U7 confirmed Day-1.** New **`telemetry/`** package: `run_id` minted at pipeline entry; **JSONL per stage-call** `{run_id, stage, ts, latency_ms, input_hash, output_hash, model, tokens, cost_usd, decision, status}`. **Wrap/reuse** existing `StageEvent` + `LLMExtractionResult` aggregates — do not replace. **`input/output_hash` = SHA-256 of payloads (this is where hashing lives).** |
| **D-8 Universality** | plan's clinic pilot vs repo's universal rule | **OWNER DIRECTIVE, overrides plan §1:** the system is **universal** across verticals/languages/countries. Clinic brands are **test fixtures, not architecture.** `DiscoveryStrategy`, KPI target tables, and ABSA aspect taxonomies are **CONFIG keyed by the profile's universal category signal, always with a universal default**; the provider registry is config (a **provider-per-source is universal**; a **vertical hack in core logic is forbidden** — standing rule 5). Benchmark stays the **multi-vertical 14-URL grid**; objective labels span verticals. |
| **D-9 Reel scene-QA** | new `reel/scene_qa.py` is active | Acknowledged as an **ACTIVE output-quality gate**. Honest status kept: it does **NOT** close the *"reel not Ledger-gated"* ⚠ — that closes only via **transcription → Ledger audit (roadmap P1.1, out of v2.2 scope** unless slack appears). |

---

## §I — Interface map (real entrypoints new units CONSUME, never rebuild)

**1 · Brand Profile** — `business_profile.build.build_profile(source, caller) -> BusinessProfile` (JSON via `write_profile`). Every scalar is `EvidencedField[T]{value, evidence[], confidence, source_type}`; a validator **drops any value lacking evidence** (the real grounding contract). `schema_version="0.2.0"`. **UNTOUCHED (PD-2).**

**2 · SWOT + TOWS** — `competitor.swot.synthesize_swot(matrix, themes=…, unique_insights=…, profile=…, trends=…) -> SWOT`; `competitor.tows.build_tows(swot, caller, profile) -> TowsResult`. `SWOTItem{text, citation[], evidence, claim_strength∈{validated, directional_not_validated, internally_supported}}`. **U8c hooks the existing `themes=` param** (review-grounded S/W/O/T) — no new SWOT engine.

**3 · Evidence Ledger — GATE ① (UNTOUCHED)** — `grounding.ledger.EvidenceLedger.from_profile(profile, *, swot, research, deep_search, subject_judge)` → `.resolve_claim(text) -> Resolution|None`, `.audit_text(text) -> list[ClaimVerdict]`, `.audit_fields(fields) -> AuditReport` (`.export()` = claim→source trail; `grounding.audit.coverage_block()` scopes gated surfaces). `Resolution{source_url, confidence, source_type, matched_quote, tier, matched_lang, copy_lang}` — **this is the canonical evidence ref for all new units (D-3).**

**4 · Concept + Variation + Closed-loop contract** — `poster.concept.build_creative_concept(profile, *, caller, variation, enforce_grounding)`; `poster.variation.build_variation(seed) -> dict`; `reel.creative_director`. **Loop contract (D-4):** the orchestrator calls `build_variation(new_seed)` then re-invokes the existing render entrypoints with `variation=…`; grounding re-runs inside those calls; `launch_bundle` emits the delta for human approval. **No re-entry API to build.**

**5 · Renderers (bundle targets, UNTOUCHED)** — Poster `poster.pipeline.generate_poster(profile, *, caller, variation, engine, product_image, …) -> PosterGenResult` (+ `.audit.json`, `.compliance.json`); Reel `reel.creative.render_creative_reel(profile, brief, photos, *, provider, out_path, featured_product)`; scene-QA `reel.scene_qa.check_scene(...)` (ACTIVE, D-9); Calendar `strategy.builder.build_strategy(profile, caller, *, days, platforms, ledger)`. (The `dashboard.build.build_dashboard(...)` renderer was deleted with the War Room on 2026-07-26 — the webapp at `api.main:app` is the only front end.)

**6 · LLM client (REUSE, D-5)** — `business_profile.llm.caller.Caller.__call__(system, user, response_model: type[BaseModel], group_name="", images=None) -> (parsed, Usage)`. Impls: `GeminiCaller` (default, GCP), `OpenAICaller` (fallback), `MockCaller` (tests). `temperature=0`, `max_retries=1`. New units bring their own response models.

**7 · Benchmark + freeze gates** — `benchmark.runner.main()`; `benchmark.graders.grade_profile(profile, url_meta, ground_truth)`; 14-URL grid in `benchmark/urls.md`; freeze `threshold_check` (min_ready ≈0.786, min_avg_swot_critical ≈0.85, per-vertical ≥0.7). **Tickets T-1/T-2 (§T).**

**8 · Telemetry (today)** — cost per-run in `BusinessProfile.extraction_meta.llm_cost_usd`; `caller._cost_for()` already fixes the silent-cost bug (unknown model → WARN + 0.0). `api/jobs/runner.py` emits in-memory `StageEvent`s (duration only, non-LLM stages have no cost). **The structured JSONL run-record layer is net-new (D-7).**

---

## §F — Proposed file plan for the nine units (D-0/1)

**NEW top-level packages** (additive; PD-4 fixtures-first, PD-5 strict on NEW models only, each with `tests/` mirror):

```
telemetry/            U7   logger.py (run_id, JSONL append+flush crash-safe, SHA-256 payload hash),
                           stage.py (decorator/ctx-mgr wrapping existing entrypoints; reuses
                           StageEvent + LLMExtractionResult aggregates — does NOT replace them)
reviews/              U8a+U8c  schemas.py (Review[author_hash validator], AspectSentiment
                           [evidence_quote substring validator], ReviewIntelligenceReport),
                           providers/{base(Protocol),fixture,google_maps(Playwright+PD-10 raw cache),vezeeta},
                           absa.py (Arabic ABSA on Caller+MockCaller), taxonomy.py (CONFIG by category + default)
ads_intel/            U2   schemas.py (AdRecord, AdvertiserSignal, MarketStrategySignals),
                           providers/{base,fixture,apify_adlibrary,official_api_stub}, analyzer.py
                           (winner rule, dedup, cadence, gap finder)
social_intel/         U9   schemas.py (SocialSignal), providers/{base,fixture,ig_business_discovery,
                           youtube_api,snapshot_parser}, objections.py
media_plan/           U1   schemas.py (MetaObjective, Destination, KPITarget, CampaignObjective,
                           OpportunityHypothesis, MediaPlan, Persona, EvidenceRef=Resolution-wrapper),
                           builder.py (objective deduction on Caller + Ledger-verified rationale +
                           test-budget validator), config.py (KPI + channel weights CONFIG by category + default)
decision_engine/      U3   schemas.py (CampaignPolicy, DecisionKind, Decision, PerformanceSnapshot),
                           evaluate.py (PURE fn), simulator.py (3 scenarios), csv_import.py (same schema)
policy_linter/        U4   schemas.py (LintFinding), rules_ar.py, rules_en.py, linter.py (deterministic),
                           rewrite.py (LLM rewrite MUST re-enter Ledger + Linter — tested loop)
advisor/              U5   schemas.py (MissingInfoReport), advisor.py (confidence %, gaps, risks;
                           consumes discovery empty-flags)
launch_bundle/        U6   schemas.py (LaunchBundle, CampaignPlanJSON, ComplianceSheet[EvidenceCoverage],
                           PrereqChecklist), bundle.py (campaign_plan.json + ads_manager.xlsx[Ads-Manager
                           field order] + assets/ + compliance.json + approval.json[DRAFT]),
                           closed_loop.py (the D-4 orchestration loop)
fixtures/             PD-4  reviews/ ads/ social/ discovery/ performance/ (+ raw-HTML caches)
eval/                       absa_gold.jsonl (human-labeled, W3) + run_absa_eval.py (P/R/F1)
scripts/                    pull_reviews.py, pull_ads.py, pull_social.py, run_pipeline.py
                            (the ONLY network paths — PD-4/§10)
```

**EXTEND-IN-PLACE** (existing packages own the concern — no parallel systems):

```
competitor/    U8b: MarketDefinition projection + Discovery v2 (candidate pool, transparent
               score_points[reason+evidence, NO magic weights], found_via, LLM-as-selector/verifier,
               CompetitorSet, empty=VALID → advisor flag). SWOT v2 via existing synthesize_swot(themes=…).
benchmark/     add urls.json + ground_truth.json (generated from urls.md, W1D1) + expected_objective/
               expected_destination fields (values W4). Freeze-gate mechanism reused as-is.
grounding/     UNTOUCHED (Gate ①). Consumed via EvidenceLedger.from_profile(...).
business_profile/ (+ llm/)   UNTOUCHED (PD-2). Caller REUSED with per-unit response models.
poster/ reel/ strategy/ dashboard/   UNTOUCHED render targets; launch_bundle.closed_loop invokes them.
```

---

## §T — Tickets (logged in Phase 0, NOT fixed now)

- **T-1** — `benchmark/report.py` names the field `contact` in results but `contact_phone` as SWOT-critical in grading — a reporting naming mismatch. Log only; do not touch during Phase 0.
- **T-2** — PARTIAL (2026-07-07): `benchmark/urls.json` generated from `urls.md` (the 14-URL grid + SWOT-unlock thresholds) → the metadata-based STRUCTURAL grading (8 fields) + freeze-gate now run. `benchmark/ground_truth.json` is MINIMAL (doc-only) → the 3 fuzzy SWOT-critical fields (audience/value_propositions/tone_of_voice) stay UNGRADED until human-labeled per URL (do NOT fabricate — an invented ground truth makes the baseline lie). Still to add: `expected_objective`/`expected_destination` for U1 (values at W4).
- **T-3** — Non-LLM stages (rules/evidence-pack/validate/grounding/render) emit no cost/latency/hash today; the `telemetry/` wrap (D-7) closes this.
- **T-4** — LEARNING-PHASE guardrail (U3 Decision Engine). Meta's learning phase exits at ~50
  conversions per ad-set per 7-day window; before that, delivery is noise. U3's evaluate() must NOT
  emit a kill/scale Decision for an ad-set below that conversion count. This is a CONVERSION-COUNT
  guardrail, distinct from U1's `test_budget` 3×-CPL BUDGET floor (`media_plan.schemas` `_learning_floor`)
  — two thresholds, two homes. Implement in U3, not U1.
- **T-5** — T-2 UPDATE (2026-07-11): `expected_objective`/`expected_destination` LABELED (owner-ratified,
  all 14) → the U1 gate is LIVE and PASSING (93% objective, `python -m benchmark.grade_u1`). The 3 fuzzy
  SWOT-critical fields remain the open half of T-2.
- **T-6** — IMAGE-ONLY MENUS (OCR/vision extraction class). Some restaurants publish their menu as
  PHOTOGRAPHS (e.g. Kababgy-style menu scans) — no text, no prices in HTML; text extraction can never
  see them. Needs a vision pass: detect menu-like images in the scrape → OCR via the Gemini caller's
  `images=[(bytes,mime)]` path → priced offerings with image evidence refs. NOTE: verified NOT the cause
  for mcdonalds_eg/zooba (their first-party domains publish no prices at all — FIX-2 diagnosis,
  process.md 2026-07-11); this ticket is for the genuinely-photographed-menu class. Universal rule
  (menu-image detection), no vertical hack.
- **T-7** — THIN-CRAWL DEFECT (scraper budget vs JS-render latency). norshek: 2/165 discovered pages
  scraped ("Budget exceeded after 1 subpages, elapsed 62.1s") → single-page evidence drove the 0.50
  structural score + empty fields; almentor similar (0 offerings). Decouple the crawl budget from
  JS-render latency (or raise it for sitemap-rich sites). Separate from, and prerequisite to, the
  fuzzy-label pass giving meaningful numbers on those URLs. Also: `run_scrape`'s 300s subprocess
  timeout is too tight post-PSL for subdomain-rich sites (alameda needed ~6min).
- **T-8** — TTS DIALECT QUALITY (owner-ratified 2026-07-11). CURRENT LIVE PATH (verified): all
  voice-over resolves via `reel/voiceover._resolve_backend` auto → **Gemini TTS**
  (`gemini-2.5-flash-preview-tts`, voice Kore, auto-detects Arabic) whenever GCP creds exist;
  OpenAI `gpt-audio-1.5` is an optional backend behind OPENAI_API_KEY (owner may restore the key
  for it, §10); free keyless `edge` (native-Egyptian voice) is the final fallback — reels are
  NEVER silent. T-8 upgrade target: **instruction-driven, dialect-capable Gemini TTS on Vertex**
  (NOT classic Cloud TTS — MSA-only voices would flatten the Egyptian-Arabic emotive read),
  GATED on a dialect-quality LISTEN TEST of the current Kore voice vs gpt-audio-1.5 vs edge on
  the same Egyptian-Arabic lines. Does not jump the queue (after U7 wiring → MarketDefinition →
  U1 assembly → U8a).

---

## §N — On sign-off (Week 1)

Per the plan, first two items only, in order, each behind its GATE:
1. **Day 1 — `telemetry/` skeleton** (D-7): `run_id` + JSONL per stage-call, retrofitted onto the existing entrypoints above. GATE: one benchmark URL produces a complete `runs/<run_id>/telemetry.jsonl` incl. cost.
2. **Week 1 — U8a** review providers (`reviews/providers/…`, fixtures-first) + `MarketDefinition` projection in `competitor/`. GATE W1 per plan.

**Nothing in §F/§N is built until this file is signed.**
