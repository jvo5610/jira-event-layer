import logging
import signal
import time
import uuid
import threading
from urllib.parse import quote

import httpx
from psycopg.types.json import Jsonb

from app.config import Settings, jira_configured
from app.dsl import Rule, RuleError, render_action
from app.event_context import EVENT_FIELDS, action_context, event_document
from app.form_enrichment import build as build_form_enrichment, candidate as form_candidate
from app.store import Store
from app.jira import AmbiguousWrite, ExecutionIdentity, Jira, PreflightRetry, WriteRateLimited
from app.health import worker_id
from app.transport import bounded_request
from app.safety import validate_safety
from app.actors import credential_key, exclusion_reason
from app.observability import log_event

class Bitbucket:
    def __init__(self, settings, client=None):
        self.settings = settings
        self.client = client or httpx.Client(timeout=20, follow_redirects=False)

    def call(self, method, action, *, body=None, external_uuid=None):
        if not self.settings.bitbucket_token:
            raise RuleError("connector_credentials_missing")
        target = f"{action.workspace}/{action.repository}"
        url = f"https://api.bitbucket.org/2.0/repositories/{target}/pipelines/"
        if external_uuid:
            url += quote(external_uuid, safe="")
        return bounded_request(self.client, method, url, json=body,
                                   headers={"Accept": "application/json", "Authorization": f"Bearer {self.settings.bitbucket_token}"}, follow_redirects=False)


class Worker:
    def __init__(self, store, settings, connector=None, jira=None):
        self.store = store
        self.settings = settings
        self.connector = connector or Bitbucket(settings)
        self.jira = jira or Jira(settings)
        self._prefer_enrichment = True

    def ensure_service_identity(self):
        if not jira_configured(self.settings):
            return
        key = credential_key(self.settings)
        if getattr(self, '_identity_key', None) == key:
            return
        identifier = self.store.jira_actor_for_credential(self.settings)
        if identifier is None:
            identifier = self.jira.service_account()
            self.store.remember_jira_actor(self.settings, identifier)
        self._identity_key = key

    def check_event_actor(self, conn, run):
        event = conn.execute('SELECT * FROM events WHERE id=%s', (run['event_id'],)).fetchone()
        reason = exclusion_reason(conn, event)
        if reason:
            raise RuleError(reason)

    @staticmethod
    def record(conn, run, status, detail=None):
        conn.execute("INSERT INTO run_log(run_id,step,status,detail) VALUES(%s,%s,%s,%s)",
                     (run["id"], run["step"], status, Jsonb(detail or {})))

    def claim(self):
        with self.store.pool.connection() as conn:
            expired = conn.execute("UPDATE runs SET status='needs_review',last_error='dispatch_lease_expired',updated_at=now(),lease_until=NULL,lease_token=NULL WHERE status='dispatching' AND lease_until<now() RETURNING *").fetchall()
            for run in expired:
                self.record(conn, run, "needs_review", {"reason": "dispatch_lease_expired"})
            conn.execute("UPDATE runs SET status='failed',last_error='rule_disabled',updated_at=now() WHERE status='pending' AND rule_name IN (SELECT name FROM rules WHERE active_version IS NULL)")
            run = conn.execute("SELECT * FROM runs WHERE status IN ('pending','waiting') AND next_at<=now() AND (lease_until IS NULL OR lease_until<now()) ORDER BY next_at FOR UPDATE SKIP LOCKED LIMIT 1").fetchone()
            if not run:
                return None
            state = "dispatching" if run["status"] == "pending" else "waiting"
            run["lease_token"] = uuid.uuid4()
            conn.execute("UPDATE runs SET status=%s,attempts=attempts+1,lease_until=now()+interval '120 seconds',lease_token=%s,updated_at=now() WHERE id=%s", (state, run["lease_token"], run["id"]))
            run["attempts"] += 1
            self.record(conn, run, state)
        log_event("run_claimed", run_id=run["id"], event_id=run["event_id"],
                  step=run["step"], attempt=run["attempts"], status=state)
        return run

    def finish(self, run, status, *, error=None, external_uuid=None, result=None, delay=0, advance=False):
        with self.store.pool.connection() as conn:
            updated = conn.execute("UPDATE runs SET status=%s,last_error=%s,external_uuid=%s,external_result=%s,next_at=now()+%s*interval '1 second',lease_until=NULL,lease_token=NULL,step=step+%s,attempts=CASE WHEN %s THEN 0 ELSE attempts END,updated_at=now() WHERE id=%s AND lease_token=%s AND lease_until>now() RETURNING id",
                         (status, error, external_uuid, Jsonb(result) if result is not None else None,
                          delay, int(advance), advance, run["id"], run["lease_token"])).fetchone()
            if not updated:
                self.record(conn, run, "late_completion", {"external_uuid": external_uuid, "result": result, "error": error})
            else:
                self.record(conn, run, status, {"error": error, "external_uuid": external_uuid, "result": result})
        log_event("run_updated" if updated else "run_late_completion",
                  level=logging.WARNING if error or not updated else logging.INFO,
                  run_id=run["id"], event_id=run["event_id"], step=run["step"],
                  status=status if updated else "late_completion", error_present=bool(error))

    def reserve_jira_write(self, run):
        with self.store.pool.connection() as conn:
            self.check_event_actor(conn, run)
            conn.execute("SELECT pg_advisory_xact_lock(9824322)")
            active = conn.execute("SELECT id FROM runs WHERE id=%s AND lease_token=%s AND lease_until>now() AND status='dispatching' FOR UPDATE", (run["id"], run["lease_token"])).fetchone()
            if not active:
                raise RuleError("jira_execution_lease_lost")
            enabled = conn.execute("SELECT active_version FROM rules WHERE name=%s", (run["rule_name"],)).fetchone()
            if not enabled or enabled["active_version"] is None:
                raise RuleError("rule_disabled")
            used = conn.execute("SELECT count(*) n FROM jira_write_attempts WHERE created_at>now()-interval '24 hours'").fetchone()["n"]
            if used >= self.settings.jira_max_daily_writes:
                raise RuleError("jira_daily_write_budget_exhausted")
            conn.execute("INSERT INTO jira_write_attempts(run_id,step) VALUES(%s,%s)", (run["id"], run["step"]))

    def reserve_bitbucket_write(self, run):
        with self.store.pool.connection() as conn:
            self.check_event_actor(conn, run)
            conn.execute("SELECT pg_advisory_xact_lock(9824323)")
            active = conn.execute("SELECT id FROM runs WHERE id=%s AND lease_token=%s AND lease_until>now() AND status='dispatching' FOR UPDATE", (run["id"], run["lease_token"])).fetchone()
            enabled = conn.execute("SELECT active_version FROM rules WHERE name=%s", (run["rule_name"],)).fetchone()
            if not active or not enabled or enabled["active_version"] is None:
                raise RuleError("dispatch_not_authorized")
            used = conn.execute("SELECT count(*) n FROM connector_write_attempts WHERE connector='bitbucket' AND created_at>now()-interval '24 hours'").fetchone()["n"]
            if used >= self.settings.bitbucket_max_daily_writes:
                raise RuleError("bitbucket_daily_write_budget_exhausted")
            conn.execute("INSERT INTO connector_write_attempts(connector,run_id,step) VALUES('bitbucket',%s,%s)", (run["id"], run["step"]))

    def heartbeat(self):
        self.store.query("INSERT INTO worker_heartbeats(worker_id,last_seen) VALUES(%s,now()) ON CONFLICT(worker_id) DO UPDATE SET last_seen=now() RETURNING worker_id", (worker_id(),))

    def tick_enrichment(self):
        event = self.store.claim_enrichment()
        if event is None:
            return False
        status, error = "completed", None
        try:
            self.ensure_service_identity()
            metadata = form_candidate(event["source"], event["event_type"], event["payload"])
            if metadata is None:
                raise RuleError("jira_form_candidate_invalid")
            def renew():
                if not self.store.renew_enrichment(event):
                    raise RuleError("enrichment_lease_lost")
                self.heartbeat()
            index, forms = self.jira.forms(metadata["issue_key"], progress=renew)
            empty_retries = getattr(self.settings, "jira_forms_empty_retries", 10)
            unsubmitted = any(item.get("submitted") is not True for item in index)
            if unsubmitted and event["enrichment_attempts"] >= empty_retries:
                self.store.fail_enrichment(event, "jira_forms_not_submitted")
                status, error = "failed", "jira_forms_not_submitted"
            elif (not index or unsubmitted) and event["enrichment_attempts"] < empty_retries:
                delay = min(5 * event["enrichment_attempts"], 30)
                error = "jira_forms_not_submitted" if unsubmitted else "jira_forms_not_ready"
                self.store.retry_enrichment(event, error, delay)
                status = "pending"
            else:
                enrichment = build_form_enrichment(index, forms)
                if not self.store.complete_enrichment(event, enrichment):
                    status, error = "late_completion", "enrichment_lease_lost"
        except PreflightRetry as exc:
            if event["enrichment_attempts"] < 10:
                self.store.retry_enrichment(event, "jira_forms_retry", exc.delay)
                status, error = "pending", "jira_forms_retry"
            else:
                self.store.fail_enrichment(event, "jira_forms_retry_exhausted")
                status, error = "failed", "jira_forms_retry_exhausted"
        except (RuleError, ValueError, TypeError) as exc:
            error = str(exc)[:100] if isinstance(exc, RuleError) else "jira_form_invalid_data"
            self.store.fail_enrichment(event, error)
            status = "failed"
        log_event("event_enrichment_updated", level=logging.WARNING if error else logging.INFO,
                  event_id=event["id"], status=status, attempt=event["enrichment_attempts"],
                  error_present=bool(error))
        return True

    def execution_context(self, run, payload):
        # Only durable results of this run and earlier steps are trusted.
        # A webhook-supplied _steps object must never impersonate a result.
        results = self.store.query("SELECT step,detail FROM run_log WHERE run_id=%s AND step<%s AND status IN ('pending','succeeded') ORDER BY id", (run["id"], run["step"]))
        return action_context(payload, [{"step": row["step"], "result": row["detail"].get("result")}
                                        for row in results])

    def tick_jira(self, run, rule, action, payload):
        try:
            context = self.execution_context(run, payload)
            revision = self.store.query("SELECT sha256 FROM rule_versions WHERE rule_name=%s AND version=%s",
                                        (run["rule_name"], run["version"]))[0]
            identity = ExecutionIdentity(rule_name=run["rule_name"], rule_version=run["version"],
                                         rule_sha256=revision["sha256"], run_id=run["id"], step=run["step"])
            result = self.jira.execute(action, context, identity, lambda: self.reserve_jira_write(run))
        except (PreflightRetry, WriteRateLimited) as exc:
            self.finish(run, "pending" if run["attempts"] < 10 else "failed",
                        error="jira_preflight_retry" if isinstance(exc, PreflightRetry) else "jira_rate_limited", delay=exc.delay)
        except AmbiguousWrite as exc:
            self.finish(run, "needs_review", error=str(exc))
        except (RuleError, ValueError, TypeError) as exc:
            # Never include Pydantic/provider payloads or user field content in logs.
            self.finish(run, "failed", error=str(exc)[:100] if isinstance(exc, RuleError) else "jira_invalid_data")
        else:
            last = run["step"] + 1 == len(rule.actions) or result.get("stop", False)
            self.finish(run, "succeeded" if last else "pending", result=result, advance=not last)
        return True

    def tick(self):
        prefer_enrichment = getattr(self, "_prefer_enrichment", True)
        if prefer_enrichment and self.tick_enrichment():
            self._prefer_enrichment = False
            return True
        run = self.claim()
        if run is None:
            if not prefer_enrichment and self.tick_enrichment():
                return True
            return False
        self._prefer_enrichment = True
        if run['status'] != 'waiting':
            try:
                self.ensure_service_identity()
            except (PreflightRetry, RuleError, ValueError, httpx.HTTPError):
                self.finish(run, 'pending', error='jira_service_identity_unverified', delay=60)
                return True
            reason = self.store.exclude_event(run['event_id'])
            if reason:
                self.finish(run, 'failed', error=reason)
                return True
        columns = ",".join("e." + key for key in EVENT_FIELDS)
        rows = self.store.query(f"""SELECT v.spec,{columns}
            FROM rule_versions v JOIN events e ON e.id=%s
            WHERE v.rule_name=%s AND v.version=%s""",
            (run["event_id"], run["rule_name"], run["version"]))
        payload = event_document(rows[0])
        try:
            rule = Rule.model_validate(rows[0]['spec'])
            action = rule.actions[run['step']]
            # Finish observing an already dispatched pipeline; never submit another
            # write from an unsafe revision. A later step is checked on its next tick.
            if not (run['status'] == 'waiting' and action.type == 'bitbucket.pipeline'):
                validate_safety(rule)
        except (RuleError, ValueError):
            self.finish(run, 'failed', error='unsafe_rule_execution_blocked')
            return True
        if action.type.startswith("jira."):
            return self.tick_jira(run, rule, action, payload)
        polling = run["status"] == "waiting"
        if polling and run["attempts"] > 720:
            self.finish(run, "needs_review", error="poll_budget_exhausted", external_uuid=run["external_uuid"])
            return True
        try:
            payload = None if polling else render_action(action, self.execution_context(run, payload))
            if not polling:
                self.reserve_bitbucket_write(run)
            response = self.connector.call("GET" if polling else "POST", action, body=payload,
                                           external_uuid=run["external_uuid"] if polling else None)
        except (RuleError, ValueError) as exc:
            self.finish(run, "failed", error=str(exc)[:250])
            return True
        except httpx.HTTPError:
            self.finish(run, "waiting" if polling else "needs_review", error="poll_network_error" if polling else "ambiguous_dispatch_network_error",
                        external_uuid=run["external_uuid"], delay=30)
            return True
        if response.status_code == 429:
            try:
                delay = max(1, min(int(response.headers.get("Retry-After", "30")), 3600))
            except ValueError:
                delay = 30
            state = ("waiting" if polling else "pending") if run["attempts"] < 10 else "failed"
            self.finish(run, state, error="rate_limited", delay=delay, external_uuid=run["external_uuid"])
            return True
        if response.status_code >= 500 or (not polling and response.status_code in {408, 409}):
            self.finish(run, "waiting" if polling else "needs_review", error="provider_5xx", delay=30, external_uuid=run["external_uuid"])
            return True
        if response.status_code != (200 if polling else 201):
            self.finish(run, "failed", error=f"provider_http_{response.status_code}", external_uuid=run["external_uuid"])
            return True
        try:
            if len(response.content) > 256000:
                raise ValueError()
            data = response.json()
            identifier = data["uuid"]
            if not isinstance(identifier, str) or len(identifier) > 100:
                raise ValueError()
            if polling and identifier != run["external_uuid"]:
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            self.finish(run, "needs_review", error="invalid_provider_response", external_uuid=run["external_uuid"])
            return True
        if not polling:
            self.finish(run, "waiting", external_uuid=identifier, delay=5)
            return True
        state = data.get("state", {})
        if state.get("name") == "COMPLETED":
            result = {"state": "COMPLETED", "result": state.get("result", {}).get("name")}
            if result["result"] == "SUCCESSFUL":
                last = run["step"] + 1 == len(rule.actions)
                self.finish(run, "succeeded" if last else "pending", external_uuid=identifier if last else None,
                            result=result, advance=not last)
            else:
                self.finish(run, "failed", error="pipeline_unsuccessful", external_uuid=identifier, result=result)
        else:
            self.finish(run, "waiting", external_uuid=identifier, delay=10)
        return True


def main():
    logging.basicConfig(level=logging.INFO)
    log_event("worker_starting")
    settings = Settings.from_env(mode="worker")
    store = Store(settings.database, settings.tenant)
    store.open()
    worker = Worker(store, settings)
    stop = threading.Event()

    def shutdown(*_):
        stop.set()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    try:
        try:
            worker.ensure_service_identity()
        except (PreflightRetry, RuleError, ValueError, httpx.HTTPError) as exc:
            log_event("worker_identity_failed", level=logging.ERROR, error_kind=type(exc).__name__)
            raise SystemExit('Jira service identity could not be verified; GET /myself requires read:jira-user. Check token scopes and connectivity. No actions were dispatched.') from None
        log_event("worker_ready")
        last_heartbeat = 0
        while not stop.is_set():
            try:
                worked = worker.tick()
                if time.monotonic() - last_heartbeat > 10:
                    worker.heartbeat()
                    last_heartbeat = time.monotonic()
                if not worked:
                    stop.wait(1)
            except Exception as exc:
                # No exception payloads: HTTP/database errors may embed secrets.
                log_event("worker_tick_failed", level=logging.ERROR, error_kind=type(exc).__name__)
                stop.wait(3)
    finally:
        worker.connector.client.close()
        worker.jira.client.close()
        try:
            store.query("DELETE FROM worker_heartbeats WHERE worker_id=%s RETURNING worker_id", (worker_id(),))
        except Exception:
            pass
        store.close()
        log_event("worker_stopped")


if __name__ == "__main__":
    main()
