import json
import yaml
from types import SimpleNamespace

import httpx

from app.worker import Bitbucket, Worker
from test_integration import env, activate, ingest, YAML, EVENT


def test_pipeline_receives_durable_prior_results_not_webhook_injection(env):
    client, store, cfg = env
    text = '''apiVersion: automation/v1
kind: Rule
name: pipeline-step-context
trigger: {source: jira, event: 'jira:issue_updated'}
when: {path: issue.key, op: exists}
actions:
  - type: bitbucket.pipeline
    workspace: jvidelaolmos
    repository: jira-event-layer-lab
    branch: main
    pipeline: setup-new-repository
  - type: bitbucket.pipeline
    workspace: jvidelaolmos
    repository: jira-event-layer-lab
    branch: main
    pipeline: setup-new-repository
    variables:
      PRIOR_STATE: '${_steps["0"].state}'
      PRIOR_RESULT: '${_steps["0"].result}'
'''
    spec = yaml.safe_load(text)
    spec['when'] = yaml.safe_load(YAML)['when']
    activate(client, yaml.safe_dump(spec))
    event = ingest(client, payload={**EVENT, '_steps': {'0': {'state': 'forged', 'result': 'forged'}}})
    writes = []

    def provider(request):
        if request.method == 'POST':
            writes.append(json.loads(request.content))
            return httpx.Response(201, json={'uuid': '{pipeline-context}'})
        return httpx.Response(200, json={'uuid': '{pipeline-context}', 'state': {'name': 'COMPLETED', 'result': {'name': 'SUCCESSFUL'}}})

    with httpx.Client(transport=httpx.MockTransport(provider)) as remote:
        worker = Worker(store, cfg, connector=Bitbucket(cfg, remote))
        try:
            for _ in range(4):
                store.query('UPDATE runs SET next_at=now() RETURNING id')
                assert worker.tick()
        finally:
            worker.jira.client.close()
    assert {v['key']: v['value'] for v in writes[1]['variables']} == {'PRIOR_STATE': 'COMPLETED', 'PRIOR_RESULT': 'SUCCESSFUL'}
    run = client.get('/v1/runs/' + event['runs'][0]['id']).json()
    assert run['status'] == 'succeeded'


def test_step_context_is_scoped_and_excludes_future_failed_or_untrusted_results():
    class Store:
        def query(self, sql, args):
            assert args == ('run-1', 2)
            assert 'step<%s' in sql and "status IN ('pending','succeeded')" in sql
            return [{'step': 0, 'detail': {'result': {'key': 'KAN-13'}}}, {'step': 1, 'detail': {'result': None}}]
    worker = Worker.__new__(Worker)
    worker.store = Store()
    original = {'_steps': {'0': {'key': 'forged'}}, 'issue': {'key': 'KAN-10'}}
    context = worker.execution_context({'id': 'run-1', 'step': 2}, original)
    assert context['_steps'] == {'0': {'key': 'KAN-13'}}
    assert original['_steps']['0']['key'] == 'forged'


def test_pipeline_dispatch_uses_the_same_trusted_context_without_infrastructure():
    spec = {'apiVersion': 'automation/v1', 'kind': 'Rule', 'name': 'step-context-unit',
            'trigger': {'source': 'jira', 'event': 'jira:issue_updated'},
            'when': {'path': 'issue.key', 'op': 'exists'},
            'actions': [{'type': 'jira.issue.get', 'issue': '${issue.key}'},
                        {'type': 'bitbucket.pipeline', 'workspace': 'jvidelaolmos',
                         'repository': 'jira-event-layer-lab', 'branch': 'main',
                         'pipeline': 'setup-new-repository',
                         'variables': {'CREATED_KEY': '${_steps["0"].key}'}}]}
    spec['when'] = yaml.safe_load(YAML)['when']
    class Store:
        def query(self, sql, args):
            if 'SELECT v.spec' in sql:
                return [{'spec': spec, 'payload': {'issue': {'key': 'KAN-10'}, '_steps': {'0': {'key': 'FORGED-1'}}}}]
            assert args == ('run-1', 1)
            return [{'step': 0, 'detail': {'result': {'key': 'KAN-13'}}}]
        def exclude_event(self, event_id):
            return None
    bodies, states = [], []
    def dispatch(method, action, **kwargs):
        bodies.append(kwargs['body'])
        return httpx.Response(201, json={'uuid': '{pipeline-context}'})
    worker = Worker.__new__(Worker)
    worker.store = Store()
    worker.settings = SimpleNamespace(check_targets=lambda rule: None)
    worker.claim = lambda: {'id': 'run-1', 'event_id': 'event-1', 'rule_name': 'step-context-unit', 'version': 1, 'step': 1, 'status': 'pending', 'attempts': 1}
    worker.connector = SimpleNamespace(call=dispatch)
    worker.reserve_bitbucket_write = lambda run: None
    worker.finish = lambda run, state, **kwargs: states.append(state)
    assert worker.tick()
    assert bodies[0]['variables'] == [{'key': 'CREATED_KEY', 'value': 'KAN-13', 'secured': False}]
    assert states == ['waiting']
