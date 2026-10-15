import copy
import json
from uuid import UUID

import httpx
import pytest
import yaml

from app.config import Settings
from app.dsl import Rule, RuleError, parse_rule
from app.jira import AmbiguousWrite, ExecutionIdentity, Jira, PreflightRetry, WriteRateLimited, append_description

IDENTITY = ExecutionIdentity(rule_name="crear-repositorio", rule_version=2, rule_sha256="a" * 64,
                             run_id=UUID("11111111-1111-4111-8111-111111111111"), step=0)


@pytest.mark.parametrize('original',[None,'', 'Existing text',
    {'type':'doc','version':1,'content':[{'type':'paragraph','content':[{'type':'text','text':'Rich','marks':[{'type':'strong'}]}]}]}])
def test_append_description_keeps_existing_content_and_supports_empty(original):
    before=copy.deepcopy(original)
    result=append_description(original,'Se asigno!')
    assert result['content'][-1]=={'type':'paragraph','content':[{'type':'text','text':'Se asigno!'}]}
    if isinstance(original,dict): assert result['content'][:-1]==original['content']
    if original=='Existing text': assert result['content'][0]['content'][0]['text']==original
    assert original==before


@pytest.mark.parametrize('original',[[],42,{'type':'doc','version':2,'content':[]},{'type':'doc','version':1,'content':{}}])
def test_append_description_rejects_unrecognized_document(original):
    with pytest.raises(RuleError,match='jira_description_invalid'):
        append_description(original,'Se asigno!')


def test_append_action_fetches_current_description_and_does_not_use_stale_webhook_text():
    original={'type':'doc','version':1,'content':[{'type':'paragraph','content':[{'type':'text','text':'Current rich text','marks':[{'type':'strong'}]}]}]}
    writes=[];reservations=[]
    def provider(request):
        if request.method=='GET':
            current=issue();current['fields']['description']=copy.deepcopy(original)
            return httpx.Response(200,json=current)
        writes.append(json.loads(request.content));return httpx.Response(204)
    adapter=Jira(config(),httpx.Client(transport=httpx.MockTransport(provider)))
    action=rule_for([{'type':'jira.issue.edit','issue':{'path':'issue.key'},'append_description':'Se asigno!'}]).actions[0]
    result=adapter.execute(action,{'issue':{'key':'DEMO-4','fields':{'description':'STALE'}}},IDENTITY,lambda:reservations.append(True))
    assert result=={'key':'DEMO-4','updated_fields':['description']}
    assert writes==[{'fields':{'description':append_description(original,'Se asigno!')}}] and reservations==[True]


def test_description_replacement_and_append_are_mutually_exclusive():
    with pytest.raises(RuleError,match='not both'):
        rule_for([{'type':'jira.issue.edit','issue':'DEMO-4','append_description':'Se asigno!',
                   'fields':{'description':'Replacement'}}])


def test_optional_append_does_not_change_serialization_of_existing_edit_rules():
    rule=rule_for([{'type':'jira.issue.edit','issue':'DEMO-4','fields':{'summary':'Existing'}}])
    assert 'append_description' not in rule.model_dump()['actions'][0]


def test_missing_current_description_does_not_silently_replace_unknown_content():
    def provider(request):
        assert request.method=='GET'
        current=issue();del current['fields']['description']
        return httpx.Response(200,json=current)
    adapter=Jira(config(),httpx.Client(transport=httpx.MockTransport(provider)))
    action=rule_for([{'type':'jira.issue.edit','issue':'DEMO-4','append_description':'Se asigno!'}]).actions[0]
    with pytest.raises(RuleError,match='jira_description_missing'):
        adapter.execute(action,{},IDENTITY,lambda:pytest.fail('No write reservation'))


@pytest.mark.parametrize('suffix',['', '   ', 'x'*8001])
def test_invalid_append_text_has_no_mutation_or_reservation(suffix):
    calls=[]
    def provider(request):
        calls.append(request.method)
        assert request.method=='GET'
        return httpx.Response(200,json=issue())
    adapter=Jira(config(),httpx.Client(transport=httpx.MockTransport(provider)))
    action=rule_for([{'type':'jira.issue.edit','issue':'DEMO-4','append_description':{'path':'suffix'}}]).actions[0]
    with pytest.raises(RuleError): adapter.execute(action,{'suffix':suffix},IDENTITY,lambda:pytest.fail('No reservation'))
    assert calls==['GET']


def config():
    return Settings("unused", "a"*40, "b"*40, jira_token="test-only",
                    jira_cloud_id="11111111-1111-4111-8111-111111111111")


def rule_for(actions):
    return parse_rule(yaml.safe_dump({"apiVersion": "automation/v1", "kind": "Rule", "name": "jira-lab",
        "trigger": {"source": "jira", "event": "jira:issue_updated"},
        "when": {"path": "issue.key", "op": "exists"}, "actions": actions}))[0]


def issue(key="DEMO-4", status="10001", labels=None):
    return {"key": key, "fields": {"project": {"key": key.split("-")[0]}, "status": {"id": status},
        "labels": [] if labels is None else labels, "summary": "Source",
        "description": {"type": "doc", "version": 1, "content": []}}}


ACTIONS = [
    {"type": "jira.issue.create", "project": "DEMO", "issue_type_id": "10003", "fields": {"summary": {"path": "issue.fields.summary"}}},
    {"type": "jira.issue.clone", "project": "DEMO", "issue_type_id": "10003", "source": {"value": "DEMO-4"}},
    {"type": "jira.issue.edit", "issue": {"value": "DEMO-4"}, "fields": {"summary": {"value": "New"}}},
    {"type": "jira.issue.transition", "issue": {"value": "DEMO-4"}, "from_status_id": "10001", "to_status_id": "10003"},
    {"type": "jira.comment.add", "issue": {"value": "DEMO-4"}, "text": {"value": "Comment"}},
    {"type": "jira.issue.link", "inward": {"value": "DEMO-4"}, "outward": {"value": "DEMO-5"}, "link_type_id": "10000"},
    {"type": "jira.issue.get", "issue": {"value": "DEMO-4"}, "require": {"path": "fields.status.id", "op": "eq", "value": "10001"}},
]


@pytest.mark.parametrize("spec", ACTIONS)
def test_all_actions_roundtrip_and_dispatch(spec):
    rule = rule_for([spec])
    assert Rule.model_validate(rule.model_dump()) == rule
    calls, reservations = [], []
    def handler(request):
        calls.append(request)
        assert request.url.host == "api.atlassian.com"
        assert request.headers["Authorization"] == "Bearer test-only"
        if request.method == "GET":
            if request.url.path.endswith("/transitions"):
                return httpx.Response(200, json={"transitions": [{"id": "31", "to": {"id": "10003"}}]})
            key = request.url.path.split("/")[-1]
            return httpx.Response(200, json=issue(key))
        if request.url.path.endswith("/transitions") or request.method == "PUT":
            return httpx.Response(204)
        return httpx.Response(201, json={"key": "DEMO-6", "id": "77"})
    adapter = Jira(config(), httpx.Client(transport=httpx.MockTransport(handler)))
    result = adapter.execute(rule.actions[0], {"issue": issue()}, IDENTITY, lambda: reservations.append(True))
    assert result
    mutations = [r for r in calls if r.method != "GET"]
    assert len(mutations) == len(reservations) == (0 if spec["type"] == "jira.issue.get" else 1)
    if spec["type"] == "jira.comment.add":
        # Jira rejects primitive JSON values in comment entity properties.
        body = json.loads(mutations[0].content)
        assert body["properties"] == [{"key": "automation.operation", "value": IDENTITY.provenance()}]
    if spec["type"] in {"jira.issue.create", "jira.issue.clone"}:
        body = json.loads(mutations[0].content)
        assert body["fields"]["labels"] == [IDENTITY.flow_label]
        assert body["fields"]["summary"] == "Source"
        assert body["properties"][0]["value"] == IDENTITY.provenance()


def test_jira_field_binding_and_current_issue_guard_accept_new_paths():
    spec = {"type": "jira.issue.create", "project": "DEMO", "issue_type_id": "10003",
            "fields": {"summary": {"path": 'issue.fields["summary.source"][0].text'}}}
    calls = []
    def create(request):
        calls.append(json.loads(request.content))
        return httpx.Response(201, json={"key": "DEMO-6", "id": "77"})
    payload = {"issue": {"fields": {"summary.source": [{"text": "Copied safely"}]}}}
    adapter = Jira(config(), httpx.Client(transport=httpx.MockTransport(create)))
    adapter.execute(rule_for([spec]).actions[0], payload, IDENTITY, lambda: None)
    assert calls[0]["fields"]["summary"] == "Copied safely"
    guard = {"type": "jira.issue.get", "issue": {"path": "issue.key"},
             "require": {"path": "fields.status.id", "op": "eq", "value": "10001"}}
    adapter = Jira(config(), httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=issue()))))
    # The webhook says done, but require must read the fetched issue's current state.
    result = adapter.execute(rule_for([guard]).actions[0], {"issue": issue(status="10003")}, IDENTITY, lambda: pytest.fail("read only"))
    assert result["guard_matched"] is True


@pytest.mark.parametrize("key", ["DEMO-1/../../x", "https://evil.test", "DEMO-01", "DEMO-1?x=y"])
def test_reject_target_before_any_request(key):
    spec = {"type": "jira.comment.add", "issue": {"value": key}, "text": {"value": "hello"}}
    adapter = Jira(config(), httpx.Client(transport=httpx.MockTransport(lambda _: pytest.fail("network forbidden"))))
    with pytest.raises(RuleError):
        adapter.execute(rule_for([spec]).actions[0], {}, IDENTITY, lambda: pytest.fail("write forbidden"))


@pytest.mark.parametrize('value', [None, ['DEMO-4','DEMO-5'], {'key':'DEMO-4'}, 10004, '', 'project=DEMO', 'DEMO-4,DEMO-5'])
def test_dynamic_target_rejects_non_key_without_http_or_budget(value):
    spec = {'type':'jira.issue.edit','issue':{'path':'issue.fields.parent.key'},
            'fields':{'summary':{'value':'Updated'}}}
    adapter = Jira(config(), httpx.Client(transport=httpx.MockTransport(lambda _: pytest.fail('No HTTP allowed'))))
    with pytest.raises(RuleError):
        adapter.execute(rule_for([spec]).actions[0], {'issue':{'fields':{'parent':{'key':value}}}},
                        IDENTITY, lambda: pytest.fail('No budget reservation allowed'))


def test_parent_reference_edits_only_parent_not_trigger_issue():
    calls, budget = [], []
    def provider(request):
        calls.append((request.method,request.url.path))
        assert '/DEMO-4' in request.url.path
        return httpx.Response(200,json=issue('DEMO-4')) if request.method == 'GET' else httpx.Response(204)
    spec = {'type':'jira.issue.edit','issue':{'path':'issue.fields.parent.key'},
            'fields':{'summary':{'value':'Updated parent'}}}
    adapter = Jira(config(),httpx.Client(transport=httpx.MockTransport(provider)))
    result = adapter.execute(rule_for([spec]).actions[0],{'issue':{'key':'DEMO-9','fields':{'parent':{'key':'DEMO-4'}}}},
                             IDENTITY,lambda:budget.append(True))
    assert result['key']=='DEMO-4' and len(calls)==2 and budget==[True]


@pytest.mark.parametrize("field", ["project", "status", "security", "reporter", "issuetype", "unknown"])
def test_forbidden_fields_rejected_at_parse(field):
    action = copy.deepcopy(ACTIONS[2])
    action["fields"] = {field: {"value": "x"}}
    with pytest.raises(RuleError):
        rule_for([action])


def test_projects_and_customfields_do_not_require_server_allowlists():
    rule = rule_for([ACTIONS[0]])
    Settings("", "", "").check_targets(rule)
    action = copy.deepcopy(ACTIONS[2])
    action["fields"] = {"customfield_999": {"value": "x"}}
    config().check_targets(rule_for([action]))


@pytest.mark.parametrize('project', ['DEMO', 'OTHER'])
def test_customfield_write_in_any_jira_authorized_project(project):
    key = project + '-4'
    writes = []
    def handle(request):
        if request.method == 'GET':
            assert request.url.params['fields'] == '*all'
            return httpx.Response(200, json=issue(key))
        writes.append(json.loads(request.content))
        return httpx.Response(204)
    action = {'type': 'jira.issue.edit', 'issue': {'value': key},
              'fields': {'customfield_999': {'value': 'business-value'}}}
    adapter = Jira(config(), httpx.Client(transport=httpx.MockTransport(handle)))
    adapter.execute(rule_for([action]).actions[0], {}, IDENTITY, lambda: None)
    assert writes == [{'fields': {'customfield_999': 'business-value'}}]


def test_actual_project_must_still_match_issue_key():
    data = issue()
    data['fields']['project']['key'] = 'OTHER'
    adapter = Jira(config(), httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=data))))
    with pytest.raises(RuleError, match='scope_mismatch'):
        adapter.execute(rule_for([ACTIONS[2]]).actions[0], {}, IDENTITY, lambda: pytest.fail('no write'))


def test_ticket_without_labels_can_be_read():
    adapter = Jira(config(), httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=issue(labels=[])))))
    assert adapter.execute(rule_for([ACTIONS[6]]).actions[0], {}, IDENTITY, lambda: pytest.fail("read does not write"))["key"] == "DEMO-4"


@pytest.mark.parametrize("status", [401, 403, 404])
def test_permanent_read_error_never_writes(status):
    adapter = Jira(config(), httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(status))))
    with pytest.raises(RuleError, match=f"preflight_http_{status}"):
        adapter.execute(rule_for([ACTIONS[2]]).actions[0], {}, IDENTITY, lambda: pytest.fail("write forbidden"))


@pytest.mark.parametrize("status", [429, 500, 503])
def test_read_failures_retry_before_write(status):
    adapter = Jira(config(), httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(status, headers={"Retry-After": "2"}))))
    with pytest.raises(PreflightRetry):
        adapter.execute(rule_for([ACTIONS[2]]).actions[0], {}, IDENTITY, lambda: pytest.fail("write forbidden"))


@pytest.mark.parametrize("mode", ["timeout", "500", "408", "409", "malformed"])
def test_create_ambiguous_never_retried(mode):
    requests = []
    def handler(request):
        requests.append(request)
        if mode == "timeout":
            raise httpx.ReadTimeout("secret content must never be logged")
        return httpx.Response(201, json={}) if mode == "malformed" else httpx.Response(int(mode))
    adapter = Jira(config(), httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(AmbiguousWrite):
        adapter.execute(rule_for([ACTIONS[0]]).actions[0], {"issue": issue()}, IDENTITY, lambda: None)
    assert len(requests) == 1


def test_write_rate_limit_distinguished_from_ambiguous_write():
    adapter = Jira(config(), httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(429, headers={"Retry-After": "4"}))))
    with pytest.raises(WriteRateLimited) as exc:
        adapter.execute(rule_for([ACTIONS[0]]).actions[0], {"issue": issue()}, IDENTITY, lambda: None)
    assert exc.value.delay == 4


@pytest.mark.parametrize("state,expected", [("10003", "no_change"), ("10000", "jira_source_status_changed")])
def test_transition_checks_current_status(state, expected):
    adapter = Jira(config(), httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=issue(status=state)))))
    action = rule_for([ACTIONS[3]]).actions[0]
    if expected == "no_change":
        assert adapter.execute(action, {}, IDENTITY, lambda: pytest.fail("write forbidden"))["no_change"]
    else:
        with pytest.raises(RuleError, match=expected):
            adapter.execute(action, {}, IDENTITY, lambda: pytest.fail("write forbidden"))


def test_false_guard_stops_before_any_write():
    adapter = Jira(config(), httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=issue(status="10000")))))
    assert adapter.execute(rule_for([ACTIONS[6]]).actions[0], {}, IDENTITY, lambda: pytest.fail("write forbidden"))["stop"]


def test_plain_description_becomes_adf_and_missing_binding_rejected():
    action = copy.deepcopy(ACTIONS[0])
    action["fields"]["description"] = {"value": "literal $(not-executed)"}
    bodies = []
    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(201, json={"key": "DEMO-6"})
    adapter = Jira(config(), httpx.Client(transport=httpx.MockTransport(handler)))
    adapter.execute(rule_for([action]).actions[0], {"issue": issue()}, IDENTITY, lambda: None)
    assert bodies[0]["fields"]["description"]["type"] == "doc"
    with pytest.raises(RuleError, match="binding_missing"):
        adapter.execute(rule_for([action]).actions[0], {}, IDENTITY, lambda: pytest.fail("write forbidden"))


@pytest.mark.parametrize("field,value", [
    ("assignee", {"accountId": "test-account"}), ("priority", {"id": "3"}),
    ("labels", ["automation-lab-managed", "triaged"]), ("duedate", "2026-10-20"),
])
def test_edit_business_fields(field, value):
    action = copy.deepcopy(ACTIONS[2])
    action["fields"] = {field: {"value": value}}
    bodies = []
    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=issue())
        bodies.append(json.loads(request.content))
        return httpx.Response(204)
    adapter = Jira(config(), httpx.Client(transport=httpx.MockTransport(handler)))
    adapter.execute(rule_for([action]).actions[0], {}, IDENTITY, lambda: None)
    assert bodies == [{"fields": {field: value}}]


def test_create_subtask_uses_verified_parent():
    action = copy.deepcopy(ACTIONS[0])
    action["parent"] = {"value": "DEMO-4"}
    action["issue_type_id"] = "10004"
    bodies = []
    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=issue())
        bodies.append(json.loads(request.content))
        return httpx.Response(201, json={"key": "DEMO-6"})
    adapter = Jira(config(), httpx.Client(transport=httpx.MockTransport(handler)))
    adapter.execute(rule_for([action]).actions[0], {"issue": issue()}, IDENTITY, lambda: None)
    assert bodies[0]["fields"]["parent"] == {"key": "DEMO-4"}
    assert bodies[0]["fields"]["issuetype"] == {"id": "10004"}


@pytest.mark.parametrize("transitions", [[], [{"id": "1", "to": {"id": "10003"}}, {"id": "2", "to": {"id": "10003"}}]])
def test_transition_must_resolve_unambiguously(transitions):
    def handler(request):
        return httpx.Response(200, json={"transitions": transitions} if request.url.path.endswith("/transitions") else issue())
    adapter = Jira(config(), httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(RuleError, match="missing_or_ambiguous"):
        adapter.execute(rule_for([ACTIONS[3]]).actions[0], {}, IDENTITY, lambda: pytest.fail("write forbidden"))


def test_update_can_remove_old_managed_label():
    action = copy.deepcopy(ACTIONS[2])
    action["fields"] = {"labels": {"value": ["other"]}}
    writes = []
    def handle(request):
        if request.method == "GET":
            return httpx.Response(200, json=issue(labels=["automation-managed"]))
        writes.append(json.loads(request.content))
        return httpx.Response(204)
    adapter = Jira(config(), httpx.Client(transport=httpx.MockTransport(handle)))
    adapter.execute(rule_for([action]).actions[0], {}, IDENTITY, lambda: None)
    assert writes[0]["fields"]["labels"] == ["other"]


def test_lifecycle_example_parses():
    from pathlib import Path
    rule, _ = parse_rule(Path("examples/jira-create-from-source.yaml").read_text())
    settings = config()
    settings.check_targets(rule)
    assert len(rule.actions) == 10
