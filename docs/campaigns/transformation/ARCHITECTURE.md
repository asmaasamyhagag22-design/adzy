# Campaign architecture and decisions

The browser starts a Campaign run with a request ID and an expected revision.
FastAPI authenticates the account and captures the brand/profile/current-run
snapshot. The deterministic planner fetches bounded HTML outside the write
transaction. PostgreSQL locks the brand at write time and compares that snapshot
before creating a new run. Completed request replay checks its input binding.
The optional curated edition matches only its configured source, language and
intent. Its selected fields and visible body digest have separate meanings.

The evidence contract combines capture URL/time and hashes with subject, one
support span, a bounded neighborhood and linked qualification spans. Spans
identify an HTML block ordinal and character range in normalized visible text.
The seven-day freshness policy and per-option destination remain part of the
proposal. Captured text reports publisher statements. Reviewers are responsible
for scope, factual meaning, rights and support for the final use.

Chromium renders portrait and square into local preview archives. Approval of a
source package requires the hashes received from both previews. The service
freezes the ZIP, poster hashes, measurements and proposal identity with the
approval record in one transaction. Owners can recover those bytes from
PostgreSQL even after losing the temporary preview directory. Delivery checks the
active run and approval around artifact retrieval. Corrupt bytes fail closed.
Historical approvals remain visible and need fresh review to authorize download.

## Decisions

- Preserve the deterministic workflow and existing conservative rewrite rules.
  A new semantic judge would need independently labelled support and usefulness
  evidence. Current model experiments remain advisory.
- For known qualifications, offer a source guide whose copy makes no feature
  promise. Automatic translation of exceptions could omit material conditions.
- Store bounded finalized ZIPs in PostgreSQL for an atomic approval and a single
  backup boundary. External object storage would require a second commit,
  recovery and orphan handling. Preview cache remains replaceable.
- Pin a validated DNS answer at the Campaign socket and keep the original TLS
  hostname. A preflight IP check with an independent fetch leaves the DNS gap.
- Keep a loopback-only, single-worker pilot with one trusted operator, interactive
  account provisioning, no public signup and explicit deployment credentials.
  A network service with tenant isolation and workers sharing one queue is a
  future operating requirement, with durable admission and stronger session
  management needed for that boundary.
- Retain legacy Poster workflows and historical evaluation files as separate
  paths. The Campaign pilot exposes only the selected Campaign and account
  surfaces. This keeps older examples reproducible and reduces pilot exposure.
