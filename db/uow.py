# -*- coding: utf-8 -*-
"""One synchronous SQLAlchemy Session and one transaction per application-service call."""
from __future__ import annotations

from types import TracebackType
from typing import Callable

from sqlalchemy.orm import Session

from db.repositories import (
    ApprovalRepository,
    AssetRepository,
    AttemptRepository,
    BrandRepository,
    CalendarRepository,
    CompetitorRepository,
    GalleryBlobRepository,
    GalleryEntryRepository,
    JobEventRepository,
    JobResultRepository,
    JobRepository,
    OwnerInputRepository,
    ProfileRepository,
    RunRepository,
    UsageRepository,
    UserRepository,
)


class UnitOfWork:
    """Own BEGIN/COMMIT/ROLLBACK and expose repositories sharing exactly one Session."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory
        self.session: Session | None = None
        self._finished = False

    def __enter__(self) -> "UnitOfWork":
        if self.session is not None:
            raise RuntimeError("UnitOfWork cannot be entered twice")
        self.session = self._session_factory()
        self.session.begin()
        self.users = UserRepository(self.session)
        self.brands = BrandRepository(self.session)
        self.profiles = ProfileRepository(self.session)
        self.runs = RunRepository(self.session)
        self.approvals = ApprovalRepository(self.session)
        self.jobs = JobRepository(self.session)
        self.job_events = JobEventRepository(self.session)
        self.job_results = JobResultRepository(self.session)
        self.calendars = CalendarRepository(self.session)
        self.assets = AssetRepository(self.session)
        self.attempts = AttemptRepository(self.session)
        self.owner_inputs = OwnerInputRepository(self.session)
        self.competitors = CompetitorRepository(self.session)
        self.gallery_blobs = GalleryBlobRepository(self.session)
        self.gallery_entries = GalleryEntryRepository(self.session)
        self.usage = UsageRepository(self.session)
        return self

    def commit(self) -> None:
        if self.session is None or self._finished:
            raise RuntimeError("UnitOfWork has no active transaction")
        self.session.commit()
        self._finished = True

    def rollback(self) -> None:
        if self.session is not None and not self._finished:
            self.session.rollback()
            self._finished = True

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        assert self.session is not None
        try:
            if exc_type is not None or not self._finished:
                self.rollback()
        finally:
            self.session.close()
            self.session = None
        return False


def unit_of_work_factory(session_factory: Callable[[], Session]) -> Callable[[], UnitOfWork]:
    """Composition helper; no global Session and no database connection at import time."""
    return lambda: UnitOfWork(session_factory)


__all__ = ["UnitOfWork", "unit_of_work_factory"]
