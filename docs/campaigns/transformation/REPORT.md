# ADZY Campaign transformation

**Status: deployment-tested pilot candidate for a single trusted workstation operator.**
Base `133181f820f99eba7b81269d53ece8cb36359422`. Linux, Python 3.12.16,
154 locked application packages, Docker 29.9.3, PostgreSQL 16 and
Playwright Chromium. Application dependencies passed `uv pip check`.
Local application work made zero paid provider calls.

## Measured changes

| Check | Base | Candidate |
| --- | --- | --- |
| Fixed synthetic context corpus | 6/10 expected reviewed outcomes. The tracker example reached READY with an unconditional Arabic claim; conditions after the paragraph were omitted. | 10/10. Seven reviewed packages, including three with a source guide preserving the qualifying span, and three expected blocks. Four extra adversarial tests cover qualifier headings, asides and ambiguous references. |
| Exposed lifecycle replay, per policy | 8/10 READY and 2/10 intended acquisition blocks. Supported edits stable 1/8 and candidate 7/8. Critical unsafe edits 0/9 accepted for each. | Same counts and selected evidence. Five Guys remains on Bacon Burger. |
| Deliberately narrowed source context | — | Actual evaluator returned `KEEP_STABLE`, exit 1, and identified all three omitted-qualification cases. |
| Approval under cache loss | Cache loss plus changed renderer delivered a changed square PNG under the same approval. | Both preview digests are required; exact ZIP survived preview-cache deletion and clean PostgreSQL restore. Corrupt stored bytes fail closed. |
| Campaign source SSRF | Controlled probes reached a local peer via DNS rebinding and a process proxy. | Both routes are closed, with 35 local transport cases covering DNS, IPv6, HTTPS identity, redirects, partial responses and bounded reads. Controlled OS egress and the isolated Campaign surface remain part of the operating boundary. |
| Clean-source Buffalo browser replay | 40.25 s wall, 32.99 s case. 0 provider usage rows and 0 browser errors. | 41.99 s wall, 35.51 s case. More preview operations. Both digests bind every approval; stale revision and unsupported copy return 409; same-run archive matches after restart; final poster digests match base. A fresh tracked-source copy repeated the journey in 37.75 s wall. |
| Unknown generic provider cost | Unpriced and incompletely reported model usage could appear as zero. | Estimates are unknown if rate or required usage is unavailable. A potentially billable failed attempt also leaves the aggregate unknown. Available token counts remain recorded. |

[Full replay rows and fault injection](replay-outcomes.json) · [Browser results](browser-results.json) · [Versioned context manifest](context-manifest.json)

## Public-source diagnostics

Two release comparisons used byte-identical saved pages, one Arabic food
product and one English electronics service. For each, the operator supplied
the brand, objective, URL and language. There were zero copy edits, curated
edition configurations, supplied assets or provider calls. The original
Fairphone support URL returned HTTP 403. A separate public repair page supplied
the comparison input. The initial Almarai fetch timed out and a direct
diagnostic capture succeeded. Both selected pages and capture hashes appear
in the outcome file. A follow-up with the pinned-network candidate retrieved all three URLs, including the original Fairphone support URL. The publication HTML stays outside the repository.

Both inputs produced reviewed packages on both builds. The English output
moved from a dangling list introduction to a full repair/warranty sentence.
The Arabic output kept its publisher-linked product and replaced a generic
line with a source invitation naming the product. Real Chromium rendered four
PNG outputs per build across portrait and square and passed geometry checks.
These are two development diagnostics influenced by the observed baseline.
Independent reviewers have yet to score editorial usefulness, translation,
product fidelity or readability. [Diagnostic rows and raster measurements](publisher-diagnostics.json) · [Fairphone publisher page](https://www.fairphone.com/repairs) · [Almarai publisher page](https://www.almarai.com/ar/brands/almarai/liquid-dairy/lactofree/lactofree-milk)

## Verification and boundaries

- Focused offline transport, context, lifecycle, copy and model-accounting
  tests had 100 passes. Real PostgreSQL Campaign and artifact suites had 26 and 13 passes, along with
  Alembic connection and startup checks. The broader DB foundation check
  reproduced the same 65 passes and 3 pre-existing failures on each build.
  Those failures concern upward imports and an existing missing reservation
  table in the checked-in schema mirror. The complete suite was outside the
  verification run.
- The disposable operations drill used real PostgreSQL, Chromium, an
  authenticated HTTP client, approved PNGs, database dumps and a local HTTP
  alert receiver. It measured sequential and burst load, restarted with an
  empty preview store, and exercised binary and database rollback.
  [Deployment commands, SLOs, recovery details and limits](OPERATIONS.md)
- The updated CI configuration wires the real Campaign journey and fault checks
  to pull requests. A hosted run on this branch and GitHub required-check
  enforcement await authorization to change remote state.
- Public operation, parallel workers, a durable research queue, broadly
  qualified semantics and independent creative validation remain open. The
  runbook specifies the verified single-worker loopback profile. External
  inference or evaluation needs separate authorization and independently
  collected labels for quality claims. [Risk register](RISKS.md) · [Blank independent-review instrument](REVIEW-PACKET.md)

## Reproduce

Install `requirements-app.txt`, Playwright Chromium and the dependencies named
in the [README](../../../README.md). Use scratch paths and the exact commands there
for the browser runner. Run the release evaluator with these commands.

```bash
python -m workflows.campaign_eval --output /tmp/adzy-campaign-replay.json
python -m workflows.verify -q tests/test_campaign_evidence_contract.py \
  tests/test_campaign_public_transport.py tests/test_campaign_lifecycle.py \
  tests/test_campaign_copy_quality.py tests/test_llm_extractor.py \
  tests/test_caller_thinking_tokens.py tests/test_vertex_spend.py
```

To test the release gate itself, use a disposable copy and restrict the
qualification scan in `strategy/campaign_evidence.py` to the supporting text
block. Execute the same evaluator and expect exit 1 and `KEEP_STABLE`.

## Interview and CV

“Implemented a source-linked Campaign review workflow with direct validated
publisher fetches, version-specific approval of both poster previews, and
checksum-verified ZIP recovery from PostgreSQL. Exercised the workflow with
Chromium, database restore, concurrency and regression tests on a local pilot.”

Walk through one example from quote and qualifier offsets to a checked preview
hash and committed approval. Explain why a stale write loses, why approval
identity matters after expensive work, and which state has to be restored
before rolling back the application. [Technical walkthrough](../INTERVIEW.md)
