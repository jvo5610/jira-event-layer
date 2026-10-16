"""Client-credentials OAuth and workspace Bearer auth, using mocked providers only."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx
import pytest

import app.jira as jira_module
from app.actors import credential_key
from app.config import Settings, jira_configured
from app.dsl import RuleError
from app.jira import Jira, PreflightRetry
from test_runtime import configuration

CLOUD = "11111111-1111-4111-8111-111111111111"


def oauth_settings():
    return Settings({}, None, "", jira_cloud_id=CLOUD,
                    jira_client_id="client-test-only", jira_client_secret="secret-test-only")


def grant(token="access-test-only", expires=3600):
    return httpx.Response(200, json={"access_token": token, "expires_in": expires, "token_type": "Bearer"})


def test_env_file_supports_oauth_and_workspace_token_without_email(tmp_path, configuration):
    path = tmp_path / "mounted.env"
    path.write_text(f"JIRA_CLOUD_ID={CLOUD}\nJIRA_CLIENT_ID=client-test-only\n"
                    "JIRA_CLIENT_SECRET=secret-test-only\nBITBUCKET_API_TOKEN=workspace-test-only\n")
    configuration.setenv("ENV_FILE", str(path))
    cfg = Settings.from_env("worker")
    assert jira_configured(cfg)
    assert cfg.jira_client_id == "client-test-only"
    assert cfg.jira_client_secret == "secret-test-only"
    assert cfg.bitbucket_token == "workspace-test-only"
    assert "secret-test-only" not in repr(cfg) and "workspace-test-only" not in repr(cfg)


def test_oauth_secret_can_be_mounted_individually(tmp_path, configuration):
    path = tmp_path / "client-secret"
    path.write_text("secret-test-only\n")
    configuration.setenv("JIRA_CLOUD_ID", CLOUD)
    configuration.setenv("JIRA_CLIENT_ID", "client-test-only")
    configuration.setenv("JIRA_CLIENT_SECRET_FILE", str(path))
    assert Settings.from_env("worker").jira_client_secret == "secret-test-only"


@pytest.mark.parametrize("missing", ["JIRA_CLIENT_ID", "JIRA_CLIENT_SECRET", "JIRA_CLOUD_ID"])
def test_incomplete_oauth_configuration_fails_before_network(configuration, missing):
    for name, value in {"JIRA_CLIENT_ID": "client-test-only", "JIRA_CLIENT_SECRET": "secret-test-only", "JIRA_CLOUD_ID": CLOUD}.items():
        if name != missing:
            configuration.setenv(name, value)
    with pytest.raises(RuntimeError):
        Settings.from_env("worker")


def test_static_and_oauth_credentials_are_not_silently_mixed(configuration):
    for name, value in {"JIRA_CLIENT_ID": "client-test-only", "JIRA_CLIENT_SECRET": "secret-test-only", "JIRA_CLOUD_ID": CLOUD, "JIRA_API_TOKEN": "static-test-only"}.items():
        configuration.setenv(name, value)
    with pytest.raises(RuntimeError, match="not both"):
        Settings.from_env("worker")


def test_oauth_exchange_cache_and_refresh_before_expiry(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(jira_module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    tokens, api_calls = [], []

    def provider(request):
        if request.url.host == "auth.atlassian.com":
            assert str(request.url) == "https://auth.atlassian.com/oauth/token"
            assert parse_qs(request.content.decode()) == {"grant_type": ["client_credentials"],
                "client_id": ["client-test-only"], "client_secret": ["secret-test-only"]}
            tokens.append("access-test-" + str(len(tokens)))
            return grant(tokens[-1])
        api_calls.append(request)
        return httpx.Response(200, json={"accountId": "relay-service", "active": True})

    with httpx.Client(transport=httpx.MockTransport(provider)) as client:
        jira = Jira(oauth_settings(), client)
        assert jira.service_account() == "relay-service"
        clock[0] = 3500
        jira.read("myself")
        assert len(tokens) == 1
        clock[0] = 3641  # First token would expire at 3700; refresh 60 seconds early.
        jira.read("myself")
        assert len(tokens) == 2
    assert [r.headers["authorization"] for r in api_calls] == ["Bearer access-test-0", "Bearer access-test-0", "Bearer access-test-1"]
    assert all(str(r.url).startswith(f"https://api.atlassian.com/ex/jira/{CLOUD}/rest/api/3/") for r in api_calls)


def test_concurrent_callers_share_one_grant():
    grants = []
    def provider(request):
        grants.append(request)
        return grant()
    with httpx.Client(transport=httpx.MockTransport(provider)) as client:
        jira = Jira(oauth_settings(), client)
        with ThreadPoolExecutor(max_workers=8) as pool:
            assert set(pool.map(lambda _: jira.access_token(), range(20))) == {"access-test-only"}
    assert len(grants) == 1


def test_actor_credential_key_stays_stable_across_access_token_refresh():
    cfg = oauth_settings()
    before = credential_key(cfg)
    calls = []
    with httpx.Client(transport=httpx.MockTransport(lambda request: (calls.append(request) or grant("token-" + str(len(calls)))))) as client:
        jira = Jira(cfg, client)
        first = jira.access_token()
        jira._token_expires_at = 0
        assert jira.access_token() != first
        assert credential_key(cfg) == before
        cfg.jira_client_secret = "rotated-test-secret"
        assert credential_key(cfg) != before
        jira.access_token()
    assert len(calls) == 3


@pytest.mark.parametrize("status", [400, 401, 403, 429, 500])
def test_oauth_failure_never_calls_jira_or_exposes_credentials(status):
    calls = []
    def provider(request):
        calls.append(request)
        return httpx.Response(status, json={"error_description": "secret-test-only"})
    with httpx.Client(transport=httpx.MockTransport(provider)) as client:
        with pytest.raises((RuleError, PreflightRetry)) as exc:
            Jira(oauth_settings(), client).request("POST", "issue", {})
    assert len(calls) == 1 and calls[0].url.host == "auth.atlassian.com"
    assert "secret-test-only" not in str(exc.value)


@pytest.mark.parametrize("payload", [{}, {"access_token": "bad\nheader", "expires_in": 3600, "token_type": "Bearer"},
    {"access_token": "test-only", "expires_in": 0, "token_type": "Bearer"},
    {"access_token": "test-only", "expires_in": True, "token_type": "Bearer"},
    {"access_token": "test-only", "expires_in": 3600, "token_type": "Basic"}])
def test_invalid_token_responses_are_rejected(payload):
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))) as client:
        with pytest.raises(RuleError, match="jira_oauth_invalid_response"):
            Jira(oauth_settings(), client).access_token()


def test_oauth_redirect_is_not_followed():
    calls = []
    def provider(request):
        calls.append(request)
        return httpx.Response(302, headers={"Location": "https://untrusted.example/token"})
    with httpx.Client(transport=httpx.MockTransport(provider), follow_redirects=True) as client:
        with pytest.raises(RuleError, match="jira_oauth_http_302"):
            Jira(oauth_settings(), client).access_token()
    assert len(calls) == 1


def test_token_network_failure_is_retryable_before_any_jira_mutation():
    calls = []
    def provider(request):
        calls.append(request)
        raise httpx.ConnectError("private diagnostics must not escape", request=request)
    with httpx.Client(transport=httpx.MockTransport(provider)) as client:
        with pytest.raises(PreflightRetry) as exc:
            Jira(oauth_settings(), client).request("POST", "issue", {})
    assert len(calls) == 1 and calls[0].url.host == "auth.atlassian.com"
    assert "private" not in str(exc.value)


def test_invalid_cloud_id_does_not_issue_token_request():
    cfg = oauth_settings()
    cfg.jira_cloud_id = "invalid"
    with httpx.Client(transport=httpx.MockTransport(lambda request: pytest.fail("must not call provider"))) as client:
        with pytest.raises(RuleError, match="jira_cloud_id_invalid"):
            Jira(cfg, client).read("myself")


@pytest.mark.parametrize("method,expected_grants,expected_calls", [("GET", 2, 2), ("POST", 1, 1)])
def test_expired_access_token_retries_only_reads(method, expected_grants, expected_calls):
    grants, calls = [], []
    def provider(request):
        if request.url.host == "auth.atlassian.com":
            grants.append(request)
            return grant("access-" + str(len(grants)))
        calls.append(request)
        return httpx.Response(401 if len(calls) == 1 else 200, json={})
    with httpx.Client(transport=httpx.MockTransport(provider)) as client:
        Jira(oauth_settings(), client).request(method, "issue")
    assert len(grants) == expected_grants and len(calls) == expected_calls
