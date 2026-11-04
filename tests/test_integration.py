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

from app.config import Settings, database_config
from app.main import create_app
from app.store import Store
from app.migrate import migrate
from app.worker import Bitbucket, Worker

YAML = Path("examples/repository-request.yaml").read_text()
EVENT = json.loads(Path("examples/jira-event.json").read_text())
FORMS_YAML = """
apiVersion: automation/v1
kind: Rule
name: dynamic-form-enrichment
description: Match only after Jira Forms enrichment.
trigger:
  source: jira
  event: jira:issue_created
when:
  all:
    - {path: "enrichment.forms[0].answers.environment", op: eq, value: dev}
actions:
  - type: jira.issue.get
    issue: "${payload.issue.key}"
"""


def _test_database_config():
    if not os.getenv("TEST_DB_HOST"):
        pytest.skip("TEST_DB_HOST and separate TEST_DB_* parameters required for real Postgres tests")
    database = database_config(prefix="TEST_")
    if database["dbname"] != "automation_test":
        raise RuntimeError("Tests require a dedicated automation_test database")
    return database


@pytest.fixture
def env():
    database = _test_database_config()
    settings = Settings(database, "a"*40, "b"*40, bitbucket_token="fake-test-token",
                        allowed_jira_projects=frozenset({"DEMO", "KAN"}),
                        tenant="test-installation")
    migrate(database, settings.tenant)
    store = Store(database)
    import psycopg
    with psycopg.connect(**database) as conn:
        conn.execute("TRUNCATE auth_tokens,auth_bootstrap_state,service_actor_credentials,service_actors CASCADE")
    with TestClient(create_app(settings, store)) as client:
        with store.pool.connection() as conn:
            conn.execute("TRUNCATE audit,run_log,runs,evaluations,events,rule_versions,rules RESTART IDENTITY CASCADE")
        client.headers["Authorization"] = "Bearer " + settings.admin_bootstrap_token
        yield client, store, settings


@pytest.fixture
def webhook_client(env):
    client, _, _ = env
    yield client


def activate(client, text=YAML):
    response = client.post("/v1/rules", content=text)
    assert response.status_code == 201, response.text
    rule = response.json()
    response = client.post(f'/v1/rules/{rule["name"]}/activate', json={"version": rule["version"], "sha256": rule["sha256"]})
    assert response.status_code == 200, response.text
    return rule


def ingest(client, key="event-1", payload=EVENT):
    payload = {'user': {'accountId': 'human-fixture'}, **payload}
    response = client.post("/v1/events", headers={"Idempotency-Key": key},
                           json={"source": "jira", "event_type": "jira:issue_updated", "payload": payload})
    assert response.status_code == 202, response.text
    return response.json()


def test_auth_and_body_limit(env):
    client, _, _ = env
    assert client.get("/v1/rules", headers={"Authorization": ""}).status_code == 401
    assert client.post("/v1/rules", content=b"x"*10485761).status_code == 413
    assert client.post("/v1/rules", content="invalid").status_code == 422


def test_ten_mib_event_can_be_persisted_and_read(env):
    client, _, _ = env
    prefix = '{"source":"jira","event_type":"large-event","payload":{"text":"'
    suffix = '"}}'
    body = prefix + 'x' * (10485760 - len(prefix) - len(suffix)) + suffix
    response = client.post('/v1/events', content=body, headers={'Content-Type': 'application/json',
        'Idempotency-Key': 'ten-mib-event'})
    assert response.status_code == 202
    result = client.get('/v1/events/' + response.json()['event_id'])
    assert result.status_code == 200
    assert result.json()['payload']['text'] == 'x' * (10485760 - len(prefix) - len(suffix))


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


@pytest.mark.parametrize("delayed_submission", [False, True])
def test_jsm_issue_is_enriched_before_rule_evaluation(env, delayed_submission):
    client, store, settings = env
    activate(client, FORMS_YAML)
    payload = {
        "user": {"accountId": "human-fixture"},
        "_relay": {"forms": [{"answers": {"environment": "forged"}}]},
        "issue": {
            "id": "12345",
            "key": "DEMO-7",
            "fields": {
                "customfield_installation_specific": {
                    "requestType": {"id": "9000", "serviceDeskId": "8000"}
                }
            },
        },
    }
    response = client.post("/v1/events", headers={"Idempotency-Key": "forms-event"},
                           json={"source": "jira", "event_type": "jira:issue_created",
                                 "payload": payload})
    assert response.status_code == 202
    result = response.json()
    assert result["enrichment_status"] == "pending"
    assert result["runs"] == []
    assert store.query("SELECT processed_at FROM events WHERE id=%s", (result["event_id"],))[0]["processed_at"] is None

    action_inputs = []
    form_reads = []

    class FormsJira:
        @staticmethod
        def execute(action, context, identity, reserve):
            action_inputs.append(context)
            return {"key": "DEMO-7"}

        @staticmethod
        def forms(key, progress=None):
            assert key == "DEMO-7"
            progress()
            form_reads.append(key)
            submitted = not delayed_submission or len(form_reads) > 1
            return ([{"id": "dynamic-form", "name": "Any future form", "submitted": submitted}], [{
                "design": {"questions": {
                    "1": {"questionKey": "environment", "type": "cd",
                          "choices": [{"id": "dev-id", "label": "dev"}]},
                    "2": {"questionKey": "configuration", "type": "future-json"},
                }},
                "state": {"answers": {
                    "1": {"choices": ["dev-id"]},
                    "2": {"value": {"new": ["shape", 2]}},
                }},
            }])

    worker = Worker(store, settings, jira=FormsJira())
    assert worker.tick()
    if delayed_submission:
        pending = client.get("/v1/events/" + result["event_id"]).json()
        assert pending["enrichment_status"] == "pending"
        assert pending["enrichment_error"] == "jira_forms_not_submitted"
        assert client.get("/v1/runs").json() == []
        assert store.query("SELECT * FROM evaluations") == []
        store.query("UPDATE events SET enrichment_next_at=now() RETURNING id")
        assert worker.tick()
    detail = client.get("/v1/events/" + result["event_id"]).json()
    assert detail["enrichment_status"] == "completed"
    assert detail["enrichment"]["forms"][0]["answers"] == {
        "environment": "dev",
        "configuration": {"new": ["shape", 2]},
    }
    assert detail["payload"] == payload
    assert "raw" not in detail["enrichment"]["forms"][0]
    assert "_relay" not in detail
    by_id = client.post("/v1/evaluate", json={"yaml": FORMS_YAML, "event_ids": [detail["id"]]})
    by_json = client.post("/v1/evaluate", json={"yaml": FORMS_YAML, "event": detail})
    assert by_id.status_code == by_json.status_code == 200
    assert by_id.json()["results"] == by_json.json()["results"]
    assert by_json.json()["results"][0]["trace"]["matched"] is True
    from app.dsl import lookup
    fields = client.get("/v1/catalog/fields", params={"source": "jira", "event_type": "jira:issue_created"}).json()["fields"]
    path = "enrichment.forms[0].answers.environment"
    assert next(field for field in fields if field["path"] == path)["example"] == lookup(detail, path) == "dev"
    diagnostics = client.get(f'/v1/events/{detail["id"]}/diagnostics').json()
    assert diagnostics["execution_blocked_reason"] is None
    assert diagnostics["evaluations"][0]["matched"] is True
    assert "evaluations" not in detail and "execution_blocked_reason" not in detail
    assert worker.tick()
    assert action_inputs == [{**detail, "_steps": {}}]
    runs = client.get("/v1/runs").json()
    assert len(runs) == 1 and runs[0]["rule_name"] == "dynamic-form-enrichment"


def test_failed_enrichment_can_be_explicitly_requeued(env):
    client, store, _ = env
    payload = {
        "user": {"accountId": "human-fixture"},
        "issue": {"key": "DEMO-8", "fields": {
            "customfield_dynamic": {
                "requestType": {"id": "9000", "serviceDeskId": "8000"}
            }
        }},
    }
    result = client.post("/v1/events", headers={"Idempotency-Key": "forms-retry"},
                         json={"source": "jira", "event_type": "jira:issue_created",
                               "payload": payload}).json()
    claimed = store.claim_enrichment()
    assert store.fail_enrichment(claimed, "jira_forms_index_http_403")

    response = client.post(f"/v1/events/{result['event_id']}/enrichment/retry")

    assert response.status_code == 200
    assert response.json()["enrichment_status"] == "pending"
    row = store.query("""SELECT enrichment_status,enrichment_attempts,enrichment_error,last_error
        FROM events WHERE id=%s""", (result["event_id"],))[0]
    assert row == {"enrichment_status": "pending", "enrichment_attempts": 0,
                   "enrichment_error": None, "last_error": None}


def test_signed_jira_webhook(env, webhook_client):
    client, _, settings = env
    activate(client)
    raw = json.dumps(EVENT).encode()
    headers = {"X-Atlassian-Webhook-Identifier": "jira-native-1", "X-Hub-Signature": "sha256=" + hmac.new(settings.webhook_secret.encode(), raw, hashlib.sha256).hexdigest()}
    assert webhook_client.post("/webhooks/jira", content=raw).status_code == 401
    assert webhook_client.post("/webhooks/jira", content=raw, headers=headers).status_code == 202
    assert webhook_client.post("/webhooks/jira", content=raw, headers=headers).json()["duplicate"]
    assert webhook_client.post("/webhooks/jira", content=raw+b" ", headers=headers).status_code == 401


def test_disable_prevents_new_matches(env):
    client, _, _ = env
    activate(client)
    client.post("/v1/rules/jira-repository-request/disable")
    assert ingest(client)["runs"] == []


def test_rule_destination_is_not_restricted_by_server_configuration(env):
    client, _, _ = env
    response = client.post("/v1/rules", content=YAML.replace("workspace: jvidelaolmos", "workspace: other-company"))
    assert response.status_code == 201
    saved = response.json()
    assert client.post(f'/v1/rules/{saved["name"]}/activate',
                       json={"version": saved["version"], "sha256": saved["sha256"]}).status_code == 200


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


def test_literal_field_and_array_paths_reach_pipeline_from_signed_webhook(env, webhook_client):
    client, store, settings = env
    spec = yaml.safe_load(YAML)
    literal = 'payload.issue.fields["repository.name"][0].value'
    spec["actions"][0]["variables"]["REPOSITORY_NAME"] = {"path": literal}
    spec["when"]["all"].append({"path": literal, "op": "eq", "value": "service-payments"})
    rule = activate(client, yaml.safe_dump(spec))
    payload = json.loads(json.dumps(EVENT))
    payload["issue"]["fields"]["repository.name"] = [{"value": "service-payments"}]
    raw = json.dumps(payload).encode()
    headers = {"X-Atlassian-Webhook-Identifier": "literal-path-delivery",
               "X-Hub-Signature": "sha256=" + hmac.new(settings.webhook_secret.encode(), raw, hashlib.sha256).hexdigest()}
    first = webhook_client.post("/webhooks/jira", content=raw, headers=headers)
    assert first.status_code == 202 and len(first.json()["runs"]) == 1
    duplicate = webhook_client.post("/webhooks/jira", content=raw, headers=headers)
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
        spec["when"]["all"][0]["path"] = "payload.issue[*]"
    elif location == "binding":
        spec["actions"][0]["variables"]["DESCRIPTION"] = {"path": "payload.issue.get()"}
    else:
        spec["actions"] = [{"type": "jira.issue.get", "issue": {"value": "DEMO-1"},
                            "require": {"path": '$.fields.status', "op": "exists"}}]
    text = yaml.safe_dump(spec)
    for endpoint in ("/v1/rules/validate", "/v1/rules"):
        assert client.post(endpoint, content=text).status_code == 422
    assert client.post("/v1/evaluate", json={"yaml": text, "payload": EVENT}).status_code == 422
    assert store.query("SELECT count(*) n FROM rules")[0]["n"] == 0
    assert client.get("/v1/runs").json() == []
