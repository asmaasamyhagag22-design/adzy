# -*- coding: utf-8 -*-
"""Which store owns a write — decided once, here, and never inferred.

The application has two possible persistence authorities: the file-backed stores it has always
used, and the PostgreSQL services proven in phases 2A-2F. Exactly one of them owns any given
operation, and this module is the only place that chooses.

THREE RULES, and each exists because the alternative is a real failure mode:

1. LEGACY IS THE DEFAULT. `PERSISTENCE_BACKEND` must say `postgres` in so many words. A configured
   `BASEERA_DATABASE_URL` means "a database is reachable", not "the database is in charge" — CI,
   migration rehearsals and the test suite all set a DSN while the application is still legacy-
   authoritative, and inferring authority from it would hand production to PostgreSQL by accident.

2. FAIL CLOSED. If `postgres` is selected and the database is missing, misconfigured, or not at the
   expected migration head, this raises. It does NOT quietly use the file stores. A silent fallback
   is the worst outcome available: writes would land in whichever store happened to answer, and no
   later reconciliation could tell which rows were authoritative.

3. NO DUAL WRITE. `get_persistence()` returns ONE backend. There is no path that writes both. A
   "write legacy too, just to be safe" shadow write creates two divergent truths and destroys the
   single-authority property the whole migration exists to establish.

Routes call the returned object's domain operations. They do not import SQLAlchemy models,
repositories, sessions or Alembic, and they do not branch on the backend name.

WHAT IS WIRED, AND WHAT IS DELIBERATELY NOT
-------------------------------------------
This section was stale for two phases and is now regenerated from the route matrix rather than
remembered. `tests/test_route_persistence_classification_2gb.py` is the executable version and
fails if this drifts again; the summary here exists so a reader of this file is not told a story
from a previous phase.

Live matrix: 56 (method, path) pairs = 9 framework + 32 provider-wired + 11 backend-independent
+ 4 intentionally unavailable.

Wired through this provider — identical behaviour under the default backend, and moving wholesale
when the switch is thrown: identity, brands, scrape, analysis and the approval gate, calendar,
social, influencer, owner inputs, the inpaint/reel/calendar creative writes, and — since the Job
cutover — the whole Job lifecycle: creation, progress, success, failure, polling, result and SSE.

Still unavailable under `postgres`, and NOT because they were overlooked:

  * `GET /api/history` and the three `POST /api/generate*` routes. The gallery index is still
    `history.json`, and writing it while PostgreSQL holds the brand it belongs to is the dual write
    rule 3 forbids. `supports_gallery_writes()` answers False so the route refuses BEFORE spending
    on a render, rather than discovering the gap after the customer paid for the image.

THE JOB LIFECYCLE CROSSED AS ONE UNIT, and that is the load-bearing part of its design. An earlier
attempt moved the three read routes alone: it passed its own targeted suite and broke the
application, because the write side still created in-memory 16-hex ids that a durable read could
never find. So creation, progress and finalisation are provider operations here too, and each
finalizer commits its business result, the job's terminal status and its terminal event in ONE
transaction — never "write the result, then finish the job", which is the pair a crash lands
between.

`LegacyPersistence` still uses the in-memory `JobStore` internally. That is what an adapter is for,
and it is not the same thing as PostgreSQL authority: under `postgres` no route, helper or runner
reaches a JobStore at all.

The honest summary: with `postgres` selected, this application can authenticate, scrape, analyse,
run the whole job lifecycle end to end — and cannot serve the generated-image gallery.
"""
from __future__ import annotations

import binascii
import hmac
import logging
import os
import threading
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, Optional, Protocol, runtime_checkable

if TYPE_CHECKING:  # keeps import cost at zero and the DB driver optional
    from uuid import UUID

LEGACY = "legacy"
POSTGRES = "postgres"
_VALID = (LEGACY, POSTGRES)

ENV_BACKEND = "PERSISTENCE_BACKEND"

def _head_from_migration_files() -> Optional[str]:
    """The head revision, read from the migration FILES this build ships with.

    Filesystem only: it reads `revision = "..."` / `down_revision = "..."` out of the version
    scripts with a regex and returns the revision nothing points back to. It does NOT import the
    scripts, connect to anything, or run Alembic — a startup check that could execute migration
    code would be a worse problem than the stale constant it replaces.

    Derived rather than hand-maintained because the constant is a manual step that is only ever
    noticed when it is already wrong. Returns None if the directory is missing or ambiguous, and
    the constant below stands in — a wrong-but-present head fails CLOSED (the app refuses to serve),
    which is the safe direction for a guess to fall.
    """
    import re
    from pathlib import Path

    versions = Path(__file__).resolve().parents[1] / "db" / "migrations" / "versions"
    if not versions.is_dir():
        return None
    revisions: set[str] = set()
    parents: set[str] = set()
    for script in versions.glob("*.py"):
        text = script.read_text(encoding="utf-8", errors="replace")
        found = re.search(r"^revision\s*=\s*[\"']([^\"']+)[\"']", text, re.M)
        parent = re.search(r"^down_revision\s*=\s*[\"']([^\"']+)[\"']", text, re.M)
        if found:
            revisions.add(found.group(1))
        if parent:
            parents.add(parent.group(1))
    heads = revisions - parents
    return heads.pop() if len(heads) == 1 else None


# The migration head this application build expects. A database ahead of or behind this is not
# something to guess about at startup. Derived from the shipped migration scripts, with the last
# known-good value as the fallback for an unreadable or branched directory.
EXPECTED_SCHEMA_HEAD = _head_from_migration_files() or "0005_strategy_approvals"


class BackendMisconfigured(RuntimeError):
    """The selected backend cannot be served. Never downgraded into a fallback."""


class CapabilityNotWired(RuntimeError):
    """The selected backend does not implement this operation yet.

    Raised INSTEAD of silently using the other store. Phase 2G wires the identity, brand-ownership
    and scrape/job lifecycle surface; anything not yet wired must fail loudly rather than split
    authority across two stores for different endpoints of the same application.
    """


class GalleryWriteFailed(RuntimeError):
    """The render succeeded and the bytes are durable, but the publication was not recorded.

    A distinct class because the caller's situation is distinct: this is not "the feature is
    unavailable" (they would not have been charged) and not "your request was wrong" (it was
    fine). It is work that was done and cannot be shown, which the client has to be told plainly
    instead of receiving a 200 holding an image that will not be in their gallery.
    """


class BrandNotVisible(LookupError):
    """This caller may not see this brand — absent and foreign-owned are ONE answer.

    Kept as one error on purpose. Telling the two apart at the API boundary would let a caller
    enumerate which domains exist in other accounts, which is the hole `get_brand` closed by
    answering 404 for both.
    """


class InvalidSwotSubject(ValueError):
    """PostgreSQL approval requires a reviewed Run UUID."""


class SwotSubjectNotVisible(LookupError):
    """Unknown, foreign, wrong-kind and unsuccessful subjects share one answer."""


class SwotNotReady(RuntimeError):
    """There is no current successful SWOT to approve."""


class StaleApproval(RuntimeError):
    """The SWOT the caller approved is no longer the current one (§17).

    Storing the approval anyway would be harmless; letting it open the strategy gate would not. A
    newer SWOT became current while the human was reading the older one, so their decision is about
    a document the system has already replaced.
    """


class StrategyNotReady(RuntimeError):
    """There is no current canonical strategy Run to decide."""


class StrategyNotApprovable(RuntimeError):
    """The persisted verifier result does not permit approval."""


class StaleStrategyApproval(RuntimeError):
    """The submitted strategy Run is not the current immutable strategy Run."""


class CalendarPostMissing(LookupError):
    """Nothing in the current calendar matches this id or date."""


class AmbiguousCalendarDate(LookupError):
    """More than one post shares this date, so a date does not identify a post.

    The legacy code took the first match. Two posts on one day therefore meant a coin flip that
    produced a real, billable creative for a post the caller did not choose — and no way to notice,
    because the response looked exactly like success. §22: 0 matches -> 404, 1 -> resolve,
    many -> 409. Never first-match.
    """

    def __init__(self, date: str, count: int) -> None:
        super().__init__(f"{count} calendar posts share {date!r}; address the post by id")
        self.date = date
        self.count = count


def _post_identity(post: Mapping[str, Any]) -> str:
    return str(post.get("post_id") or post.get("id") or "")


def _select_post(posts, *, post_id: str = "", date: str = "") -> dict:
    """The one post being addressed, or an error naming exactly why there isn't one."""
    wanted_id = str(post_id or "").strip()
    if wanted_id:
        for post in posts:
            if _post_identity(post) == wanted_id:
                return dict(post)
        raise CalendarPostMissing(wanted_id)

    wanted_date = str(date or "").strip()
    if not wanted_date:
        raise CalendarPostMissing("no post id or date was given")
    matches = [p for p in posts if str(p.get("date") or "") == wanted_date]
    if not matches:
        raise CalendarPostMissing(wanted_date)
    if len(matches) > 1:
        raise AmbiguousCalendarDate(wanted_date, len(matches))
    return dict(matches[0])


_log = logging.getLogger(__name__)


def selected_backend(env: Optional[dict] = None) -> str:
    """The configured authority. Unset, blank or unknown -> legacy, and unknown is an error."""
    raw = (env or os.environ).get(ENV_BACKEND)
    if raw is None or not str(raw).strip():
        return LEGACY
    name = str(raw).strip().lower()
    if name not in _VALID:
        raise BackendMisconfigured(
            f"{ENV_BACKEND}={raw!r} is not one of {_VALID}. Refusing to guess which store owns "
            f"your writes.")
    return name


# =====================================================================
# the contract routes may depend on
# =====================================================================

@runtime_checkable
class PersistenceProvider(Protocol):
    """Domain operations, not storage operations.

    Deliberately typed and narrow: there is no `update(key, dict)`, no `mutate`, no
    `replace_block`. The API layer must not be able to express "patch this document", because that
    is the shape the PostgreSQL schema exists to replace.
    """

    name: str

    # --- identity ---
    def user_by_id(self, user_id: str) -> Optional[dict]: ...
    def user_by_email(self, email: str) -> Optional[dict]: ...
    def user_for_token_subject(self, subject: str) -> Optional[dict]: ...
    def create_user(self, *, email: str, password: str, full_name: str = "") -> Optional[dict]: ...
    def authenticate(self, *, email: str, password: str) -> Optional[dict]: ...

    # --- brand ownership (the C2 surface) ---
    def owner_of_domain(self, domain_key: str) -> Optional[str]: ...
    def visible_brand(self, *, domain_key: str, user_id: str) -> Optional[dict]: ...
    def visible_brands(self, *, user_id: str) -> list[dict]: ...
    def archive_brand(self, *, domain_key: str, user_id: str) -> str: ...

    # --- enrichment: a stage's contribution, stored as its own unit ---
    def record_enrichment(self, *, domain_key: str, user_id: str, kind: str,
                          patch: Mapping[str, Any]) -> Optional[dict]: ...
    def replace_section(self, *, domain_key: str, user_id: str, kind: str, section: str,
                        value: Mapping[str, Any]) -> Optional[dict]: ...

    # --- the generated-image gallery (§27) ---
    #
    # `record_gallery_entry` takes the SAME dict on both backends: the legacy index appends it, the
    # durable backend splits it into physical bytes and a logical entry. The caller never learns
    # which happened, which is why the three image routes needed no branch when this crossed.
    #
    # `supports_gallery_writes` stays on the surface even though both backends now answer True. It
    # is asked BEFORE rendering, so a future backend that cannot own the gallery refuses while the
    # render is still free rather than after the customer paid for it.
    def supports_gallery_writes(self) -> bool: ...
    def record_gallery_entry(self, *, entry: Mapping[str, Any], user_id: str
                             ) -> Optional[dict]: ...
    def gallery_history(self, *, user_id: str, limit: int = 100) -> list[dict]: ...

    # --- canonical poster execution/provenance ---
    def begin_canonical_poster_run(self, *, domain_key: str, user_id: str,
                                   contract: Mapping[str, Any]) -> dict: ...
    def fail_canonical_poster_run(self, *, run_id: str, attempt_id: str,
                                  user_id: str, error: str) -> None: ...

    # --- the scrape lifecycle (C2) ---
    def begin_scrape(self, *, domain_key: str, user_id: str) -> str: ...
    def commit_scrape(self, *, domain_key: str, user_id: str, ticket: str,
                      brand: Mapping[str, Any], profile: Mapping[str, Any],
                      source_url: str, display_name: str,
                      warning: str = "") -> Optional[dict]: ...
    def abandon_scrape(self, *, ticket: str, user_id: str, error: str) -> None: ...

    # --- gated state ---
    def approve_swot(self, *, domain_key: str, user_id: str,
                     expected_run_id: Any = None) -> dict: ...
    def swot_is_approved(self, *, domain_key: str, user_id: str) -> bool: ...
    def supports_canonical_strategy(self) -> bool: ...
    def decide_strategy(self, *, domain_key: str, user_id: str, run_id: str,
                        decision: str, note: str = "") -> dict: ...
    def canonical_strategy_context(self, *, domain_key: str, user_id: str,
                                   run_id: str) -> Optional[dict]: ...

    # --- owner-supplied values ---
    def owner_inputs(self, *, domain_key: str, user_id: str) -> Optional[dict]: ...
    def set_owner_inputs(self, *, domain_key: str, user_id: str,
                         values: Mapping[str, Any]) -> Optional[dict]: ...

    # --- calendar targeting (§22) ---
    def resolve_calendar_post(self, *, domain_key: str, user_id: str,
                              post_id: str = "", date: str = "") -> dict: ...

    # --- the job lifecycle ---
    #
    # Typed and semantic on purpose. A generic `create_job(dict)` would erase the one distinction
    # that matters here: a web-app job is owned by an authenticated USER and a `/api/run` job is
    # owned by a CAPABILITY whose raw token is never stored. Those are different ownership models
    # with different lookup paths, and collapsing them into one dict-shaped call would make the
    # difference a runtime key check instead of a signature.
    #
    # Reads take the principals the request actually presented and answer None for BOTH "no such
    # job" and "not yours" — the same rule brand reads follow, so a job id cannot be probed for
    # existence. No UnitOfWork, repository, model or SQLAlchemy type appears here: a route that
    # could take a transaction is a route that can branch on the backend.
    #
    # There is deliberately NO `stream_job_events`. SSE is HTTP transport — framing, keepalive and
    # disconnect belong to the route; durable retrieval belongs here. `job_events_since` is the
    # whole storage half of that contract.
    def create_user_job(self, *, owner_user_id: str, domain_key: str = "") -> str: ...
    def create_capability_job(self, *, capability_digest: str, domain_key: str = "") -> str: ...
    def job_snapshot(self, *, job_id: str,
                     principals: "Sequence[str]") -> Optional[dict]: ...
    def job_result(self, *, job_id: str,
                   principals: "Sequence[str]") -> Optional[dict]: ...
    def job_events_since(self, *, job_id: str, principals: "Sequence[str]",
                         after_seq: int = 0, limit: int = 500) -> Optional[list[dict]]: ...
    def job_is_terminal(self, *, job_id: str, principals: "Sequence[str]") -> bool: ...

    # ------------------------------------------------------------------ job lifecycle: writes
    #
    # WHY THE WRITE SIDE IS HERE AND NOT IN THE ROUTES. Reads and writes of one job have to cross
    # to a new backend TOGETHER. Moving the reads alone was tried and measured: the write side kept
    # creating 16-hex in-memory ids, a provider-backed read looked for them in PostgreSQL, found
    # nothing, and every poll 404ed forever. Half a lifecycle is not a smaller step, it is a broken
    # application — so progress, success and failure are provider operations too.
    #
    # Each verb is one FACT, not one row. `finish_direct_result_job` and `finish_enrichment_job`
    # each commit their business result, the job's terminal status and its terminal event in ONE
    # transaction; that is why they are separate methods rather than "write the result" followed by
    # "now finish the job", which is exactly the pair a crash can land between.
    def record_job_event(self, *, job_id: str, stage: str, status: str, occurred_at: Any,
                         duration_ms: int = 0, payload: Optional[Mapping[str, Any]] = None,
                         error: str = "") -> None: ...
    def hold_job_result(self, *, job_id: str, profile: Mapping[str, Any],
                        manifest_path: str = "", profile_path: str = "") -> None: ...
    def finish_direct_result_job(self, *, job_id: str, profile: Mapping[str, Any],
                                 manifest_path: str = "", profile_path: str = "",
                                 payload: Optional[Mapping[str, Any]] = None) -> bool: ...
    def finish_enrichment_job(self, *, job_id: str, domain_key: str, user_id: str, kind: str,
                              patch: Mapping[str, Any],
                              payload: Optional[Mapping[str, Any]] = None) -> Optional[dict]: ...
    def finish_canonical_strategy_job(self, *, job_id: str, domain_key: str, user_id: str,
                                      build: Any,
                                      payload: Optional[Mapping[str, Any]] = None
                                      ) -> Optional[dict]: ...
    def finish_job_success(self, *, job_id: str, payload: Optional[Mapping[str, Any]] = None,
                           warning: str = "") -> bool: ...
    def finish_job_failure(self, *, job_id: str, error: str,
                           payload: Optional[Mapping[str, Any]] = None) -> bool: ...

    # Crash repair. On the provider rather than in a module of its own because WHAT a crashed job
    # looks like is a property of the store that holds it — and because a caller that had to ask
    # "which backend am I on" before reconciling would be the branch this whole layer removes.
    def reconcile_jobs(self) -> dict: ...


# =====================================================================
# legacy adapter
# =====================================================================

class LegacyPersistence:
    """Adapts the file-backed stores to the same domain contract.

    The adapter exists so routes stop asking "is this JSON or PostgreSQL?". Internally it still
    uses BrandStore/UserStore — that is the point of an adapter — but nothing above it can tell.
    """

    name = LEGACY

    # --- identity ---
    def user_by_id(self, user_id: str) -> Optional[dict]:
        from api.user_auth import UserStore
        return UserStore().get_by_id(str(user_id))

    def user_by_email(self, email: str) -> Optional[dict]:
        from api.user_auth import UserStore
        return UserStore().get_by_email(email)

    def user_for_token_subject(self, subject: str) -> Optional[dict]:
        """Legacy tokens carry the legacy 16-hex id, which IS the legacy primary key."""
        return self.user_by_id(subject)

    def create_user(self, *, email: str, password: str, full_name: str = "") -> Optional[dict]:
        from api.user_auth import UserStore
        return UserStore().create(email, password, full_name)

    def authenticate(self, *, email: str, password: str) -> Optional[dict]:
        from api.user_auth import UserStore
        return UserStore().authenticate(email, password)

    # --- brand ownership ---
    def owner_of_domain(self, domain_key: str) -> Optional[str]:
        from api.brand_store import BrandStore
        return BrandStore().owner_of(domain_key)

    def visible_brand(self, *, domain_key: str, user_id: str) -> Optional[dict]:
        from api.brand_store import BrandStore
        return BrandStore().get(domain_key, for_user=user_id or None)

    def visible_brands(self, *, user_id: str) -> list[dict]:
        from api.brand_store import BrandStore
        return BrandStore().list(for_user=user_id or None)

    def archive_brand(self, *, domain_key: str, user_id: str) -> str:
        from api.brand_store import BrandStore
        return BrandStore().delete(domain_key, for_user=user_id or None)

    # --- enrichment ---
    def record_enrichment(self, *, domain_key: str, user_id: str, kind: str,
                          patch: Mapping[str, Any]) -> Optional[dict]:
        """Legacy semantics: merge the stage's patch into the one mutable document.

        `kind` is accepted and ignored here — the file store has nowhere to record which stage
        produced which key, which is precisely the property the relational side restores.
        """
        from api.brand_store import BrandStore
        return BrandStore().update(domain_key, dict(patch), for_user=user_id or None)

    def replace_section(self, *, domain_key: str, user_id: str, kind: str, section: str,
                        value: Mapping[str, Any]) -> Optional[dict]:
        """Replace a whole section outright — `update` would MERGE and quietly change nothing.

        Measured: the influencer reset passed an empty `candidates` dict through the merging path,
        `_merge_one_level` recursed into the old dict and removed nothing, so the reset was a no-op,
        the second click left two candidates, and the fourth hit MAX_CANDIDATES and surfaced to the
        owner as "Generation step failed." Replacement is the operation; merging is a different one.
        """
        from api.brand_store import BrandStore
        return BrandStore().replace_block(domain_key, section, dict(value),
                                          for_user=user_id or None)

    # --- gallery ---
    def supports_gallery_writes(self) -> bool:
        return True

    def record_gallery_entry(self, *, entry: Mapping[str, Any], user_id: str
                             ) -> Optional[dict]:
        import base64

        from api.brand_store import record_history
        raw = str(entry.get("image_b64") or "")
        try:
            payload = base64.b64decode(raw, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("gallery image bytes are not valid base64") from exc
        truthful, _decision = _truthful_poster_entry(entry, payload=payload)
        record_history(truthful, owner_id=user_id or None)
        return None

    def gallery_history(self, *, user_id: str, limit: int = 100) -> list[dict]:
        from api.brand_store import read_history
        return read_history(limit, for_user=user_id or None)

    def begin_canonical_poster_run(self, *, domain_key: str, user_id: str,
                                   contract: Mapping[str, Any]) -> dict:
        raise CapabilityNotWired(
            "canonical poster Runs require PostgreSQL; legacy output is noncanonical")

    def fail_canonical_poster_run(self, *, run_id: str, attempt_id: str,
                                  user_id: str, error: str) -> None:
        raise CapabilityNotWired(
            "canonical poster Runs require PostgreSQL; legacy output is noncanonical")

    # --- scrape lifecycle ---
    def begin_scrape(self, *, domain_key: str, user_id: str) -> str:
        """Create the job the caller will poll, and return ITS id — one handle, not two.

        This used to answer "" because the durable ticket and the client-facing job id were
        different objects: the route created an in-memory job for the SPA and a durable job for
        PostgreSQL, and only one of them was ever polled. That is the half-lifecycle in miniature —
        two ids for one piece of work, with the answer living behind whichever one the reader did
        not have. Both backends now return the id the client will actually use.
        """
        return self.create_user_job(owner_user_id=user_id, domain_key=domain_key)

    def commit_scrape(self, *, domain_key: str, user_id: str, ticket: str,
                      brand: Mapping[str, Any], profile: Mapping[str, Any],
                      source_url: str, display_name: str,
                      warning: str = "") -> Optional[dict]:
        """C2 unchanged: `upsert` re-reads the owner under the storage lock and claims on success."""
        from api.brand_store import BrandStore
        from api.routes.scrape import _PRESERVE_ON_RESCRAPE

        return BrandStore().upsert(dict(brand), owner_id=user_id or None,
                                   preserve_existing=_PRESERVE_ON_RESCRAPE)

    def abandon_scrape(self, *, ticket: str, user_id: str, error: str) -> None:
        """The scrape-shaped name for the one failure verb; see `finish_job_failure`."""
        self.finish_job_failure(job_id=ticket, error=error)
        return None

    # --- gated state ---
    def approve_swot(self, *, domain_key: str, user_id: str,
                     expected_run_id: Any = None) -> dict:
        """Approve the SWOT that is stored RIGHT NOW, and record WHICH one that was.

        The hash is what makes the approval invalidatable: `generate_swot` compares the SWOT it
        just built against `swot_approved_hash` and drops the flag when they differ. Storing only
        the boolean — as this did — left a signature with no subject, so a regenerated SWOT
        inherited it.
        """
        from api.brand_store import BrandStore
        from api.webapp_analysis import swot_identity

        def approve(brand):
            if not brand.get("has_swot"):
                raise SwotNotReady()
            current = str(brand.get("swot_content_hash") or "") or swot_identity(brand.get("swot"))
            return {**brand, "swot_approved": True, "swot_content_hash": current,
                    "swot_approved_hash": current, "swot_approval_invalidated": None}

        updated = BrandStore().mutate(domain_key, approve, for_user=user_id or None)
        if updated is None:
            raise BrandNotVisible(domain_key)
        return {"swot_approved": True, "swot_approved_hash": updated["swot_approved_hash"]}

    def swot_is_approved(self, *, domain_key: str, user_id: str) -> bool:
        brand = self.visible_brand(domain_key=domain_key, user_id=user_id) or {}
        return bool(brand.get("swot_approved"))

    def supports_canonical_strategy(self) -> bool:
        return False

    def decide_strategy(self, *, domain_key: str, user_id: str, run_id: str,
                        decision: str, note: str = "") -> dict:
        raise CapabilityNotWired("canonical strategy authority requires PostgreSQL")

    def canonical_strategy_context(self, *, domain_key: str, user_id: str,
                                   run_id: str) -> Optional[dict]:
        """A file document cannot prove an immutable approved StrategyVersion."""
        raise CapabilityNotWired("canonical strategy authority requires PostgreSQL")

    # --- owner inputs ---
    def owner_inputs(self, *, domain_key: str, user_id: str) -> Optional[dict]:
        brand = self.visible_brand(domain_key=domain_key, user_id=user_id)
        return None if brand is None else (brand.get("owner_inputs") or {})

    def set_owner_inputs(self, *, domain_key: str, user_id: str,
                         values: Mapping[str, Any]) -> Optional[dict]:
        from api.brand_store import BrandStore
        saved = BrandStore().replace_block(domain_key, "owner_inputs", dict(values),
                                           for_user=user_id or None)
        return None if saved is None else dict(values)

    # --- calendar targeting ---
    def resolve_calendar_post(self, *, domain_key: str, user_id: str,
                              post_id: str = "", date: str = "") -> dict:
        """Resolve a calendar post out of the stored document, by id or by date.

        The ambiguity rule is enforced HERE rather than at the route, so both backends answer it the
        same way. Legacy calendars were addressed by date and the old code took the first match;
        two posts sharing a date made that a coin flip the caller could not see.
        """
        brand = self.visible_brand(domain_key=domain_key, user_id=user_id)
        if brand is None:
            raise BrandNotVisible(domain_key)
        posts = [p for p in ((brand.get("content_calendar") or {}).get("calendar") or [])
                 if isinstance(p, dict)]
        return _select_post(posts, post_id=post_id, date=date)

    # ------------------------------------------------------------------ jobs
    #
    # The process-local JobStore, behind the same typed surface PostgreSQL implements. Behaviour is
    # unchanged — this moves WHO calls the store, not what it does.

    def _job(self, job_id: str, principals: "Sequence[str]"):
        """The job this caller may see, or None. Absent and foreign-owned are ONE answer.

        Constant-time comparison against every principal the request actually presented, matching
        `api/job_access.request_owns_job`. Returning None for a foreign job rather than a distinct
        error is what stops a job id from being probed for existence.
        """
        from .jobs.store import active_store

        store = active_store()
        if store is None:
            return None
        job = store.get(job_id)
        if job is None:
            return None
        expected = str(getattr(job, "owner_principal", "") or "")
        if not expected:
            return None
        if not any(hmac.compare_digest(expected, str(p or "")) for p in principals):
            return None
        return job

    def _mounted_store(self):
        """The store serving THIS request, or a refusal. Never a silently dropped job."""
        from .jobs.store import active_store

        store = active_store()
        if store is None:
            raise BackendMisconfigured("no JobStore is mounted in this process")
        return store

    @staticmethod
    def _attach_loop(store, job_id: str) -> None:
        """Record the loop the worker thread must marshal back to.

        Legacy-only plumbing, and that is why it lives behind the provider rather than in a route:
        the durable backend has no loop to attach, so a route that called `attach_loop` itself
        would be a route that knows which backend it is on. Creation outside a running loop (tests,
        CLI) is not an error — there is simply nothing to marshal to yet.
        """
        import asyncio

        try:
            store.attach_loop(job_id, asyncio.get_running_loop())
        except RuntimeError:
            pass

    def create_user_job(self, *, owner_user_id: str, domain_key: str = "") -> str:
        from .job_access import user_job_principal

        store = self._mounted_store()
        job = store.create(owner_principal=user_job_principal(owner_user_id))
        if domain_key:
            job.domain_key = domain_key
        self._attach_loop(store, job.job_id)
        return job.job_id

    def create_capability_job(self, *, capability_digest: str, domain_key: str = "") -> str:
        store = self._mounted_store()
        job = store.create(owner_principal=capability_digest)
        if domain_key:
            job.domain_key = domain_key
        self._attach_loop(store, job.job_id)
        return job.job_id

    def job_snapshot(self, *, job_id: str, principals: "Sequence[str]") -> Optional[dict]:
        from .job_projection import project_job

        job = self._job(job_id, principals)
        if job is None:
            return None
        return project_job(
            job_id=job.job_id, status=job.status, current_stage=job.current_stage,
            domain_key=job.domain_key, warning=job.warning, error=job.error)

    def job_result(self, *, job_id: str, principals: "Sequence[str]") -> Optional[dict]:
        """The terminal answer, or a non-terminal one that says so. None only means 'not yours'.

        `status` is returned RAW (queued/running/succeeded/failed) rather than projected, because
        the route has to distinguish "failed" from "not finished" to choose 500 vs 409 — a
        distinction the client-facing projection deliberately collapses.
        """
        job = self._job(job_id, principals)
        if job is None:
            return None
        return {
            "job_id": job.job_id,
            "status": getattr(job.status, "value", job.status),
            "profile": job.result,
            "manifest_path": job.manifest_path,
            "profile_path": job.profile_path,
            "error": job.error or None,
        }

    def job_events_since(self, *, job_id: str, principals: "Sequence[str]",
                         after_seq: int = 0, limit: int = 500) -> Optional[list[dict]]:
        """Events after `after_seq`, where seq is the 1-based position in the job's own history.

        The in-memory store appends to a list, so its index IS a stable per-job sequence for the
        life of the job — the same resumption contract the durable `job_events.seq` provides.
        """
        from .job_projection import project_event

        job = self._job(job_id, principals)
        if job is None:
            return None
        start = max(0, int(after_seq or 0))
        events = list(job.events)[start:start + max(1, int(limit or 500))]
        return [
            project_event(stage=ev.stage, status=ev.status, occurred_at=ev.timestamp,
                          duration_ms=getattr(ev, "duration_ms", 0) or 0,
                          payload=getattr(ev, "payload", None),
                          error=getattr(ev, "error", None), seq=start + offset + 1)
            for offset, ev in enumerate(events)
        ]

    def job_is_terminal(self, *, job_id: str, principals: "Sequence[str]") -> bool:
        job = self._job(job_id, principals)
        return bool(job is not None and job.is_terminal())

    # --- job lifecycle: writes ---

    def record_job_event(self, *, job_id: str, stage: str, status: str, occurred_at: Any,
                         duration_ms: int = 0, payload: Optional[Mapping[str, Any]] = None,
                         error: str = "") -> None:
        """Append one progress event. The store derives the job status from it, as it always did."""
        from .jobs.store import active_store
        from .schemas import Stage, StageEvent, StageStatus

        store = active_store()
        if store is None:
            return
        store.emit_threadsafe(job_id, StageEvent(
            job_id=job_id, stage=Stage(str(stage)), status=StageStatus(str(status)),
            timestamp=occurred_at, duration_ms=int(duration_ms or 0),
            payload=dict(payload) if payload is not None else None, error=error or None))

    def hold_job_result(self, *, job_id: str, profile: Mapping[str, Any],
                        manifest_path: str = "", profile_path: str = "") -> None:
        """The in-memory Job IS this backend's result store, so the pipeline's output lands here."""
        store = active_store_or_none()
        if store is None:
            return
        store.set_result_threadsafe(job_id, dict(profile),
                                    manifest_path or None, profile_path or None)

    def finish_direct_result_job(self, *, job_id: str, profile: Mapping[str, Any],
                                 manifest_path: str = "", profile_path: str = "",
                                 payload: Optional[Mapping[str, Any]] = None) -> bool:
        """Result + success, published as ONE transition on the loop (`finish_success` sets the
        result before the terminal event, so a SUCCEEDED job never has a missing one)."""
        store = active_store_or_none()
        if store is None:
            return False
        if not profile:
            return False
        store.set_result_threadsafe(job_id, dict(profile),
                                    manifest_path or None, profile_path or None)
        return store.finish_success_threadsafe(
            job_id, result=dict(profile), payload=dict(payload) if payload else None)

    def finish_enrichment_job(self, *, job_id: str, domain_key: str, user_id: str, kind: str,
                              patch: Mapping[str, Any],
                              payload: Optional[Mapping[str, Any]] = None) -> Optional[dict]:
        """Persist the stage's patch, then publish success carrying what was persisted."""
        persisted = self.record_enrichment(domain_key=domain_key, user_id=user_id,
                                           kind=kind, patch=patch)
        if persisted is None:
            return None
        store = active_store_or_none()
        published = store is not None and store.finish_success_threadsafe(
            job_id, result=persisted, payload=dict(payload) if payload else None)
        if not published:
            # The patch IS persisted, so reporting failure to the caller would be the lie in the
            # other direction. But a job left non-terminal beside a committed result is the Class-A
            # shape, so it is said out loud rather than absorbed — an absence read as success is
            # exactly the failure this lifecycle is about.
            _log.warning("brand job %s persisted its patch but published no terminal state", job_id)
        return persisted

    def finish_canonical_strategy_job(self, *, job_id: str, domain_key: str, user_id: str,
                                      build: Any,
                                      payload: Optional[Mapping[str, Any]] = None
                                      ) -> Optional[dict]:
        raise CapabilityNotWired("legacy storage cannot persist a canonical strategy")

    def finish_job_success(self, *, job_id: str, payload: Optional[Mapping[str, Any]] = None,
                           warning: str = "") -> bool:
        store = active_store_or_none()
        if store is None:
            return False
        return store.finish_success_threadsafe(
            job_id, payload=dict(payload) if payload else None, warning=warning)

    def finish_job_failure(self, *, job_id: str, error: str,
                           payload: Optional[Mapping[str, Any]] = None) -> bool:
        store = active_store_or_none()
        if store is None:
            return False
        return store.finish_failure_threadsafe(
            job_id, str(error), payload=dict(payload) if payload else None)

    def reconcile_jobs(self) -> dict:
        """Nothing to reconcile, and that is a property rather than an omission.

        The in-memory store dies with the process, so a crash takes the jobs with it — there is no
        surviving record to be inconsistent WITH. Stuck non-terminal jobs inside a LIVE process are
        already reclaimed by `JobStore`'s own sweeper. Returning zeros here (rather than raising,
        or not existing) is what lets startup call this unconditionally instead of asking which
        backend it is on.
        """
        return {"class_a": 0, "class_b": 0, "scanned": 0}


def active_store_or_none():
    """The store bound to this context, or None. Absent is an answer, not an error — see
    `api/jobs/store.py`: outside a request, and under PostgreSQL, there is no store at all."""
    from .jobs.store import active_store

    return active_store()


# =====================================================================
# postgres adapter
# =====================================================================

_LEGACY_ID = __import__("re").compile(r"^[0-9a-f]{16}$")

#: Imported rather than redefined — see api/job_access.py.
from .job_access import CAPABILITY_PREFIX as _CAPABILITY_PRINCIPAL_PREFIX  # noqa: E402
from .job_access import USER_PREFIX as _USER_PRINCIPAL_PREFIX  # noqa: E402

#: A crawl plus LLM enrichment is minutes. Short leases would expire mid-scrape and make a healthy
#: job look crashed, which is worse than holding a lease slightly too long.
_SCRAPE_LEASE_SECONDS = 3600


def _utcnow():
    """The same clock the durable store stamps its own rows with, so one job's events cannot be
    ordered by two clocks that disagree."""
    from db.repositories import utcnow

    return utcnow()


#: Fields that become COLUMNS on `gallery_entries`. Everything else in the caller's dict is
#: metadata, and `image_b64` is the bytes rather than a property of the publication.
_GALLERY_COLUMNS = frozenset({
    "image_b64", "filename", "brand_name", "domain_key", "content_type", "style",
    "created_at", "owner_id", "unclaimed",
})


def _gallery_metadata(entry: Mapping[str, Any]) -> dict:
    """Everything the schema does not model, with ABSENCE PRESERVED.

    `is_preview` and `trust_violation` appear on 23 of the 28 measured historical entries. Filling
    the other five with `False` would be inventing a disclosure the source never made — and a
    render that was NOT recorded as a preview is a different statement from one recorded as not a
    preview. Sparse in, sparse out.
    """
    return {key: value for key, value in entry.items() if key not in _GALLERY_COLUMNS}


def _truthful_poster_entry(
    entry: Mapping[str, Any],
    *,
    payload: bytes,
) -> tuple[dict, Any]:
    """Derive every acceptance projection from raw authoritative QA and these exact bytes."""
    from poster.vision_qa import QA_FAIL, QA_PASS, poster_acceptance_from_output

    normalized = dict(entry)
    decision = poster_acceptance_from_output(
        normalized,
        payload=payload,
        expected_attempt_id=str(normalized.get("generation_attempt_id") or ""),
    )
    status = ("pass" if decision.qa_status == QA_PASS
              else "fail" if decision.qa_status == QA_FAIL
              else "not_checked")
    normalized.update({
        "qa_status": status,
        "qa_failed": status == "fail",
        "qa_note": "" if status == "pass" else decision.explanation,
        "canonical_qa_status": decision.qa_status,
        "qa_executed": decision.qa_executed,
        "qa_passed": decision.qa_passed,
        "accepted": decision.accepted,
        "publishable": decision.publishable,
        "deliverable": decision.deliverable,
        "acceptance_decision": decision.model_dump(mode="json"),
    })
    if normalized.get("canonical"):
        from poster.claim_assessment import project_factual_acceptance
        normalized = project_factual_acceptance(normalized, payload=payload)
    return normalized, decision


def _country_of(payload: Mapping[str, Any]):
    """The country decision for a profile, with its provenance (C6-R1).

    Reuses the SAME resolver the poster/creative path uses rather than re-deriving a country here.
    Two independent country rules would eventually disagree, and the stored `country_source` would
    then describe a decision no code made.
    """
    from poster.locale import country_decision
    return country_decision(payload)


class PostgresPersistence:
    """Adapts the verified 2C services to the same contract.

    Holds no session of its own: every call goes through `PersistenceService`, which opens and
    closes a unit of work per operation. Nothing here keeps a transaction open across a provider
    call, and nothing here talks to SQLAlchemy directly.
    """

    name = POSTGRES

    def __init__(self, service: Any) -> None:
        self._service = service

    # --- identity ---
    @staticmethod
    def _public(record: Any) -> Optional[dict]:
        """The shape the frontend already stores as `currentUser`. Never the password hash."""
        if record is None:
            return None
        return {"id": str(record.id), "email": record.email,
                "full_name": getattr(record, "full_name", "") or "",
                "created_at": getattr(record, "created_at", None)}

    def user_by_id(self, user_id: str) -> Optional[dict]:
        from uuid import UUID
        try:
            parsed = UUID(str(user_id))
        except (ValueError, AttributeError, TypeError):
            return None
        return self._public(self._service.user_by_id(parsed))

    def user_by_email(self, email: str) -> Optional[dict]:
        return self._public(self._service.user_by_email((email or "").strip().lower()))

    def user_for_token_subject(self, subject: str) -> Optional[dict]:
        """A token issued BEFORE cutover carries the legacy 16-hex id; one issued after carries the
        UUID. Both must resolve, and nothing else may.

        The legacy branch is an indexed `legacy_user_id` lookup, not a UUIDv5 recomputation: the
        mapping is already recorded in the row, and recomputing a hash on every authenticated
        request would make migration arithmetic a permanent part of the auth path. A subject that
        is neither shape is rejected outright rather than being coerced into a lookup.
        """
        text = str(subject or "").strip()
        if not text:
            return None
        from uuid import UUID
        try:
            return self._public(self._service.user_by_id(UUID(text)))
        except (ValueError, AttributeError, TypeError):
            pass
        if _LEGACY_ID.match(text):
            return self._public(self._service.user_by_legacy_id(text))
        return None

    def create_user(self, *, email: str, password: str, full_name: str = "") -> Optional[dict]:
        from api.user_auth import hash_password
        from db.errors import AlreadyExists

        canonical = (email or "").strip().lower()
        if not canonical:
            return None
        try:
            record = self._service.create_user(
                email=canonical, hashed_password=hash_password(password),
                full_name=(full_name or "").strip())
        except AlreadyExists:
            return None
        return self._public(record)

    def authenticate(self, *, email: str, password: str) -> Optional[dict]:
        """The hash format is self-describing (`pbkdf2_sha256$…`), so a migrated row verifies with
        the same function the file store used. No rehash, no reset, no plaintext anywhere.

        The stored hash never reaches this module: the persistence layer takes a verifier and
        returns only the public record, so a password hash cannot be logged or serialised by
        accident from up here.
        """
        from api.user_auth import verify_password

        return self._public(self._service.verify_credentials(
            email=(email or "").strip().lower(),
            verifier=lambda stored: verify_password(password, stored)))

    # --- brand ownership ---
    def owner_of_domain(self, domain_key: str) -> Optional[str]:
        """Advisory only — the final transaction re-checks under the row lock (C2)."""
        ownership = self._service.owner_of(domain_key)
        if ownership is None or not getattr(ownership, "exists", True):
            return None
        owner = getattr(ownership, "owner_id", None)
        return str(owner) if owner else ""

    @staticmethod
    def _uuid(value: Any):
        """A user/brand id, or None when the string cannot be one. Never a coerced lookup."""
        from uuid import UUID
        try:
            return UUID(str(value))
        except (ValueError, AttributeError, TypeError):
            return None

    def _brand_row(self, *, domain_key: str, user_id: str):
        """The BrandRecord this caller may see, or None. Absent and foreign-owned are one answer."""
        from db.errors import NotFound, NotVisible

        actor = self._uuid(user_id)
        if actor is None:
            return None
        try:
            return self._service.get_visible_brand(user_id=actor, domain_key=domain_key)
        except (NotFound, NotVisible):
            return None

    @property
    def _read_model(self):
        from api.read_model import BrandReadModel
        return BrandReadModel(self._service)

    def visible_brand(self, *, domain_key: str, user_id: str) -> Optional[dict]:
        row = self._brand_row(domain_key=domain_key, user_id=user_id)
        if row is None:
            return None
        return self._read_model.document(brand=row, user_id=self._uuid(user_id))

    def visible_brands(self, *, user_id: str) -> list[dict]:
        actor = self._uuid(user_id)
        if actor is None:
            return []
        rows = self._service.list_visible_brands(user_id=actor)
        return self._read_model.documents(brands=rows, user_id=actor)

    def archive_brand(self, *, domain_key: str, user_id: str) -> str:
        """Soft archive. §33/§49: history, profiles and Runs are untouched; visibility changes.

        The legacy vocabulary is preserved so the route does not have to branch on backend: DELETED
        when this call archived it, UNCLAIMED when nobody owns it (a human must claim it first),
        ABSENT otherwise — which also covers another account's brand, deliberately.
        """
        from api import brand_store as bs

        actor = self._uuid(user_id)
        if actor is None:
            return bs.NOT_FOUND
        ownership = self._service.owner_of(domain_key)
        if ownership is None or not getattr(ownership, "exists", False):
            return bs.NOT_FOUND
        if getattr(ownership, "owner_id", None) is None:
            return bs.UNCLAIMED
        archived = self._service.archive_brand(domain_key=domain_key, user_id=actor)
        return bs.DELETED if archived else bs.NOT_FOUND

    # --- enrichment ---
    def _enrichment_run_data(self, *, domain_key: str, user_id: str, kind: str,
                             patch: Mapping[str, Any]):
        """The `LiveRunData` both enrichment forms create, or None if this caller may not see it.

        ONE construction, deliberately. The route-facing form and the composed form differ only in
        who owns the transaction; if each built its own record they could drift in `kind`, in the
        `input_profile_id` linkage, or in the output schema version, and the composed path — the one
        with no separate commit to inspect afterwards — is where such a drift would be hardest to
        notice.
        """
        from db.models import RUN_KINDS
        from db.services import LiveRunData

        if kind not in RUN_KINDS:
            # The database would refuse this anyway (`kind_is_known`). Refusing here turns a
            # constraint violation mid-transaction into a plain programming error at the caller —
            # which matters more in the composed form, where that violation would abort a
            # transaction that also carries the caller's Job terminal event.
            raise ValueError(f"{kind!r} is not one of {RUN_KINDS}")
        actor = self._uuid(user_id)
        row = self._brand_row(domain_key=domain_key, user_id=user_id)
        if row is None or actor is None:
            return None
        return LiveRunData(
            brand_id=row.id, user_id=actor, kind=kind, status="succeeded",
            input_profile_id=row.current_profile_id, output=dict(patch),
            output_schema_version=1)

    def _canonical_strategy_run_data(self, *, domain_key: str, user_id: str, build: Any):
        """Materialize the pure Gate 2 build as one fully attributed immutable Run."""
        from db.services import LiveRunData

        actor = self._uuid(user_id)
        row = self._brand_row(domain_key=domain_key, user_id=user_id)
        if row is None or actor is None:
            return None
        dependencies = []
        for raw_id, relation in tuple(getattr(build, "dependencies", ()) or ()):
            ident = self._uuid(raw_id)
            if ident is None:
                raise ValueError("canonical strategy dependency is not a UUID")
            dependencies.append((ident, str(relation)))
        return LiveRunData(
            brand_id=row.id, user_id=actor, kind="strategy", status="succeeded",
            input_profile_id=row.current_profile_id,
            requested_config=dict(build.requested_config),
            effective_config=dict(build.effective_config), config_schema_version=1,
            output=dict(build.output), output_schema_version=int(build.schema_version),
            dependencies=tuple(dependencies))

    def record_enrichment(self, *, domain_key: str, user_id: str, kind: str,
                          patch: Mapping[str, Any]) -> Optional[dict]:
        """Store this stage's contribution as its OWN immutable succeeded Run.

        Not a document update. The patch becomes `runs.output`, the run becomes current for its kind
        under the approved `kind_seq` allocation, and the brand document is re-derived from the fold
        (see api/read_model.py). The previous run stays on disk and stays queryable — superseded,
        not overwritten — which is what makes a bad generation recoverable instead of destructive.

        This form owns its transaction and is what the routes call. A Job-backed finalizer must use
        `record_enrichment_locked` instead, so the Run and the Job terminal event commit as one fact.
        """
        data = self._enrichment_run_data(
            domain_key=domain_key, user_id=user_id, kind=kind, patch=patch)
        if data is None:
            return None
        self._service.create_live_run(data)
        return self.visible_brand(domain_key=domain_key, user_id=user_id)

    def record_enrichment_locked(self, uow, *, domain_key: str, user_id: str, kind: str,
                                 patch: Mapping[str, Any]):
        """The same enrichment Run, created INSIDE the caller's UnitOfWork. No commit here.

        §A. `record_enrichment` calls `create_live_run`, which opens and commits its own
        transaction. That is right for a route and wrong for a job-backed completion: the Run would
        commit while the Job that produced it is still running, which is the C3 Class-A window —
        successful durable business state beside a nonterminal Job, with nothing afterwards able to
        say whether the work finished. The scrape path closed that window; every enrichment path
        would have reopened it the moment a durable Job was wired behind these routes.

        Returns the RunRecord so the caller can name it in the terminal event's payload, and None
        when the brand is not visible — absent and foreign-owned stay one answer here too.

        NOT on `PersistenceProvider`: a UnitOfWork is a storage concept, and the provider protocol
        is deliberately domain operations only. A route that could take a transaction is a route
        that can branch on the backend.
        """
        from db.services import _create_live_run_locked

        data = self._enrichment_run_data(
            domain_key=domain_key, user_id=user_id, kind=kind, patch=patch)
        if data is None:
            return None
        return _create_live_run_locked(uow, data)

    def replace_section(self, *, domain_key: str, user_id: str, kind: str, section: str,
                        value: Mapping[str, Any]) -> Optional[dict]:
        """A new Run whose output carries the whole section — the old set is superseded, not erased.

        §29/§48: the legacy reset destroyed the previous candidate set in place, so "start over" and
        "the earlier faces never existed" were the same operation and there was no way to answer
        what the owner rejected. Here the previous Run stays queryable; it simply stops being
        current, and the fold in api/read_model.py replaces the section wholesale because a later
        run's value for a top-level key wins.
        """
        return self.record_enrichment(domain_key=domain_key, user_id=user_id, kind=kind,
                                      patch={section: dict(value)})

    # --- gallery ---
    def supports_gallery_writes(self) -> bool:
        """Yes, since the gallery cutover. Still ASKED before rendering, deliberately.

        This answered False for two phases while the gallery index was `history.json`: writing that
        file while PostgreSQL held the brand it belonged to would have split creative history
        across two stores with nothing able to say afterwards which was authoritative. The gap was
        checked BEFORE the render so the customer never paid for an image the system could not
        keep, and that ordering is kept now that the answer is True — a capability question asked
        after the money is spent is not a gate.
        """
        return True

    def poster_operations(self):
        """Keep PostgreSQL operation services behind the existing persistence seam."""
        from db.poster_operations import PosterOperations, PosterOperationError
        service=PosterOperations(self)
        class Boundary:
            def __getattr__(self, name):
                method=getattr(service,name)
                def invoke(*args,**kwargs):
                    try: return method(*args,**kwargs)
                    except PosterOperationError as exc:
                        from fastapi import HTTPException
                        raise HTTPException(exc.status_code,detail=exc.detail) from None
                return invoke
        return Boundary()

    def begin_canonical_poster_run(self, *, domain_key: str, user_id: str,
                                   contract: Mapping[str, Any]) -> dict:
        """Create the real poster Run and its first Attempt before any provider call."""
        from db.services import LiveRunData

        actor = self._uuid(user_id)
        strategy_id = self._uuid(contract.get("strategy_version_id"))
        profile_id = self._uuid(contract.get("profile_snapshot_id"))
        row = self._brand_row(domain_key=domain_key, user_id=user_id)
        if actor is None or strategy_id is None or profile_id is None or row is None:
            raise CapabilityNotWired("canonical poster lineage could not be resolved")
        if row.current_profile_id != profile_id:
            raise CapabilityNotWired("canonical poster profile is no longer current")

        run = self._service.create_live_run(LiveRunData(
            brand_id=row.id, user_id=actor, kind="poster", status="running",
            input_profile_id=profile_id,
            requested_config={
                "creative_contract_id": str(contract.get("creative_contract_id") or ""),
                "strategy_version_id": str(strategy_id),
            },
            effective_config={"creative_contract": dict(contract)},
            config_schema_version=1,
            dependencies=((strategy_id, "uses_strategy"),),
        ))
        try:
            attempt = self._service.create_creative_attempt(run_id=run.id, user_id=actor)
        except Exception as exc:
            self._service.finalize_run_failure(
                run_id=run.id, user_id=actor,
                error=f"poster attempt creation failed: {type(exc).__name__}")
            raise
        return {"run_id": str(run.id), "attempt_id": str(attempt.id)}

    def fail_canonical_poster_run(self, *, run_id: str, attempt_id: str,
                                  user_id: str, error: str) -> None:
        """Make a pre-publication canonical failure terminal; never mask the original error."""
        actor = self._uuid(user_id)
        ident = self._uuid(run_id)
        attempt_ident = self._uuid(attempt_id)
        if actor is None or ident is None or attempt_ident is None:
            return
        try:
            self._service.finalize_creative_failure(
                run_id=ident, attempt_id=attempt_ident, user_id=actor,
                error=str(error or "poster generation failed")[:500])
        except Exception:
            # The gallery path may already have finalized the Run before a later publication
            # failure. Terminal Runs are immutable; the caller still reports the gallery failure.
            _log.exception("canonical poster Run could not be marked failed: %s", ident)

    def record_gallery_entry(self, *, entry: Mapping[str, Any], user_id: str
                             ) -> Optional[dict]:
        """Durable bytes FIRST, then one short transaction for the identity and the publication.

        The ordering is §2 and it is not an implementation detail. `gallery_store.store` writes,
        fsyncs, atomically renames and then READS BACK to verify the digest; only after that does
        anything relational claim these bytes exist. If this method dies between the two, the
        result is an orphan file — recoverable and identifiable. The reverse ordering would leave a
        row pointing at bytes that never landed, which renders as a broken image forever and cannot
        be told apart from a real entry.
        """
        import base64

        from . import gallery_store

        raw = str(entry.get("image_b64") or "")
        if not raw:
            raise ValueError("a gallery entry needs image bytes")
        try:
            payload = base64.b64decode(raw, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("gallery image bytes are not valid base64") from exc

        if entry.get('canonical'):
            # The admitted immutable Run owns evidence authority, never caller-supplied JSON.
            admitted = self._service.run_for_user(
                run_id=self._uuid(entry.get('generation_run_id')), user_id=self._uuid(user_id))
            authoritative = (admitted.effective_config or {}).get('creative_contract') if admitted else None
            entry = dict(entry)
            if entry.get('creative_contract') != authoritative:
                entry['factual_assessment'] = None
            entry['creative_contract'] = authoritative
        entry, acceptance = _truthful_poster_entry(entry, payload=payload)
        stored = gallery_store.store(payload)          # <- durable BEFORE the transaction

        actor = self._uuid(user_id)
        domain_key = str(entry.get("domain_key") or "") or None
        # The Brand is resolved from the domain the CALLER named on a brand they own — the same
        # visibility rule the route already applied to authorize the render. That is proof, not
        # inference: it is this request's own subject. The import path never does this, because
        # there the domain is a string in a file and nothing connects it to a relational Brand.
        brand_id = None
        if domain_key and actor is not None:
            row = self._brand_row(domain_key=domain_key, user_id=user_id)
            brand_id = None if row is None else row.id

        metadata = _gallery_metadata(entry)
        execution: dict[str, str] = {}
        if bool(entry.get("canonical")):
            from io import BytesIO

            from PIL import Image
            from db.services import CreativeFinalization, VerifiedAssetData

            actor_id = self._uuid(user_id)
            run_id = self._uuid(entry.get("generation_run_id"))
            attempt_id = self._uuid(entry.get("generation_attempt_id"))
            if actor_id is None or run_id is None or attempt_id is None or brand_id is None:
                raise ValueError("canonical gallery entry needs a real Run, Attempt, Brand, and user")
            with Image.open(BytesIO(payload)) as rendered:
                width, height = rendered.size
            committed = self._service.finalize_creative(CreativeFinalization(
                user_id=actor_id,
                run_id=run_id,
                attempt_id=attempt_id,
                asset=VerifiedAssetData(
                    storage_key=stored.storage_key,
                    kind="poster",
                    mime=stored.mime,
                    byte_count=stored.byte_size,
                    checksum_sha256=stored.checksum,
                    durable=True,
                    created_by_user_id=actor_id,
                    width=width,
                    height=height,
                ),
                qa={
                    "strategy": entry.get("strategy_verification") or {},
                    "visual": entry.get("visual_qa") or {},
                    "trust_violation": bool(entry.get("trust_violation")),
                    "acceptance": acceptance.model_dump(mode="json"),
                },
                qa_passed=acceptance.qa_passed,
                output=metadata,
                output_schema_version=1,
                select_attempt=entry.get("accepted") is True,
            ))
            execution = {
                "generation_run_id": str(committed.run.id),
                "generation_attempt_id": str(committed.attempt.id),
                "asset_id": str(committed.asset.id),
            }
            metadata.update(execution)

        self._service.record_gallery_entry(
            checksum=stored.checksum, byte_size=stored.byte_size, mime=stored.mime,
            storage_key=stored.storage_key,
            filename=str(entry.get("filename") or f"{stored.checksum[:16]}.png"),
            owner_user_id=actor, brand_id=brand_id, domain_key=domain_key,
            brand_name=str(entry.get("brand_name") or "") or None,
            content_type=str(entry.get("content_type") or "") or None,
            style=str(entry.get("style") or "") or None,
            metadata=metadata, origin="live")
        if execution:
            return {**execution, **{key: entry.get(key) for key in (
                'accepted', 'publishable', 'deliverable', 'factual_eligible', 'draft',
                'factual_assessment')}}
        return None

    def gallery_history(self, *, user_id: str, limit: int = 100) -> list[dict]:
        """The same list shape the legacy index returns, rebuilt from rows plus the managed bytes.

        `image_b64` is reconstituted here because that is the public contract: the SPA renders
        `data:image/png;base64,` + this field directly. The bytes are NOT in the database — the
        entry names a checksum and the blob names a file — so this reads the managed store per row.

        An entry whose bytes are missing is returned WITHOUT `image_b64` rather than dropped. It is
        a real publication whose file is gone; hiding it would quietly shrink someone's history to
        make a storage fault invisible.
        """
        rows = self._service.gallery_history(user_id=self._uuid(user_id), limit=limit)
        return self._project_gallery_rows(rows)

    def poster_delivery(self, *, user_id: str, run_id: str):
        from db.poster_delivery import inspect
        from . import gallery_store
        return inspect(self._service, actor=user_id, run_id=run_id, load=gallery_store.load)

    def poster_gallery_result(self, *, user_id: str, run_id: str = "", domain_key: str = ""):
        row = self._service.poster_gallery_result(
            user_id=self._uuid(user_id), run_id=self._uuid(run_id), domain_key=domain_key or None)
        return self._project_gallery_rows([row])[0] if row else None

    @staticmethod
    def _project_gallery_rows(rows):
        import base64
        from . import gallery_store
        out: list[dict] = []
        for row in rows:
            item: dict[str, Any] = dict(row.metadata or {})
            item.update({
                "filename": row.filename,
                "brand_name": row.brand_name or "",
                "domain_key": row.domain_key or "",
                "content_type": row.content_type or "",
                "style": row.style or "",
                # Epoch seconds, the shape the legacy index recorded and the client already reads.
                "created_at": int(row.created_at.timestamp()) if row.created_at else 0,
            })
            if row.owner_user_id is None:
                # The six entries nobody can attribute. Flagged, never assigned to the asker.
                item["unclaimed"] = True
            else:
                item["owner_id"] = str(row.owner_user_id)
            payload = gallery_store.load(row.storage_key)
            if payload is not None:
                item["image_b64"] = base64.b64encode(payload).decode("ascii")
            out.append(item)
        return out

    # --- scrape lifecycle ---
    def begin_scrape(self, *, domain_key: str, user_id: str) -> str:
        """Create the durable Job BEFORE any crawling, and return its id.

        §8, and the reason it is job-first rather than brand-first: for a first-ever domain there is
        no Brand yet and there must not be one. A Brand created up front to "hold" the job would
        survive a failed scrape as a namespace nobody asked for — pollution that later reads cannot
        distinguish from a real, empty brand. So the Job carries `requested_domain_key` with
        `brand_id`/`run_id` NULL, and the Brand comes into existence only inside the success
        transaction.
        """
        actor = self._uuid(user_id)
        if actor is None:
            raise BrandNotVisible(domain_key)
        job = self._service.create_user_job(owner_user_id=actor,
                                            requested_domain_key=domain_key)
        # Claimed immediately: this process IS the worker, and `jobs.finish` refuses a job that is
        # not running under the claiming worker's lease. The lease is generous because a crawl plus
        # LLM enrichment is minutes, not seconds; a crashed process leaves the lease to expire and
        # become the reconciliation worker's problem, which is deliberately NOT wired in this phase.
        self._service.claim_job(job_id=job.id, worker_id=str(job.id),
                                lease_seconds=_SCRAPE_LEASE_SECONDS)
        return str(job.id)

    def commit_scrape(self, *, domain_key: str, user_id: str, ticket: str,
                      brand: Mapping[str, Any], profile: Mapping[str, Any],
                      source_url: str, display_name: str,
                      warning: str = "") -> Optional[dict]:
        """Brand + claim + immutable profile + scrape Run + Job terminal state, in ONE transaction.

        The crawl and any model call already finished before this is entered — nothing external runs
        while the brand row is locked (§51). A foreign owner discovered here, after a preflight that
        passed, makes the transaction refuse: the preflight is a cost gate and was never the
        concurrency authority.
        """
        from db.errors import ClaimLost, ForeignOwner, InvalidTransition
        from db.services import ProfileData, ScrapeSuccess

        actor = self._uuid(user_id)
        job_id = self._uuid(ticket)
        if actor is None or job_id is None:
            return None

        payload = dict(profile or {})
        country = _country_of(payload)
        try:
            result = self._service.commit_scrape_success(ScrapeSuccess(
                job_id=job_id, user_id=actor, worker_id=str(job_id),
                domain_key=domain_key, display_name=display_name or None,
                source_url=source_url or None,
                profile=ProfileData(
                    payload=payload, schema_version=1,
                    country_canonical=country.country, country_source=country.source,
                    country_raw=country.raw,
                    primary_language=str(payload.get("primary_language") or "") or None,
                    language_source="profile" if payload.get("primary_language") else None),
                requested_config={"source_url": source_url},
                warning=warning or None,
            ))
        except (ClaimLost, ForeignOwner):
            # The race loser. Nothing was written; the caller terminalizes the job separately so a
            # rolled-back business transaction cannot also lose the job's terminal state.
            return None
        except InvalidTransition:
            return None
        _ = result
        return self.visible_brand(domain_key=domain_key, user_id=user_id)

    def abandon_scrape(self, *, ticket: str, user_id: str, error: str) -> None:
        """Terminalize the durable Job after a failure, in its OWN transaction.

        §11: a first-ever failure leaves the Job failed and NO Brand, NO Profile, NO Run. Separate
        transaction on purpose — the business transaction has already rolled back, so terminalizing
        inside it would roll back too and leave the job running forever.
        """
        from db.errors import PersistenceError

        actor = self._uuid(user_id)
        job_id = self._uuid(ticket)
        if actor is None or job_id is None:
            return None
        try:
            self.finish_job_failure(job_id=str(job_id), error=str(error)[:500])
        except PersistenceError:
            # Already terminal (or claimed elsewhere). Terminal never reopens; nothing to repair.
            return None
        return None

    # --- gated state ---
    def approve_swot(self, *, domain_key: str, user_id: str,
                     expected_run_id: Any = None) -> dict:
        """Authorize first; bind the reviewed UUID inside the shared brand-lock transaction."""
        from db.errors import ApprovalMissing, NotVisible, StaleApprovalSubject

        actor = self._uuid(user_id)
        row = self._brand_row(domain_key=domain_key, user_id=user_id)
        if row is None or actor is None:
            raise BrandNotVisible(domain_key)
        submitted = self._uuid(expected_run_id) if isinstance(expected_run_id, str) else None
        if submitted is None:
            raise InvalidSwotSubject()
        try:
            approval = self._service.approve_current_swot(
                brand_id=row.id, user_id=actor, expected_run_id=submitted)
        except NotVisible as exc:
            raise SwotSubjectNotVisible() from exc
        except StaleApprovalSubject as exc:
            raise StaleApproval() from exc
        except ApprovalMissing as exc:
            raise SwotNotReady() from exc
        return {"swot_approved": approval.decision == "approved",
                "run_id": str(approval.subject_run_id)}

    def swot_is_approved(self, *, domain_key: str, user_id: str) -> bool:
        actor = self._uuid(user_id)
        row = self._brand_row(domain_key=domain_key, user_id=user_id)
        if row is None or actor is None:
            return False
        approval = self._service.approval_for_current_swot(brand_id=row.id, user_id=actor)
        return approval is not None and approval.decision == "approved"

    def supports_canonical_strategy(self) -> bool:
        return True

    def decide_strategy(self, *, domain_key: str, user_id: str, run_id: str,
                        decision: str, note: str = "") -> dict:
        from db.errors import ApprovalIneligible, ApprovalMissing, StaleApprovalSubject

        actor = self._uuid(user_id)
        row = self._brand_row(domain_key=domain_key, user_id=user_id)
        ident = self._uuid(run_id)
        if row is None or actor is None:
            raise BrandNotVisible(domain_key)
        if ident is None:
            raise StaleStrategyApproval(run_id)
        try:
            record = self._service.decide_current_strategy(
                brand_id=row.id, user_id=actor, run_id=ident,
                decision=str(decision).lower(), note=note or None)
        except ApprovalIneligible as exc:
            raise StrategyNotApprovable(str(exc)) from exc
        except ApprovalMissing as exc:
            raise StrategyNotReady(str(exc)) from exc
        except StaleApprovalSubject as exc:
            raise StaleStrategyApproval(str(exc)) from exc
        return {"run_id": str(record.subject_run_id), "decision": record.decision,
                "approval_state": record.decision}

    def canonical_strategy_context(self, *, domain_key: str, user_id: str,
                                   run_id: str) -> Optional[dict]:
        """Resolve the exact Run, its exact ProfileSnapshot, currentness, and real approval row.

        The JSON payload's embedded ``approval_state`` is intentionally ignored: Gate 2B proved
        that it may still say PENDING after the separate append-only Approval row says approved.
        """
        from db.errors import NotFound, NotVisible

        actor = self._uuid(user_id)
        brand = self._brand_row(domain_key=domain_key, user_id=user_id)
        ident = self._uuid(run_id)
        if actor is None or brand is None or ident is None:
            return None
        try:
            run = self._service.run_for_user(run_id=ident, user_id=actor)
        except (NotFound, NotVisible):
            return None
        current = self._service.current_successful_run(
            brand_id=brand.id, kind="strategy", user_id=actor)
        approval = (self._service.decision_for_current_strategy(
            brand_id=brand.id, user_id=actor)
                    if current is not None and current.id == run.id else None)
        profile = (self._service.profile_version(profile_id=run.input_profile_id)
                   if run.input_profile_id is not None else None)
        return {
            "strategy_version_id": str(run.id),
            "run_kind": run.kind,
            "run_status": run.status,
            "run_brand_id": str(run.brand_id),
            "brand_id": str(brand.id),
            "domain_reference": brand.domain_key,
            "is_current": bool(current is not None and current.id == run.id),
            "approval_state": (approval.decision if approval is not None else ""),
            "profile_snapshot_id": (str(profile.id) if profile is not None else ""),
            "run_input_profile_id": (str(run.input_profile_id) if run.input_profile_id else ""),
            "profile": (dict(profile.profile) if profile is not None else {}),
            "requested_config": dict(run.requested_config or {}),
            "output": dict(run.output or {}),
        }

    # --- owner inputs ---
    def owner_inputs(self, *, domain_key: str, user_id: str) -> Optional[dict]:
        actor = self._uuid(user_id)
        row = self._brand_row(domain_key=domain_key, user_id=user_id)
        if row is None or actor is None:
            return None
        return self._service.owner_inputs(brand_id=row.id, user_id=actor) or {}

    def set_owner_inputs(self, *, domain_key: str, user_id: str,
                         values: Mapping[str, Any]) -> Optional[dict]:
        actor = self._uuid(user_id)
        row = self._brand_row(domain_key=domain_key, user_id=user_id)
        if row is None or actor is None:
            return None
        self._service.set_owner_inputs(brand_id=row.id, user_id=actor, values=dict(values))
        return dict(values)

    # --- calendar targeting ---
    def resolve_calendar_post(self, *, domain_key: str, user_id: str,
                              post_id: str = "", date: str = "") -> dict:
        brand = self.visible_brand(domain_key=domain_key, user_id=user_id)
        if brand is None:
            raise BrandNotVisible(domain_key)
        posts = [p for p in ((brand.get("content_calendar") or {}).get("calendar") or [])
                 if isinstance(p, dict)]
        return _select_post(posts, post_id=post_id, date=date)

    # ------------------------------------------------------------------ jobs
    #
    # Every method here delegates to an existing, already-proven 2C service call. Nothing about job
    # state is reimplemented in the API layer: this class translates principals into the right
    # lookup and rows into the public projection, and that is all.

    def _job_row(self, job_id: str, principals: "Sequence[str]"):
        """The durable Job this caller may see, or None. Absent and foreign-owned are ONE answer.

        The principal decides WHICH lookup runs, not merely whether the answer is returned. A
        `user:` principal can only ever resolve through `job_for_user`, a `capability:` principal
        only through `job_for_capability` — so a capability token cannot reach a user-owned job even
        if it somehow matched, because the query that would find it is never issued.
        """
        from db.errors import NotFound, NotVisible

        ident = self._uuid(job_id)
        if ident is None:
            return None
        for principal in principals:
            text = str(principal or "")
            try:
                if text.startswith(_USER_PRINCIPAL_PREFIX):
                    actor = self._uuid(text[len(_USER_PRINCIPAL_PREFIX):])
                    if actor is None:
                        continue
                    return self._service.job_for_user(job_id=ident, user_id=actor)
                if text.startswith(_CAPABILITY_PRINCIPAL_PREFIX):
                    return self._service.job_for_capability(
                        job_id=ident,
                        capability_digest=text[len(_CAPABILITY_PRINCIPAL_PREFIX):])
            except (NotFound, NotVisible):
                continue
        return None

    def _claim(self, job_id) -> None:
        """Take the worker lease at creation, because THIS process is the worker.

        Not optional plumbing: `jobs.finish` refuses a job that is not running under the claiming
        worker's lease, so a job created and never claimed could emit progress and then be unable
        to terminalise — a job that can start and cannot finish. The lease is generous because a
        crawl plus model calls is minutes; an expired lease is what makes a crashed worker's job
        visible to reconciliation instead of invisible forever.
        """
        from db.errors import ConcurrencyConflict

        try:
            self._service.claim_job(job_id=job_id, worker_id=str(job_id),
                                    lease_seconds=_SCRAPE_LEASE_SECONDS)
        except ConcurrencyConflict:
            # Idempotent ONLY for the worker that already holds it — verified, not assumed.
            # "Someone claimed this" and "I claimed this" are different facts, and swallowing the
            # first as the second would let a second worker believe it may finish the job.
            row = self._service.job_by_id(job_id=job_id)
            if row is None or str(row.claimed_by or "") != str(job_id):
                raise

    def create_user_job(self, *, owner_user_id: str, domain_key: str = "") -> str:
        actor = self._uuid(owner_user_id)
        if actor is None:
            raise ValueError("a durable user job needs a real user id")
        job = self._service.create_user_job(
            owner_user_id=actor, requested_domain_key=domain_key or None)
        self._claim(job.id)
        return str(job.id)

    def create_capability_job(self, *, capability_digest: str, domain_key: str = "") -> str:
        """`capability_digest` is already a one-way digest — see `api/job_access`. The raw token
        never reaches this layer, and there is no code path that could store it."""
        digest = str(capability_digest or "")
        if digest.startswith(_CAPABILITY_PRINCIPAL_PREFIX):
            digest = digest[len(_CAPABILITY_PRINCIPAL_PREFIX):]
        if not digest:
            raise ValueError("a durable capability job needs a capability digest")
        job = self._service.create_capability_job(
            capability_digest=digest, requested_domain_key=domain_key or None)
        self._claim(job.id)
        return str(job.id)

    def job_snapshot(self, *, job_id: str, principals: "Sequence[str]") -> Optional[dict]:
        from .job_projection import project_job

        row = self._job_row(job_id, principals)
        if row is None:
            return None
        return project_job(
            job_id=str(row.id), status=row.status, current_stage=row.current_stage,
            domain_key=row.requested_domain_key, warning=row.warning, error=row.error)

    def job_result(self, *, job_id: str, principals: "Sequence[str]") -> Optional[dict]:
        """The business result, read from the durable Run the Job finished — not a second store.

        A succeeded Job carries `run_id`; the Run's `output` IS the result. Nothing is copied into
        a job-shaped result table, so "the job says succeeded" and "the business output exists"
        cannot disagree: they are one row reached through another.

        `status` is raw, not projected, because the route must tell "failed" from "not finished" to
        choose 500 vs 409 — a distinction the client projection deliberately collapses.
        """
        from db.errors import NotFound, NotVisible

        row = self._job_row(job_id, principals)
        if row is None:
            return None

        # WHICH STORE HOLDS THE ANSWER IS DECIDED HERE, ONCE. A run-backed job's authority is its
        # Run's output; a direct-result job's authority is its `job_results` row. The route asks for
        # "the result" and never learns which shape produced it — that is the whole point of putting
        # the resolution in the provider instead of letting a caller branch on `run_id`.
        profile = None
        source = None
        if row.status == "succeeded":
            if row.run_id is not None and row.owner_user_id is not None:
                try:
                    run = self._service.run_for_user(run_id=row.run_id, user_id=row.owner_user_id)
                    profile = dict(run.output) if run.output else None
                    source = "run"
                    # A SCRAPE run's output is a POINTER, not the deliverable: the profile itself is
                    # an immutable `profiles` version, and `commit_scrape_success` deliberately puts
                    # `profile_snapshot_id` in `runs.output` rather than a second copy of the JSON —
                    # two copies of one profile are two rows that can disagree. So the pointer is
                    # followed HERE. Following it to the brand's CURRENT profile instead would hand
                    # a later re-scrape's output to a caller asking about an older job.
                    if run.input_profile_id is not None and "profile_snapshot_id" in (profile or {}):
                        snapshot = self._service.profile_version(profile_id=run.input_profile_id)
                        if snapshot is not None:
                            profile = dict(snapshot.profile)
                except (NotFound, NotVisible):
                    profile = None
            else:
                direct = self._service.direct_job_result(job_id=row.id)
                if direct is not None:
                    profile = dict(direct.payload)
                    source = "job_result"
        return {
            "job_id": str(row.id),
            "status": row.status,
            "profile": profile,
            # The durable store records the artefact graph, not filesystem paths; the legacy store
            # answered with disk locations. None is the truthful answer here rather than a path no
            # row contains — and under PostgreSQL the file on disk is NOT the business authority.
            "manifest_path": None,
            "profile_path": None,
            "error": row.error or None,
            #: "run" | "job_result" | None. Diagnostic, not a branch for the route to take: a
            #: succeeded job with source None means the durable result is genuinely missing, which
            #: is a repair case rather than an empty answer.
            "result_source": source,
        }

    def job_events_since(self, *, job_id: str, principals: "Sequence[str]",
                         after_seq: int = 0, limit: int = 500) -> Optional[list[dict]]:
        """Durable events after `after_seq`, ordered by the per-job sequence.

        Ordered by `seq` and NOT by time: two events committed in one transaction share a timestamp
        to the microsecond, so a time ordering would be a coin flip and a resuming client could
        replay or skip one.
        """
        from .job_projection import project_event

        row = self._job_row(job_id, principals)
        if row is None:
            return None
        events = self._service.job_events_since(
            job_id=row.id, after_seq=max(0, int(after_seq or 0)),
            limit=max(1, int(limit or 500)))
        return [
            project_event(stage=ev.stage, status=ev.status, occurred_at=ev.occurred_at,
                          duration_ms=ev.duration_ms, payload=ev.payload,
                          error=ev.error, seq=ev.seq)
            for ev in events
        ]

    def job_is_terminal(self, *, job_id: str, principals: "Sequence[str]") -> bool:
        from .job_projection import TERMINAL_STATUSES

        row = self._job_row(job_id, principals)
        return bool(row is not None and row.status in TERMINAL_STATUSES)

    # --- job lifecycle: writes ---
    #
    # No principal is presented to these. They are the WORKER's half of the lifecycle, reached only
    # from the process that created the job and already holds its lease; the reads above are the
    # CLIENT's half and are ownership-scoped. Requiring a principal here would mean carrying the
    # caller's credentials into a background thread that outlives the request.

    def _status_of(self, job_id: str) -> Optional[str]:
        ident = self._uuid(job_id)
        if ident is None:
            return None
        row = self._service.job_by_id(job_id=ident)
        return None if row is None else str(row.status)

    def record_job_event(self, *, job_id: str, stage: str, status: str, occurred_at: Any,
                         duration_ms: int = 0, payload: Optional[Mapping[str, Any]] = None,
                         error: str = "") -> None:
        """One durable event, and the job transition it implies, in ONE short transaction.

        An `error` event terminalises the job here — that is §5's "one short truthful transaction",
        and it is why the pipeline's own failure path needs no second call to make the job failed.
        """
        from db.errors import PersistenceError

        ident = self._uuid(job_id)
        if ident is None:
            return
        try:
            self._service.append_job_event(
                job_id=ident, stage=str(stage), status=str(status), occurred_at=occurred_at,
                duration_ms=int(duration_ms or 0),
                payload=dict(payload) if payload is not None else None,
                error=error or None)
        except PersistenceError:
            # Already terminal, or the lease is gone. Progress after a decided outcome is not an
            # error to raise into a worker thread — the outcome stands and the event is dropped,
            # exactly as the in-memory store drops an event for a terminal job.
            return

    def hold_job_result(self, *, job_id: str, profile: Mapping[str, Any],
                        manifest_path: str = "", profile_path: str = "") -> None:
        """Deliberately nothing. The durable result is written ONLY inside the transaction that
        terminalises the job (`finish_direct_result_job`, `finish_enrichment_job`, `commit_scrape`).

        Writing it here would be the C3 Class-A window by construction: a committed business result
        beside a job still marked running, with nothing afterwards able to say whether the work
        finished. The pipeline's output is carried to the finalizer in memory instead, because in
        memory it is still just a value the caller holds — not a durable claim about what happened.
        """
        return None

    def finish_direct_result_job(self, *, job_id: str, profile: Mapping[str, Any],
                                 manifest_path: str = "", profile_path: str = "",
                                 payload: Optional[Mapping[str, Any]] = None) -> bool:
        """`job_results` row + Job succeeded + FINAL/DONE — one transaction, one commit.

        A missing profile answers False rather than committing an empty payload: `payload NOT NULL`
        exists so that "succeeded and produced nothing" is unrepresentable, and quietly writing
        `{}` here would route around it.
        """
        from db.errors import PersistenceError

        ident = self._uuid(job_id)
        if ident is None or not profile:
            return False
        try:
            _result, event = self._service.finish_direct_result_job_success(
                job_id=ident, worker_id=str(ident), result_kind="business_profile",
                payload=dict(profile),
                event_payload=dict(payload) if payload else None)
        except PersistenceError:
            return False
        if event is not None:
            return True
        # (None, None) means the job was ALREADY terminal. Truthful only if it is terminal as a
        # success — a failed job must not report that this call completed it.
        return self._status_of(job_id) == "succeeded"

    def finish_enrichment_job(self, *, job_id: str, domain_key: str, user_id: str, kind: str,
                              patch: Mapping[str, Any],
                              payload: Optional[Mapping[str, Any]] = None) -> Optional[dict]:
        """Enrichment Run + Job succeeded + FINAL/DONE — one transaction, one commit (§4).

        `record_enrichment` is NOT used: it commits the Run in a transaction of its own, which is
        right for a route and wrong here — see `db/services.finish_enrichment_job_success`.
        """
        from db.errors import PersistenceError

        ident = self._uuid(job_id)
        if ident is None:
            return None
        data = self._enrichment_run_data(
            domain_key=domain_key, user_id=user_id, kind=kind, patch=patch)
        if data is None:
            return None
        try:
            run, event = self._service.finish_enrichment_job_success(
                job_id=ident, worker_id=str(ident), data=data,
                event_payload=dict(payload) if payload else None)
        except PersistenceError:
            return None
        if run is None or event is None:
            return None
        return self.visible_brand(domain_key=domain_key, user_id=user_id)

    def finish_canonical_strategy_job(self, *, job_id: str, domain_key: str, user_id: str,
                                      build: Any,
                                      payload: Optional[Mapping[str, Any]] = None
                                      ) -> Optional[dict]:
        """Canonical Run + terminal Job event in the existing single transaction."""
        from db.errors import PersistenceError

        ident = self._uuid(job_id)
        if ident is None:
            return None
        data = self._canonical_strategy_run_data(
            domain_key=domain_key, user_id=user_id, build=build)
        if data is None:
            return None
        try:
            run, event = self._service.finish_enrichment_job_success(
                job_id=ident, worker_id=str(ident), data=data,
                event_payload=dict(payload) if payload else None)
        except PersistenceError:
            return None
        if run is None or event is None:
            return None
        return self.visible_brand(domain_key=domain_key, user_id=user_id)

    def finish_job_success(self, *, job_id: str, payload: Optional[Mapping[str, Any]] = None,
                           warning: str = "") -> bool:
        """Publish success for a job whose business state is ALREADY committed.

        The scrape finalizer commits the Brand, the Run, the job's terminal status and its terminal
        event together, so by the time this is reached the job is succeeded and there is nothing to
        publish. It says so by CHECKING the durable status rather than assuming it — an absence
        read as success is the failure mode this whole cutover is about — and appends the terminal
        event only for a job that somehow reached here still running.
        """
        from db.errors import PersistenceError

        # `warning` is already on the durable job row: the scrape finalizer committed it with the
        # terminal status, in the same transaction. There is nothing to write here and nothing to
        # lose by ignoring it — unlike the in-memory Job, which has no other writer.
        _ = warning
        status = self._status_of(job_id)
        if status == "succeeded":
            return True
        if status == "failed" or status is None:
            return False
        ident = self._uuid(job_id)
        try:
            event = self._service.append_job_event(
                job_id=ident, stage="final", status="done", occurred_at=_utcnow(),
                payload=dict(payload) if payload else None)
        except PersistenceError:
            return False
        return event is not None

    def finish_job_failure(self, *, job_id: str, error: str,
                           payload: Optional[Mapping[str, Any]] = None) -> bool:
        """ONE short transaction: the ERROR event and the failed job, together.

        Idempotent against a pipeline that already failed itself: a job that is terminal answers
        whether it is terminal as a FAILURE, so a caller cannot read "already decided" as "I failed
        it" when the decision was success.
        """
        from db.errors import PersistenceError

        ident = self._uuid(job_id)
        if ident is None:
            return False
        try:
            event = self._service.append_job_event(
                job_id=ident, stage="final", status="error", occurred_at=_utcnow(),
                error=str(error)[:500], payload=dict(payload) if payload else None)
        except PersistenceError:
            event = None
        if event is not None:
            return True
        return self._status_of(job_id) == "failed"

    # --- crash repair ---

    def reconcile_jobs(self) -> dict:
        """Repair jobs a dead worker left mid-flight. Idempotent and multi-process safe.

        TWO CLASSES, IN THIS ORDER, AND THE ORDER MATTERS.

        Class A — a successful durable result exists (a succeeded Run, or a `job_results` row) and
        the job never reached terminal. Normal finalisation cannot produce this: result, terminal
        status and terminal event commit together. It survives here for abnormal recovery only,
        and the repair VERIFIES the result before terminalising anything.

        Class B — the lease expired, the job is still running, and there is NO successful durable
        result. The truthful outcome is failure. Class A is repaired first because the durable
        detector for B matches on `run_id IS NULL`, which a direct-result job also satisfies; a
        Class-A job reaching `repair_class_b` would be marked failed on top of a result it really
        produced. `repair_class_b` refuses that independently, so the ordering is an optimisation
        and the refusal is the guarantee.

        Every repair locks its job row FOR UPDATE, so two processes reconciling at once serialise:
        one repairs, the other reads a terminal status and does nothing. Neither can write a second
        terminal event.
        """
        from db.errors import PersistenceError

        repaired = {"class_a": 0, "class_b": 0, "scanned": 0}
        seen: set = set()
        for detect in (self._service.detect_crash_class_a,
                       self._service.detect_crash_class_a_direct):
            for row in detect():
                if row.id in seen:
                    continue
                seen.add(row.id)
                repaired["scanned"] += 1
                try:
                    if self._service.repair_class_a(job_id=row.id):
                        repaired["class_a"] += 1
                except PersistenceError:
                    continue
        for row in self._service.detect_crash_class_b():
            if row.id in seen:
                continue
            seen.add(row.id)
            repaired["scanned"] += 1
            try:
                if self._service.repair_class_b(job_id=row.id):
                    repaired["class_b"] += 1
            except PersistenceError:
                continue
        return repaired


# =====================================================================
# selection
# =====================================================================

_lock = threading.Lock()
_cached: Optional[Any] = None
_cached_for: Optional[str] = None


def verify_postgres_ready() -> str:
    """Prove the database is reachable AND at the expected head. Never migrates.

    Running migrations from application startup would let a rollout silently reshape a database
    while requests are arriving. The application's job is to REFUSE to serve a schema it was not
    built against; moving the schema is an operator action.
    """
    from sqlalchemy import text as sql

    from db.config import DatabaseNotConfigured, database_url, safe_dsn
    from db.session import create_engine_from
    from db.config import get_settings

    try:
        dsn = database_url()
    except DatabaseNotConfigured as exc:
        raise BackendMisconfigured(
            f"{ENV_BACKEND}={POSTGRES} but no database is configured: {exc}") from exc

    engine = create_engine_from(get_settings())
    try:
        with engine.connect() as conn:
            head = conn.execute(sql("SELECT version_num FROM alembic_version")).scalar()
    except Exception as exc:  # noqa: BLE001 — any failure to reach/read the schema is fatal here
        raise BackendMisconfigured(
            f"{ENV_BACKEND}={POSTGRES} but the database at {safe_dsn(dsn)} could not be verified: "
            f"{type(exc).__name__}. Refusing to fall back to the legacy stores.") from exc
    finally:
        engine.dispose()

    if head != EXPECTED_SCHEMA_HEAD:
        raise BackendMisconfigured(
            f"database is at migration {head!r} but this build expects "
            f"{EXPECTED_SCHEMA_HEAD!r}. Run migrations deliberately; startup will not do it.")
    return head


def build_persistence(name: Optional[str] = None) -> Any:
    """Construct the selected backend. Raises rather than degrading."""
    chosen = name or selected_backend()
    if chosen == LEGACY:
        return LegacyPersistence()
    verify_postgres_ready()
    from db.services import PersistenceService
    from db.session import get_session_factory
    from db.uow import unit_of_work_factory

    return PostgresPersistence(
        PersistenceService(unit_of_work_factory(get_session_factory())))


def get_persistence() -> Any:
    """The one authority for this process. Cached per selected backend."""
    global _cached, _cached_for
    chosen = selected_backend()
    with _lock:
        if _cached is None or _cached_for != chosen:
            _cached = build_persistence(chosen)
            _cached_for = chosen
        return _cached


def reset_persistence() -> None:
    """Drop the cached backend. For tests and for an explicit operator-driven reconfiguration."""
    global _cached, _cached_for
    with _lock:
        _cached = None
        _cached_for = None


__all__ = [
    "BackendMisconfigured", "CapabilityNotWired", "EXPECTED_SCHEMA_HEAD", "ENV_BACKEND",
    "StrategyNotApprovable", "StrategyNotReady", "StaleStrategyApproval",
    "LEGACY", "POSTGRES", "LegacyPersistence", "PersistenceProvider", "PostgresPersistence",
    "build_persistence", "get_persistence", "reset_persistence", "selected_backend",
    "verify_postgres_ready",
]
