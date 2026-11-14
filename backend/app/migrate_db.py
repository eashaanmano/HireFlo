"""Copy every HireFlo table from one database to another, e.g. the local SQLite file to Neon.

    python -m app.migrate_db --to "postgresql://user:pass@ep-xxx-pooler.region.aws.neon.tech/neondb?sslmode=require"

* The source defaults to the local SQLite database (sqlite:///./data/driveops.db).
* The target schema is created if it doesn't exist. If the target already holds data the copy stops, unless
  --replace is given, which drops and recreates every HireFlo table there first.
* Rows are copied parent tables first, in batches, inside one transaction: either everything arrives or nothing.
* On PostgreSQL the id sequences are moved past the copied ids so new rows don't collide.
* Uploaded resume files live on disk (UPLOAD_DIR), not in the database, so they are not copied.
"""
from __future__ import annotations

import argparse
import sys

from sqlalchemy import func, inspect, select, text
from sqlalchemy.engine import Engine

from .core.database import Base, _make_engine, normalise_url

BATCH = 500


def _mask(url: str) -> str:
    """Hide the password when printing a URL."""
    if "@" not in url or "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    creds, host = rest.split("@", 1)
    user = creds.split(":", 1)[0]
    return f"{scheme}://{user}:***@{host}"


def _counts(eng: Engine) -> dict[str, int]:
    names = set(inspect(eng).get_table_names())
    out: dict[str, int] = {}
    with eng.connect() as c:
        for t in Base.metadata.sorted_tables:
            if t.name in names:
                out[t.name] = c.execute(select(func.count()).select_from(t)).scalar_one()
    return out


def _is_int(col) -> bool:  # noqa: ANN001
    try:
        return col.type.python_type is int
    except NotImplementedError:
        return False


def migrate(source_url: str, target_url: str, replace: bool = False, log=print) -> dict[str, int]:  # noqa: ANN001
    from . import models  # noqa: F401  (register every table)

    src, dst = _make_engine(source_url), _make_engine(target_url)
    if str(src.url) == str(dst.url):
        raise SystemExit("Source and target are the same database.")
    if replace:
        log("Dropping existing HireFlo tables on the target ...")
        Base.metadata.drop_all(dst)
    Base.metadata.create_all(dst)
    existing = {k: v for k, v in _counts(dst).items() if v}
    if existing:
        raise SystemExit("The target already has data (" + ", ".join(f"{k}={v}" for k, v in existing.items())
                         + "). Run again with --replace to overwrite it.")

    copied: dict[str, int] = {}
    src_tables = set(inspect(src).get_table_names())
    with src.connect() as s, dst.begin() as d:
        for table in Base.metadata.sorted_tables:
            if table.name not in src_tables:
                copied[table.name] = 0
                continue
            cols = [c.name for c in table.columns]
            src_cols = {c["name"] for c in inspect(src).get_columns(table.name)}
            usable = [c for c in cols if c in src_cols]
            n = 0
            result = s.execute(select(*[table.c[c] for c in usable]))
            while True:
                rows = result.fetchmany(BATCH)
                if not rows:
                    break
                d.execute(table.insert(), [dict(zip(usable, r, strict=True)) for r in rows])
                n += len(rows)
            copied[table.name] = n
            log(f"  {table.name:<28} {n:>6} rows")
        if d.dialect.name == "postgresql":
            for table in Base.metadata.sorted_tables:
                pk = list(table.primary_key.columns)
                if len(pk) == 1 and pk[0].autoincrement in (True, "auto") and _is_int(pk[0]):
                    col = pk[0].name
                    d.execute(text(
                        f"SELECT setval(pg_get_serial_sequence('\"{table.name}\"', '{col}'), "
                        f"COALESCE(MAX(\"{col}\"), 1), MAX(\"{col}\") IS NOT NULL) FROM \"{table.name}\""))
    return copied


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Copy HireFlo data between databases (e.g. SQLite to Neon).")
    p.add_argument("--from", dest="source", default="sqlite:///./data/driveops.db",
                   help="source database URL (default: local SQLite file)")
    p.add_argument("--to", dest="target", required=True, help="target database URL, e.g. your Neon URL")
    p.add_argument("--replace", action="store_true", help="drop and recreate HireFlo tables on the target first")
    a = p.parse_args(argv)
    source, target = normalise_url(a.source), normalise_url(a.target)
    print(f"From: {_mask(source)}\nTo:   {_mask(target)}")
    copied = migrate(source, target, a.replace)
    print(f"Done: {sum(copied.values())} rows in {len(copied)} tables. "
          "Now set DATABASE_URL to the target URL in backend/.env and restart the API.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
