# -*- coding: utf-8 -*-
"""Domain failure -> HTTP status, decided in ONE place.

Two things go wrong when each route maps its own errors. The statuses drift — the same conflict
answers 409 on one endpoint and 500 on another, and the SPA cannot write one handler. And the
messages drift, which is the dangerous half: a route that lets a persistence exception reach the
client publishes constraint names, table names, and sometimes the DSN. `psycopg` puts the failing
SQL in `str(exc)`, so "just return the exception text" is a disclosure, not a diagnostic.

So: every domain error is translated here, and the client gets a sentence written for a human. The
detail is a CONSTANT per error class — never interpolated from the exception — because that is the
only version of this rule that cannot be undone by a later edit that "just adds a bit of context".
The original exception is chained (`from exc`) so the server log keeps everything.

STATUS CHOICES, and why:

  404  absent, foreign-owned, and archived brands answer alike. Distinguishing them would let a
       caller enumerate other accounts' domains by status code.
  409  the state is real but not the one the request assumes: another account manages this brand,
       the SWOT moved, the date names two posts, the job already finished. Retrying MIGHT work
       after the caller does something, which is what separates 409 from 422.
  422  the request itself cannot be satisfied whatever the state.
  503  the persistence layer is unreachable. Explicitly NOT 500: the request was well-formed and
       will work later, and the operator needs these separated from bugs in the log.
"""
from __future__ import annotations

from fastapi import HTTPException

from api.persistence import (
    AmbiguousCalendarDate,
    BackendMisconfigured,
    BrandNotVisible,
    CalendarPostMissing,
    CapabilityNotWired,
    GalleryWriteFailed,
    StaleStrategyApproval,
    StaleApproval,
    StrategyNotApprovable,
    StrategyNotReady,
    SwotNotReady,
    InvalidSwotSubject,
    SwotSubjectNotVisible,
)

#: (exception, status, detail). Order matters only where classes are related; these are disjoint.
#: The detail is fixed text. Nothing from the exception is interpolated into it, ever.
_RULES: tuple[tuple[type[BaseException], int, str], ...] = (
    (BrandNotVisible, 404, "Brand not found."),
    (InvalidSwotSubject, 422, "swot_run_id must be a valid reviewed SWOT Run UUID."),
    (SwotSubjectNotVisible, 404, "SWOT run not found."),
    (CalendarPostMissing, 404, "That calendar post no longer exists."),
    (AmbiguousCalendarDate, 409,
     "More than one post is scheduled on that date. Choose the specific post."),
    (StaleApproval, 409,
     "The SWOT changed since you opened it. Review the current SWOT and approve it again."),
    (StaleStrategyApproval, 409,
     "The strategy changed since you opened it. Review the current strategy Run and decide again."),
    (SwotNotReady, 409, "Generate the SWOT before approving it."),
    (StrategyNotReady, 409, "Generate a canonical strategy before deciding it."),
    (StrategyNotApprovable, 409,
     "This strategy needs repair before it can be approved. Review its verifier defects."),
    # A capability gap and a misconfigured backend are both operator conditions, not client
    # mistakes, and both must read as "unavailable" rather than as a validation failure — a 4xx
    # here would tell the SPA to change the request, which cannot help.
    (CapabilityNotWired, 503,
     "This feature is not available on the configured storage backend."),
    (BackendMisconfigured, 503, "Storage is unavailable. Please try again shortly."),
    # The render happened and the bytes are on disk; only the record failed. 503 rather than 500
    # for the reason above — the request was well-formed and a retry can work — but with its own
    # sentence, because "unavailable" would not tell the owner that the image they just paid for
    # is not in their gallery.
    (GalleryWriteFailed, 503,
     "The image was generated but could not be saved to your gallery, so it is not there to "
     "open. Please try again."),
)


def _translate(exc: BaseException) -> HTTPException | None:
    for kind, status, detail in _RULES:
        if isinstance(exc, kind):
            return HTTPException(status_code=status, detail=detail)
    return None


def as_http(exc: BaseException) -> HTTPException:
    """The HTTPException for a domain error, or a bare 500 that discloses nothing.

    An unrecognised exception deliberately does NOT get its text forwarded. If a new domain error
    appears, the honest outcome is a generic 500 in the client and a full traceback in the log —
    not a leak that happens to be informative.
    """
    return _translate(exc) or HTTPException(status_code=500, detail="Internal error.")


def reraise_as_http(exc: BaseException) -> None:
    """Translate and raise if this is a known domain error; otherwise let the original propagate.

    Used in `except` blocks where an unknown exception should keep its own traceback and reach the
    server's error handler untouched, rather than being flattened into a 500 here.
    """
    translated = _translate(exc)
    if translated is not None:
        raise translated from exc
