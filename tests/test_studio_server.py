import threading
import httpx
import pytest

from tools.studio_server import Gateway


@pytest.fixture
def gateway(tmp_path):
    (tmp_path/'index.html').write_text('<h1>Editor</h1>')
    server=Gateway(('127.0.0.1',0),tmp_path,'http://private-api','private-server-only-token')
    calls=[]
    def upstream(request):
        calls.append(request)
        return httpx.Response(200,json={'ok':True})
    server.client.close();server.client=httpx.Client(transport=httpx.MockTransport(upstream))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    with httpx.Client(base_url=f'http://127.0.0.1:{server.server_port}') as client:yield client,calls
    server.shutdown();server.server_close();thread.join(timeout=2)


def test_gateway_never_returns_secret_and_preserves_idempotency(gateway):
    client,calls=gateway
    assert client.get('/studio-config').json()['environment']=='Fett'
    response=client.post('/api/v1/events',json={'source':'jira'},headers={'Idempotency-Key':'stable','Authorization':'Bearer browser-supplied'})
    assert response.status_code==200
    assert calls[0].headers['Authorization']=='Bearer private-server-only-token'
    assert calls[0].headers['Idempotency-Key']=='stable'
    assert 'private-server-only-token' not in response.text
    assert client.get('/').headers['content-security-policy']


def test_gateway_blocks_other_origins_hosts_and_arbitrary_proxy_paths(gateway):
    client,calls=gateway
    assert client.get('/api/v1/rules',headers={'Origin':'https://untrusted.example'}).status_code==403
    assert client.get('/api/v1/rules',headers={'Host':'untrusted.example'}).status_code==403
    assert client.get('/api/v1/rules',headers={'Sec-Fetch-Site':'cross-site'}).status_code==403
    assert client.get('/api/anything').status_code==404
    assert client.delete('/api/v1/rules').status_code==405
    assert not calls


def test_gateway_static_paths_and_body_limit(gateway):
    client,calls=gateway
    assert client.get('/assets/not-found.js').status_code==404
    assert client.get('/%2e%2e/private').status_code==404
    assert client.post('/api/v1/events',content='x'*1048577).status_code==413
    assert not calls
