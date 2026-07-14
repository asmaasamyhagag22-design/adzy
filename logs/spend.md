# Spend ledger

Metered external spend this run. Hard caps enforced in code; this is the human-readable mirror.

## Caps (ratified)
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
