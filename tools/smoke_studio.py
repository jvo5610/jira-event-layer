"""Provider-free GUI acceptance against real Docker API/Postgres, optionally held for visual QA.

All credentials are disposable test credentials, never copied from operator configuration.
--serve binds only loopback and prints its URL; Enter removes only this test's resources.
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import socket
import tempfile
import uuid

import httpx

if __package__:
    from .smoke_container import command, eventually
else:
    from smoke_container import command, eventually

TEST_LOGIN = ('operator', 'disposable-console-password')
TEST_ADMIN = 'disposable-local-bootstrap-' + 't' * 40


def run(args):
    name = 'relay-gui-smoke-' + uuid.uuid4().hex[:12]
    targets = []
    command('docker', 'network', 'create', name)
    try:
        with tempfile.TemporaryDirectory(prefix=name) as directory:
            def envfile(label, values):
                path = Path(directory) / label
                with open(path, 'x', opener=lambda p, f: os.open(p, f, 0o600)) as stream:
                    stream.write('\n'.join(f'{key}={value}' for key, value in values.items()) + '\n')
                return str(path)

            def launch(label, image, values, port=None, cmd=()):
                target = name + '-' + label
                targets.append(target)
                invocation = ['docker', 'run', '-d', '--name', target, '--network', name,
                              '--env-file', envfile(label, values)]
                if label != 'db':
                    invocation += ['--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges:true',
                                   '--tmpfs', '/tmp:size=16m,mode=1777']
                if port:
                    invocation += ['-p', port]
                command(*invocation, image, *cmd)
                return target

            password = secrets.token_urlsafe(40)
            db = launch('db', 'postgres:17', {'POSTGRES_PASSWORD':password, 'POSTGRES_DB':'automation_test'})
            eventually(lambda: command('docker','exec',db,'pg_isready','-U','postgres',check=False).returncode == 0)
            runtime = {'DB_HOST':db,'DB_PORT':'5432','DB_NAME':'automation_test','DB_USER':'postgres',
                       'DB_PASSWORD':password,'TENANT_ID':name,
                       'ADMIN_BOOTSTRAP_TOKEN':TEST_ADMIN,
                       'JIRA_WEBHOOK_SECRET':secrets.token_urlsafe(48)}
            migration = envfile('migration', runtime)
            command('docker','run','--rm','--network',name,'--env-file',migration,args.api_image,'python','-m','app.migrate')
            api = launch('api',args.api_image,runtime,'127.0.0.1::8080')
            api_port = command('docker','port',api,'8080/tcp').stdout.decode().strip().rsplit(':',1)[1]
            api_url = 'http://127.0.0.1:' + api_port
            with httpx.Client(base_url=api_url,headers={'Authorization':'Bearer '+TEST_ADMIN},trust_env=False,timeout=15) as backend:
                eventually(lambda: backend.get('/readyz').status_code == 200)
                issued = backend.post('/v1/auth/tokens',json={'name':'gui-service',
                    'permissions':['api:read','rules:write','flows:operate'],'expires_in_seconds':3600})
                issued.raise_for_status()
                delegated = issued.json()['token']
                # Reserve a loopback publication port before setting the exact browser origin.
                with socket.socket() as reservation:
                    reservation.bind(('127.0.0.1',0))
                    port = reservation.getsockname()[1]
                origin = f'http://127.0.0.1:{port}'
                gui_env = {'STUDIO_BACKEND_URL':f'http://{api}:8080','STUDIO_BACKEND_TOKEN':delegated,
                           'STUDIO_PUBLIC_ORIGIN':origin,'STUDIO_LOGIN_USERNAME':TEST_LOGIN[0],
                           'STUDIO_LOGIN_PASSWORD':TEST_LOGIN[1]}
                gui = launch('gui',args.studio_image,gui_env,f'127.0.0.1:{port}:8080')
                with httpx.Client(base_url=origin,auth=TEST_LOGIN,trust_env=False,timeout=15) as browser:
                    eventually(lambda: browser.get('/readyz').status_code == 200)
                    assert browser.get('/').status_code == 200
                    unsafe_yaml = '''apiVersion: automation/v1
kind: Rule
name: unsafe-gui-draft
trigger: {source: jira, event: 'jira:issue_updated'}
when: {path: issue.key, op: exists}
actions:
  - type: jira.issue.edit
    issue: {path: issue.key}
    fields: {summary: {value: 'Too broad'}}
'''
                    yaml_headers = {'Content-Type': 'application/yaml'}
                    for query in ('', '?targets=false'):
                        blocked = browser.post('/api/v1/rules/validate' + query, content=unsafe_yaml, headers=yaml_headers)
                        assert blocked.status_code == 422 and 'unsafe_write_scope' in blocked.json()['detail']
                    draft = browser.post('/api/v1/rules', content=unsafe_yaml, headers=yaml_headers)
                    draft.raise_for_status()
                    blocked = browser.post('/api/v1/rules/unsafe-gui-draft/activate', json={
                        'version': draft.json()['version'], 'sha256': draft.json()['sha256']})
                    assert blocked.status_code == 422 and 'unsafe_write_scope' in blocked.json()['detail']
                    assert browser.get('/access/tokens').status_code == 403
                    assert browser.post('/access/session',json={'token':delegated}).status_code == 403
                    browser.post('/access/session',json={'token':TEST_ADMIN}).raise_for_status()
                    created = browser.post('/access/tokens',json={'name':'gui-acceptance-readonly',
                        'permissions':['api:read'],'expires_in_seconds':3600}).json()
                    assert created['token'] not in browser.get('/access/tokens').text
                    assert browser.post('/api/v1/auth/tokens',json={}).status_code == 403
                    browser.post('/access/tokens/'+created['id']+'/revoke').raise_for_status()
                    assert backend.get('/v1/schema',headers={'Authorization':'Bearer '+created['token']}).status_code == 401
                    if args.serve:
                        print(json.dumps({'url':origin,'credentials':'disposable test only; see TEST_LOGIN/TEST_ADMIN in this script'}),flush=True)
                        input('Visual QA ready; press Enter to finish rotation tests and remove temporary services. ')
                    pending = browser.post('/access/rotation/start').json()
                    assert pending['token'] not in browser.get('/access/session').text
                    browser.post('/access/rotation/confirm').raise_for_status()
                    assert browser.get('/access/tokens').status_code == 200
                    assert backend.get('/v1/schema').status_code == 401
                    backend.headers['Authorization'] = 'Bearer '+pending['token']
                    browser.post('/access/logout').raise_for_status()
                    assert browser.get('/access/tokens').status_code == 403
            print('GUI Docker acceptance passed: real API/DB, broad-write validation/activation blocked, admin sessions, issuance, revocation, rotation and logout. No provider calls.')
    finally:
        for target in reversed(targets):
            command('docker','rm','-f','-v',target,check=False)
        command('docker','network','rm',name,check=False)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--api-image',required=True)
    parser.add_argument('--studio-image',required=True)
    parser.add_argument('--serve',action='store_true')
    run(parser.parse_args())
