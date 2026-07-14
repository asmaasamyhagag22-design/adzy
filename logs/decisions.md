# Autonomous decisions — Client-Deliverable 12-hour run

Every choice made without the owner present. Safest evidence-honest default, rationale, and a
FLAG where the owner should review. Newest first.

## Mission operating defaults (start of run)

- **D-0.1 — Never fabricate to pass the rubric.** Where the rubric demands a number the
  pipeline cannot source (a KPI target, a budget benchmark), the honest line
  "يُعاير بعد أول أسبوعين تشغيل / calibrated after the first two weeks" is used and an advisor
  gap is raised. No invented targets, ever. FLAG: none — this is the standing law.
- **D-0.2 — Dashboard failure policy.** Client-facing UI shows only achievements + the honest
  "what we don't know yet" panel. Every failure degrades gracefully → `logs/` + advisor gaps,
  with a discreet "التفاصيل في سجل التشغيل" link. No red errors / stack traces / FAIL banners
  client-side. Failures are relocated, never deleted.
- **D-0.3 — Owner-gated actions are PREPARED, never executed.** No reel/poster EXECUTE (HITL
  law). No spend beyond the ratified caps. Competitor-registry identity changes are queued for
  owner approval, never auto-applied.
- **D-0.4 — Suite-green invariant.** Every commit leaves the full suite green, count read from
  the run's own output (not a pipe). Self-contained commits so a 12-hour interruption loses
  nothing.
- **D-0.5 — Priority order** P0→P3→P1→P4→P2→P5→P6→P7; execution defaults to phase order but a
  blocked subtask (missing creds / 404 model / API refusal) is logged, ticketed, SKIPPED, and
  the queue continues.

<!-- new decisions appended below -->

## Phase 3 — dashboard rebuild

- **D-3.1 — Browser-preview visual check unavailable.** `preview_start` on the local
  file:// render timed out (300s) with the studio dev server already bound to :8770.
  Per never-halt, I verify the dashboard STRUCTURALLY (render succeeds + grep the composed
  copy in the HTML) instead of by screenshot, and rely on the deliberately-authored CSS for
  the design bar. FLAG: owner may want a screenshot pass in an interactive session; the
  rendered HTML files are in the scratchpad for direct opening.

- **D-3.2 — RTL applied without screenshot verification.** Set `<html lang="ar" dir="rtl">` on
  the standalone deliverable (Arabic-primary content) + an Arabic-capable font stack (Segoe UI /
  Sakkal Majalla). New sections use logical CSS props so they mirror cleanly; older flex/grid
  sections reflow via bidi. Structural verify passed (dir attr + cleaned citations + 0 raw
  leaks); a pixel-level RTL check needs the owner's interactive screenshot pass (browser preview
  was unavailable, D-3.1). FLAG: if any older section shows mixed-direction breakage on screen,
  it's cheap to pin with a logical-property fix — noted as the one visual risk in this pass.

- **D-3.3 — test_api_run.py flakes under full-suite contention.** Two API-run tests
  (returns_job_id, rejects_when_llm_required) failed once during a full-suite run while a heavy
  render/other work contended for the port/timing, then passed 9/9 in isolation. Not caused by
  the dashboard changes. Treating as a load flake; re-running the full suite clean before each
  commit. FLAG: worth a port-isolation/retry hardening pass on those tests (ticket T-APIFLAKE).

- **D-3.4 — Tier-mismatch cap is conservative to protect the U1 gate.** The decisive cap in
  peer_match._aggregate only fires on EXTREME mismatch (size score <0.20 ≈ 100x reviews, or the
  opposite price tier), so genuine near-tier peers the U1 objective-deduction gate selects are
  untouched (verified by test_genuine_near_tier_peer_is_untouched). A full U1 re-gate on live
  data is the owner-side confirmation that discovery quality didn't regress — ticket T-TIER-GATE
  (needs network + is measurement-sensitive; not run inside this offline mission).

## Render #3 — reel consistency prompt pack

- **D-R3.1 — Resolved image model ids (pack 🌐 verify-at-decision-time).** Probed live on
  radiant-octane: `gemini-3.1-flash-image-preview` (Nano Banana 2, fast lane) and
  `gemini-3-pro-image-preview` (Pro lane, legible text) both LIVE at Vertex
  `location="global"`; 404 at us-central1. Legacy `gemini-2.5-flash-image` live at
  us-central1 as fallback. Seed generator switches Imagen → Nano Banana per §10; resolved
  model id logged per request.
- **D-R3.2 — Calibration fixture built from render #2.** tests/fixtures/render2_sheet.png
  (5 frames — the 6th timestamp fell past the last keyframe; 5 are sufficient and visibly
  carry the diseases: ≥4 different protagonists, the banned smile-at-laptop beat, a glowing
  sci-fi UI panel). G2 MUST fail this sheet or the gate itself is broken (§5).
- **D-R3.3 — Temperature 0 for G2 not directly controllable** through the caller protocol
  (temp lives inside the caller implementation). The BINDING part is preserved in code: the
  PASS conjunction is computed from the boolean fields in code (the model never self-passes),
  matching the pack's code-level invariant. FLAG: if G2 verdicts prove noisy, expose a
  temperature override on the Gemini caller.
- **D-R3.4 — VO↔EVIDENCE check upgraded from "cheap LLM check" to the Ledger.** G1's VO trace
  uses ledger.audit_text (deterministic, the actual moat) instead of an LLM opinion — stronger
  than the pack's minimum, same intent.

- **D-R3.5 — G2 calibration RUN LIVE and PASSED (i.e., correctly FAILED render #2).**
  verdict=FAIL, same_person=false with 4 identity offenders (blurred face / protagonist
  absent / back turned / different woman without hijab), same_world_grade=false,
  arc_readable=false. Nuance vs the pack's expectation: junk_screens=[] — the judge read
  frame 5's glow as architectural light behind glass, not on-screen UI; the FAIL is carried
  decisively by identity+grade+arc, which is the mandatory core. The CI assertion pins
  verdict==FAIL && same_person==false; live test gated behind RUN_LIVE_GATES=1 (this repo's
  CI is hermetic by policy).

## D-R3.6 — G1 calibrated against the live director (2026-07-13)
Three measured drifts from the first live NTI runs, each fixed at the root:
(1) the R6 prompt never named `NONE`, so the model invented `N/A` → NONE is now
option (a) in the prompt; `N/A` stays rejected (pinned in tests). (2) the model
mirrors REAL_CONTENT's `:<desc>` onto the safe tokens (`OUT_OF_FOCUS:A monitor…`)
→ lint + expander accept the suffix as descriptive flavor; the safety clause
always rides along. (3) one corrective retry was not enough — each retry fixed
the named issue and rolled a new one (VO 52→47 words, then a stray smile) →
retry cap raised to 3 with CUMULATIVE deduped feedback; director calls are cheap
text (the HITL law governs image/video spend). R8 now says "COUNT the words".
Result: live run passes G1 on the first corrective retry (VO 59 words).

## D-429.1 / D-429.2 — the 429 round, two honest notes (2026-07-13)
(a) **The double-submit lock was the real first-cause.** `/analyze` had NO concurrency guard
(unlike `/generate`); a double-click / SSE-reconnect spawned TWO concurrent crawls of the same
host, and two crawls hammering at once is what first tripped Cloudflare's per-IP 429. Shipped as
the `_INFLIGHT (slug, "analyze")` guard.
(b) **My own diagnostics caused much of the observed 429.** Probe/measure/categorize scripts
fired 1000+ requests at topshoes/bobana/rawafrican during diagnosis; on a shared IP that
degraded our Cloudflare reputation so the crawler (headless) got 429 while curl got 200. Encoded
as a hard rule: memory `diagnostic-isolation-shared-ip` + a diagnostic ledger in logs/spend.md.

## D-429.3 — footprint fix is two-phase, NOT a blunt request cut (owner ruling 2026-07-13)
The 106 product images are the SAME data U1 fought for (rawafrican offerings) — do not drop them.
PHASE A (crawl): fetch ALL text/content/prices/offerings; only COLLECT image URLs (cheap text),
do NOT download image bytes inline → homepage ~300 -> ~50-70 requests, ZERO content loss.
PHASE B (post-crawl, metered): download ONLY what's used — the screenshot asset + the poster/reel
candidate images that already pass the quality gate — not all 106. `--thorough` preserves the old
full-image behavior; two-phase is the new DEFAULT. Gate: extraction coverage regression >5% = revert.

## D-R3.7 — G2 junk-screen detection strengthened for C1 (owner, 2026-07-13)
Owner C1 requires the G2 gate to flag render#2's glowing-cyan frame (frame 5) in junk_screens.
Earlier (D-R3.5) the judge read that glow as architectural light -> junk_screens=[]; the FAIL
was carried only by identity+grade+arc. Strengthened the `_G2_PROMPT` junk_screens definition
to flag sci-fi glow (neon/cyan LED strips, holographic panels) EVEN when it could pass as ambient
lighting ("when in doubt about a glow, FLAG it"), while explicitly NOT flagging a plain real
monitor with ordinary text. Re-ran live x2: stable junk_screens=[5], verdict=FAIL,
same_person=false. Calibration test now pins `5 in junk_screens`. Blocking mechanism, code
conjunction, locked blocks, spend order all UNCHANGED — only the junk-detection sensitivity.

## D-SEQ.1 — reel front first, scraper two-phase FROZEN (owner, 2026-07-13)
Do NOT run both fronts in parallel across the 12h window. The reel gates real spend and C1-C5 are
met with the gate correctly failing render#2 — it takes priority and now waits ONLY on the owner's
EXECUTE decision on render#3. The scraper two-phase build is FROZEN mid-design; its dependency map
is parked in logs/scraper_two_phase_map.md. Scraper resumes with full focus AFTER the reel closes.

## D-429.4 — two-phase carve-out: block PRODUCT image bytes only (owner, 2026-07-13)
The map caught that blocking ALL homepage image bytes regresses logo detection (getBoundingClientRect
collapses -> _suitable_logo_shape false -> +12 bonus + floor-rescue lost). Resolution: block
PRODUCT-image bytes only; ALLOW dimensions/bytes for logo candidates (logo = 1-2 images, not 106).
Keeps the footprint win (products are the bulk) AND logo detection intact. Implement on resume, then
the mandated before/after (request count + logo-pick + products/prices coverage; revert if >5%).

## HITL #1 — render#3 EXECUTE gate (owner, 2026-07-13)
Despite the earlier "after C1-C5, proceed to seeds", the owner judges the CONCEPT before ANY spend.
No seed/G2/Veo spend until an explicit EXECUTE. C1-C5 all met (C2 resolved: mole her-left, card
clean/consistent, text aligned). G2 strengthening + C1 pin + C2 alignment shipped (suite 1405).

## FRONT 1 CLOSE — reel finalized (Run#2, 2026-07-14)
render#3 EXECUTEd earlier this session (owner-approved, per-step). Deliverable =
outputs/reels/nti_render3_final.mp4 (= guardian_reel_v4). Veo authorization consumed — NO further
Veo this run. G2 seed gate PASSed (zero regens). Reel evolved Layla→pharmacist(domain-drift, fixed
by R10)→Karim/guardian(testimonial, real NTI, lip-synced).

## FRONT 1 — code-review remediation (Run#2, 2026-07-14)
Owner injected a dual-lens (Staff-Eng × Creative-Director) review of the render3 subsystem. Fixed
the VERIFIED findings, each with a hermetic test, in one cohesive commit (same front, interlocking
correctness/safety fixes): C1 (gender still hardcoded in seed_prompt/veo_prompt — critical, LIVE bug
I introduced when the gender refactor was left half-applied), C2 (fail-open moat → fail-visible),
H1 (duration-sum), L1 (id-uniqueness), H2 (content-bound HITL approval token gates continue_render3
spend), M4 (no blind-regen on unfixable arc failure). L2 already satisfied.

### TICKET T-M3 — explicit `Shot.legible_text` field (deferred, medium)
`_needs_pro_lane` decides the text-capable image model via brittle substring cues
("legible"/"clearly see"/"readable"/"the title") on action+camera. A cleaner design is an explicit
`legible_text: bool` on `Shot` that the Director sets. Deferred because it's a schema + Director-prompt
change (LLM must populate it) that deserves its own measured commit + a live Director call to validate,
not a half-measure inside the review-fix batch. Current heuristic works (tested); this is hardening.

### FLAG — owner's open offer (Director prompt rewrite)
Owner ended the review asking "تحبي أكتبلك برومبت الـDirector المحسّن كامل؟" (want me to write the
improved Director prompt — hook contract, callback, sound design, VO-binding, input-floor). That is
the biggest CRAFT leverage but is the OWNER's to author. Awaiting it; not blocking engineering fronts.

## OWNER UNBLOCKS (human actions that release blocked units)
- **REEL VOICE**: upgrade ElevenLabs to Creator (~$22/mo) — free tier CANNOT use library voices via
  API. Then say "ظبطها": I add a native Egyptian voice (Moatassem/Ziad/ELareef), re-synth the VO,
  re-run WaveSpeed VEED lip-sync on the 2 to-camera shots. One command, minutes. Prep scripts ready
  (add_egyptian_voices.py + assemble_v4.py).
- (F6 will add: U2 Meta ads identity confirm; U8 review-labeling kit; Apify cap; IG/FB token state.)
