import hashlib
import hmac
import json
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from psycopg import OperationalError

from app.config import Settings
from app.main import create_app
from app import observability
from test_integration import env, activate, ingest
from app.worker import Worker


class Authenticator:
    async def authenticate(self, authorization):
        return None


class Store:
    def open(self):
        pass

    def close(self):
        pass

    def query(self, *args):
        return [{"ready": True}]

    def ingest(self, *args):
        return {"event_id": UUID("11111111-1111-4111-8111-111111111111"),
                "duplicate": False, "runs": [], "ignored_reason": None}


@pytest.fixture
def logs(caplog, monkeypatch):
    monkeypatch.setattr(observability.log, "handlers", [caplog.handler])
    return caplog


def entries(logs, event):
    return [json.loads(r.getMessage()) for r in logs.records
            if r.name == "jira_relay" and json.loads(r.getMessage())["event"] == event]


@pytest.fixture
def api(logs):
    cfg = Settings({}, None, "webhook-test-only-" * 3)
    with TestClient(create_app(cfg, Store(), Authenticator())) as client:
        yield client, cfg


def headers(cfg, raw, delivery="delivery-test-only"):
    return {"X-Atlassian-Webhook-Identifier": delivery,
            "X-Hub-Signature": "sha256=" + hmac.new(cfg.webhook_secret.encode(), raw, hashlib.sha256).hexdigest()}


@pytest.mark.parametrize("signature,reason", [(None, "missing_webhook_signature"),
    ("sha256=invalid-private-value", "invalid_webhook_signature")])
def test_signature_rejections_are_visible_without_secrets(api, logs, signature, reason):
    client, cfg = api
    request_headers = {"Authorization": "Bearer private-admin-value", "Cookie": "private-cookie-value",
                       "X-Atlassian-Webhook-Identifier": "private-delivery-value",
                       "CF-Ray": "a4e9b11f2a653f3a-EZE"}
    if signature:
        request_headers["X-Hub-Signature"] = signature
    response = client.post("/webhooks/jira?secret=private-query-value", content=b'private-body-value', headers=request_headers)
    assert response.status_code == 401
    received, = entries(logs, "webhook_received")
    completed, = entries(logs, "http_request_completed")
    assert received["signature_present"] == bool(signature)
    assert received["delivery_id_sha256"] == hashlib.sha256(b"private-delivery-value").hexdigest()
    assert completed["request_id"] == received["request_id"] == response.headers["X-Request-ID"]
    assert completed["status"] == 401 and completed["reason"] == reason
    assert completed["cf_ray"] == "a4e9b11f2a653f3a-EZE"
    text = "\n".join(r.getMessage() for r in logs.records if r.name == "jira_relay")
    for value in (cfg.webhook_secret, "private-admin-value", "private-cookie-value", "private-query-value",
                  "private-body-value", "private-delivery-value", signature):
        if value:
            assert value not in text


@pytest.mark.parametrize("raw,delivery,status,reason", [
    (b'{"webhookEvent":"jira:issue_updated"}', "", 400, "missing_webhook_identifier"),
    (b'{"webhookEvent":"jira:issue_updated"}', "x" * 251, 400, "invalid_webhook_identifier"),
    (b'not-json-private-content', "delivery", 400, "invalid_webhook_payload"),
    (b'{"webhookEvent":42}', "delivery", 400, "invalid_webhook_payload"),
])
def test_webhook_validation_reasons(api, logs, raw, delivery, status, reason):
    client, cfg = api
    response = client.post("/webhooks/jira", content=raw, headers=headers(cfg, raw, delivery))
    assert response.status_code == status
    completed, = entries(logs, "http_request_completed")
    assert completed["reason"] == reason


def test_successful_webhook_has_correlated_access_log(api, logs):
    client, cfg = api
    raw = b'{"webhookEvent":"jira:issue_updated"}'
    response = client.post("/webhooks/jira", content=raw, headers=headers(cfg, raw))
    assert response.status_code == 202
    completed, = entries(logs, "http_request_completed")
    assert completed["status"] == 202 and completed["reason"] is None
    assert completed["route"] == "/webhooks/jira" and completed["body_bytes"] == len(raw)


def test_body_limit_is_logged_before_signature_validation(api, logs):
    client, cfg = api
    cfg.max_body = 10
    assert client.post("/webhooks/jira", content=b"x" * 11).status_code == 413
    completed, = entries(logs, "http_request_completed")
    assert completed["reason"] == "body_too_large"


def test_webhook_database_failure_is_logged(api, logs, monkeypatch):
    client, cfg = api
    def fail(*args):
        raise OperationalError("private-database-value")
    monkeypatch.setattr(Store, "ingest", fail)
    raw = b'{"webhookEvent":"jira:issue_updated"}'
    response = client.post("/webhooks/jira", content=raw, headers=headers(cfg, raw))
    assert response.status_code == 503
    completed, = entries(logs, "http_request_completed")
    assert completed["reason"] == "persistence_unavailable"
    assert entries(logs, "event_ingested") == []


def test_unexpected_failure_logs_only_exception_kind(logs, monkeypatch):
    def fail(*args):
        raise RuntimeError("private-error-value")
    monkeypatch.setattr(Store, "ingest", fail)
    cfg = Settings({}, None, "webhook-test-only-" * 3)
    with TestClient(create_app(cfg, Store(), Authenticator()), raise_server_exceptions=False) as client:
        raw = b'{"webhookEvent":"jira:issue_updated"}'
        assert client.post("/webhooks/jira", content=raw, headers=headers(cfg, raw)).status_code == 500
    failed, = entries(logs, "http_request_failed")
    assert failed["error_kind"] == "RuntimeError" and failed["status"] == 500
    assert "private-error-value" not in json.dumps(failed)
    assert observability.request_id.get() is None


def test_unmatched_routes_queries_and_errors_do_not_disclose_details(api, logs, monkeypatch):
    client, cfg = api
    response = client.get("/private-path-value?token=private-query-value", headers={"CF-Ray": "private-ray-value"})
    assert response.status_code == 404
    record, = entries(logs, "http_request_completed")
    assert record["route"] == "unmatched" and "cf_ray" not in record
    assert "private-" not in json.dumps(record)
    def fail(*args):
        raise OperationalError("private-database-value")
    monkeypatch.setattr(Store, "query", fail)
    assert client.get("/readyz").status_code == 503
    assert entries(logs, "http_request_completed")[-1]["reason"] == "persistence_unavailable"
    assert "private-database-value" not in "\n".join(r.getMessage() for r in logs.records if r.name == "jira_relay")


def test_event_commit_duplicate_and_worker_logs(env, logs):
    client, store, cfg = env
    activate(client)
    first = ingest(client, key="log-test-delivery")
    duplicate = ingest(client, key="log-test-delivery")
    assert duplicate["duplicate"]
    records = entries(logs, "event_ingested")
    assert records[-2]["event_id"] == records[-1]["event_id"] == first["event_id"]
    assert records[-2]["duplicate"] is False and records[-1]["duplicate"] is True
    assert records[-1]["runs_count"] == 1
    assert records[-1]["request_id"]
    conflict = client.post("/v1/events", headers={"Idempotency-Key": "log-test-delivery"},
                           json={"source": "jira", "event_type": "jira:issue_updated", "payload": {}})
    assert conflict.status_code == 409
    assert len(entries(logs, "event_ingested")) == len(records)
    assert entries(logs, "http_request_completed")[-1]["reason"] == "conflict"
    worker = Worker(store, cfg)
    run = worker.claim()
    worker.finish(run, "failed", error="private-error-value", result={"token": "private-result-value"})
    claimed, = entries(logs, "run_claimed")
    updated, = entries(logs, "run_updated")
    assert claimed["event_id"] == first["event_id"]
    assert updated["run_id"] == str(run["id"]) and updated["status"] == "failed"
    text = "\n".join(r.getMessage() for r in logs.records if r.name == "jira_relay")
    assert "private-error-value" not in text and "private-result-value" not in text


def test_logging_context_does_not_leak_between_requests(api, logs):
    client, cfg = api
    first = client.get("/healthz")
    second = client.get("/healthz")
    assert first.headers["X-Request-ID"] != second.headers["X-Request-ID"]
    assert len(entries(logs, "http_request_completed")) == 2
    assert observability.request_id.get() is None
