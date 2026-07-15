# Spend ledger

Metered external spend this run. Hard caps enforced in code; this is the human-readable mirror.

## Caps (ratified)
- **Vertex AI (Veo video + Nano-Banana/gemini image renders): monthly cap enforced by `vertex_spend.py`.**
  Default **$50/month** (`VERTEX_MONTHLY_CAP_USD`); each render is metered (Veo by second, images per
  model tier) to `runs/vertex_spend.jsonl` and REFUSED before any network call once the month's metered
  total would breach the cap. WHY (2026-07-14): $228 of a $300 Vertex credit burned in ~4 days with NO
  local tracking — Veo reel renders (render3 rendered 3×) + image gen, all invisible. Text calls are
  cents and are not metered. Rates (`VEO_USD_PER_SEC=0.40`, `PRO_IMAGE_USD=0.12`, `NANO_IMAGE_USD=0.04`)
  are conservative ESTIMATES for the guard; the authoritative number is Cloud Billing. Check anytime:
  `python -m vertex_spend`.
- Apify (L4 acquisition provider): **$20/month HARD CAP** — enforced by `social_intel/spend_guard.py`.
- Meta Ad Library: free (official API); blocked on identity confirmation, not spend.
- Google Places (reviews/discovery): pay-per-call on the owner's key.
- **Diagnostic external fetches (probe/verify scripts): HARD RULE** — rate-capped + request
  ceiling, one lightweight request over a browser render where possible, logged here. The dev
  machine shares its IP with the owner's live browser; uncapped diagnostics poison her clicks.
  See memory `diagnostic-isolation-shared-ip`.

## Diagnostic external-fetch ledger

| when (UTC) | target | requests | cap applied | notes |
|-----------|--------|----------|-------------|-------|
| 2026-07-13 ~08:00–08:40 | topshoes.store, bobana-eg.com, rawafrican.net | **1000+ (UNCAPPED — the incident)** | none | probe/measure/categorize scripts during 429 diagnosis degraded shared-IP Cloudflare reputation → crawler 429 on all three while curl=200. THE lesson that created the rule above. |

## This run

| when (UTC) | source | action | est. cost | running total | notes |
|-----------|--------|--------|-----------|---------------|-------|
| _(none yet)_ | | | | | |
| 2026-07-13 ~19:16 | nti.sci.eg (client's own site) | 6 brand-asset GETs (logo + campus/lecture photos) for the reel — CAPPED (6, 1s apart) | free | — | grounds the NTI-brand rework of the guardian reel; real assets per owner ruling |
| 2026-07-13 ~19:25 | nti.sci.eg | 4 more building/lab GETs (real Smart Village HQ + labs) — CAPPED | free | — | owner: use the REAL main building, not the low-res one |
| 2026-07-13 ~19:47 | WaveSpeed (VEED lipsync) | lip-sync SPIKE: 7s audio (Karim still + VO snippet) — 2 uploads + 1 lipsync | ~$0.21 | ~$0.21 | SUCCESS — agency-grade mouth motion, identity preserved, no artifacts. Path confirmed for the testimonial reel. $0.15/5s of audio. |
| 2026-07-14 (autonomous closeout) | — | **ZERO paid ops.** No Veo/image renders, no scrapes, no LLM text gens. All work was hermetic code + deterministic tests (suite 1595). | $0.00 | — | Session per owner directive "no reel/poster gens". The launch-bundle CLI + all gates/linters/advisor run offline with no network/LLM. Nothing to meter. |
| 2026-07-15 ~defense-brief prep | Gemini 2.5 Pro (Vertex) | **UNINTENDED — `python -m benchmark.grade_u1`**: 12 live objective-deduction calls (1 per labelled URL with a cached profile) | **~$0.10 (est.)** | ~$0.10 | **My error, logged for honesty.** The brief said read-only/no paid calls; I ran grade_u1 believing it offline. It is NOT: `build_campaign_objective` is a live Gemini call (`benchmark/grade_u1.py:9`, `media_plan/builder.py`), and `grade_u1._load_env()` (line ~148) loads the repo-root `.env`, so creds were present even though my shell had none. Est. from _PRICING (in $1.25/1M, out $10/1M) × ~5k in + ~200 out per call; NOT metered in telemetry (grade_u1 does not open a RunTelemetry). Lesson: `grade_u1` is a PAID gate — it must not be described as offline. |
