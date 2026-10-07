import hashlib
import hmac
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.store import Store
from app.migrate import migrate
from app.worker import Bitbucket, Worker

YAML = Path("examples/repository-request.yaml").read_text()
EVENT = json.loads(Path("examples/jira-event.json").read_text())


@pytest.fixture
def env():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL required for real Postgres tests")
    if url.rsplit("/", 1)[-1] != "automation_test":
        raise RuntimeError("Tests require a dedicated automation_test database")
    settings = Settings(url, "a"*40, "b"*40, "lab@example.com", "fake-test-token",
                        allowed_repositories=("jvidelaolmos/jira-event-layer-lab",),
                        tenant="test-installation", jira_required_label="automation-lab-managed")
    migrate(url, settings.tenant)
    store = Store(url)
    with TestClient(create_app(settings, store)) as client:
        with store.pool.connection() as conn:
            conn.execute("TRUNCATE audit,run_log,runs,evaluations,events,rule_versions,rules RESTART IDENTITY CASCADE")
        client.headers["Authorization"] = "Bearer " + settings.admin_token
        yield client, store, settings


def activate(client, text=YAML):
    response = client.post("/v1/rules", content=text)
    assert response.status_code == 201, response.text
    rule = response.json()
    response = client.post(f'/v1/rules/{rule["name"]}/activate', json={"version": rule["version"], "sha256": rule["sha256"]})
    assert response.status_code == 200, response.text
    return rule


def ingest(client, key="event-1", payload=EVENT):
    response = client.post("/v1/events", headers={"Idempotency-Key": key},
                           json={"source": "jira", "event_type": "jira:issue_updated", "payload": payload})
    assert response.status_code == 202, response.text
    return response.json()


def test_auth_and_body_limit(env):
    client, _, _ = env
    assert client.get("/v1/rules", headers={"Authorization": ""}).status_code == 401
    assert client.post("/v1/rules", content=b"x"*1048577).status_code == 413
    assert client.post("/v1/rules", content="invalid").status_code == 422


def test_yaml_openapi_contract(env):
    client, _, _ = env
    schema = client.get("/openapi.json").json()
    assert "application/yaml" in schema["paths"]["/v1/rules"]["post"]["requestBody"]["content"]


def test_non_json_numbers_rejected(env):
    client, _, _ = env
    response = client.post("/v1/events", headers={"Idempotency-Key": "bad-nan"},
                           content='{"source":"jira","event_type":"jira:issue_updated","payload":{"x":NaN}}')
    assert response.status_code == 400


def test_revisioning_draft_activation_and_rollback(env):
    client, _, _ = env
    v1 = activate(client)
    same = client.post("/v1/rules", content=YAML).json()
    assert same["unchanged"] and same["version"] == 1
    v2 = client.post("/v1/rules", content=YAML.replace("value: Done", "value: Closed")).json()
    assert v2["version"] == 2
    assert client.get("/v1/rules").json()[0]["active_version"] == 1
    assert client.post("/v1/rules/jira-repository-request/activate", json={"version": 1, "sha256": "0"*64}).status_code == 409
    event = ingest(client)
    assert len(event["runs"]) == 1
    assert event["runs"][0]["version"] == v1["version"]


def test_compare_does_not_execute(env):
    client, _, _ = env
    event = ingest(client)
    response = client.post("/v1/compare", json={"left_yaml": YAML, "right_yaml": YAML.replace("value: Done", "value: Closed"), "event_ids": [event["event_id"]]})
    assert response.status_code == 200, response.text
    assert response.json()["changed"] == 1
    assert response.json()["executed"] is False
    assert client.get("/v1/runs").json() == []


def test_duplicates_and_conflicting_payload(env):
    client, store, _ = env
    activate(client)
    a = ingest(client)
    b = ingest(client)
    assert a["event_id"] == b["event_id"] and b["duplicate"]
    assert len(client.get("/v1/runs").json()) == 1
    response = client.post("/v1/events", headers={"Idempotency-Key": "event-1"}, json={"source": "jira", "event_type": "jira:issue_updated", "payload": {}})
    assert response.status_code == 409
    assert store.query("SELECT processed_at FROM events")[0]["processed_at"] is not None


def test_concurrent_duplicate_deliveries(env):
    client, store, settings = env
    activate(client)
    raw = json.dumps(EVENT).encode()
    def send(_):
        return store.ingest(settings.tenant, "jira", "same-concurrent-id", "jira:issue_updated", EVENT, raw)
    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(send, range(10)))
    assert len({r["event_id"] for r in results}) == 1
    assert sum(not r["duplicate"] for r in results) == 1
    assert len(store.query("SELECT * FROM runs")) == 1


def test_signed_jira_webhook(env):
    client, _, settings = env
    activate(client)
    raw = json.dumps(EVENT).encode()
    headers = {"X-Atlassian-Webhook-Identifier": "jira-native-1", "X-Hub-Signature": "sha256=" + hmac.new(settings.webhook_secret.encode(), raw, hashlib.sha256).hexdigest()}
    assert client.post("/webhooks/jira", content=raw).status_code == 401
    assert client.post("/webhooks/jira", content=raw, headers=headers).status_code == 202
    assert client.post("/webhooks/jira", content=raw, headers=headers).json()["duplicate"]
    assert client.post("/webhooks/jira", content=raw+b" ", headers=headers).status_code == 401


def test_disable_prevents_new_matches(env):
    client, _, _ = env
    activate(client)
    client.post("/v1/rules/jira-repository-request/disable")
    assert ingest(client)["runs"] == []


def test_target_allowlist(env):
    client, _, _ = env
    response = client.post("/v1/rules", content=YAML.replace("workspace: jvidelaolmos", "workspace: other-company"))
    assert response.status_code == 422


def worker_for(store, settings, handler):
    return Worker(store, settings, Bitbucket(settings, httpx.Client(transport=httpx.MockTransport(handler))))


def test_worker_dispatch_poll_success_and_no_duplicate(env):
    client, store, settings = env
    activate(client)
    ingest(client)
    methods = []
    def handler(request):
        methods.append(request.method)
        if request.method == "POST":
            body = json.loads(request.content)
            assert body["target"]["selector"]["pattern"] == "setup-new-repository"
            assert len(body["variables"]) == 7
            return httpx.Response(201, json={"uuid": "{test-pipeline}"})
        return httpx.Response(200, json={"uuid": "{test-pipeline}", "state": {"name": "COMPLETED", "result": {"name": "SUCCESSFUL"}}})
    worker = worker_for(store, settings, handler)
    assert worker.tick()
    assert client.get("/v1/runs").json()[0]["status"] == "waiting"
    with store.pool.connection() as conn:
        conn.execute("UPDATE runs SET next_at=now()")
    assert worker.tick()
    ingest(client)
    assert not worker.tick()
    assert methods == ["POST", "GET"]
    assert client.get("/v1/runs").json()[0]["status"] == "succeeded"


@pytest.mark.parametrize("failure", ["timeout", "500", "invalid_response"])
def test_ambiguous_posts_never_retry(env, failure):
    client, store, settings = env
    activate(client)
    ingest(client)
    calls = []
    def handler(request):
        calls.append(request.method)
        if failure == "timeout":
            raise httpx.ReadTimeout("timeout")
        return httpx.Response(500 if failure == "500" else 201, json={})
    worker = worker_for(store, settings, handler)
    worker.tick()
    assert client.get("/v1/runs").json()[0]["status"] == "needs_review"
    assert not worker.tick()
    assert calls == ["POST"]


def test_expired_dispatch_lease_requires_review(env):
    client, store, settings = env
    activate(client)
    ingest(client)
    with store.pool.connection() as conn:
        conn.execute("UPDATE runs SET status='dispatching',lease_until=now()-interval '1 second'")
    worker = worker_for(store, settings, lambda _: pytest.fail("must not call Bitbucket"))
    assert not worker.tick()
    assert client.get("/v1/runs").json()[0]["status"] == "needs_review"


def test_rate_limit_safe_retry(env):
    client, store, settings = env
    activate(client)
    ingest(client)
    worker = worker_for(store, settings, lambda _: httpx.Response(429, headers={"Retry-After": "1"}))
    worker.tick()
    run = client.get("/v1/runs").json()[0]
    assert run["status"] == "pending" and run["last_error"] == "rate_limited"


def test_two_workers_claim_once(env):
    client, store, settings = env
    activate(client)
    ingest(client)
    workers = [Worker(store, settings), Worker(store, settings)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda w: w.claim(), workers))
    assert sum(r is not None for r in claims) == 1


def test_literal_field_and_array_paths_reach_pipeline_from_signed_webhook(env):
    client, store, settings = env
    spec = yaml.safe_load(YAML)
    literal = 'issue.fields["repository.name"][0].value'
    spec["actions"][0]["variables"]["REPOSITORY_NAME"] = {"path": literal}
    spec["when"]["all"].append({"path": literal, "op": "eq", "value": "service-payments"})
    rule = activate(client, yaml.safe_dump(spec))
    payload = json.loads(json.dumps(EVENT))
    payload["issue"]["fields"]["repository.name"] = [{"value": "service-payments"}]
    raw = json.dumps(payload).encode()
    headers = {"X-Atlassian-Webhook-Identifier": "literal-path-delivery",
               "X-Hub-Signature": "sha256=" + hmac.new(settings.webhook_secret.encode(), raw, hashlib.sha256).hexdigest()}
    first = client.post("/webhooks/jira", content=raw, headers=headers)
    assert first.status_code == 202 and len(first.json()["runs"]) == 1
    duplicate = client.post("/webhooks/jira", content=raw, headers=headers)
    assert duplicate.json()["duplicate"]
    calls = []
    def handler(request):
        calls.append(request.method)
        if request.method == "POST":
            variables = {v["key"]: v["value"] for v in json.loads(request.content)["variables"]}
            assert variables["REPOSITORY_NAME"] == "service-payments"
            assert variables["JIRA_ISSUE_KEY"] == payload["issue"]["key"]
            return httpx.Response(201, json={"uuid": "{literal-path-pipeline}"})
        return httpx.Response(200, json={"uuid": "{literal-path-pipeline}", "state": {"name": "COMPLETED", "result": {"name": "SUCCESSFUL"}}})
    worker = worker_for(store, settings, handler)
    assert worker.tick()
    with store.pool.connection() as conn:
        conn.execute("UPDATE runs SET next_at=now()")
    assert worker.tick() and not worker.tick()
    assert calls == ["POST", "GET"]
    assert client.get("/v1/runs").json()[0]["status"] == "succeeded"
    restored = client.get(f'/v1/rules/{rule["name"]}/versions/1').json()
    assert restored["sha256"] == rule["sha256"]
    assert restored["spec"]["actions"][0]["variables"]["REPOSITORY_NAME"]["path"] == literal


@pytest.mark.parametrize("location", ["filter", "binding", "guard"])
def test_path_syntax_rejected_before_api_persistence(env, location):
    client, store, settings = env
    spec = yaml.safe_load(YAML)
    if location == "filter":
        spec["when"]["all"][0]["path"] = "issue[*]"
    elif location == "binding":
        spec["actions"][0]["variables"]["DESCRIPTION"] = {"path": "issue.get()"}
    else:
        settings.allowed_jira_projects = ("DEMO",)
        spec["actions"] = [{"type": "jira.issue.get", "issue": {"value": "DEMO-1"},
                            "require": {"path": '$.fields.status', "op": "exists"}}]
    text = yaml.safe_dump(spec)
    for endpoint in ("/v1/rules/validate", "/v1/rules"):
        assert client.post(endpoint, content=text).status_code == 422
    assert client.post("/v1/evaluate", json={"yaml": text, "payload": EVENT}).status_code == 422
    assert store.query("SELECT count(*) n FROM rules")[0]["n"] == 0
    assert client.get("/v1/runs").json() == []
