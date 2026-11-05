import hashlib
import uuid
from contextvars import ContextVar

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from app.dsl import Rule, RuleError, match
from app.event_context import event_document
from app.form_enrichment import candidate
from app.safety import validate_safety
from app.migrate import check_schema
from app.config import database_options
from app.actors import credential_key, exclusion_reason, account_id

principal = ContextVar("principal", default="system")


class Conflict(ValueError):
    pass


class Store:
    def __init__(self, database, tenant=None):
        self.tenant = tenant
        self.pool = ConnectionPool(min_size=1, max_size=8, open=False,
                                   kwargs={**database_options(database), "row_factory": dict_row, "connect_timeout": 10,
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

    def jira_actor_for_credential(self, settings):
        cloud, digest = credential_key(settings)
        rows = self.query("SELECT account_id FROM service_actor_credentials WHERE provider='jira' AND cloud_id=%s AND credential_sha256=%s", (cloud, digest))
        return rows[0]['account_id'] if rows else None

    def remember_jira_actor(self, settings, identifier):
        if account_id(identifier) is None:
            raise ValueError('Invalid service account identity')
        cloud, digest = credential_key(settings)
        with self.pool.connection() as conn:
            conn.execute("INSERT INTO service_actors(provider,account_id) VALUES('jira',%s) ON CONFLICT DO NOTHING", (identifier,))
            added = conn.execute("INSERT INTO service_actor_credentials(provider,cloud_id,credential_sha256,account_id) VALUES('jira',%s,%s,%s) ON CONFLICT DO NOTHING RETURNING account_id", (cloud, digest, identifier)).fetchone()
            stored = conn.execute("SELECT account_id FROM service_actor_credentials WHERE provider='jira' AND cloud_id=%s AND credential_sha256=%s", (cloud, digest)).fetchone()
            if stored['account_id'] != identifier:
                raise Conflict('Service credential identity changed unexpectedly')
            if added:
                self.audit(conn, 'register_service_actor', 'jira', {'account_id': identifier, 'cloud_id': cloud})

    @staticmethod
    def mark_excluded(conn, event, reason):
        conn.execute("""UPDATE events SET last_error=%s,processed_at=coalesce(processed_at,now()),
            attempts=greatest(attempts,1),
            enrichment_status=CASE WHEN enrichment_status IN ('pending','enriching') THEN 'failed' ELSE enrichment_status END,
            enrichment_error=CASE WHEN enrichment_status IN ('pending','enriching') THEN %s ELSE enrichment_error END,
            enrichment_lease_until=NULL,enrichment_lease_token=NULL
            WHERE id=%s""", (reason, reason, event['id']))
        cancelled = conn.execute("UPDATE runs SET status='failed',last_error=%s,updated_at=now(),lease_until=NULL,lease_token=NULL WHERE event_id=%s AND status='pending' RETURNING id,step", (reason, event['id'])).fetchall()
        for run in cancelled:
            conn.execute("INSERT INTO run_log(run_id,step,status,detail) VALUES(%s,%s,'failed',%s)", (run['id'], run['step'], Jsonb({'error': reason})))
        if event.get('last_error') != reason:
            Store.audit(conn, 'exclude_event', str(event['id']), {'reason': reason})

    def exclude_event(self, event_id):
        with self.pool.connection() as conn:
            event = conn.execute('SELECT * FROM events WHERE id=%s FOR UPDATE', (event_id,)).fetchone()
            reason = exclusion_reason(conn, event)
            if reason:
                self.mark_excluded(conn, event, reason)
                if event["enrichment_status"] in {"pending", "enriching"}:
                    event["enrichment_status"] = "failed"
            return reason

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
            validate_safety(Rule.model_validate(row['spec']))
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
        reason = exclusion_reason(conn, event)
        if reason:
            Store.mark_excluded(conn, event, reason)
            return
        versions = conn.execute("SELECT v.* FROM rules r JOIN rule_versions v ON v.rule_name=r.name AND v.version=r.active_version").fetchall()
        for version in versions:
            try:
                rule = Rule.model_validate(version['spec'])
                validate_safety(rule)
                trace = match(rule, event["source"], event["event_type"], event_document(event))
            except RuleError as exc:
                trace = {"matched": False, "error": str(exc)}
            inserted = conn.execute("INSERT INTO evaluations(event_id,rule_name,version,matched,trace) VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING matched",
                                   (event["id"], version["rule_name"], version["version"], trace["matched"], Jsonb(trace))).fetchone()
            if inserted and trace["matched"]:
                conn.execute("INSERT INTO runs(id,event_id,rule_name,version) VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                             (uuid.uuid4(), event["id"], version["rule_name"], version["version"]))
        conn.execute("UPDATE events SET processed_at=now(), attempts=attempts+1 WHERE id=%s", (event["id"],))

    def ingest(self, tenant, source, delivery_id, event_type, payload, raw):
        from app.observability import fingerprint, log_event
        if source == "jira" and event_type.rsplit(":", 1)[-1].startswith("filter_"):
            log_event("event_ignored", source=source, reason="ignored_event_type",
                      delivery_id_sha256=fingerprint(delivery_id))
            return {"event_id": None, "duplicate": False, "runs": [],
                    "ignored_reason": "ignored_event_type", "persisted": False,
                    "enrichment_status": "not_required"}
        digest = hashlib.sha256(raw).hexdigest()
        enrichment_status = "pending" if candidate(source, event_type, payload) else "not_required"
        with self.pool.connection() as conn:
            event = conn.execute("""INSERT INTO events(
                id,tenant,source,delivery_id,event_type,payload,raw_body,sha256,enrichment_status)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT DO NOTHING RETURNING *""",
                (uuid.uuid4(), tenant, source, delivery_id, event_type, Jsonb(payload), raw, digest,
                 enrichment_status)).fetchone()
            duplicate = event is None
            if duplicate:
                event = conn.execute("SELECT * FROM events WHERE tenant=%s AND source=%s AND delivery_id=%s", (tenant, source, delivery_id)).fetchone()
                if event["sha256"] != digest:
                    raise Conflict("Delivery identifier reused with different payload")
            reason = exclusion_reason(conn, event)
            if reason:
                self.mark_excluded(conn, event, reason)
                if event["enrichment_status"] in {"pending", "enriching"}:
                    event["enrichment_status"] = "failed"
            elif not duplicate and event["enrichment_status"] == "not_required":
                self.fanout(conn, event)
            runs = conn.execute("SELECT id,status,rule_name,version FROM runs WHERE event_id=%s", (event["id"],)).fetchall()
            result = {"event_id": event["id"], "duplicate": duplicate, "runs": runs,
                      "ignored_reason": reason, "enrichment_status": event["enrichment_status"]}
        log_event("event_ingested", event_id=event["id"], source=source if source in {"jira", "test"} else "other",
                  delivery_id_sha256=fingerprint(delivery_id), duplicate=duplicate,
                  runs_count=len(runs), ignored_reason=reason,
                  enrichment_status=event["enrichment_status"])
        return result

    def claim_enrichment(self):
        with self.pool.connection() as conn:
            conn.execute("""UPDATE events SET enrichment_status='pending',
                enrichment_error='enrichment_lease_expired',enrichment_lease_until=NULL,
                enrichment_lease_token=NULL,enrichment_next_at=now()
                WHERE enrichment_status='enriching' AND enrichment_lease_until<now()""")
            event = conn.execute("""SELECT * FROM events
                WHERE enrichment_status='pending' AND enrichment_next_at<=now()
                ORDER BY enrichment_next_at,received_at
                FOR UPDATE SKIP LOCKED LIMIT 1""").fetchone()
            if not event:
                return None
            token = uuid.uuid4()
            updated = conn.execute("""UPDATE events SET enrichment_status='enriching',
                enrichment_attempts=enrichment_attempts+1,
                enrichment_lease_until=now()+interval '120 seconds',
                enrichment_lease_token=%s,enrichment_error=NULL
                WHERE id=%s RETURNING *""", (token, event["id"])).fetchone()
            return updated

    def retry_enrichment(self, event, error, delay):
        with self.pool.connection() as conn:
            return conn.execute("""UPDATE events SET enrichment_status='pending',
                enrichment_error=%s,enrichment_next_at=now()+%s*interval '1 second',
                enrichment_lease_until=NULL,enrichment_lease_token=NULL
                WHERE id=%s AND enrichment_status='enriching'
                  AND enrichment_lease_token=%s AND enrichment_lease_until>now()
                RETURNING id""", (error, delay, event["id"], event["enrichment_lease_token"])).fetchone()

    def renew_enrichment(self, event):
        with self.pool.connection() as conn:
            return conn.execute("""UPDATE events
                SET enrichment_lease_until=now()+interval '120 seconds'
                WHERE id=%s AND enrichment_status='enriching'
                  AND enrichment_lease_token=%s AND enrichment_lease_until>now()
                RETURNING id""", (event["id"], event["enrichment_lease_token"])).fetchone()

    def fail_enrichment(self, event, error):
        with self.pool.connection() as conn:
            return conn.execute("""UPDATE events SET enrichment_status='failed',
                enrichment_error=%s,last_error=%s,
                enrichment_lease_until=NULL,enrichment_lease_token=NULL
                WHERE id=%s AND enrichment_status='enriching'
                  AND enrichment_lease_token=%s AND enrichment_lease_until>now()
                RETURNING id""", (error, error, event["id"], event["enrichment_lease_token"])).fetchone()

    def complete_enrichment(self, event, enrichment):
        with self.pool.connection() as conn:
            updated = conn.execute("""UPDATE events SET enrichment_status='completed',
                enrichment=%s,enriched_at=now(),enrichment_error=NULL,
                enrichment_lease_until=NULL,enrichment_lease_token=NULL
                WHERE id=%s AND enrichment_status='enriching'
                  AND enrichment_lease_token=%s AND enrichment_lease_until>now()
                RETURNING *""", (Jsonb(enrichment), event["id"],
                                  event["enrichment_lease_token"])).fetchone()
            if not updated:
                return False
            self.fanout(conn, updated)
            return True

    def requeue_enrichment(self, event_id):
        with self.pool.connection() as conn:
            event = conn.execute("SELECT * FROM events WHERE id=%s FOR UPDATE", (event_id,)).fetchone()
            if not event:
                raise KeyError(str(event_id))
            if event["payload_redacted_at"] is not None:
                raise Conflict("Retained delivery identity has no enrichable payload")
            if event["enrichment_status"] != "failed":
                raise Conflict("Only failed enrichment can be retried")
            reason = exclusion_reason(conn, event)
            if reason:
                raise Conflict("Excluded events cannot be enriched: " + reason)
            if candidate(event["source"], event["event_type"], event["payload"]) is None:
                raise Conflict("Event is not eligible for Jira Forms enrichment")
            row = conn.execute("""UPDATE events SET enrichment_status='pending',
                enrichment_attempts=0,enrichment_next_at=now(),enrichment_error=NULL,
                enrichment_lease_until=NULL,enrichment_lease_token=NULL,last_error=NULL
                WHERE id=%s RETURNING id,enrichment_status""", (event_id,)).fetchone()
            self.audit(conn, "retry_event_enrichment", str(event_id), {})
            return row

    def execute(self, event_id):
        with self.pool.connection() as conn:
            event = conn.execute("SELECT * FROM events WHERE id=%s FOR UPDATE", (event_id,)).fetchone()
            if not event:
                raise KeyError(str(event_id))
            if event["payload_redacted_at"] is not None:
                raise Conflict("Retained delivery identity has no executable payload")
            if event["enrichment_status"] not in {"not_required", "completed"}:
                raise Conflict("Event enrichment is not complete")
            reason = exclusion_reason(conn, event)
            if reason:
                self.mark_excluded(conn, event, reason)
            else:
                self.fanout(conn, event)
                self.audit(conn, "evaluate_active_versions", str(event_id), {})
                runs = conn.execute("SELECT id,status,rule_name,version FROM runs WHERE event_id=%s", (event_id,)).fetchall()
        if reason:
            raise Conflict('Excluded events cannot be executed: ' + reason)
        return runs
