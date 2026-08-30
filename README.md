# Universal AI Marketing Strategist

You give it the URL of a business. It reads the site, works out what the business
actually sells and who it competes with, and produces a campaign pack: posters, a
vertical video ad, a competitor SWOT, and a content calendar.

The constraint that makes it interesting: every factual claim in the finished creative —
a price, a phone number, a delivery time, a competitor comparison — has to trace back to
something the scraper actually read on a real page. The system exports that trail as an
audit sheet.

<p align="center">
  <img src="frontend/assets/showcase/01_nahdi_pharmacy_app.png" width="45%" alt="Poster generated for Nahdi Pharmacy">
  <img src="frontend/assets/showcase/02_volume_qatar_cafe.png" width="45%" alt="Poster generated for Volume Cafe, Qatar">
</p>

<p align="center"><sub>
Generated end to end from the brands' own websites. Arabic and English, brand palette and
logo taken from the real site, copy grounded in claims found on the pages.
</sub></p>

## A run, concretely

```bash
python -m scraper https://volumeqatar.com
```

Crawls the site with Playwright, respects robots.txt, and writes an evidence manifest:
text blocks with stable IDs, the logo and colour palette it identified, contact details,
forms, and a language breakdown. Nothing downstream re-fetches the site.

```bash
python -m business_profile scrapes/volumeqatar_com_<ts>/manifest.json
```

Turns the manifest into a structured profile — what the business sells, its positioning,
its offers. Every field carries a citation back to the block it came from.

```bash
python -m poster --profile <profile.json>
python -m reel   --profile <profile.json>
```

Produce the creative. The poster and reel pipelines pull copy from the profile, colours
and logo from the scraped brand identity, and refuse to render a claim that has no source.

## The hard part

A language model writes fluent copy and will happily invent a discount that doesn't
exist. That is a legal and commercial problem, not a quality problem, so the system
doesn't try to solve it by prompting more carefully.

Instead, output is split in two:

*Facts* — prices, offers, phone numbers, piece counts, competitor claims. These are
extracted deterministically from the scrape and validated. The model is allowed to help
*find* them, never to be their source.

*Design* — wording, imagery, layout, pacing, camera movement. Here the model is the
author, bounded only by the brand's identity and by the facts it is allowed to use.

`grounding/` enforces the split. A claim without a citation doesn't reach the render.

The same rule applies to imagery. When a reel shows a product, the packaging is
composited from the brand's own photography — moved, scaled, shadowed, never redrawn.
Generative video handles the room, the light and the people; it never touches a logo or
a line of Arabic. Fidelity is measured against the source file afterwards, not assumed.

## The reel pipeline

Generating video is the easy half. Deciding whether the result works as an advertisement
is harder, so the pipeline ends with a critic instead of a checksum.

`motion_contract.py`, `pacing.py`, `continuity.py` and `grade.py` turn a creative brief
into something a renderer can honour. `film_critic.py` then watches the finished cut and
judges it as advertising, and `revision.py` feeds those notes back into another pass —
the critic changes the film rather than only rejecting it. `final_screening.py` is the
last gate before delivery.

## Architecture

```
URL
 └── scraper/              Playwright crawl → ScrapeManifest
      └── business_profile/  rules + LLM + RAG + validator → BusinessProfile
           ├── grounding/       evidence ledger, audit, compliance sheet
           ├── competitor/      discovery → comparison matrix → SWOT → TOWS
           ├── brand/           palette, logo, tone → BrandBook
           ├── poster/          concept → render → QA gates
           ├── reel/            brief → shots → composite → critic → revision
           ├── strategy/        content calendar → campaign/ fan-out
           ├── media_plan/      objective deduction, budget shape
           ├── ads_intel/       Meta Ad Library, competitor live ads
           └── instagram_intelligence/  collection + engagement analytics
```

`api/` is a FastAPI service: authentication, a brand store with a claim flow, async jobs
with a server-sent-event progress stream, and it serves the frontend. `frontend/` is a
single static surface — an earlier build had a Next.js app and a separate dashboard, and
both were removed in favour of one.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env               # add credentials for the providers you intend to use
uvicorn api.main:app --reload --port 8000
```

Main endpoints: `POST /run` starts a pipeline job; `GET /jobs/{id}/stream` streams
progress; `GET /jobs/{id}/result` returns the artifacts. `api/routes/` also exposes
`auth`, `brands`, `analysis`, `generate`, `influencer` and `scrape`.

## Tests

```bash
pytest -q
```

3,702 tests, roughly 57k lines of test code against 77k lines of source. They are
hermetic by design: a `conftest` guard fails any test that opens a network connection, so
the suite can't quietly depend on a paid API or a third-party site being reachable. Live
runs are opt-in behind an environment flag.

The count is high because the failure mode this project cares about is a wrong claim
reaching a customer, and most of those bugs are only catchable at the seams — a gate that
passes when it should abstain, a threshold that drifts, an extractor that returns
something plausible and wrong. Those get a regression test each.

## Layout

| Path | Lines | |
|---|---:|---|
| `reel/` | 15.5k | video: brief, storyboard, compositing, critic chain |
| `poster/` | 11.5k | static creative and its QA gates |
| `scraper/` | 9.8k | crawl, extraction, robots, readiness scoring |
| `competitor/` | 8.1k | discovery, comparison matrix, SWOT/TOWS |
| `business_profile/` | 7.1k | profile synthesis and validation |
| `api/` | 6.6k | FastAPI service |
| `benchmark/` | 2.3k | measurement harnesses |
| `instagram_intelligence/` | 2.2k | collection and engagement analytics |
| `grounding/` | 2.1k | evidence ledger |

`process.md` carries the working history: architecture decisions, what is built, what
isn't, and the lessons behind specific thresholds. `info/` holds runbooks and state.

## Status

Actively being built, with the gaps tracked in `process.md §6` rather than smoothed over.
The media-buying layer — objective deduction, live competitor ad intelligence, kill-switch
governance — is partially built. Several extraction thresholds are calibrated against a
measured corpus and carry documented watch-items rather than being solved in general.

The system remediates ungrounded claims; it does not prevent them at the model level.
That distinction is the reason the audit trail exists.

## Built with

Python 3.11, Playwright, FastAPI, Google Vertex AI (Gemini, Imagen, Veo), ElevenLabs,
Pillow, OpenCV, FFmpeg, pytest.

---

© Asmaa Hagag
