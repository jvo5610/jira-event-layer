from app.catalog import discover
from app.paths import check_path
from test_integration import env


def test_discovery_handles_all_types_and_executable_paths():
    fields = discover([{"issue": {"fields": {"status": {"name": "Done"}, "a.b": [False, None, 3]}}}])
    by_path = {f["path"]: f for f in fields}
    assert by_path["issue.fields.status.name"]["values"] == ["Done"]
    assert by_path['issue.fields["a.b"][0]']["types"] == ["boolean"]
    for path in by_path:
        check_path(path)


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
    assert cfg.admin_token not in str(result)
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


def test_syntax_import_does_not_require_destinations_but_save_does(env):
    client, _, cfg = env
    from test_integration import YAML
    cfg.allowed_repositories = ()
    assert client.post('/v1/rules/validate', content=YAML).status_code == 422
    result = client.post('/v1/rules/validate?targets=false', content=YAML)
    assert result.status_code == 200 and result.json()['targets_checked'] is False
    assert client.post('/v1/rules', content=YAML).status_code == 422
    assert client.post('/v1/rules/validate?targets=false', content='when: &x {not: *x}').status_code == 422
