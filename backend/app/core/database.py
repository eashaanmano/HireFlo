"""SQLAlchemy engine and session management. Works with SQLite (local) and PostgreSQL, including Neon.

Neon notes
* Neon gives URLs like ``postgresql://user:pass@ep-x-pooler.region.aws.neon.tech/neondb?sslmode=require&channel_binding=require``.
  They are normalised to the psycopg 3 driver (``postgresql+psycopg://``) and keep their SSL parameters.
* A ``-pooler`` host goes through PgBouncer in transaction mode, which cannot keep server-side prepared statements,
  so psycopg's automatic preparation is switched off for it.
* Neon suspends idle compute. ``pool_pre_ping`` replaces connections that died while it slept, and
  ``pool_recycle`` retires connections before Neon closes them.
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


def normalise_url(url: str) -> str:
    """Accept the URL exactly as Neon, Heroku-style or SQLAlchemy writes it and pick the psycopg 3 driver."""
    url = url.strip().strip('"').strip("'")
    for prefix in ("postgres://", "postgresql://", "postgresql+psycopg2://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def is_neon(url: str) -> bool:
    return ".neon.tech" in (urlsplit(normalise_url(url)).hostname or "")


def _make_engine(url: str) -> Engine:
    url = normalise_url(url)
    kwargs: dict[str, Any] = {"pool_pre_ping": True, "future": True}
    if url.startswith("sqlite"):
        db_path = url.split("///", 1)[-1]
        if db_path and db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        host = urlsplit(url).hostname or ""
        connect_args: dict[str, Any] = {"connect_timeout": 15, "application_name": "hireflo"}
        if "-pooler" in host:
            connect_args["prepare_threshold"] = None  # PgBouncer transaction mode: no prepared statements
        kwargs.update(pool_size=5, max_overflow=5, pool_recycle=240, pool_timeout=30, connect_args=connect_args)
        if is_neon(url) and "sslmode=" not in url:
            kwargs["connect_args"]["sslmode"] = "require"
    eng = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        @event.listens_for(eng, "connect")
        def _sqlite_pragmas(dbapi_conn: Any, _record: Any) -> None:
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()
    return eng


engine: Engine = _make_engine(get_settings().database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def configure_engine(url: str, **kwargs: Any) -> None:
    """Rebind the global engine (used by tests)."""
    global engine
    engine = create_engine(url, **kwargs) if kwargs else _make_engine(url)
    SessionLocal.configure(bind=engine)


def create_all() -> None:
    from .. import models  # noqa: F401  (register models)

    Base.metadata.create_all(bind=engine)


def backend_name() -> str:
    """Human label for the health endpoint: sqlite, postgres or neon."""
    url = str(engine.url)
    if url.startswith("sqlite"):
        return "sqlite"
    return "neon" if ".neon.tech" in (engine.url.host or "") else "postgres"


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
