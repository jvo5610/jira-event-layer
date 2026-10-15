import copy
from types import SimpleNamespace

import httpx
import pytest
import yaml
from psycopg.types.json import Jsonb

from app.dsl import Rule, RuleError
from app.jira import Jira
from app.safety import validate_safety
from app.worker import Worker
from test_integration import env, ingest

PROJECT = {'path': 'issue.fields.project.key', 'op': 'eq', 'value': 'KAN'}
FIELD = {'path': 'field', 'op': 'eq', 'value': 'status'}
VALUE = {'path': 'toString', 'op': 'eq', 'value': 'Done'}
CHANGE = {'some': {'path': 'changelog.items', 'where': {'all': [FIELD, VALUE]}}}
EXISTS = {'path': 'issue.key', 'op': 'exists'}


def spec():
    return {'apiVersion': 'automation/v1', 'kind': 'Rule', 'name': 'bounded-writes',
            'trigger': {'source': 'jira', 'event': 'jira:issue_updated'},
            'when': {'all': [copy.deepcopy(PROJECT), copy.deepcopy(CHANGE)]},
            'actions': [{'type': 'jira.issue.edit', 'issue': '${issue.key}',
                         'fields': {'summary': {'value': 'Updated'}}}]}


@pytest.mark.parametrize('scope', [EXISTS, {'not': PROJECT},
    {'path': 'issue.fields.project.key', 'op': 'ne', 'value': 'OTHER'},
    {'path': 'issue.fields.project.key', 'op': 'in', 'value': []},
    {'path': 'issue.fields.project.key', 'op': 'contains', 'value': ''},
    {'any': [PROJECT, EXISTS]}, {'any': [PROJECT, {'not': EXISTS}]},
    {'some': {'path': 'items', 'where': PROJECT}},
    {'path': 'issue.fields.project.key', 'op': 'eq', 'value': '*'},
    {'path': 'issue.fields.project.key', 'op': 'eq', 'value': None}])
def test_every_write_branch_needs_positive_finite_source_scope(scope):
    raw = spec()
    raw['when'] = {'all': [scope, CHANGE]}
    with pytest.raises(RuleError, match='unsafe_write_scope'):
        validate_safety(Rule.model_validate(raw))


@pytest.mark.parametrize('change', [EXISTS, {'not': CHANGE},
    {'some': {'path': 'changelog.items', 'where': FIELD}},
    {'some': {'path': 'changelog.items', 'where': VALUE}},
    {'some': {'path': 'changelog.items', 'where': {'all': [FIELD, {'path': 'to', 'op': 'exists'}]}}},
    {'some': {'path': 'changelog.items', 'where': {'any': [FIELD, VALUE]}}},
    {'any': [CHANGE, EXISTS]},
    {'all': [{'some': {'path': 'changelog.items', 'where': FIELD}},
             {'some': {'path': 'changelog.items', 'where': VALUE}}]},
    {'some': {'path': 'unrelated.items', 'where': {'all': [FIELD, VALUE]}}}])
def test_changed_field_and_value_must_be_guaranteed_in_same_item(change):
    raw = spec()
    raw['when'] = {'all': [PROJECT, change]}
    with pytest.raises(RuleError, match='unsafe_change_scope'):
        validate_safety(Rule.model_validate(raw))


def test_nested_or_with_bounded_branches_and_bracket_aliases_is_accepted():
    raw = spec()
    raw['when'] = {'any': [raw['when'], {'all': [
        {'path': 'issue["key"]', 'op': 'in', 'value': ['OPS-1', 'DEV-2']}, CHANGE]}]}
    validate_safety(Rule.model_validate(raw))


@pytest.mark.parametrize('target', ['OTHER-${issue.fields.id}',
    '${_steps["0"].key}', '${_steps["99"].key}', {'path': 'issue.key', 'default': 'OPS-1'}])
def test_unbounded_or_future_targets_are_rejected(target):
    raw = spec()
    raw['actions'][0]['issue'] = target
    with pytest.raises(RuleError, match='unsafe_action_target'):
        validate_safety(Rule.model_validate(raw))


def test_explicit_cross_project_target_and_trusted_previous_step_are_allowed():
    raw = spec()
    raw['actions'][0]['issue'] = 'OPS-4'
    raw['actions'].append({'type': 'jira.comment.add', 'issue': '${_steps["0"].key}', 'text': 'Updated'})
    validate_safety(Rule.model_validate(raw))


@pytest.mark.parametrize('path', ['issue.fields.parent.key', 'issue.fields.customfield_10090',
    'issue.fields.issuelinks[0].inwardIssue.key'])
def test_scalar_event_reference_targets_are_allowed(path):
    raw = spec()
    raw['actions'][0]['issue'] = {'path': path}
    validate_safety(Rule.model_validate(raw))


def test_nested_previous_result_target_is_allowed_but_future_and_fallback_are_not():
    raw = spec()
    raw['actions'].insert(0, {'type':'jira.issue.get','issue':{'path':'issue.key'}})
    raw['actions'][1]['issue'] = {'path':'_steps["0"].fields.parent.key'}
    validate_safety(Rule.model_validate(raw))
    for path in ['_steps["1"].fields.parent.key', '_steps["01"].key', '_steps[0].key', '_steps']:
        raw['actions'][1]['issue'] = {'path':path}
        with pytest.raises(RuleError, match='unsafe_action_target'):
            validate_safety(Rule.model_validate(raw))


@pytest.mark.parametrize('origin', [None, '10003'])
def test_transition_requires_distinct_origin(origin):
    raw = spec()
    raw['actions'] = [{'type': 'jira.issue.transition', 'issue': '${issue.key}',
                       'from_status_id': origin, 'to_status_id': '10003'}]
    with pytest.raises(RuleError, match='unsafe_transition'):
        validate_safety(Rule.model_validate(raw))


def test_read_only_drafts_do_not_need_write_scope():
    raw = spec()
    raw['when'] = EXISTS
    raw['actions'] = [{'type': 'jira.issue.get', 'issue': '${issue.key}'}]
    validate_safety(Rule.model_validate(raw))


def test_created_event_requires_scope_but_not_changelog():
    raw = spec()
    raw['trigger']['event'] = 'jira:issue_created'
    raw['when'] = PROJECT
    validate_safety(Rule.model_validate(raw))


def test_test_source_is_not_a_blanket_safety_exemption():
    raw = spec()
    raw['trigger'] = {'source': 'test', 'event': 'run'}
    raw['when'] = EXISTS
    with pytest.raises(RuleError):
        validate_safety(Rule.model_validate(raw))
    raw['when'] = {'path': 'test_id', 'op': 'eq', 'value': 'explicit-request-id'}
    validate_safety(Rule.model_validate(raw))


def test_already_dispatched_pipeline_can_still_be_observed_without_new_writes():
    raw = spec()
    raw['when'] = EXISTS
    raw['actions'] = [{'type': 'bitbucket.pipeline', 'workspace': 'lab', 'repository': 'lab',
                       'branch': 'main', 'pipeline': 'observe'}]
    methods, states = [], []
    def call(method, action, **kwargs):
        methods.append(method)
        assert kwargs['body'] is None
        return httpx.Response(200, json={'uuid': '{pipeline}', 'state': {'name': 'COMPLETED', 'result': {'name': 'SUCCESSFUL'}}})
    worker = Worker.__new__(Worker)
    worker.store = SimpleNamespace(query=lambda *args: [{'spec': raw, 'payload': {}}])
    worker.settings = SimpleNamespace(check_targets=lambda rule: None)
    worker.connector = SimpleNamespace(call=call)
    worker.claim = lambda: {'id': 'run', 'event_id': 'event', 'rule_name': raw['name'],
                            'version': 1, 'step': 0, 'status': 'waiting', 'attempts': 1, 'external_uuid': '{pipeline}'}
    worker.reserve_bitbucket_write = lambda run: pytest.fail('no new writes')
    worker.finish = lambda run, state, **kwargs: states.append(state)
    assert worker.tick() and methods == ['GET'] and states == ['succeeded']


def test_broad_draft_is_saved_but_cannot_validate_or_activate(env):
    client, store, _ = env
    raw = spec()
    raw['when'] = EXISTS
    text = yaml.safe_dump(raw)
    for query in ('', '?targets=false'):
        result = client.post('/v1/rules/validate' + query, content=text)
        assert result.status_code == 422 and 'unsafe_write_scope' in result.json()['detail']
    saved = client.post('/v1/rules', content=text)
    assert saved.status_code == 201
    revision = saved.json()
    result = client.post('/v1/rules/bounded-writes/activate', json={'version': revision['version'], 'sha256': revision['sha256']})
    assert result.status_code == 422
    assert store.query("SELECT active_version FROM rules WHERE name='bounded-writes'")[0]['active_version'] is None
    assert ingest(client)['runs'] == []


def test_old_unsafe_active_rule_does_not_fan_out(env):
    client, store, _ = env
    raw = spec()
    raw['when'] = EXISTS
    client.post('/v1/rules', content=yaml.safe_dump(raw)).raise_for_status()
    store.query("UPDATE rules SET active_version=1 WHERE name='bounded-writes' RETURNING name")
    assert ingest(client)['runs'] == []
    trace = store.query('SELECT trace FROM evaluations')[0]['trace']
    assert not trace['matched'] and 'unsafe_write_scope' in trace['error']


def test_rejected_revision_does_not_replace_safe_active_version(env):
    client, store, _ = env
    raw = spec()
    safe = client.post('/v1/rules', content=yaml.safe_dump(raw)).json()
    client.post('/v1/rules/bounded-writes/activate', json={'version': safe['version'], 'sha256': safe['sha256']}).raise_for_status()
    raw['when'] = {'all': [PROJECT, {'any': [CHANGE, EXISTS]}]}
    unsafe = client.post('/v1/rules', content=yaml.safe_dump(raw)).json()
    rejected = client.post('/v1/rules/bounded-writes/activate', json={'version': unsafe['version'], 'sha256': unsafe['sha256']})
    assert rejected.status_code == 422 and 'unsafe_change_scope' in rejected.json()['detail']
    assert store.query("SELECT active_version FROM rules WHERE name='bounded-writes'")[0]['active_version'] == safe['version']


def test_queued_unsafe_revision_cannot_call_provider(env):
    client, store, cfg = env
    raw = spec()
    saved = client.post('/v1/rules', content=yaml.safe_dump(raw)).json()
    client.post('/v1/rules/bounded-writes/activate', json={'version': saved['version'], 'sha256': saved['sha256']}).raise_for_status()
    event = ingest(client)
    assert len(event['runs']) == 1
    raw['when'] = EXISTS
    store.query('UPDATE rule_versions SET spec=%s RETURNING rule_name', (Jsonb(raw),))
    with httpx.Client(transport=httpx.MockTransport(lambda _: pytest.fail('provider must not be called'))) as remote:
        worker = Worker(store, cfg, jira=Jira(cfg, remote))
        try:
            assert worker.tick()
        finally:
            worker.connector.client.close()
    detail = client.get('/v1/runs/' + event['runs'][0]['id']).json()
    assert detail['status'] == 'failed' and detail['last_error'] == 'unsafe_rule_execution_blocked'
    assert store.query('SELECT count(*) n FROM jira_write_attempts')[0]['n'] == 0
