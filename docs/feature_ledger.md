# Feature Ledger — Baseera (Universal AI Marketing Strategist)

The honesty artifact required by the v2.2 System DoD (§6-W8 / §11): every claim about the system
carries a status, so nothing is oversold. Statuses:

- **Shipped & Gated** — built, wired where applicable, hermetic tests + a passing gate.
- **Simulated** — real logic, exercised on synthetic/imported data (no live account yet, by design).
- **Roadmap** — designed/partly built; blocked on owner credentials/data or not yet reached.
- **Rejected** — deliberately not built (policy/Prime-Directive decision).

| Unit / capability | Status | Notes |
|---|---|---|
| Scrape → Brand Profile (Schema v1.3) | **Shipped & Gated** | frozen; SHA-256 content hashing; the whole system is additive on top (PD-2). |
| Evidence Ledger — Gate ① | **Shipped & Gated** | `grounding/ledger.py`; the moat — every hard claim sourced or rejected/softened (PD-1/PD-3). |
| U1 Media Plan | **Shipped & Gated** | `media_plan/`; objective (exact Meta strings) + destination/KPI/persona/geo; learning-floor validator; U1 objective gate ~93%. Dashboard path (CLI wiring = roadmap). |
| U2 Ad Intelligence | **Roadmap** | `ads_intel/` built + tested; **blocked on the owner's Meta identity confirmation + `META_ADS_TOKEN`** for the live pull. Not yet wired into SWOT/plan. |
| U3 Decision Engine + Simulator | **Shipped & Gated (Simulated)** | `decision_engine/`; pure `evaluate()` ladder with the T-4 learning-phase guardrail; simulator winner/loser/fatigue + CSV import. No live ad account needed. |
| U4 Meta Policy Linter — Gate ② | **Shipped & Gated** | `policy_linter/`; deterministic ar+en detectors (personal-attributes, before/after, guaranteed-results, medical-claims); verdict recomputed in code; zero false-negatives on block rules. LLM `suggested_rewrite` re-gate = roadmap. |
| U5 Missing-Info & Confidence Advisor | **Shipped & Gated** | `advisor/`; concrete gaps/risks + per-axis grounding. NO composite confidence % (§10). |
| U6 Launch Bundle | **Shipped & Gated** | `launch_bundle/`; `campaign_plan.json` (Meta hierarchy, PAUSED), Ads-Manager import CSV, compliance sheet + policy-lint + advisor embedded, DRAFT `approval.json` with a content-bound token. xlsx export = roadmap (openpyxl not bundled → CSV interim). |
| Closed loop (governance) | **Shipped & Gated (Simulated)** | `launch_bundle/loop.py`; performance → decision → variation → **re-gate (Evidence Ledger + Policy Linter)** → fresh DRAFT bundle. Nothing publishes (PD-7). |
| U7 Ops / Telemetry | **Shipped & Gated** | `telemetry/`; per-stage JSONL incl. cost on LLM stages. Non-LLM-stage cost/latency = **roadmap (T-3)**. |
| U8a Review acquisition | **Shipped & Gated** | `reviews/`; fixtures-first providers; live Maps/Vezeeta pulls need owner data. |
| U8b MarketDefinition + Discovery v2 | **Shipped & Gated** | `competitor/market_definition.py`, discovery/registry/tier-match; anti-hallucination test permanent. |
| U8c Arabic ABSA → SWOT v2 | **Shipped & Gated** | `reviews/absa.py` → `competitor/swot.py`; verbatim-substring validator. ABSA gold labels (~100) = owner task. |
| U9 Social Organic Intelligence | **Roadmap** | `social_intel/`; IG Tier-1 live-validated; Apify Tier-2 behind the $20/mo cap (code-enforced: `social_intel/spend_guard.py:18`, called on the only network path `scripts/pull_facebook.py:30`, pinned by `tests/test_social_intel_facebook.py:78`); `snapshot_parser` (Tier-3) **shipped 2026-07-14 (`03c20ef`)** — stdlib parser + demo fixture, authors hashed at ingestion (PD-6); real FB/IG selectors still pending owner raw samples, and **still not wired into SWOT**. |
| Reel / poster creative + gates | **Shipped & Gated** | copy is Evidence-Ledger-gated; reel coherence gate + product compositing; imagery/Veo-extended audio still ungated (roadmap). |
| Vertex spend guard | **Shipped & Gated** | `vertex_spend.py`; meters + caps Veo + Nano-Banana image renders ($50/mo default). |
| Tier-4 autonomous FB/IG scraping | **Rejected** | PD-10 — forbidden; snapshots are human-provided inputs only. |
| Real ad publishing / Meta write API / real spend | **Rejected** | PD-7 — the system advises; a human executes. Bundle DRAFT→APPROVED is human-only. |

Owner-action register (credentials/labels/decisions that unblock Roadmap items) lives in `process.md`
+ `logs/decisions.md`. This ledger is regenerated as statuses change — it must never claim more than
the code proves.
