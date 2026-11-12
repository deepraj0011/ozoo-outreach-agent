from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import DateTime, Engine, create_engine, event
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator):
    """Timezone-aware UTC datetimes on every backend (SQLite drops tzinfo)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: Optional[datetime], dialect: Dialect) -> Optional[datetime]:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime not allowed; pass timezone-aware UTC")
        return value.astimezone(timezone.utc)

    def process_result_value(self, value: Optional[datetime], dialect: Dialect) -> Optional[datetime]:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class Base(DeclarativeBase):
    pass


SessionLocal = sessionmaker(autoflush=False, expire_on_commit=False)


def make_engine(url: str, **kwargs: Any) -> Engine:
    engine = create_engine(url, **kwargs)
    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_conn, _record) -> None:  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    return engine


def init_db(engine: Engine) -> None:
    from . import models  # noqa: F401  (register tables)

    Base.metadata.create_all(engine)
