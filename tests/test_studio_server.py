import base64
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient
import httpx
import pytest

from tools.studio_server import Settings, create_app


@pytest.fixture
def config(tmp_path):
    (tmp_path / 'index.html').write_text('<h1>Editor</h1>')
    (tmp_path / '.env').write_text('private-test-config')
    (tmp_path / 'assets').mkdir()
    (tmp_path / 'assets' / 'app.js').write_text('export const version=1;')
    return Settings.from_env({
        'STUDIO_BACKEND_URL': 'http://private-api:8080',
        'STUDIO_PUBLIC_ORIGIN': 'http://127.0.0.1:8093',
        'STUDIO_BACKEND_TOKEN': 'private-server-only-token',
        'STUDIO_AUTH_MODE': 'local', 'STUDIO_STATIC_DIR': str(tmp_path),
    })


@pytest.fixture
def gateway(config):
    calls = []
    def upstream(request):
        calls.append(request)
        return httpx.Response(200, json={'ok': True})
    with TestClient(create_app(config, transport=httpx.MockTransport(upstream)),
                    base_url=config.public_origin) as client:
        yield client, calls


def test_runtime_config_and_server_only_credentials(gateway):
    client, calls = gateway
    runtime = client.get('/runtime-config')
    assert runtime.json() == {'backend_url': 'http://private-api:8080', 'api_path': '/api', 'auth_mode': 'local'}
    response = client.post('/api/v1/events', json={'source': 'jira'}, headers={
        'Idempotency-Key': 'stable', 'Authorization': 'Bearer browser-supplied', 'Cookie': 'private=cookie'})
    assert response.status_code == 200
    assert calls[0].headers['Authorization'] == 'Bearer private-server-only-token'
    assert calls[0].headers['Idempotency-Key'] == 'stable'
    assert 'cookie' not in calls[0].headers
    assert 'private-server-only-token' not in response.text + runtime.text
    assert client.get('/').headers['content-security-policy']
    assert runtime.headers['cache-control'] == 'no-store'


def test_blocks_other_origins_hosts_and_arbitrary_paths(gateway):
    client, calls = gateway
    for headers in ({'Origin': 'https://untrusted.example'}, {'Host': 'untrusted.example'},
                    {'Sec-Fetch-Site': 'cross-site'}, {'Host':'wrong', 'X-Forwarded-Host':'127.0.0.1:8093'}):
        assert client.get('/api/v1/rules', headers=headers).status_code == 403
    assert client.get('/api/anything').status_code == 404
    assert client.get('/api/v1/%2e%2e/admin').status_code == 404
    assert client.delete('/api/v1/rules').status_code == 405
    assert not calls


def test_static_paths_cache_and_body_limit(gateway):
    client, calls = gateway
    assert client.get('/assets/not-found.js').status_code == 404
    assert client.get('/%2e%2e/private').status_code == 404
    assert client.get('/.env').status_code == 404
    assert client.post('/api/v1/events', content='x' * 1048577).status_code == 413
    assert client.post('/api/v1/events', headers={'Content-Length': 'bad'}).status_code == 400
    assert client.get('/assets/app.js').headers['cache-control'].startswith('public,')
    assert client.get('/').headers['cache-control'] == 'no-store'
    assert not calls


@pytest.mark.parametrize('backend,origin', [
    ('https://backend.example/api', 'https://console.example'),
    ('http://service.internal:9000', 'https://ui.example:8443'),
])
def test_same_build_supports_different_runtime_addresses(config, backend, origin):
    settings = replace(config, backend_url=backend, public_origin=origin, auth_mode='basic',
                       username='operator', password='test-login-password-123')
    calls = []
    def upstream(request):
        calls.append(request)
        return httpx.Response(200, json={'ok': True}, headers={'Set-Cookie':'leak=blocked'})
    with TestClient(create_app(settings, transport=httpx.MockTransport(upstream)), base_url=origin) as client:
        for path in ['/', '/runtime-config', '/assets/app.js', '/api/v1/rules']:
            assert client.get(path).status_code == 401
        assert client.get('/healthz').status_code == 200
        assert client.get('/readyz').status_code == 200
        calls.clear()
        auth = ('operator', 'test-login-password-123')
        assert client.get('/runtime-config', auth=auth).json()['backend_url'] == backend
        response = client.get('/api/v1/rules?limit=2', auth=auth)
        assert response.status_code == 200 and 'set-cookie' not in response.headers
        assert str(calls[0].url) == backend + '/v1/rules?limit=2'
        assert calls[0].headers['authorization'] == 'Bearer private-server-only-token'
        for header in ['Basic !invalid!', 'Bearer value', 'Basic ' + base64.b64encode(b'operator:wrong').decode()]:
            assert client.get('/runtime-config', headers={'Authorization': header}).status_code == 401


def test_backend_unavailable_never_exposes_connection_secrets(config):
    def unavailable(request):
        raise httpx.ConnectError('secret credentials and internal diagnostics', request=request)
    with TestClient(create_app(config, transport=httpx.MockTransport(unavailable)), base_url=config.public_origin) as client:
        assert client.get('/readyz').status_code == 503
        response = client.get('/api/v1/rules')
        assert response.status_code == 502
        assert 'secret' not in response.text


def test_redirect_not_followed(config):
    calls = []
    def upstream(request):
        calls.append(request)
        return httpx.Response(302, headers={'Location': 'https://other.example/steal'})
    with TestClient(create_app(config, transport=httpx.MockTransport(upstream)), base_url=config.public_origin) as client:
        response = client.get('/api/v1/rules')
        assert response.status_code == 302 and 'location' not in response.headers
        assert len(calls) == 1


def valid_env(tmp_path):
    return {'STUDIO_BACKEND_URL':'https://backend.example', 'STUDIO_PUBLIC_ORIGIN':'https://console.example',
            'STUDIO_BACKEND_TOKEN':'backend-test-token', 'STUDIO_LOGIN_USERNAME':'operator',
            'STUDIO_LOGIN_PASSWORD':'login-test-password-long', 'STUDIO_STATIC_DIR':str(tmp_path)}


@pytest.mark.parametrize('changes', [
    {'STUDIO_BACKEND_URL':''}, {'STUDIO_BACKEND_URL':'https://user:secret@backend.example'},
    {'STUDIO_BACKEND_URL':'https://backend.example?token=secret'}, {'STUDIO_BACKEND_URL':'file:///etc/passwd'},
    {'STUDIO_PUBLIC_ORIGIN':'https://console.example/path'}, {'STUDIO_PUBLIC_ORIGIN':'http://console.example'},
    {'STUDIO_LOGIN_PASSWORD':''}, {'STUDIO_LOGIN_PASSWORD':'short'}, {'STUDIO_BACKEND_TOKEN':''},
    {'STUDIO_LOGIN_USERNAME':'operator:wrong'}, {'STUDIO_AUTH_MODE':'none'},
    {'STUDIO_AUTH_MODE':'local', 'STUDIO_HOST':'0.0.0.0'},
    {'STUDIO_AUTH_MODE':'local', 'STUDIO_HOST':'127.0.0.1'},
    {'STUDIO_PORT':'0'}, {'STUDIO_PORT':'65536'}, {'STUDIO_BACKEND_TOKEN':'bad\nheader'},
])
def test_invalid_configuration_fails_closed(tmp_path, changes):
    with pytest.raises(ValueError):
        Settings.from_env(valid_env(tmp_path) | changes)


def test_secret_files_and_default_authenticated_mode(tmp_path):
    token = tmp_path / 'token'
    token.write_text('file-token\n')
    password = tmp_path / 'password'
    password.write_text('file-login-password-long\n')
    env = valid_env(tmp_path)
    del env['STUDIO_BACKEND_TOKEN'], env['STUDIO_LOGIN_PASSWORD']
    env.update(STUDIO_BACKEND_TOKEN_FILE=str(token), STUDIO_LOGIN_PASSWORD_FILE=str(password))
    settings = Settings.from_env(env)
    assert settings.auth_mode == 'basic'
    assert settings.backend_token == 'file-token'
    assert settings.password == 'file-login-password-long'
    assert settings.backend_token not in repr(settings) and settings.password not in repr(settings)
    with pytest.raises(ValueError):
        Settings.from_env(env | {'STUDIO_BACKEND_TOKEN':'ambiguous'})


def test_product_has_no_deployment_provider_dependencies():
    root = Path(__file__).resolve().parents[1]
    gateway = (root / 'tools/studio_server.py').read_text().lower()
    for marker in ['boto3', 'kubectl', 'subprocess', 'fett', "'ssm'", '58188', '192.168.']:
        assert marker not in gateway
    for relative in ['frontend/src/main.tsx', 'frontend/src/i18n.ts']:
        product = (root / relative).read_text().lower()
        for marker in ['fett', 'kubectl', 'ssm', 'docker', '192.168.']:
            assert marker not in product
    assert 'nginx.conf.template' not in (root / 'frontend/Dockerfile').read_text()
