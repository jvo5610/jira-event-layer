import asyncio

import pytest
from fastapi.testclient import TestClient

from app.auth import Identity, required_permission
from app.tokens import DatabaseAuthenticator, digest
from app.config import Settings
from app.main import create_app


class StoreStub:
    def open(self): pass
    def close(self): pass


def test_unknown_mutation_is_not_implicitly_delegated():
    assert required_permission("POST", "/v1/future-dangerous-operation") == "auth:admin"
    assert required_permission("GET", "/v1/auth/tokens") == "auth:admin"


@pytest.mark.parametrize("header", ["", "Basic anything", "Bearer wrong", "Bearer ", "Bearer " + "t" * 40 + " extra"])
def test_authentication_rejects_invalid_credentials(header):
    class Manager:
        def lookup(self, value): return None
    assert asyncio.run(DatabaseAuthenticator(Manager()).authenticate(header)) is None


def test_identity_has_no_credential_and_accepts_case_insensitive_scheme():
    class Manager:
        def lookup(self, value): return Identity("operator", frozenset({"api:read"}))
    identity = asyncio.run(DatabaseAuthenticator(Manager()).authenticate("bearer " + "t" * 40))
    assert identity == Identity("operator", frozenset({"api:read"}))
    assert "t" * 40 not in repr(identity)
    assert len(digest("t" * 40)) == 64 and "t" * 40 not in digest("t" * 40)


def test_authenticator_can_be_replaced_without_changing_endpoints():
    class FutureValidator:
        async def authenticate(self, header):
            return Identity("corporate-user", frozenset({"api:read"})) if header == "Bearer future-access-token" else None

    settings = Settings({}, "t" * 40, "s" * 40)
    with TestClient(create_app(settings, StoreStub(), FutureValidator())) as client:
        assert client.get("/v1/schema").status_code == 401
        client.headers["Authorization"] = "Bearer future-access-token"
        assert client.get("/v1/schema").status_code == 200
        assert client.post("/v1/rules", content="not parsed without permission").status_code == 403


@pytest.mark.parametrize("name", [base + suffix for base in ("API_TOKEN", "ADMIN_TOKEN", "READER_TOKEN", "WRITER_TOKEN", "OPERATOR_TOKEN") for suffix in ("", "_FILE")])
def test_legacy_api_credentials_are_explicitly_rejected(monkeypatch, name):
    monkeypatch.setenv(name, "obsolete-value")
    with pytest.raises(RuntimeError, match="Legacy API tokens"):
        Settings.from_env()
