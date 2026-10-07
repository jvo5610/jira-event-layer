"""Explicit, transactional, checksummed migrations. No DDL at runtime startup."""
import hashlib
import sys
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from app.config import secret, tenant_id

MIGRATIONS = Path(__file__).with_name("migrations")


def scripts():
    return [(p.name, hashlib.sha256(p.read_bytes()).hexdigest(), p.read_text())
            for p in sorted(MIGRATIONS.glob("[0-9]*.sql"))]


def check_schema(conn, tenant=None):
    if conn.execute("SELECT to_regclass('public.schema_migrations') AS name").fetchone()["name"] is None:
        raise RuntimeError("Database requires explicit migration: python -m app.migrate")
    applied = conn.execute("SELECT name,sha256 FROM schema_migrations ORDER BY name").fetchall()
    if [(r["name"], r["sha256"]) for r in applied] != [(name, digest) for name, digest, _ in scripts()]:
        raise RuntimeError("Database schema is missing, newer or modified; run the matching migration release")
    row = conn.execute("SELECT tenant FROM installation WHERE singleton=true").fetchone()
    if not row or (tenant is not None and row["tenant"] != tenant):
        raise RuntimeError("TENANT_ID does not match this database installation")


def migrate(url, tenant):
    with psycopg.connect(url, row_factory=dict_row, connect_timeout=10,
                         options="-c search_path=public -c statement_timeout=60000 -c lock_timeout=30000") as conn:
        conn.execute("SELECT pg_advisory_xact_lock(9824321)")
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name text PRIMARY KEY, sha256 text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())")
        applied = {r["name"]: r["sha256"] for r in conn.execute("SELECT * FROM schema_migrations")}
        available = scripts()
        if set(applied) - {name for name, _, _ in available}:
            raise RuntimeError("Database is newer than this release; downgrade refused")
        for name, digest, sql in available:
            if name in applied:
                if applied[name] != digest:
                    raise RuntimeError("Applied migration checksum mismatch")
                continue
            conn.execute(sql)
            conn.execute("INSERT INTO schema_migrations(name,sha256) VALUES(%s,%s)", (name, digest))
        # Preserve deduplication scope when adopting a pre-migration MVP database.
        old_tenants = conn.execute("SELECT DISTINCT tenant FROM events").fetchall()
        if any(row["tenant"] != tenant for row in old_tenants):
            raise RuntimeError("Existing events belong to another tenant; adoption refused")
        conn.execute("INSERT INTO installation(singleton,tenant) VALUES(true,%s) ON CONFLICT DO NOTHING", (tenant,))
        check_schema(conn, tenant)


def main():
    try:
        migrate(secret("DATABASE_URL", required=True), tenant_id())
    except Exception as exc:
        # DB errors can contain connection credentials; only intentional diagnostics are safe.
        print(str(exc) if isinstance(exc, RuntimeError) else f"Migration failed ({type(exc).__name__})", file=sys.stderr)
        return 1
    print("Database migrations verified and applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
