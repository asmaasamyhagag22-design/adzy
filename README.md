# Universal AI Marketing Strategist

**Grounded in truth. Engineered for certainty.**

Give the system one business URL. It returns a complete, on-brand campaign pack —
poster, short-form vertical reel, competitor-grounded SWOT, and a multi-day content
calendar — where **every factual claim in customer-facing output is traceable to a
sourced piece of evidence**, exportable as an audit trail.

The distinction that shapes the whole codebase: this is not "AI that makes posters."
It is a system that produces agency-grade creative whose factual claims you can defend
in front of a client.

---

## The problem

Generative models are excellent at *form* and unreliable at *fact*. For marketing work
that is a commercial risk, not an academic one: an invented phone number, a fabricated
discount, a competitor claim that isn't true, or a product box whose packaging the model
quietly redrew — any of these can reach a customer.

Most tooling responds by asking the model to be more careful. This system responds
structurally instead.

## The core idea: two truth domains

Every output is split into two categories that are governed by completely different rules.

| | **FACTS** | **DESIGN** |
|---|---|---|
| Examples | offers, prices, phone numbers, piece counts, competitor claims | wording, imagery, layout, story, camera movement |
| Source | extracted deterministically from the real site, then validated | the model, freely |
| LLM's role | an extraction and verification tool — never a source of truth | the author |
| Failure mode if wrong | a false claim reaches a customer | a weaker ad |

The LLM designs. Deterministic code renders and validates. Facts must survive a
gate before they can appear anywhere.

### The Evidence Ledger

`grounding/` is the central gate. Every factual claim carries a citation back to a
scraped source block with a stable ID. Claims without evidence do not ship, and the
whole chain exports as a compliance sheet a client or a regulator can read.

A related discipline runs through the creative modules: **real brand assets are used as
pixels, never as prompts.** When a reel shows a product, the packaging is composited from
the brand's own official photography — position, scale, contact shadow and parallax only.
The generative model produces the room, the light and the people; it never redraws a logo,
Arabic lettering, or a printed box.

---

## Architecture

```
URL
 │
 ├─ scraper/              Playwright crawl, robots-aware, universal
 │                          └─ ScrapeManifest (text blocks with stable IDs,
 │                             visual identity, contacts, forms, language mix)
 │
 ├─ business_profile/     rules + LLM + RAG + validator
 │                          └─ BusinessProfile (every field evidence-cited)
 │
 ├─ grounding/            EVIDENCE LEDGER — the central gate, audit + compliance sheet
 │
 ├─ competitor/           router → Places/SERP → comparison matrix → SWOT → TOWS
 ├─ brand/                BrandCreativeDNA / BrandBook — identity understanding
 ├─ poster/               concept → variation → render → QA gates
 ├─ reel/                 story → stills → video → captions → end card
 ├─ strategy/             content calendar  →  campaign/  fan-out to creatives
 ├─ media_plan/           objective deduction, budget shape
 ├─ decision_engine/      kill-switch governance, explicit ABSTAIN states
 └─ trends/               keyless trend sources
 
api/        FastAPI job runner (:8000) — async jobs, SSE progress stream
frontend/   Next.js 15 (:3000)
benchmark/  measurement harnesses
tests/      1,629 tests
```

### Design rules the code actually enforces

- **Universality.** Works for any vertical, language, or country. No vertical-specific
  hacks — category-specific *guidance* selected by a universal signal is allowed; a
  hardcoded branch for "restaurants" is not.
- **Fail closed, and say so.** A check that cannot run returns an explicit unknown state,
  never a silent pass. `decision_engine/` has a dedicated `ABSTAIN_NO_TARGET` verdict
  because "I refuse to judge" and "keep spending" are different answers.
- **Measure before believing.** Behavioural claims in this repo are backed by a harness
  in `benchmark/`, not by intuition.
- **Spend is metered and capped.** `vertex_spend.py` enforces a per-run *and* a monthly
  ceiling on paid media generation, because a monthly cap alone cannot stop one runaway job.

---

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

Scrape a site into an evidence manifest:

```bash
python -m scraper https://example.com
```

Build a business profile from that manifest:

```bash
python -m business_profile scrapes/example_com_<timestamp>/manifest.json
```

Generate creative:

```bash
python -m poster  --profile <profile.json>
python -m reel    --profile <profile.json>
python -m strategy --profile <profile.json>
```

Run the API:

```bash
uvicorn api.main:app --reload --port 8000
```

| Endpoint | Purpose |
|---|---|
| `POST /run` | start a full pipeline job |
| `GET /jobs/{id}` | job status |
| `GET /jobs/{id}/stream` | server-sent progress events |
| `GET /jobs/{id}/result` | completed artifacts |
| `POST /poster/from-profile` · `POST /reel/from-profile` · `POST /swot/from-profile` | single-stage entry points |

---

## Testing

```bash
pytest -q
```

**1,629 tests / ~28,600 lines of test code against ~52,000 lines of source.**

Tests are hermetic by construction: a `conftest` network guard fails any test that
attempts a live call, so the suite cannot silently depend on a paid API or a third-party
site being up. Live runs are opt-in through an explicit environment flag.

---

## Repository map

| Path | Lines | Role |
|---|---:|---|
| `reel/` | 8.7k | vertical video: storyboard, compositing, typography, sound, QA gates |
| `scraper/` | 8.5k | Playwright crawl, extraction, robots, readiness scoring |
| `business_profile/` | 6.8k | profile synthesis + validation |
| `poster/` | 6.4k | static creative, render + QA |
| `competitor/` | 5.2k | discovery, comparison matrix, SWOT/TOWS |
| `dashboard/` | 3.7k | reporting surface |
| `benchmark/` | 2.2k | measurement harnesses |
| `grounding/` | 2.0k | Evidence Ledger, audit, compliance sheet |
| `api/` | 1.6k | FastAPI job runner |
| *others* | 4.6k | `brand/` `media_plan/` `trends/` `strategy/` `launch_bundle/` `social_intel/` `decision_engine/` `telemetry/` `policy_linter/` `ads_intel/` `campaign/` `creative/` `advisor/` |

`process.md` is the working source of truth: vision, architecture, built-feature
inventory, honest gaps, roadmap, and a log of measured lessons.

---

## Status and honest limitations

This is an active build, and the limitations are tracked in the open in `process.md §6`
rather than smoothed over:

- The **Intelligent Media Buying** layer (objective deduction, live competitor ad
  intelligence via the Meta Ad Library, kill-switch governance) is partially built —
  its status is tracked honestly rather than claimed as done.
- Some extraction thresholds are calibrated against a measured corpus and carry
  documented watch-items rather than being presented as universally solved.
- The system **remediates** ungrounded claims; it does not pretend to prevent them at
  the model level. That distinction is deliberate and is why the audit trail exists.

## Tech stack

Python 3.11 · Playwright · FastAPI · Next.js 15 · Google Vertex AI (Gemini, Imagen, Veo)
· ElevenLabs · Pillow / OpenCV / NumPy · FFmpeg · pytest

---

© Asmaa Hagag
