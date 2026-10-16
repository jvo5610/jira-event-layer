from app.catalog import discover
from app.paths import check_path
from test_integration import env


def test_catalog_does_not_claim_worker_jira_credentials_are_missing(env):
    client, _, settings = env
    settings.jira_token = ""
    settings.jira_cloud_id = ""
    assert client.get('/v1/catalog').json()['connectors']['jira']['configured'] is None
    settings.jira_token = "provider-test-token"
    assert client.get('/v1/catalog').json()['connectors']['jira']['configured'] is None
    settings.jira_cloud_id = '00000000-0000-0000-0000-000000000001'
    assert client.get('/v1/catalog').json()['connectors']['jira']['configured'] is True
    # Declared configuration is not a live provider permission/health check.
    settings.bitbucket_token = ""
    assert client.get('/v1/catalog').json()['connectors']['bitbucket']['configured'] is None
    settings.bitbucket_token = "bitbucket-test-token"
    settings.bitbucket_email = ""
    assert client.get('/v1/catalog').json()['connectors']['bitbucket']['configured'] is None
    settings.bitbucket_email = "service@example.com"
    assert client.get('/v1/catalog').json()['connectors']['bitbucket'] == {'configured': True}


def context_fixture(client, store, suffix=''):
    import yaml
    from test_integration import activate, ingest
    spec={'apiVersion':'automation/v1','kind':'Rule','name':'context-example',
          'trigger':{'source':'jira','event':'jira:issue_updated'},
          'when':{'path':'issue.key','op':'exists'},'description':suffix,
          'actions':[{'type':'jira.issue.get','issue':{'path':'issue.key'}},
                     {'type':'jira.issue.get','issue':{'path':'issue.key'}}]}
    saved=activate(client,yaml.safe_dump(spec))
    event=ingest(client,key='ctx-'+suffix,payload={'issue':{'key':'KAN-1'},'_steps':{'0':{'forged':'not trusted'}}})
    run=event['runs'][0]['id']
    return saved,event,run


def test_editor_context_returns_only_durable_previous_results_with_safe_nested_examples(env):
    from psycopg.types.json import Jsonb
    client,store,_=env
    saved,event,run=context_fixture(client,store)
    for step,status,result in [(0,'pending',{'key':'KAN-1','fields':{'parent':{'key':'OPS-2'},'emailAddress':'private@example.com','token':'hidden-value','flag':False}}),
                               (1,'succeeded',{'key':'FUTURE-9'}),
                               (0,'failed',{'key':'FAILED-8'})]:
        store.query('INSERT INTO run_log(run_id,step,status,detail) VALUES(%s,%s,%s,%s) RETURNING id',
                    (run,step,status,Jsonb({'result':result})))
    path=f'/v1/rules/context-example/versions/{saved["version"]}/context'
    response=client.get(path,params={'before_step':1});assert response.status_code==200
    data=response.json(); fields={f['path']:f for f in data['fields']}
    assert data['run_id']==run and data['sample'] is True
    assert fields['_steps["0"].fields.parent.key']['example']=='OPS-2'
    assert fields['_steps["0"].fields.flag']['example'] is False
    assert fields['_steps["0"].key']['example']=='KAN-1'
    assert all(f['step']==0 for f in data['fields'])
    assert not any(f['path'].startswith('_steps') for f in data['event_fields'])
    for hidden in ['private@example.com','hidden-value','FUTURE-9','FAILED-8','not trusted']:
        assert hidden not in str(data)
    assert client.get(path,params={'before_step':3}).status_code==422
    assert client.get(path,params={'before_step':1},headers={'Authorization':''}).status_code==401


def test_editor_context_does_not_cross_runs_versions_or_redacted_events(env):
    from psycopg.types.json import Jsonb
    from test_integration import ingest
    client,store,_=env
    saved,event,run=context_fixture(client,store)
    store.query('INSERT INTO run_log(run_id,step,status,detail) VALUES(%s,0,\'pending\',%s) RETURNING id',
                (run,Jsonb({'result':{'key':'KAN-1'}})))
    # A newer run without durable outputs must not overwrite an earlier run's context.
    ingest(client,key='ctx-newer',payload={'issue':{'key':'KAN-99'}})
    path=f'/v1/rules/context-example/versions/{saved["version"]}/context?before_step=1'
    data=client.get(path).json()
    assert data['run_id']==run
    assert next(f for f in data['event_fields'] if f['path']=='issue.key')['example']=='KAN-1'
    newer,_,_=context_fixture(client,store,'v2')
    assert client.get(f'/v1/rules/context-example/versions/{newer["version"]}/context?before_step=1').json()['sample'] is False
    store.query('UPDATE events SET payload_redacted_at=now() WHERE id=%s RETURNING id',(event['event_id'],))
    assert client.get(path).json()['sample'] is False
    assert client.get('/v1/rules/context-example/versions/999/context?before_step=1').status_code==404


def test_discovery_handles_all_types_and_executable_paths():
    fields = discover([{"issue": {"fields": {"status": {"name": "Done"}, "a.b": [False, None, 3]}}}])
    by_path = {f["path"]: f for f in fields}
    assert by_path["issue.fields.status.name"]["values"] == ["Done"]
    assert by_path['issue.fields["a.b"][0]']["types"] == ["boolean"]
    for path in by_path:
        check_path(path)


def test_context_discovery_keeps_shallow_values_when_an_earlier_branch_is_large():
    result={'key':'KAN-1','fields':{'comment':{'comments':[{'body':{f'long{i}':'text' for i in range(300)}}]},
                                'summary':'Actual summary','parent':{'key':'OPS-2'},'token':'must-stay-hidden'}}
    fields={f['path']:f for f in discover([{'_steps':{'0':result}}],max_fields=100,breadth_first=True)}
    assert fields['_steps["0"].fields.summary']['example']=='Actual summary'
    assert fields['_steps["0"].fields.parent.key']['example']=='OPS-2'
    assert fields['_steps["0"].fields.token']['example_state']=='protected'
    assert 'must-stay-hidden' not in str(fields) and len(fields)<=100


def test_discovery_keeps_comparison_values_restricted_and_protects_personal_previews():
    fields = discover([{"issue": {"fields": {"summary": "Review the request", "assignee": {"emailAddress": "private@example.com"}}}}])
    assert all(not f["values"] for f in fields)
    assert "private@example.com" not in str(fields)
    by_path = {f['path']: f for f in fields}
    assert by_path['issue.fields.summary']['example'] == 'Review the request'
    assert by_path['issue.fields.assignee.emailAddress']['example_state'] == 'protected'


def test_discovery_prefers_populated_examples_and_keeps_zero_and_false():
    rows = [{'text': None, 'number': None, 'flag': None}, {'text': '', 'number': 0, 'flag': False},
            {'text': '   '}, {'text': 'Available'}, {'text': 'Another'}]
    fields = {f['path']: f for f in discover(rows)}
    assert fields['text']['example'] == 'Available'
    assert fields['text']['types'] == ['null', 'string']
    assert fields['number']['example'] == 0
    assert fields['flag']['example'] is False
    assert all(f['example_state'] == 'present' for f in fields.values())


def test_discovery_empty_sensitive_and_bounded_structural_examples():
    payload = {'missing': None, 'empty': '', 'blank': '  ', 'emptyList': [], 'emptyObject': {},
               'text': 'x' * 2000, 'access_token': 'hidden-credential',
               'credentials': {'nested': 'also-hidden'}, 'note': 'Authorization: Bearer secret-value',
               'object': {'secret': 'nested-private'}, 'list': [{'password': 'nested-password'}]}
    fields = {f['path']: f for f in discover([payload])}
    for key in ['missing', 'empty', 'blank', 'emptyList', 'emptyObject']:
        assert fields[key]['example_state'] == 'empty' and 'example' not in fields[key]
    assert fields['text']['example'] == 'x' * 120 and fields['text']['example_truncated'] is True
    for key in ['access_token', 'credentials', 'credentials.nested', 'note']:
        assert fields[key]['example_state'] == 'protected' and 'example' not in fields[key]
    assert fields['object']['example_kind'] == 'object' and fields['object']['example_count'] == 1
    assert fields['list']['example_kind'] == 'array' and fields['list']['example_count'] == 1
    for secret in ['hidden-credential', 'also-hidden', 'secret-value', 'nested-private', 'nested-password']:
        assert secret not in str(fields)


def test_discovery_gives_each_of_100_events_a_budget_and_updates_existing_paths_at_field_cap():
    rows = [{'known': None, **{f'extra{i}': i for i in range(3000)}} for _ in range(99)]
    rows += [{'known': 'Found in event 100'}, {'must_not_be_read': True}]
    fields = {f['path']: f for f in discover(rows, max_fields=12, budget=40)}
    assert len(fields) == 12
    assert fields['known']['example'] == 'Found in event 100'
    assert 'must_not_be_read' not in fields


def test_discovery_bounds_and_conflicting_types():
    assert len(discover([{str(i): i for i in range(2000)}], max_fields=12)) == 12
    assert discover([{"a": 1}, {"a": "one"}])[0]["types"] == ["number", "string"]
    assert len(discover([{"list": list(range(1000))}])) == 4
    assert len(discover([{"a": {"b": 1}}], budget=2)) == 1


def test_discovery_suggests_bounded_labels_without_free_text_values():
    result=discover([{'issue':{'fields':{'labels':['backend','release','private@example.com'],'description':'not a suggestion'}}}])
    fields={item['path']:item for item in result}
    assert fields['issue.fields.labels']['values']==['backend','release']
    assert not fields['issue.fields.description']['values']


def test_catalog_auth_and_redacted_exclusion(env):
    client, store, cfg = env
    assert client.get('/v1/catalog', headers={'Authorization': ''}).status_code == 401
    from test_integration import ingest
    event = ingest(client)
    result = client.get('/v1/catalog').json()
    assert len([d for d in result['schema']['$defs'].values() if d.get('properties', {}).get('type', {}).get('const')]) == 8
    assert cfg.admin_bootstrap_token not in str(result)
    response = client.get('/v1/catalog/fields', params={'source': 'jira', 'event_type': 'jira:issue_updated'})
    assert response.status_code == 200
    assert response.json()['sample_count'] == 1
    assert response.json()['sample_limit'] == 100
    store.query("UPDATE events SET payload_redacted_at=now() WHERE id=%s RETURNING id", (event['event_id'],))
    assert client.get('/v1/catalog/fields', params={'source':'jira','event_type':'jira:issue_updated'}).json()['sample_count'] == 0


def test_catalog_uses_last_100_matching_events_and_a_populated_example(env):
    client, _, _ = env
    from test_integration import ingest
    for i in range(105):
        payload = {'issue': {'fields': {'status': {'name': 'Done' if i == 104 else None}}}}
        if i == 0:
            payload['old_only'] = 'excluded from latest 100'
        ingest(client, key=f'discovery-{i}', payload=payload)
    data = client.get('/v1/catalog/fields', params={'source':'jira','event_type':'jira:issue_updated'}).json()
    assert data['sample_count'] == 100 and data['sample_limit'] == 100
    fields = {f['path']: f for f in data['fields']}
    assert 'old_only' not in fields
    assert fields['issue.fields.status.name']['example'] == 'Done'


def test_keyset_events_and_runs(env):
    client, _, _ = env
    from test_integration import ingest, activate
    activate(client)
    ids = [ingest(client, key=f'page-{i}')['event_id'] for i in range(4)]
    first = client.get('/v1/events?limit=2').json()
    second = client.get('/v1/events', params={'limit':2, 'before':first[-1]['id']}).json()
    assert {r['id'] for r in first + second} == set(ids)
    assert client.get('/v1/events?source=test').json() == []
    assert client.get('/v1/events?before=invalid').status_code == 422
    first_runs = client.get('/v1/runs?limit=2').json()
    second_runs = client.get('/v1/runs', params={'limit':2,'before':first_runs[-1]['id']}).json()
    assert len({r['id'] for r in first_runs + second_runs}) == 4


def test_validate_and_save_do_not_require_server_destination_configuration(env):
    client, _, cfg = env
    from test_integration import YAML
    assert client.post('/v1/rules/validate', content=YAML).status_code == 200
    result = client.post('/v1/rules/validate?targets=false', content=YAML)
    assert result.status_code == 200 and result.json()['targets_checked'] is False
    assert client.post('/v1/rules', content=YAML).status_code == 201
    assert client.post('/v1/rules/validate?targets=false', content='when: &x {not: *x}').status_code == 422
