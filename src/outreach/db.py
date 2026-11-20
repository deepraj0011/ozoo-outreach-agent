from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import DateTime, Engine, create_engine, event, inspect, text
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase
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


def make_engine(url: str, **kwargs: Any) -> Engine:
    engine = create_engine(url, **kwargs)
    if engine.dialect.name == "sqlite":
        database = engine.url.database or ""
        file_db = database not in ("", ":memory:") and "mode=memory" not in str(engine.url)

        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_conn, _record) -> None:  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            if file_db:
                # the web app and the worker write the same file: readers never block the writer,
                # and a writer waits up to 5 s for the lock instead of failing at once
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA busy_timeout=5000")
            cur.close()
            # pysqlite never emits BEGIN itself, so a SAVEPOINT would open (and its RELEASE
            # commit) the outer transaction. Take over transaction control (SQLAlchemy recipe).
            dbapi_conn.isolation_level = None

        @event.listens_for(engine, "begin")
        def _begin(conn) -> None:  # pragma: no cover - trivial
            conn.exec_driver_sql("BEGIN")

    return engine


# columns added to an existing table after it was first created (there are no migrations yet):
# init_db adds any that are missing, with their server default
LATE_COLUMNS = (
    ("p0_schedule_row", "mode"),
    ("p0_schedule_row", "at"),
    ("planned_email", "exact"),
)


def add_missing_columns(engine: Engine) -> int:
    """ALTER TABLE ... ADD COLUMN for every LATE_COLUMNS entry the database lacks. Idempotent; SQLite and
    Postgres. Returns how many columns were added."""
    from sqlalchemy.schema import CreateColumn

    added = 0
    with engine.begin() as conn:
        insp = inspect(conn)
        for table, name in LATE_COLUMNS:
            if not insp.has_table(table):
                continue
            if name in {c["name"] for c in insp.get_columns(table)}:
                continue
            column = Base.metadata.tables[table].c[name]
            ddl = CreateColumn(column).compile(dialect=engine.dialect)
            conn.execute(text(f"ALTER TABLE {engine.dialect.identifier_preparer.quote(table)} ADD COLUMN {ddl}"))
            added += 1
    return added


def init_db(engine: Engine) -> None:
    from . import models  # noqa: F401  (register tables)

    Base.metadata.create_all(engine)
    add_missing_columns(engine)
