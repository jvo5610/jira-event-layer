"""Explicit, ordered, transactional migrations; runtime startup never applies DDL."""
import argparse
import hashlib
import re
import sys
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from app.config import secret, tenant_id
from app.schema import fingerprint

MIGRATIONS = Path(__file__).with_name("migrations")
OPTIONS = "-c search_path=public -c statement_timeout=60000 -c lock_timeout=30000"


def scripts():
    result = []
    for version, path in enumerate(sorted(MIGRATIONS.glob("*.sql")), 1):
        if not re.fullmatch(r"[0-9]{3}_[a-z][a-z0-9_]*\.sql", path.name) or int(path.name[:3]) != version:
            raise RuntimeError(f"Invalid migration sequence: expected {version:03d}_<name>.sql, found {path.name}")
        content = path.read_bytes()
        result.append((path.name, hashlib.sha256(content).hexdigest(), content.decode("utf-8")))
    if not result:
        raise RuntimeError("No migration files found; release is incomplete")
    return result


def has_history(conn):
    return conn.execute("SELECT to_regclass('public.schema_migrations') AS name").fetchone()["name"] is not None


def history(conn):
    try:
        return conn.execute("SELECT name,sha256,schema_sha256 FROM public.schema_migrations ORDER BY name").fetchall()
    except (psycopg.errors.UndefinedColumn, psycopg.errors.WrongObjectType):
        raise RuntimeError("Unsupported migration ledger; v1 requires a fresh installation database") from None


def validate_history(applied, available):
    """Only an exact prefix is valid; validate ALL checksums before executing SQL."""
    names = [name for name, _, _ in available]
    for position, row in enumerate(applied):
        if position >= len(names) or row["name"] != names[position]:
            raise RuntimeError(f"Database schema migration history is not a release prefix at {row['name']}; "
                               "missing/out-of-order migration or newer database; downgrade refused")
        expected = available[position][1]
        if row["sha256"] != expected:
            raise RuntimeError(f"Database schema migration checksum mismatch: {row['name']}; "
                               f"recorded={row['sha256']}, file={expected}. Restore the original file; use a new migration for changes")


def validate_installation(conn, tenant, applied):
    actual = fingerprint(conn)
    if actual != applied[-1]["schema_sha256"]:
        raise RuntimeError(f"Database schema drift after {applied[-1]['name']}: "
                           f"recorded={applied[-1]['schema_sha256']}, actual={actual}; investigate before migrating")
    row = conn.execute("SELECT tenant FROM public.installation WHERE singleton=true").fetchone()
    if not row or (tenant is not None and row["tenant"] != tenant):
        raise RuntimeError("TENANT_ID does not match this database installation")


def require_empty(conn):
    objects = conn.execute("""
        SELECT c.relname AS name FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public'
        UNION ALL SELECT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public'
        UNION ALL SELECT t.typname FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace
        WHERE n.nspname='public' AND t.typtype IN ('e','d','r','m') LIMIT 1
    """).fetchone()
    if objects:
        raise RuntimeError("Unmanaged nonempty public schema; automatic adoption is not supported. Use a fresh installation database")


def check_schema(conn, tenant=None):
    if not has_history(conn):
        raise RuntimeError("Database requires explicit migration: python -m app.migrate")
    applied, available = history(conn), scripts()
    validate_history(applied, available)
    if len(applied) != len(available):
        raise RuntimeError("Database schema has pending migrations; run python -m app.migrate with the matching release")
    validate_installation(conn, tenant, applied)


def migrate(url, tenant):
    available = scripts()
    with psycopg.connect(url, row_factory=dict_row, connect_timeout=10, options=OPTIONS) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(9824321)")
        if has_history(conn):
            applied = history(conn)
            validate_history(applied, available)
            if not applied:
                raise RuntimeError("Empty migration ledger is not a valid installation")
            validate_installation(conn, tenant, applied)
        else:
            require_empty(conn)
            applied = []
            conn.execute("""CREATE TABLE public.schema_migrations (
                name text PRIMARY KEY, sha256 text NOT NULL, schema_sha256 text NOT NULL,
                applied_at timestamptz NOT NULL DEFAULT now())""")
        for name, digest, sql in available[len(applied):]:
            conn.execute(sql)
            if not applied and name == available[0][0]:
                conn.execute("INSERT INTO public.installation(singleton,tenant) VALUES(true,%s)", (tenant,))
            conn.execute("INSERT INTO public.schema_migrations(name,sha256,schema_sha256) VALUES(%s,%s,%s)",
                         (name, digest, fingerprint(conn)))
        check_schema(conn, tenant)


def status(url, tenant):
    available = scripts()
    with psycopg.connect(url, row_factory=dict_row, connect_timeout=10, options=OPTIONS) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        applied = history(conn) if has_history(conn) else []
        validate_history(applied, available)
        if applied:
            validate_installation(conn, tenant, applied)
        else:
            require_empty(conn)
        return [{"name": name, "status": "applied" if i < len(applied) else "pending"}
                for i, (name, _, _) in enumerate(available)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", action="store_true", help="Read-only validation and list of applied/pending migrations")
    args = parser.parse_args()
    try:
        url, tenant = secret("DATABASE_URL", required=True), tenant_id()
        if args.status:
            for row in status(url, tenant):
                print(f"{row['status']:7} {row['name']}")
        else:
            migrate(url, tenant)
            print("Database migrations verified and applied")
    except Exception as exc:
        # DB errors can contain connection credentials; only intentional diagnostics are safe.
        print(str(exc) if isinstance(exc, RuntimeError) else f"Migration failed ({type(exc).__name__})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
