import hashlib
import uuid
from contextvars import ContextVar

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from app.dsl import Rule, RuleError, match
from app.migrate import check_schema

principal = ContextVar("principal", default="system")


class Conflict(ValueError):
    pass


class Store:
    def __init__(self, url, tenant=None):
        self.tenant = tenant
        self.pool = ConnectionPool(url, min_size=1, max_size=8, open=False,
                                   kwargs={"row_factory": dict_row, "connect_timeout": 10,
                                           "options": "-c search_path=public -c statement_timeout=15000 -c lock_timeout=10000"})

    def open(self):
        self.pool.open(wait=True, timeout=15)
        try:
            with self.pool.connection() as conn:
                check_schema(conn, self.tenant)
        except Exception:
            self.pool.close()
            raise

    def close(self):
        self.pool.close()

    def query(self, sql, args=()):
        with self.pool.connection() as conn:
            return conn.execute(sql, args).fetchall()

    def save_rule(self, rule, digest, text):
        with self.pool.connection() as conn:
            conn.execute("INSERT INTO rules(name) VALUES(%s) ON CONFLICT DO NOTHING", (rule.name,))
            conn.execute("SELECT name FROM rules WHERE name=%s FOR UPDATE", (rule.name,))
            previous = conn.execute("SELECT * FROM rule_versions WHERE rule_name=%s ORDER BY version DESC LIMIT 1", (rule.name,)).fetchone()
            if previous and previous["sha256"] == digest:
                return {"name": rule.name, "version": previous["version"], "sha256": digest, "unchanged": True}
            version = previous["version"] + 1 if previous else 1
            conn.execute("INSERT INTO rule_versions(rule_name,version,sha256,yaml,spec) VALUES(%s,%s,%s,%s,%s)",
                         (rule.name, version, digest, text, Jsonb(rule.model_dump())))
            self.audit(conn, "save_rule", rule.name, {"version": version, "sha256": digest})
            return {"name": rule.name, "version": version, "sha256": digest, "unchanged": False}

    @staticmethod
    def audit(conn, operation, target, detail):
        conn.execute("INSERT INTO audit(operation,target,detail) VALUES(%s,%s,%s)",
                     (operation, target, Jsonb({**detail, "principal": principal.get()})))

    def activate(self, name, version, digest):
        with self.pool.connection() as conn:
            row = conn.execute("SELECT v.* FROM rules r JOIN rule_versions v ON r.name=v.rule_name WHERE r.name=%s AND v.version=%s FOR UPDATE OF r", (name, version)).fetchone()
            if not row:
                raise KeyError(name)
            if row["sha256"] != digest:
                raise Conflict("Revision checksum mismatch")
            conn.execute("UPDATE rules SET active_version=%s WHERE name=%s", (version, name))
            self.audit(conn, "activate", name, {"version": version})
            return {"name": name, "active_version": version}

    def disable(self, name):
        with self.pool.connection() as conn:
            row = conn.execute("UPDATE rules SET active_version=NULL WHERE name=%s RETURNING name", (name,)).fetchone()
            if not row:
                raise KeyError(name)
            conn.execute("UPDATE runs SET status='failed',last_error='rule_disabled',updated_at=now() WHERE rule_name=%s AND status='pending'", (name,))
            self.audit(conn, "disable", name, {})
            return row

    @staticmethod
    def fanout(conn, event):
        versions = conn.execute("SELECT v.* FROM rules r JOIN rule_versions v ON v.rule_name=r.name AND v.version=r.active_version").fetchall()
        for version in versions:
            try:
                trace = match(Rule.model_validate(version["spec"]), event["source"], event["event_type"], event["payload"])
            except RuleError as exc:
                trace = {"matched": False, "error": str(exc)}
            inserted = conn.execute("INSERT INTO evaluations(event_id,rule_name,version,matched,trace) VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING matched",
                                   (event["id"], version["rule_name"], version["version"], trace["matched"], Jsonb(trace))).fetchone()
            if inserted and trace["matched"]:
                conn.execute("INSERT INTO runs(id,event_id,rule_name,version) VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                             (uuid.uuid4(), event["id"], version["rule_name"], version["version"]))
        conn.execute("UPDATE events SET processed_at=now(), attempts=attempts+1 WHERE id=%s", (event["id"],))

    def ingest(self, tenant, source, delivery_id, event_type, payload, raw, suppress=False):
        digest = hashlib.sha256(raw).hexdigest()
        with self.pool.connection() as conn:
            event = conn.execute("INSERT INTO events(id,tenant,source,delivery_id,event_type,payload,raw_body,sha256) VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING *",
                                 (uuid.uuid4(), tenant, source, delivery_id, event_type, Jsonb(payload), raw, digest)).fetchone()
            duplicate = event is None
            if duplicate:
                event = conn.execute("SELECT * FROM events WHERE tenant=%s AND source=%s AND delivery_id=%s", (tenant, source, delivery_id)).fetchone()
                if event["sha256"] != digest:
                    raise Conflict("Delivery identifier reused with different payload")
            elif suppress:
                conn.execute("UPDATE events SET processed_at=now(),attempts=1,last_error='ignored_jira_actor' WHERE id=%s", (event["id"],))
            else:
                self.fanout(conn, event)
            runs = conn.execute("SELECT id,status,rule_name,version FROM runs WHERE event_id=%s", (event["id"],)).fetchall()
            return {"event_id": event["id"], "duplicate": duplicate, "runs": runs}

    def execute(self, event_id):
        with self.pool.connection() as conn:
            event = conn.execute("SELECT * FROM events WHERE id=%s FOR UPDATE", (event_id,)).fetchone()
            if not event:
                raise KeyError(str(event_id))
            if event["last_error"] == "ignored_jira_actor":
                raise Conflict("Service-actor events cannot be executed")
            if event["payload_redacted_at"] is not None:
                raise Conflict("Retained delivery identity has no executable payload")
            self.fanout(conn, event)
            self.audit(conn, "evaluate_active_versions", str(event_id), {})
            return conn.execute("SELECT id,status,rule_name,version FROM runs WHERE event_id=%s", (event_id,)).fetchall()
