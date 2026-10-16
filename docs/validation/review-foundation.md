# Review workflow foundation

The review foundation provides a compiled LangGraph, three deterministic typed LangChain inspection tools, SQLite checkpoints and actor-bound workflow lookup over canonical PostgreSQL services. It supports an interrupt for an exact human decision, fresh-process resume and recovery after an approval commit/checkpoint gap.

Its original focused suite passed16 tests. The integrated application subsequently passed23 focused tests, including real authentication/routes, concurrent starts/resumes, safe failed-start handling and local SDK export capture. Results overlap and must not be summed.

The dashboard integration supersedes the original CLI-only scope. See the [current integration result](app-review-integration.md), [application architecture](../architecture/app_review.md) and [publication checklist](../architecture/app_publication.md). Hosted telemetry, distributed execution and live provider certification remain outside the verified scope.
