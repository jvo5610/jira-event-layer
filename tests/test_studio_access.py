from dataclasses import replace
import httpx
from fastapi.testclient import TestClient

from app.main import create_app as backend_app
from test_integration import env
from test_studio_server import config
from tools.studio_server import create_app


def linked(config, api, token):
    def upstream(request):
        response = api.request(request.method, request.url.path + ('?' + request.url.query.decode() if request.url.query else ''),
                               content=request.content, headers={'Authorization': request.headers.get('authorization', ''), 'Content-Type': 'application/json'})
        return httpx.Response(response.status_code, content=response.content, headers={'Content-Type': 'application/json'})
    return create_app(replace(config, backend_token=token), transport=httpx.MockTransport(upstream))


def delegated(api):
    response = api.post('/v1/auth/tokens', json={'name':'gui', 'permissions':['api:read','rules:write','flows:operate'], 'expires_in_seconds':3600})
    assert response.status_code == 201
    return response.json()['token']


def test_gateway_requires_admin_and_never_relays_auth_management(env, config):
    api, _, cfg = env
    token = delegated(api)
    with TestClient(linked(config, api, token), base_url=config.public_origin) as gui:
        assert gui.get('/access/tokens').status_code == 403
        assert gui.post('/access/session', json={'token':token}).status_code == 403
        for path in ('/api/v1/auth/tokens', '/api/v1/auth/tokens/self/rotate'):
            assert gui.post(path, json={}).status_code == 403
        assert gui.get('/api/v1/auth/me').json()['token_id']
        assert gui.post('/access/session', json={'token':cfg.admin_bootstrap_token}, headers={'Origin':'https://evil.example'}).status_code == 403
        login = gui.post('/access/session', json={'token':cfg.admin_bootstrap_token})
        assert login.status_code == 200
        assert 'httponly' in login.headers['set-cookie'].lower()
        assert 'samesite=strict' in login.headers['set-cookie'].lower()
        assert cfg.admin_bootstrap_token not in login.text + login.headers['set-cookie']
        assert gui.get('/access/tokens').status_code == 200
        assert token not in gui.get('/access/backend').text
        created = gui.post('/access/tokens', json={'name':'from-gui','permissions':['api:read'], 'expires_in_seconds':3600})
        assert created.status_code == 201
        secret = created.json()['token']
        assert secret not in gui.get('/access/tokens').text
        assert gui.post('/access/tokens/' + created.json()['id'] + '/revoke').status_code == 200
        assert api.get('/v1/schema', headers={'Authorization':'Bearer '+secret}).status_code == 401
        assert gui.post('/access/logout').status_code == 200
        assert gui.get('/access/tokens').status_code == 403


def test_gui_admin_rotation_keeps_new_credential_only_on_server(env, config):
    api, _, cfg = env
    with TestClient(linked(config, api, delegated(api)), base_url=config.public_origin) as gui:
        gui.post('/access/session', json={'token':cfg.admin_bootstrap_token}).raise_for_status()
        replacement = gui.post('/access/rotation/start').json()
        assert replacement['token'] not in gui.get('/access/session').text
        assert gui.post('/access/rotation/confirm').status_code == 200
        assert api.get('/v1/schema').status_code == 401
        assert gui.get('/access/tokens').status_code == 200
        assert replacement['token'] not in gui.get('/runtime-config').text


def test_gui_credential_rotation_requires_updated_configuration_before_confirmation(env, config):
    api, _, cfg = env
    old = delegated(api)
    with TestClient(linked(config, api, old), base_url=config.public_origin) as gui:
        gui.post('/access/session', json={'token':cfg.admin_bootstrap_token}).raise_for_status()
        replacement = gui.post('/access/backend/rotate').json()
        assert gui.post('/access/backend/confirm').status_code == 409
        assert gui.get('/api/v1/schema').status_code == 200
    # Simulate the operator updating the mounted secret/environment and restarting the gateway.
    with TestClient(linked(config, api, replacement['token']), base_url=config.public_origin) as restarted:
        assert restarted.get('/access/session').status_code == 403
        restarted.post('/access/session', json={'token':cfg.admin_bootstrap_token}).raise_for_status()
        assert restarted.get('/access/backend').json()['pending'] is True
        assert restarted.post('/access/backend/confirm').status_code == 200
        assert restarted.get('/api/v1/schema').status_code == 200
        assert api.get('/v1/schema', headers={'Authorization':'Bearer '+old}).status_code == 401


def test_expired_and_revoked_administrator_sessions_are_denied(env, config, monkeypatch):
    import tools.studio_access as access
    api, _, cfg = env
    other = api.post('/v1/auth/tokens', json={'name':'another-admin','permissions':['auth:admin'],'expires_in_seconds':3600}).json()
    with TestClient(linked(config, api, delegated(api)), base_url=config.public_origin) as gui:
        gui.post('/access/session', json={'token':other['token']}).raise_for_status()
        api.post('/v1/auth/tokens/'+other['id']+'/revoke').raise_for_status()
        assert gui.get('/access/tokens').status_code == 403
        gui.post('/access/session', json={'token':cfg.admin_bootstrap_token}).raise_for_status()
        clock = access.clock
        monkeypatch.setattr(access, 'clock', lambda: clock() + 901)
        assert gui.get('/access/tokens').status_code == 403


def test_pending_admin_rotation_can_be_resumed_after_gateway_restart(env, config):
    api, _, cfg = env
    token = delegated(api)
    with TestClient(linked(config, api, token), base_url=config.public_origin) as first:
        first.post('/access/session', json={'token':cfg.admin_bootstrap_token}).raise_for_status()
        replacement = first.post('/access/rotation/start').json()
    with TestClient(linked(config, api, token), base_url=config.public_origin) as restarted:
        assert restarted.post('/access/session', json={'token':replacement['token']}).status_code == 403
        response = restarted.post('/access/session', json={'token':replacement['token'], 'confirm_rotation':True})
        assert response.status_code == 200
        assert restarted.get('/access/tokens').status_code == 200
        assert api.get('/v1/schema').status_code == 401


def test_delegated_replacement_cannot_resume_an_administrator_session(env, config):
    api, _, _ = env
    token = delegated(api)
    replacement = api.post('/v1/auth/tokens/self/rotate', headers={'Authorization':'Bearer '+token}).json()
    with TestClient(linked(config, api, token), base_url=config.public_origin) as gui:
        assert gui.post('/access/session', json={'token':replacement['token'], 'confirm_rotation':True}).status_code == 409
        assert api.get('/v1/schema', headers={'Authorization':'Bearer '+token}).status_code == 200


def test_admin_session_failures_do_not_reopen_basic_auth_dialog(env, config):
    api, _, cfg = env
    secured = replace(config, auth_mode='basic', username='operator', password='test-console-password', public_origin='https://console.example')
    with TestClient(linked(secured, api, delegated(api)), base_url=secured.public_origin) as gui:
        login = ('operator','test-console-password')
        assert gui.get('/access/session').status_code == 401  # Actual GUI login still uses Basic.
        locked = gui.get('/access/session', auth=login)
        assert locked.status_code == 403 and 'www-authenticate' not in locked.headers
        opened = gui.post('/access/session', json={'token':cfg.admin_bootstrap_token}, auth=login)
        assert opened.status_code == 200
        assert 'secure' in {part.strip().lower() for part in opened.headers['set-cookie'].split(';')}
