import hashlib
import json
from dataclasses import replace

import httpx
import pytest
import yaml

from app.actors import actor_ids
from app.dsl import RuleError
from app.jira import Jira
from app.worker import Worker
from test_integration import env, activate, webhook_client

CLOUD = '11111111-1111-4111-8111-111111111111'


def registered(env, actor='relay-service', token='actor-test-token'):
    _, store, cfg = env
    cfg = replace(cfg, jira_token=token, jira_cloud_id=CLOUD)
    store.remember_jira_actor(cfg, actor)
    return cfg


def read_rule(client, event_type='jira:issue_updated'):
    return activate(client, yaml.safe_dump({'apiVersion': 'automation/v1', 'kind': 'Rule', 'name': 'actor-test',
        'trigger': {'source': 'jira', 'event': event_type}, 'when': {'path': 'issue.key', 'op': 'exists'},
        'actions': [{'type': 'jira.issue.get', 'issue': '${issue.key}'}]}))


def send(env, listener, event_type, payload, key='actor-event'):
    client, _, cfg = env
    if listener:
        import hmac
        raw = json.dumps({'webhookEvent': event_type, **payload}).encode()
        headers = {'X-Atlassian-Webhook-Identifier': key,
            'X-Hub-Signature': 'sha256=' + hmac.new(cfg.webhook_secret.encode(), raw, hashlib.sha256).hexdigest()}
        result = listener.post('/webhooks/jira', content=raw, headers=headers)
    else:
        result = client.post('/v1/events', headers={'Idempotency-Key': key},
            json={'source': 'jira', 'event_type': event_type, 'payload': payload})
    assert result.status_code == 202, result.text
    return result.json()


@pytest.mark.parametrize('native', [False, True])
@pytest.mark.parametrize('event_type', ['jira:issue_created', 'jira:issue_updated', 'jira:issue_deleted',
    'comment_created', 'comment_updated', 'comment_deleted', 'issuelink_created', 'issuelink_deleted',
    'worklog_created', 'worklog_updated', 'worklog_deleted', 'jira:custom_event'])
def test_service_actor_is_ignored_globally_for_every_event_and_ingress(env, webhook_client, native, event_type):
    client, store, _ = env
    registered(env)
    read_rule(client, event_type)
    listener = webhook_client if native else None
    payload = {'user': {'accountId': 'relay-service'}, 'issue': {'key': 'KAN-1'}}
    event = send(env, listener, event_type, payload)
    assert event['runs'] == [] and event['ignored_reason'] == 'ignored_service_actor'
    assert store.query('SELECT count(*) n FROM evaluations')[0]['n'] == 0
    assert client.post('/v1/events/' + event['event_id'] + '/execute').status_code == 409
    assert send(env, listener, event_type, payload)['duplicate']
    detail = client.get('/v1/events/' + event['event_id']).json()
    assert detail['execution_blocked_reason'] == detail['last_error'] == 'ignored_service_actor'
    assert detail['payload']['user']['accountId'] == 'relay-service'


@pytest.mark.parametrize('event_type,obj,actor_field', [('comment_created', 'comment', 'author'),
    ('comment_updated', 'comment', 'updateAuthor'), ('worklog_created', 'worklog', 'author'),
    ('worklog_updated', 'worklog', 'updateAuthor')])
def test_actor_fallback_for_comments_and_worklogs(env, event_type, obj, actor_field):
    registered(env)
    payload = {'issue': {'key': 'KAN-1'}, obj: {actor_field: {'accountId': 'relay-service'}}}
    assert send(env, None, event_type, payload)['ignored_reason'] == 'ignored_service_actor'


@pytest.mark.parametrize('native', [False, True])
def test_human_change_to_service_created_ticket_still_runs(env, webhook_client, native):
    client, _, _ = env
    registered(env)
    read_rule(client)
    payload = {'user': {'accountId': 'human-user'}, 'issue': {'key': 'KAN-1', 'fields': {
        'creator': {'accountId': 'relay-service'}, 'reporter': {'accountId': 'relay-service'},
        'assignee': {'accountId': 'relay-service'}}}}
    event = send(env, webhook_client if native else None, 'jira:issue_updated', payload)
    assert len(event['runs']) == 1 and event['ignored_reason'] is None


def test_human_comment_update_does_not_use_original_service_author(env):
    registered(env)
    payload = {'issue': {'key': 'KAN-1'}, 'comment': {'author': {'accountId': 'relay-service'},
        'updateAuthor': {'accountId': 'human-user'}}}
    assert actor_ids('comment_updated', payload) == {'human-user'}
    assert send(env, None, 'comment_updated', payload)['ignored_reason'] is None


@pytest.mark.parametrize('payload', [{}, {'user': None}, {'user': {'displayName': 'Someone'}},
    {'user': {'accountId': ''}}, {'user': {'accountId': 'anonymous'}},
    {'issue': {'fields': {'creator': {'accountId': 'human-user'}}}}])
def test_unidentifiable_jira_actor_is_held_and_cannot_be_replayed(env, payload):
    client, _, _ = env
    event = send(env, None, 'jira:issue_created', payload)
    assert event['ignored_reason'] == 'held_actor_unknown' and event['runs'] == []
    assert client.post('/v1/events/' + event['event_id'] + '/execute').status_code == 409


def test_deleted_comment_author_is_not_assumed_to_be_the_deleter(env):
    registered(env)
    event = send(env, None, 'comment_deleted', {'comment': {'author': {'accountId': 'relay-service'}}})
    assert event['ignored_reason'] == 'held_actor_unknown'


def test_disagreeing_actor_fields_fail_closed(env):
    payload = {'user': {'accountId': 'human-1'}, 'comment': {'updateAuthor': {'accountId': 'human-2'}}}
    assert send(env, None, 'comment_updated', payload)['ignored_reason'] == 'held_actor_unknown'
    registered(env, 'human-2')
    assert send(env, None, 'comment_updated', payload, 'second')['ignored_reason'] == 'ignored_service_actor'


def test_automatic_identity_is_verified_once_and_shared_across_workers_and_rotation(env):
    _, store, base = env
    calls = []
    def profile(request):
        calls.append(request)
        assert request.method == 'GET' and request.url.path.endswith('/myself')
        assert request.url.host == 'api.atlassian.com'
        return httpx.Response(200, json={'accountId': 'relay-' + str(len(calls)), 'active': True})
    cfg = replace(base, jira_token='first-token', jira_cloud_id=CLOUD)
    with httpx.Client(transport=httpx.MockTransport(profile)) as remote:
        worker = Worker(store, cfg, jira=Jira(cfg, remote))
        restarted = Worker(store, cfg, jira=Jira(cfg, remote))
        rotated = Worker(store, replace(cfg, jira_token='second-token'), jira=Jira(replace(cfg, jira_token='second-token'), remote))
        try:
            worker.ensure_service_identity()
            worker.ensure_service_identity()
            restarted.ensure_service_identity()
            rotated.ensure_service_identity()
        finally:
            for item in (worker, restarted, rotated):
                item.connector.client.close()
    assert len(calls) == 2
    assert {row['account_id'] for row in store.query('SELECT account_id FROM service_actors')} == {'relay-1', 'relay-2'}
    metadata = str(store.query('SELECT * FROM service_actor_credentials'))
    assert 'first-token' not in metadata and 'second-token' not in metadata
    for number, actor in enumerate(('relay-1', 'relay-2')):
        assert send(env, None, 'jira:issue_created', {'user': {'accountId': actor}}, str(number))['ignored_reason'] == 'ignored_service_actor'


@pytest.mark.parametrize('status,profile', [(401, {}), (403, {}), (429, {}), (500, {}),
    (200, {}), (200, {'accountId': 'unknown'}), (200, {'accountId': 'relay', 'active': False})])
def test_identity_failure_never_registers_or_dispatches_writes(env, status, profile):
    client, store, base = env
    read_rule(client)
    event = send(env, None, 'jira:issue_updated', {'user': {'accountId': 'human'}, 'issue': {'key': 'KAN-1'}})
    cfg = replace(base, jira_token='unverified', jira_cloud_id=CLOUD)
    calls = []
    def provider(request):
        calls.append(request)
        assert request.method == 'GET' and request.url.path.endswith('/myself')
        return httpx.Response(status, json=profile)
    with httpx.Client(transport=httpx.MockTransport(provider)) as remote:
        worker = Worker(store, cfg, jira=Jira(cfg, remote))
        try:
            assert worker.tick()
        finally:
            worker.connector.client.close()
    detail = client.get('/v1/runs/' + event['runs'][0]['id']).json()
    assert detail['last_error'] == 'jira_service_identity_unverified'
    assert len(calls) == 1 and not store.query('SELECT * FROM service_actors')


def test_queued_service_event_is_blocked_after_identity_discovery(env):
    client, store, _ = env
    read_rule(client)
    event = send(env, None, 'jira:issue_updated', {'user': {'accountId': 'relay-service'}, 'issue': {'key': 'KAN-1'}})
    assert len(event['runs']) == 1
    cfg = registered(env)
    assert client.get('/v1/events/' + event['event_id']).json()['execution_blocked_reason'] == 'ignored_service_actor'
    with httpx.Client(transport=httpx.MockTransport(lambda _: pytest.fail('must not contact provider'))) as remote:
        worker = Worker(store, cfg, jira=Jira(cfg, remote))
        try:
            assert worker.tick()
        finally:
            worker.connector.client.close()
    assert client.get('/v1/runs/' + event['runs'][0]['id']).json()['last_error'] == 'ignored_service_actor'
    assert client.post('/v1/events/' + event['event_id'] + '/execute').status_code == 409


def test_write_budgets_recheck_actor_discovered_after_claim(env):
    client, store, base = env
    read_rule(client)
    event = send(env, None, 'jira:issue_updated', {'user': {'accountId': 'relay-service'}, 'issue': {'key': 'KAN-1'}})
    worker = Worker(store, base)
    try:
        run = worker.claim()
        registered(env)
        for reserve in (worker.reserve_jira_write, worker.reserve_bitbucket_write):
            with pytest.raises(RuleError, match='ignored_service_actor'):
                reserve(run)
    finally:
        worker.jira.client.close()
        worker.connector.client.close()
    assert store.query('SELECT count(*) n FROM jira_write_attempts')[0]['n'] == 0
    assert store.query('SELECT count(*) n FROM connector_write_attempts')[0]['n'] == 0


def test_replay_rejection_commits_cancellation_of_old_pending_runs(env):
    client, store, _ = env
    read_rule(client)
    event = send(env, None, 'jira:issue_updated', {'user': {'accountId': 'relay-service'}, 'issue': {'key': 'KAN-1'}})
    registered(env)
    assert client.post('/v1/events/' + event['event_id'] + '/execute').status_code == 409
    detail = client.get('/v1/runs/' + event['runs'][0]['id']).json()
    assert detail['status'] == 'failed' and detail['last_error'] == 'ignored_service_actor'
    assert store.query('SELECT last_error FROM events')[0]['last_error'] == 'ignored_service_actor'
    assert any(row['status'] == 'failed' for row in detail['log'])


def test_authenticated_test_source_does_not_bypass_known_service_actor(env):
    client, _, _ = env
    registered(env)
    result = client.post('/v1/events', headers={'Idempotency-Key': 'synthetic-service'}, json={
        'source': 'test', 'event_type': 'anything', 'payload': {'user': {'accountId': 'relay-service'}}})
    assert result.status_code == 202 and result.json()['ignored_reason'] == 'ignored_service_actor'
    assert client.post('/v1/events/' + result.json()['event_id'] + '/execute').status_code == 409
