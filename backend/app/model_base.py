"""Stable ORM primitives shared by the generic host and business packs.

This module deliberately has no dependency on ``app.models`` or a selected
domain pack.  Pack model registries can therefore depend on it without
creating an ``app.models <-> pack.models`` import cycle.
"""
from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import DateTime, JSON, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


SHANGHAI = ZoneInfo("Asia/Shanghai")
J = JSON().with_variant(JSONB, "postgresql")


def now() -> datetime:
    return datetime.now(SHANGHAI)


def uid() -> str:
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class IdentityMixin:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
