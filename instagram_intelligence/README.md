# Instagram Intelligence Collector — Phase 1

## What this phase does
Scrapes a public Instagram business profile (no login, by default) and produces
a validated, evidence-carrying JSON: profile info, posts, engagement stats,
media analytics, and sampled comments — with checkpointing so an interrupted
run can resume instead of restarting.

## What this phase deliberately does NOT do yet
Products/promotions/campaigns/topics/CTA detection, sentiment analysis, and
cross-competitor pattern mining are **not** built here. Reason: those layers
are exactly where fabrication risk is highest (the project's core "no
fabrication" principle), and they're much easier to validate against real
scraped JSON than to build speculatively. Once you run this against a couple
of real profiles, we build the NLP layer against actual captions/comments —
so we can check its output by hand instead of trusting it blindly.

## Setup
```bash
cd instagram_intelligence
pip install -r requirements.txt
```

## Run
This pipeline makes no industry, region, or business-type assumptions -
the same code path handles a sportswear brand, a skincare pharmacy, a
coffee chain, or a SaaS company identically. `<username>` below is just a
placeholder for whatever public Instagram business account you're pointing
it at.
```bash
python cli.py <target_business_username>
python cli.py <target_business_username> <competitor_username_1> <competitor_username_2>
```
Output lands in `instagram_intelligence_output/json/<username>.json`.

## Why the defaults are what they are
- **No-login mode by default.** Login mode doesn't meaningfully increase
  throughput for public accounts in 2026 (both modes converge around the
  same request ceiling) but does risk the logged-in account getting
  temporarily or permanently restricted. Only switch to `login` in
  `config/config.yaml` if you specifically need a private account.
- **30 posts/profile, comments sampled from only the top 8 by engagement.**
  Full comment scraping costs 2-5x more requests than everything else
  combined and adds little signal from low-engagement posts. This keeps a
  6-profile run (target + 5 competitors) under ~1.5 hours at the safe rate
  limit instead of multiple hours with a high chance of a mid-run block.
- **Every rate/volume knob is in `config/config.yaml`**, not hardcoded, so
  you can tune this if your actual demo environment turns out more or less
  forgiving than the general 2026 figures this was sized against.

## If you hit persistent 429s
The rate limiter already backs off exponentially and checkpoints progress.
If it still exhausts retries: increase `min_delay_seconds`/`max_delay_seconds`
in the config, reduce `posts_per_profile`, or reduce `max_competitors`. Do
not run multiple collector instances in parallel against the same IP.

## Roadmap (Phase 2+)
1. **NLP layer** (products, promotions, campaigns, topics, CTAs) — built as
   a hybrid: seed lexicons in `config/lexicons/` (starting points, not
   closed lists) expanded per-brand via embedding similarity, since a truly
   zero-seed "emergent" detector isn't realistic without labeled training
   data specific to your domain.
2. **Sentiment analysis** over `sample_comments`.
3. **Competitor Discovery bridge** — wiring this collector to your existing
   `competitive_swot_engine.py` discovery cascade (Google Maps → search →
   OSM → LLM) so competitor usernames feed in automatically instead of
   being typed manually on the CLI.
4. **Cross-competitor pattern detection** — the "winning vs. weak
   strategies" layer, built only once real engagement data exists to run
   the statistical comparisons against.
5. **PlaywrightProvider** as the fallback path when Instaloader breaks
   against an Instagram markup change (matches the priority cascade in the
   original spec).
