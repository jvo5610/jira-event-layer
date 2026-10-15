from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime

from fastapi.testclient import TestClient
import pytest

from app.main import create_app
from app.store import Store
from app.tokens import TokenManager, digest
from test_integration import env, YAML


def auth(token):
    return {"Authorization": "Bearer " + token}


def issue(client, permissions=None, seconds=3600):
    response = client.post("/v1/auth/tokens", json={"name": "test-client", "permissions": permissions or ["api:read"], "expires_in_seconds": seconds})
    assert response.status_code == 201, response.text
    return response.json()


def test_hashes_metadata_audit_and_permissions(env):
    client, store, cfg = env
    token = issue(client)
    headers = auth(token["token"])
    assert client.get("/v1/schema", headers=headers).status_code == 200
    assert client.post("/v1/rules/validate", headers=headers, content=YAML).status_code == 200
    for path in ("/v1/auth/tokens", "/v1/rules", "/v1/events"):
        assert client.post(path, headers=headers, json={}).status_code == 403
    assert client.get("/v1/auth/tokens", headers=headers).status_code == 403
    metadata = client.get("/v1/auth/tokens").json()
    assert token["token"] not in str(metadata) and "token_hash" not in str(metadata)
    rows = store.query("SELECT token_hash FROM auth_tokens WHERE id=%s", (token["id"],))
    assert rows[0]["token_hash"] == digest(token["token"])
    audits = store.query("SELECT detail FROM audit")
    assert token["token"] not in str(audits) and cfg.admin_bootstrap_token not in str(audits)
    assert client.post(f'/v1/auth/tokens/{token["id"]}/revoke').status_code == 200
    assert client.get("/v1/schema", headers=headers).status_code == 401


def test_author_and_operator_permissions_are_separate(env):
    client, _, _ = env
    author = issue(client, ["api:read", "rules:write"])
    assert client.post("/v1/rules", headers=auth(author["token"]), content=YAML).status_code == 201
    assert client.post("/v1/events", headers=auth(author["token"]), json={}).status_code == 403
    operator = issue(client, ["api:read", "flows:operate"])
    assert client.post("/v1/rules", headers=auth(operator["token"]), content=YAML).status_code == 403
    assert client.post("/v1/events", headers={**auth(operator["token"]), "Idempotency-Key": "operator-permission"},
                       json={"source": "test", "event_type": "test", "payload": {}}).status_code == 202


def test_bootstrap_is_atomic_and_only_reads_secret_once(env):
    _, store, _ = env
    with store.pool.connection() as conn:
        conn.execute("TRUNCATE auth_tokens,auth_bootstrap_state")
    calls = []
    def supplier():
        calls.append(True)
        return "new-bootstrap" * 4
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(lambda _: TokenManager(store).bootstrap(supplier), range(3)))
    assert calls == [True]
    assert len(store.query("SELECT id FROM auth_tokens")) == 1


@pytest.mark.parametrize("value", ["", "short", "x" * 32 + "\n", "b" * 40])
def test_invalid_bootstrap_does_not_mark_initialization_complete(env, value):
    _, store, cfg = env
    with store.pool.connection() as conn:
        conn.execute("TRUNCATE auth_tokens,auth_bootstrap_state")
    with pytest.raises(RuntimeError):
        TokenManager(store).bootstrap(lambda: value, cfg.webhook_secret)
    assert store.query("SELECT * FROM auth_bootstrap_state") == []
    assert store.query("SELECT * FROM auth_tokens") == []


def test_delegated_rotation_cannot_escalate_permissions(env):
    client, _, _ = env
    original = issue(client)
    replacement = client.post("/v1/auth/tokens/self/rotate", headers=auth(original["token"])).json()
    assert replacement["permissions"] == ["api:read"]
    assert client.post("/v1/auth/tokens/self/confirm", headers=auth(replacement["token"])).status_code == 200
    assert client.post("/v1/auth/tokens", headers=auth(replacement["token"]), json={}).status_code == 403
    assert client.post("/v1/rules", headers=auth(replacement["token"]), content=YAML).status_code == 403


def test_stale_identity_cannot_issue_tokens_after_revocation(env):
    client, store, _ = env
    administrator = issue(client, ["auth:admin"])
    manager = TokenManager(store)
    stale = manager.lookup(administrator["token"])
    assert client.post(f'/v1/auth/tokens/{administrator["id"]}/revoke').status_code == 200
    with pytest.raises(Exception) as failure:
        manager.create(stale, "must-not-exist", ["auth:admin"], 3600)
    assert failure.value.status_code == 401


def test_rotation_confirm_revokes_old_and_survives_changed_bootstrap(env, monkeypatch):
    client, store, cfg = env
    response = client.post("/v1/auth/tokens/self/rotate")
    assert response.status_code == 200, response.text
    new = response.json()
    remaining = (datetime.fromisoformat(new["pending_until"]) - datetime.fromisoformat(new["created_at"])).total_seconds()
    assert 3595 <= remaining <= 3600
    assert client.get("/v1/schema", headers=auth(new["token"])).status_code == 200
    assert client.post("/v1/auth/tokens/self/rotate").status_code == 409
    assert client.post("/v1/auth/tokens/self/confirm").status_code == 409
    assert client.post("/v1/auth/tokens", headers=auth(new["token"]), json={}).status_code == 403
    assert client.post("/v1/auth/tokens/self/confirm", headers=auth(new["token"])).status_code == 200
    assert client.get("/v1/schema").status_code == 401
    assert client.get("/v1/auth/tokens", headers=auth(new["token"])).status_code == 200
    monkeypatch.setenv("ADMIN_BOOTSTRAP_TOKEN_FILE", "/missing/ignored/after/bootstrap")
    monkeypatch.delenv("ADMIN_BOOTSTRAP_TOKEN", raising=False)
    for bootstrap in (None, "different-bootstrap" * 3, cfg.admin_bootstrap_token):
        with TestClient(create_app(replace(cfg, admin_bootstrap_token=bootstrap), Store(cfg.database, cfg.tenant))) as restarted:
            assert restarted.get("/v1/schema", headers=auth(cfg.admin_bootstrap_token)).status_code == 401
            assert restarted.get("/v1/schema", headers=auth(new["token"])).status_code == 200
    assert len(store.query("SELECT * FROM auth_bootstrap_state")) == 1


def test_expired_unconfirmed_rotation_keeps_parent_and_can_retry(env):
    client, store, _ = env
    new = client.post("/v1/auth/tokens/self/rotate").json()
    with store.pool.connection() as conn:
        conn.execute("UPDATE auth_tokens SET pending_until=now()-interval '1 second' WHERE id=%s", (new["id"],))
    assert client.get("/v1/schema", headers=auth(new["token"])).status_code == 401
    assert client.post("/v1/auth/tokens/self/confirm", headers=auth(new["token"])).status_code == 401
    assert client.get("/v1/schema").status_code == 200
    assert client.post("/v1/auth/tokens/self/rotate").status_code == 200


def test_expiry_and_parent_revocation_invalidate_pending_replacement(env):
    client, store, _ = env
    token = issue(client, seconds=60)
    rotated = client.post("/v1/auth/tokens/self/rotate", headers=auth(token["token"])).json()
    assert rotated["expires_at"] == token["expires_at"]
    assert rotated["pending_until"] == token["expires_at"]
    assert client.post(f'/v1/auth/tokens/{token["id"]}/revoke').status_code == 200
    assert client.get("/v1/schema", headers=auth(rotated["token"])).status_code == 401
    expired = issue(client)
    with store.pool.connection() as conn:
        conn.execute("UPDATE auth_tokens SET expires_at=now()-interval '1 second' WHERE id=%s", (expired["id"],))
    assert client.get("/v1/schema", headers=auth(expired["token"])).status_code == 401


def test_last_admin_protected_and_bootstrap_never_reopens_after_token_deletion(env):
    client, store, _ = env
    token_id = client.get("/v1/auth/tokens").json()[0]["id"]
    assert client.post(f"/v1/auth/tokens/{token_id}/revoke").status_code == 409
    with store.pool.connection() as conn:
        conn.execute("DELETE FROM auth_tokens")
    def forbidden_supplier():
        raise AssertionError("Bootstrap must not be read after initialization")
    TokenManager(store).bootstrap(forbidden_supplier)
    assert store.query("SELECT * FROM auth_tokens") == []


def test_rotation_concurrency_has_one_winner(env):
    client, store, cfg = env
    manager = TokenManager(store)
    identity = manager.lookup(cfg.admin_bootstrap_token)
    def rotate(_):
        try:
            manager.rotate(identity)
            return 200
        except Exception as exc:
            return getattr(exc, "status_code", 500)
    with ThreadPoolExecutor(max_workers=3) as pool:
        assert sorted(pool.map(rotate, range(3))) == [200, 409, 409]


@pytest.mark.parametrize("body", [
    {"name": "x", "permissions": ["superuser"], "expires_in_seconds": 3600},
    {"name": "x", "permissions": [], "expires_in_seconds": 3600},
    {"name": "x", "permissions": ["api:read"], "expires_in_seconds": True},
    {"name": "x", "permissions": ["api:read"], "expires_in_seconds": 0},
])
def test_invalid_token_requests_are_rejected(env, body):
    client, _, _ = env
    assert client.post("/v1/auth/tokens", json=body).status_code == 422
