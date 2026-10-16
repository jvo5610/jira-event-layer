import hashlib
import hmac
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest

from app.config import Settings, secret, database_config
from app.health import worker_healthy, worker_id
from app.migrate import check_schema, migrate, scripts
from app.store import Store
from app.retention import redact
from app.transport import bounded_request
from app.worker import Bitbucket, Worker
from app.jira import Jira
from tools.init_installation import initialize
from test_integration import activate, env, webhook_client, ingest, YAML, EVENT


@pytest.fixture
def configuration(monkeypatch):
    # Isolate all environment inputs, never consume developer/provider credentials.
    names = {"DATABASE_URL", "DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD", "ADMIN_BOOTSTRAP_TOKEN", "JIRA_WEBHOOK_SECRET", "BITBUCKET_API_TOKEN", "JIRA_API_TOKEN",
             "JIRA_CLIENT_ID", "JIRA_CLIENT_SECRET", "JIRA_CLOUD_ID", "ENV_FILE",
             "BITBUCKET_ACCOUNT_EMAIL", "JIRA_ACCOUNT_EMAIL", "BITBUCKET_EMAIL", "BITBUCKET_TOKEN",
             "JIRA_EMAIL", "JIRA_TOKEN",
             "API_TOKEN", "ADMIN_TOKEN", "READER_TOKEN", "WRITER_TOKEN", "OPERATOR_TOKEN", "TENANT_ID", "AUTH_ROTATION_TTL_SECONDS",
             "MAX_BODY_BYTES",
             "JIRA_MAX_DAILY_WRITES", "BITBUCKET_MAX_DAILY_WRITES"}
    for name in names:
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name + "_FILE", raising=False)
    monkeypatch.setenv("DB_HOST", "example")
    monkeypatch.setenv("DB_NAME", "db")
    monkeypatch.setenv("DB_USER", "runtime")
    monkeypatch.setenv("DB_PASSWORD", "db-test-password")
    monkeypatch.setenv("ADMIN_BOOTSTRAP_TOKEN", "a" * 40)
    monkeypatch.setenv("JIRA_WEBHOOK_SECRET", "b" * 40)
    monkeypatch.setenv("TENANT_ID", "independent-installation")
    return monkeypatch


def test_portable_default_configuration(configuration):
    cfg = Settings.from_env()
    assert cfg.tenant == "independent-installation"
    assert cfg.max_body == 10485760
    assert "example-workspace" not in repr(cfg)
    assert "a" * 40 not in repr(cfg) and "db-test-password" not in repr(cfg)


def test_database_parameters_are_passed_without_url_encoding(configuration, tmp_path):
    password = "p@ss:/% ?'\\[]"
    path = tmp_path / "password"
    path.write_text(password + "\n")
    configuration.delenv("DB_PASSWORD")
    configuration.setenv("DB_PASSWORD_FILE", str(path))
    cfg = Settings.from_env()
    assert cfg.database == {"host": "example", "port": 5432, "dbname": "db", "user": "runtime", "password": password}
    assert password not in repr(cfg)
    configuration.setenv("DB_PASSWORD", "direct-password")
    configuration.setenv("DB_PASSWORD_FILE", str(tmp_path / "missing"))
    assert Settings.from_env().database["password"] == "direct-password"


@pytest.mark.parametrize("name", ["DATABASE_URL", "DATABASE_URL_FILE"])
def test_database_connection_string_environment_is_rejected(configuration, name):
    configuration.setenv(name, "not-a-supported-configuration")
    with pytest.raises(RuntimeError, match="not supported"):
        database_config()


@pytest.mark.parametrize("name,value", [("DB_HOST", ""), ("DB_HOST", " host "),
    ("DB_NAME", ""), ("DB_USER", ""), ("DB_PASSWORD", ""),
    ("DB_PORT", "0"), ("DB_PORT", "65536"), ("DB_PORT", "invalid")])
def test_database_configuration_fails_closed(configuration, name, value):
    configuration.setenv(name, value)
    with pytest.raises(RuntimeError, match=name):
        database_config()


def test_compose_keeps_migrator_credentials_out_of_runtime():
    import yaml
    compose = yaml.safe_load(Path("compose.yaml").read_text())
    migrator = compose["services"]["migrate"]
    assert migrator["environment"]["DB_USER"] == "${DB_MIGRATION_USER:?Set DB_MIGRATION_USER}"
    assert migrator["secrets"] == ["db_migrator_password"]
    assert "webhooks" not in compose["services"]
    for service in ("api", "worker"):
        config = compose["services"][service]
        assert config["environment"]["DB_PASSWORD_FILE"] == "/run/secrets/db_runtime_password"
        assert "db_migrator_password" not in config["secrets"]


def test_v1_connector_environment_names(configuration):
    configuration.setenv("BITBUCKET_API_TOKEN", "bitbucket-secret")
    configuration.setenv("JIRA_API_TOKEN", "jira-secret")
    cfg = Settings.from_env()
    assert cfg.bitbucket_token == "bitbucket-secret"
    assert cfg.jira_token == "jira-secret"


@pytest.mark.parametrize("name", [
    "BITBUCKET_EMAIL", "BITBUCKET_TOKEN", "BITBUCKET_TOKEN_FILE",
    "JIRA_EMAIL", "JIRA_TOKEN", "JIRA_TOKEN_FILE",
])
def test_legacy_connector_environment_names_are_rejected(configuration, name):
    configuration.setenv(name, "legacy")
    with pytest.raises(RuntimeError, match="not supported in v1"):
        Settings.from_env()


def test_maximum_body_size_is_supported(configuration):
    configuration.setenv("MAX_BODY_BYTES", "10485760")
    assert Settings.from_env().max_body == 10485760
    configuration.setenv("MAX_BODY_BYTES", "10485761")
    with pytest.raises(RuntimeError, match="MAX_BODY_BYTES"):
        Settings.from_env()


def test_worker_uses_pinned_rule_identity_not_event_or_new_active_version(env):
    client, store, cfg = env
    cfg.jira_token = "test-token"
    cfg.jira_cloud_id = "11111111-1111-4111-8111-111111111111"
    store.remember_jira_actor(cfg, 'relay-fixture')
    text = '''apiVersion: automation/v1
kind: Rule
name: crear-repositorio
trigger: {source: jira, event: "jira:issue_updated"}
when:
  all:
    - {path: issue.key, op: eq, value: DEMO-1}
    - some:
        path: changelog.items
        where:
          all:
            - {path: field, op: eq, value: status}
            - {path: toString, op: eq, value: Done}
actions:
  - type: jira.issue.create
    project: DEMO
    issue_type_id: "1"
    fields:
      summary: {value: Solicitud de repositorio}
  - type: jira.comment.add
    issue: {path: '_steps["0"].key'}
    text: {value: Solicitud registrada}
'''
    first = activate(client, text)
    event = ingest(client, payload={"issue": {"key": "DEMO-1"}, "rule_name": "forged", "_steps": {"0": {"key": "OTHER-1"}},
        'changelog': {'items': [{'field': 'status', 'toString': 'Done'}]}})
    activate(client, text.replace("Solicitud registrada", "Nueva revision"))
    writes = []
    def provider(request):
        if request.method == "GET":
            return httpx.Response(200, json={"key": "DEMO-2", "fields": {"project": {"key": "DEMO"},
                "labels": writes[0]["fields"]["labels"]}})
        writes.append(json.loads(request.content))
        return httpx.Response(201, json={"key": "DEMO-2", "id": "99"})
    with httpx.Client(transport=httpx.MockTransport(provider)) as remote:
        worker = Worker(store, cfg, jira=Jira(cfg, remote))
        try:
            assert worker.tick() and worker.tick()
        finally:
            worker.connector.client.close()
    run = client.get("/v1/runs/" + event["runs"][0]["id"]).json()
    assert run["status"] == "succeeded"
    assert writes[0]["fields"]["labels"] == ["automation-flujo-crear-repositorio"]
    for step, body in enumerate(writes):
        provenance = body["properties"][0]["value"]
        assert provenance["rule_name"] == "crear-repositorio"
        assert provenance["rule_version"] == first["version"] == 1
        assert provenance["rule_sha256"] == first["sha256"]
        assert provenance["run_id"] == run["id"] and provenance["step"] == step


def test_installation_secrets_never_overwrite(tmp_path):
    directory = tmp_path / "secrets"
    initialize(directory, "runtime-password", "owner-password")
    assert directory.stat().st_mode & 0o777 == 0o700
    assert (directory / "admin_bootstrap_token").stat().st_mode & 0o777 == 0o444
    previous = (directory / "admin_bootstrap_token").read_text()
    assert not any((directory / name).exists() for name in ("reader_token", "writer_token", "operator_token", "admin_token"))
    with pytest.raises(FileExistsError):
        initialize(directory, "different-password", "owner-password")
    assert (directory / "admin_bootstrap_token").read_text() == previous


def test_provider_stream_stops_at_bound():
    chunks = []
    class Body(httpx.SyncByteStream):
        def __iter__(self):
            for i in range(100):
                chunks.append(i)
                yield b"x" * 16384
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=Body()))) as client:
        with pytest.raises(httpx.ReadError):
            bounded_request(client, "GET", "https://provider.test/", max_bytes=20000)
    assert len(chunks) == 2


def test_provider_compressed_response():
    import gzip
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200,
            headers={"Content-Encoding": "gzip", "Retry-After": "20"}, content=gzip.compress(b'{"ok":true}')))) as client:
        response = bounded_request(client, "GET", "https://provider.test/")
        assert response.json() == {"ok": True}
        assert response.headers["Retry-After"] == "20"


def test_file_secrets_and_environment_precedence(configuration, tmp_path):
    source = tmp_path / "credential"
    source.write_text("x" * 40 + "\n")
    configuration.delenv("ADMIN_BOOTSTRAP_TOKEN")
    configuration.setenv("ADMIN_BOOTSTRAP_TOKEN_FILE", str(source))
    assert secret("ADMIN_BOOTSTRAP_TOKEN") == "x" * 40
    configuration.setenv("ADMIN_BOOTSTRAP_TOKEN", "y" * 40)
    assert secret("ADMIN_BOOTSTRAP_TOKEN") == "y" * 40
    configuration.setenv("ADMIN_BOOTSTRAP_TOKEN_FILE", str(tmp_path / "missing"))
    assert secret("ADMIN_BOOTSTRAP_TOKEN") == "y" * 40


def test_empty_environment_does_not_fall_back_to_file(configuration, tmp_path):
    configuration.setenv("ADMIN_BOOTSTRAP_TOKEN", "")
    configuration.setenv("ADMIN_BOOTSTRAP_TOKEN_FILE", str(tmp_path / "missing"))
    with pytest.raises(RuntimeError, match="is required"):
        secret("ADMIN_BOOTSTRAP_TOKEN", required=True)


@pytest.mark.parametrize("name", ["DB_PASSWORD", "ADMIN_BOOTSTRAP_TOKEN", "JIRA_WEBHOOK_SECRET",
    "JIRA_API_TOKEN", "BITBUCKET_API_TOKEN"])
def test_all_secret_inputs_prefer_environment(configuration, tmp_path, name):
    configuration.setenv(name, "direct-value")
    configuration.setenv(name + "_FILE", str(tmp_path / "missing"))
    assert secret(name, required=True) == "direct-value"


def test_live_runner_secret_precedence(configuration, tmp_path):
    from tools.e2e_live import required
    path = tmp_path / "token"
    path.write_text("file-token\n")
    configuration.delenv("JIRA_API_TOKEN", raising=False)
    configuration.setenv("JIRA_API_TOKEN_FILE", str(path))
    assert required("JIRA_API_TOKEN") == "file-token"
    configuration.setenv("JIRA_API_TOKEN", "direct-token")
    configuration.setenv("JIRA_API_TOKEN_FILE", str(tmp_path / "missing"))
    assert required("JIRA_API_TOKEN") == "direct-token"
    configuration.setenv("JIRA_API_TOKEN", "")
    with pytest.raises(RuntimeError, match="Set JIRA_API_TOKEN"):
        required("JIRA_API_TOKEN")


@pytest.mark.parametrize("value", [b"x" * 16385, b"\xff"])
def test_secret_file_bounds(configuration, tmp_path, value):
    path = tmp_path / "secret"
    path.write_bytes(value)
    configuration.setenv("JIRA_API_TOKEN_FILE", str(path))
    with pytest.raises(RuntimeError, match="bounded UTF-8"):
        secret("JIRA_API_TOKEN")


@pytest.mark.parametrize("name,value", [
    ("TENANT_ID", ""), ("TENANT_ID", "../bad"),
    ("AUTH_ROTATION_TTL_SECONDS", "0"),
    ("MAX_BODY_BYTES", "-1"), ("MAX_BODY_BYTES", "eleven"),
    ("JIRA_MAX_DAILY_WRITES", "0"), ("BITBUCKET_MAX_DAILY_WRITES", "100001")])
def test_invalid_configuration_fails_closed(configuration, name, value):
    configuration.setenv(name, value)
    with pytest.raises(RuntimeError):
        Settings.from_env("api")


def test_worker_does_not_need_inbound_secrets(configuration):
    configuration.delenv("ADMIN_BOOTSTRAP_TOKEN")
    configuration.delenv("JIRA_WEBHOOK_SECRET")
    assert Settings.from_env("worker").admin_bootstrap_token is None
    with pytest.raises(RuntimeError, match="JIRA_WEBHOOK_SECRET"):
        Settings.from_env("api")


def test_api_bootstrap_is_loaded_lazily(configuration):
    configuration.delenv("ADMIN_BOOTSTRAP_TOKEN")
    assert Settings.from_env("api").admin_bootstrap_token is None


def test_single_api_registers_management_and_webhook_routes():
    from app.main import create_app
    paths = {route.path for route in create_app().routes}
    assert {'/healthz', '/readyz', '/webhooks/jira', '/v1/auth/tokens', '/v1/schema'} <= paths
    with pytest.raises(ValueError, match="Unknown service entrypoint"):
        Settings.from_env("webhooks")


def test_schema_and_runtime_startup_never_apply_ddl(env):
    _, store, cfg = env
    migrate(cfg.database, cfg.tenant)
    second = Store(cfg.database, cfg.tenant)
    second.open()
    second.close()
    with store.pool.connection() as conn:
        assert len(conn.execute("SELECT * FROM schema_migrations").fetchall()) == len(scripts())
        with conn.transaction(force_rollback=True):
            conn.execute("UPDATE schema_migrations SET sha256='tampered'")
            with pytest.raises(RuntimeError, match="schema"):
                check_schema(conn, cfg.tenant)
        with conn.transaction(force_rollback=True):
            conn.execute("DROP TABLE schema_migrations")
            with pytest.raises(RuntimeError, match="explicit migration"):
                check_schema(conn, cfg.tenant)


def test_wrong_tenant_and_concurrent_migrations(env):
    _, store, cfg = env
    with pytest.raises(RuntimeError, match="TENANT_ID"):
        migrate(cfg.database, "different-installation")
    assert store.query("SELECT tenant FROM installation")[0]["tenant"] == cfg.tenant
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(lambda _: migrate(cfg.database, cfg.tenant), range(3)))
    wrong = Store(cfg.database, "different-installation")
    with pytest.raises(RuntimeError, match="TENANT_ID"):
        wrong.open()


def signed(cfg, key="native-1", payload=None):
    raw = json.dumps(payload or EVENT).encode()
    return raw, {"X-Atlassian-Webhook-Identifier": key,
                 "X-Hub-Signature": "sha256=" + hmac.new(cfg.webhook_secret.encode(), raw, hashlib.sha256).hexdigest()}


def test_unified_api_keeps_management_and_webhook_auth_independent(env, webhook_client):
    client, _, cfg = env
    assert client.get("/openapi.json", headers={"Authorization": ""}).status_code == 401
    for path in ("/v1/schema", "/v1/rules", "/openapi.json", "/metrics"):
        assert client.get(path, headers={"Authorization": ""}).status_code == 401
    assert client.get("/docs").status_code == 404
    assert webhook_client.get("/healthz").status_code == 200
    raw, headers = signed(cfg)
    assert webhook_client.post("/webhooks/jira", content=raw, headers=headers).status_code == 202
    assert client.post("/webhooks/jira", content=raw, headers=headers).json()["duplicate"] is True
    # A valid management token cannot replace the webhook HMAC signature.
    assert client.post("/webhooks/jira", content=raw).status_code == 401
    # A webhook signature cannot grant management privileges.
    assert client.get("/v1/schema", headers={**headers, "Authorization": ""}).status_code == 401
    assert client.get("/v1/schema").status_code == 200


def test_single_operator_can_author_and_operate(env):
    client, _, _ = env
    saved = activate(client)
    assert client.get("/v1/schema").status_code == 200
    assert client.post("/v1/rules/validate", content=YAML).status_code == 200
    assert client.post("/v1/rules", content=YAML).status_code == 201
    assert client.post(f'/v1/rules/{saved["name"]}/disable').status_code == 200
    assert client.post("/v1/events", headers={"Idempotency-Key": "role-test"},
                       json={"source": "test", "event_type": "test", "payload": {}}).status_code == 202


def test_audit_records_identity_not_token(env):
    client, store, cfg = env
    response = client.post("/v1/rules", content=YAML)
    assert response.status_code == 201
    detail = store.query("SELECT detail FROM audit WHERE operation='save_rule'")[0]["detail"]
    assert detail["principal"].startswith("token:")
    assert cfg.admin_bootstrap_token not in json.dumps(detail)


def test_configurable_body_limit(env):
    client, _, cfg = env
    cfg.max_body = 1024
    assert client.post("/v1/rules", content="x" * 1025).status_code == 413


def test_service_actor_webhooks_are_persisted_but_cannot_execute(env, webhook_client):
    client, store, cfg = env
    activate(client)
    store.query("INSERT INTO service_actors(provider,account_id) VALUES('jira','service-account') RETURNING account_id")
    raw, headers = signed(cfg, payload={**EVENT, "user": {"accountId": "service-account"}})
    event = webhook_client.post("/webhooks/jira", content=raw, headers=headers).json()
    assert event["runs"] == []
    assert store.query("SELECT last_error FROM events")[0]["last_error"] == "ignored_service_actor"
    assert client.post(f'/v1/events/{event["event_id"]}/execute').status_code == 409
    assert webhook_client.post("/webhooks/jira", content=raw, headers=headers).json()["duplicate"]


def test_heartbeat_and_queue_metrics(env):
    client, store, cfg = env
    worker = Worker(store, cfg)
    worker.heartbeat()
    assert worker_healthy(cfg.database, worker_id())
    assert not worker_healthy(cfg.database, "missing-worker")
    with store.pool.connection() as conn:
        conn.execute("UPDATE worker_heartbeats SET last_seen=now()-interval '5 minutes'")
    assert not worker_healthy(cfg.database, worker_id())
    metrics = client.get("/metrics").text
    assert "automation_workers_healthy 0" in metrics
    assert "automation_oldest_pending_seconds" in metrics
    worker.connector.client.close()
    worker.jira.client.close()


def test_bitbucket_budget_and_concurrent_claim(env):
    client, store, cfg = env
    activate(client)
    ingest(client, "first")
    cfg.bitbucket_max_daily_writes = 1
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(201, json={"uuid": "{test-pipeline}"})
    worker = Worker(store, cfg, Bitbucket(cfg, httpx.Client(transport=httpx.MockTransport(handler))))
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda _: worker.claim(), range(4)))
    assert sum(r is not None for r in claims) == 1
    run = next(r for r in claims if r is not None)
    worker.reserve_bitbucket_write(run)
    worker.finish(run, "needs_review", error="simulated_crash_after_reservation")
    ingest(client, "second")
    assert worker.tick()
    assert len(calls) == 0
    assert any(r["last_error"] == "bitbucket_daily_write_budget_exhausted" for r in client.get("/v1/runs").json())


@pytest.mark.parametrize("status", [408, 409])
def test_bitbucket_ambiguous_dispatch_requires_review(env, status):
    client, store, cfg = env
    activate(client)
    ingest(client)
    worker = Worker(store, cfg, Bitbucket(cfg, httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(status)))))
    assert worker.tick()
    assert client.get("/v1/runs").json()[0]["status"] == "needs_review"
    assert not worker.tick()


def test_retention_preserves_dedup_and_blocks_replay(env):
    client, store, _ = env
    saved = activate(client)
    event = ingest(client)
    with store.pool.connection() as conn:
        conn.execute("UPDATE events SET received_at=now()-interval '100 days'")
    assert redact(store)["count"] == 0  # pending is never eligible
    with store.pool.connection() as conn:
        conn.execute("UPDATE runs SET status='needs_review'")
    assert redact(store)["count"] == 0  # ambiguous results require investigation
    with store.pool.connection() as conn:
        conn.execute("UPDATE runs SET status='succeeded'")
    assert redact(store) == {"applied": False, "count": 1}
    assert client.get(f'/v1/events/{event["event_id"]}').json()["payload"]
    assert redact(store, apply=True) == {"applied": True, "count": 1}
    assert client.get(f'/v1/events/{event["event_id"]}').json()["payload"] == {}
    assert ingest(client)["duplicate"]  # identical delivery remains deduplicated
    assert len(client.get("/v1/runs").json()) == 1
    assert client.post(f'/v1/events/{event["event_id"]}/execute').status_code == 409
    assert client.post("/v1/evaluate", json={"yaml": YAML, "event_ids": [event["event_id"]]}).status_code == 409
