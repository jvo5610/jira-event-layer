import logging
import signal
import time
import uuid
import threading
from urllib.parse import quote

import httpx
from psycopg.types.json import Jsonb

from app.config import Settings
from app.dsl import Rule, RuleError, render_action
from app.store import Store
from app.jira import AmbiguousWrite, ExecutionIdentity, Jira, PreflightRetry, WriteRateLimited
from app.health import worker_id
from app.transport import bounded_request

log = logging.getLogger("automation.worker")


class Bitbucket:
    def __init__(self, settings, client=None):
        self.settings = settings
        self.client = client or httpx.Client(timeout=20, follow_redirects=False)

    def call(self, method, action, *, body=None, external_uuid=None):
        if not self.settings.bitbucket_token or not self.settings.bitbucket_email:
            raise RuleError("connector_credentials_missing")
        target = f"{action.workspace}/{action.repository}"
        if target not in self.settings.allowed_repositories:
            raise RuleError("connector_target_not_allowed")
        url = f"https://api.bitbucket.org/2.0/repositories/{target}/pipelines/"
        if external_uuid:
            url += quote(external_uuid, safe="")
        return bounded_request(self.client, method, url, json=body,
                                   auth=(self.settings.bitbucket_email, self.settings.bitbucket_token),
                                   headers={"Accept": "application/json"})


class Worker:
    def __init__(self, store, settings, connector=None, jira=None):
        self.store = store
        self.settings = settings
        self.connector = connector or Bitbucket(settings)
        self.jira = jira or Jira(settings)

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
            return run

    def finish(self, run, status, *, error=None, external_uuid=None, result=None, delay=0, advance=False):
        with self.store.pool.connection() as conn:
            updated = conn.execute("UPDATE runs SET status=%s,last_error=%s,external_uuid=%s,external_result=%s,next_at=now()+%s*interval '1 second',lease_until=NULL,lease_token=NULL,step=step+%s,attempts=CASE WHEN %s THEN 0 ELSE attempts END,updated_at=now() WHERE id=%s AND lease_token=%s AND lease_until>now() RETURNING id",
                         (status, error, external_uuid, Jsonb(result) if result is not None else None,
                          delay, int(advance), advance, run["id"], run["lease_token"])).fetchone()
            if not updated:
                self.record(conn, run, "late_completion", {"external_uuid": external_uuid, "result": result, "error": error})
                return
            self.record(conn, run, status, {"error": error, "external_uuid": external_uuid, "result": result})

    def reserve_jira_write(self, run):
        with self.store.pool.connection() as conn:
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

    def tick_jira(self, run, rule, action, payload):
        try:
            self.settings.check_targets(rule)
            results = self.store.query("SELECT step,detail FROM run_log WHERE run_id=%s AND step<%s AND status IN ('pending','succeeded') ORDER BY id", (run["id"], run["step"]))
            context = dict(payload)
            context["_steps"] = {str(r["step"]): r["detail"]["result"] for r in results if r["detail"].get("result") is not None}
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
        run = self.claim()
        if run is None:
            return False
        rows = self.store.query("SELECT v.spec,e.payload FROM rule_versions v JOIN events e ON e.id=%s WHERE v.rule_name=%s AND v.version=%s", (run["event_id"], run["rule_name"], run["version"]))
        rule = Rule.model_validate(rows[0]["spec"])
        action = rule.actions[run["step"]]
        if action.type.startswith("jira."):
            return self.tick_jira(run, rule, action, rows[0]["payload"])
        polling = run["status"] == "waiting"
        if polling and run["attempts"] > 720:
            self.finish(run, "needs_review", error="poll_budget_exhausted", external_uuid=run["external_uuid"])
            return True
        try:
            self.settings.check_targets(rule)
            payload = None if polling else render_action(action, rows[0]["payload"])
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
    settings = Settings.from_env(mode="worker")
    store = Store(settings.database_url, settings.tenant)
    store.open()
    worker = Worker(store, settings)
    stop = threading.Event()

    def shutdown(*_):
        stop.set()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    try:
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
                log.error("worker_tick_failed kind=%s", type(exc).__name__)
                stop.wait(3)
    finally:
        worker.connector.client.close()
        worker.jira.client.close()
        try:
            store.query("DELETE FROM worker_heartbeats WHERE worker_id=%s RETURNING worker_id", (worker_id(),))
        except Exception:
            pass
        store.close()


if __name__ == "__main__":
    main()
