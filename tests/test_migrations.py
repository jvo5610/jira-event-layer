"""Real PostgreSQL migration tests, always in a newly created disposable database."""
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

import app.migrate as migration
from app.schema import fingerprint
from test_integration import _test_database_config

BASELINE = Path("app/migrations/001_initial.sql").read_text()


@pytest.fixture
def release(tmp_path, monkeypatch):
    (tmp_path / "001_initial.sql").write_text(BASELINE)
    monkeypatch.setattr(migration, "MIGRATIONS", tmp_path)
    return tmp_path


@pytest.fixture
def database():
    source = make_conninfo(**_test_database_config())
    name = "automation_migration_" + uuid.uuid4().hex
    with psycopg.connect(source, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(name)))
    target = make_conninfo(source, dbname=name)
    try:
        yield target
    finally:
        with psycopg.connect(source, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


def connect(database):
    return psycopg.connect(database, row_factory=dict_row, options=migration.OPTIONS)


@pytest.mark.parametrize("name", ["003_gap.sql", "000_old.sql", "001_bad-name.sql", "initial.sql"])
def test_invalid_release_sequence(release, name):
    (release / name).write_text("SELECT 1")
    with pytest.raises(RuntimeError, match="Invalid migration sequence"):
        migration.scripts()


def test_empty_release_refused(release):
    (release / "001_initial.sql").unlink()
    with pytest.raises(RuntimeError, match="No migration files"):
        migration.scripts()


def test_fresh_install_status_repeat_and_concurrency(database, release):
    assert migration.status(database, "review") == [{"name": "001_initial.sql", "status": "pending"}]
    with connect(database) as conn:
        assert not migration.has_history(conn)  # --status has not created even the ledger.
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(lambda _: migration.migrate(database, "review"), range(3)))
    assert migration.status(database, "review") == [{"name": "001_initial.sql", "status": "applied"}]
    with connect(database) as conn:
        before = conn.execute("SELECT * FROM schema_migrations").fetchall()
    migration.migrate(database, "review")
    with connect(database) as conn:
        assert conn.execute("SELECT * FROM schema_migrations").fetchall() == before
        migration.check_schema(conn, "review")


def test_upgrade_preserves_data_and_rejects_old_release(database, release):
    migration.migrate(database, "review")
    with connect(database) as conn:
        conn.execute("INSERT INTO rules(name) VALUES('keep-me')")
    second = release / "002_correlation.sql"
    second.write_text("ALTER TABLE events ADD COLUMN correlation_id text;")
    assert [row["status"] for row in migration.status(database, "review")] == ["applied", "pending"]
    with connect(database) as conn, pytest.raises(RuntimeError, match="pending migrations"):
        migration.check_schema(conn, "review")
    migration.migrate(database, "review")
    with connect(database) as conn:
        assert conn.execute("SELECT name FROM rules").fetchone()["name"] == "keep-me"
        conn.execute("SELECT correlation_id FROM events")
        migration.check_schema(conn, "review")
    second.unlink()
    with pytest.raises(RuntimeError, match="downgrade refused"):
        migration.migrate(database, "review")


def test_checksum_mismatch_rejected_before_pending_sql(database, release):
    migration.migrate(database, "review")
    with connect(database) as conn:
        before = fingerprint(conn)
    (release / "001_initial.sql").write_text(BASELINE + "\n-- changed\n")
    (release / "002_pending.sql").write_text("CREATE TABLE must_not_exist(id integer);")
    with pytest.raises(RuntimeError, match=r"checksum mismatch: 001_initial.sql; recorded=.*file="):
        migration.migrate(database, "review")
    with connect(database) as conn:
        assert fingerprint(conn) == before
        assert conn.execute("SELECT count(*) AS n FROM schema_migrations").fetchone()["n"] == 1


def test_history_gap_rejected_before_pending_sql(database, release):
    (release / "002_middle.sql").write_text("SELECT 1;")
    (release / "003_last.sql").write_text("SELECT 1;")
    migration.migrate(database, "review")
    with connect(database) as conn:
        conn.execute("DELETE FROM schema_migrations WHERE name='002_middle.sql'")
    with pytest.raises(RuntimeError, match="not a release prefix"):
        migration.migrate(database, "review")


def test_retroactive_insert_rejected(database, release):
    migration.migrate(database, "review")
    (release / "001_initial.sql").rename(release / "002_initial.sql")
    (release / "001_earlier.sql").write_text("CREATE TABLE must_not_exist(id integer);")
    with pytest.raises(RuntimeError, match="not a release prefix"):
        migration.migrate(database, "review")


@pytest.mark.parametrize("ddl", [
    "ALTER TABLE events DROP CONSTRAINT events_tenant_source_delivery_id_key",
    "ALTER TABLE runs DROP CONSTRAINT runs_event_id_rule_name_version_key",
    "ALTER TABLE evaluations DROP CONSTRAINT evaluations_event_id_fkey",
    "ALTER TABLE events DROP COLUMN payload_redacted_at",
    "ALTER TABLE events ALTER COLUMN delivery_id DROP NOT NULL",
    "DROP INDEX runnable",
    "ALTER TABLE rules ALTER COLUMN created_at DROP DEFAULT",
    "ALTER SEQUENCE audit_id_seq INCREMENT BY 2",
    "ALTER TABLE runs ENABLE ROW LEVEL SECURITY",
])
def test_structural_drift_blocks_startup_and_upgrade(database, release, ddl):
    migration.migrate(database, "review")
    with connect(database) as conn:
        conn.execute(ddl)
    with connect(database) as conn, pytest.raises(RuntimeError, match="schema drift"):
        migration.check_schema(conn, "review")
    (release / "002_pending.sql").write_text("CREATE TABLE must_not_exist(id integer);")
    with pytest.raises(RuntimeError, match="schema drift"):
        migration.migrate(database, "review")
    with connect(database) as conn:
        assert conn.execute("SELECT to_regclass('must_not_exist') AS name").fetchone()["name"] is None


def test_unmanaged_database_is_not_adopted_or_changed(database, release):
    with connect(database) as conn:
        conn.execute("CREATE TABLE events(id integer); INSERT INTO events VALUES(123)")
    for operation in (migration.status, migration.migrate):
        with pytest.raises(RuntimeError, match="automatic adoption is not supported"):
            operation(database, "review")
    with connect(database) as conn:
        assert conn.execute("SELECT id FROM events").fetchone()["id"] == 123
        assert not migration.has_history(conn)


def test_old_ledger_rejected_without_upgrade(database, release):
    with connect(database) as conn:
        conn.execute("CREATE TABLE schema_migrations(name text,sha256 text)")
    with pytest.raises(RuntimeError, match="Unsupported migration ledger"):
        migration.migrate(database, "review")


def test_wrong_tenant_cannot_apply_pending_migrations(database, release):
    migration.migrate(database, "review")
    (release / "002_pending.sql").write_text("CREATE TABLE must_not_exist(id integer);")
    with pytest.raises(RuntimeError, match="TENANT_ID"):
        migration.migrate(database, "different")
    with connect(database) as conn:
        assert conn.execute("SELECT to_regclass('must_not_exist') AS name").fetchone()["name"] is None


@pytest.mark.parametrize("existing", [False, True])
def test_failed_batch_rolls_back_only_pending_changes(database, release, existing):
    if existing:
        migration.migrate(database, "review")
        with connect(database) as conn:
            conn.execute("INSERT INTO rules(name) VALUES('keep-me')")
    (release / "002_good.sql").write_text("CREATE TABLE must_rollback(id integer);")
    (release / "003_bad.sql").write_text("SELECT 1/0;")
    with pytest.raises(psycopg.errors.DivisionByZero):
        migration.migrate(database, "review")
    with connect(database) as conn:
        assert conn.execute("SELECT to_regclass('must_rollback') AS name").fetchone()["name"] is None
        if existing:
            assert conn.execute("SELECT name FROM rules").fetchone()["name"] == "keep-me"
            assert conn.execute("SELECT count(*) AS n FROM schema_migrations").fetchone()["n"] == 1
        else:
            assert not migration.has_history(conn)
            assert conn.execute("SELECT to_regclass('rules') AS name").fetchone()["name"] is None


def test_runtime_role_and_data_changes_do_not_change_fingerprint(database, release):
    migration.migrate(database, "review")
    role = "migration_runtime_" + uuid.uuid4().hex
    # Roles are cluster-global; always remove this uniquely named test role.
    with connect(database) as conn:
        conn.execute(sql.SQL("CREATE ROLE {} NOSUPERUSER").format(sql.Identifier(role)))
    try:
        with connect(database) as conn:
            conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role)))
            conn.execute(sql.SQL("GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO {}").format(sql.Identifier(role)))
            conn.execute(sql.SQL("GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO {}").format(sql.Identifier(role)))
            conn.execute(sql.SQL("REVOKE INSERT,UPDATE,DELETE ON schema_migrations,installation FROM {}").format(sql.Identifier(role)))
            before = fingerprint(conn)
            # Normal writes and nextval must not change the structure hash.
            conn.execute("INSERT INTO audit(operation,target,detail) VALUES('test','test','{}')")
            assert fingerprint(conn) == before
            conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))
            migration.check_schema(conn, "review")
    finally:
        with connect(database) as conn:
            conn.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
            conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
