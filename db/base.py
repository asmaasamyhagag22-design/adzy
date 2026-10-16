"""Declarative base + the metadata every model and every migration shares.

The naming convention is not cosmetic. Alembic diffs constraints BY NAME, and PostgreSQL's
auto-generated names for a composite foreign key are not stable across the ways a constraint can
be declared. Naming them here means a future migration can drop or alter `fk_brands_current_profile`
by name instead of hunting for whatever the server called it.
"""
from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Base(DeclarativeBase):
    metadata = metadata
