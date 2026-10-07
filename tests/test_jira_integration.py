import copy
import json

import httpx
import pytest
import yaml

from app.jira import Jira
from app.worker import Worker
from test_integration import activate, env, ingest
from test_jira import ACTIONS, config, issue, rule_for


def setup(env, actions, handler):
    client, store, settings = env
    cfg = config()
    for field in ("jira_email", "jira_token", "jira_cloud_id", "allowed_jira_projects"):
        setattr(settings, field, getattr(cfg, field))
    rule = rule_for(actions)
    activate(client, yaml.safe_dump(rule.model_dump()))
    return client, store, settings, Worker(store, settings, jira=Jira(settings, httpx.Client(transport=httpx.MockTransport(handler))))


def test_create_then_comment_reuses_durable_step_result(env):
    requests = []
    def handler(request):
        requests.append(request)
        if request.method == "GET":
            assert "/issue/DEMO-6" in request.url.path
            return httpx.Response(200, json=issue("DEMO-6"))
        return httpx.Response(201, json={"key": "DEMO-6", "id": "78"})
    comment = {"type": "jira.comment.add", "issue": {"path": "/_steps/0/key"}, "text": {"value": "Created from source"}}
    client, store, settings, worker = setup(env, [ACTIONS[0], comment], handler)
    event = ingest(client, payload={"issue": issue(), "_steps": {"0": {"key": "OTHER-1"}}})
    assert worker.tick()
    run = client.get("/v1/runs").json()[0]
    assert run["step"] == 1 and run["status"] == "pending"
    # New worker instance reads the first step's result from Postgres after a restart.
    restarted = Worker(store, settings, jira=Jira(settings, httpx.Client(transport=httpx.MockTransport(handler))))
    assert restarted.tick()
    assert client.get("/v1/runs").json()[0]["status"] == "succeeded"
    assert client.post(f'/v1/events/{event["event_id"]}/execute').status_code == 200
    assert not restarted.tick()
    assert [r.method for r in requests] == ["POST", "GET", "POST"]


@pytest.mark.parametrize("mode", ["timeout", "500", "invalid_response"])
def test_ambiguous_jira_write_stays_in_review_and_cannot_use_bitbucket_reconcile(env, mode):
    requests = []
    def handler(request):
        requests.append(request)
        if mode == "timeout":
            raise httpx.ReadTimeout("private message")
        return httpx.Response(500 if mode == "500" else 201, json={})
    client, store, settings, worker = setup(env, [ACTIONS[0]], handler)
    ingest(client, payload={"issue": issue()})
    assert worker.tick()
    run = client.get("/v1/runs").json()[0]
    assert run["status"] == "needs_review"
    assert not worker.tick() and len(requests) == 1
    assert "private message" not in json.dumps(run)
    assert client.post(f'/v1/runs/{run["id"]}/reconcile', json={"external_uuid": "00000000-0000-0000-0000-000000000000"}).status_code == 422


def test_jira_read_retries_without_reserving_write_budget(env):
    client, store, settings, worker = setup(env, [ACTIONS[2]], lambda _: httpx.Response(503))
    ingest(client)
    worker.tick()
    assert client.get("/v1/runs").json()[0]["status"] == "pending"
    assert store.query("SELECT count(*) n FROM jira_write_attempts")[0]["n"] == 0


def test_write_budget_prevents_runaway_even_for_distinct_events(env):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(201, json={"key": "DEMO-6"})
    client, store, settings, worker = setup(env, [ACTIONS[0]], handler)
    settings.jira_max_daily_writes = 1
    ingest(client, "first", {"issue": issue()})
    ingest(client, "second", {"issue": issue()})
    worker.tick()
    worker.tick()
    assert len(requests) == 1
    assert {r["status"] for r in client.get("/v1/runs").json()} == {"succeeded", "failed"}
    assert any(r["last_error"] == "jira_daily_write_budget_exhausted" for r in client.get("/v1/runs").json())


def test_disabled_rule_stops_pending_steps(env):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(201, json={"key": "DEMO-6"})
    client, _, _, worker = setup(env, [ACTIONS[0], ACTIONS[4]], handler)
    ingest(client, payload={"issue": issue()})
    worker.tick()
    assert client.post("/v1/rules/jira-lab/disable").status_code == 200
    assert not worker.tick() and len(calls) == 1
    assert client.get("/v1/runs").json()[0]["last_error"] == "rule_disabled"


def test_late_worker_cannot_overwrite_review_after_lease_expiry(env):
    client, store, _, worker = setup(env, [ACTIONS[0]], lambda _: pytest.fail("no request"))
    ingest(client, payload={"issue": issue()})
    claimed = worker.claim()
    with store.pool.connection() as conn:
        conn.execute("UPDATE runs SET lease_until=now()-interval '1 second'")
    assert worker.claim() is None
    worker.finish(claimed, "succeeded", result={"key": "DEMO-6"})
    run = client.get("/v1/runs").json()[0]
    assert run["status"] == "needs_review"
    log = client.get(f'/v1/runs/{run["id"]}').json()["log"]
    assert log[-1]["status"] == "late_completion"


def test_guard_false_stops_following_actions(env):
    client, store, _, worker = setup(env, [ACTIONS[6], ACTIONS[4]], lambda _: httpx.Response(200, json=issue(status="10000")))
    ingest(client)
    worker.tick()
    run = client.get("/v1/runs").json()[0]
    assert run["status"] == "succeeded" and run["external_result"]["guard_matched"] is False
    assert not worker.tick()
    assert store.query("SELECT count(*) n FROM jira_write_attempts")[0]["n"] == 0


def test_rate_limited_write_retries_and_consumes_budget(env):
    client, store, _, worker = setup(env, [ACTIONS[0]], lambda _: httpx.Response(429, headers={"Retry-After": "1"}))
    ingest(client, payload={"issue": issue()})
    worker.tick()
    run = client.get("/v1/runs").json()[0]
    assert run["status"] == "pending" and run["last_error"] == "jira_rate_limited"
    assert store.query("SELECT count(*) n FROM jira_write_attempts")[0]["n"] == 1


def test_cross_project_link_rejected_before_write(env):
    action = copy.deepcopy(ACTIONS[5])
    action["outward"] = {"value": "OTHER-1"}
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=issue())
    client, store, _, worker = setup(env, [action], handler)
    ingest(client)
    worker.tick()
    assert client.get("/v1/runs").json()[0]["status"] == "failed"
    assert all(r.method == "GET" for r in requests)
    assert store.query("SELECT count(*) n FROM jira_write_attempts")[0]["n"] == 0
