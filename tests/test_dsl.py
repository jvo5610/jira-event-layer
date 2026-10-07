from pathlib import Path

import pytest
import yaml

from app.dsl import RuleError, evaluate, lookup, match, parse_rule, render_action, validate_filter

EXAMPLE = Path("examples/repository-request.yaml").read_text()


def template_rule(value):
    raw = yaml.safe_load(EXAMPLE)
    raw["actions"][0]["variables"] = {"INPUT": value}
    return parse_rule(yaml.safe_dump(raw))[0]


@pytest.mark.parametrize("text,payload,expected", [
    ("${issue.key}", {"issue": {"key": "KAN-1"}}, "KAN-1"),
    ('${issue.fields["some.value"][0].name}', {"issue": {"fields": {"some.value": [{"name": "repo"}]}}}, "repo"),
    ('${issue.fields["a}b"]}', {"issue": {"fields": {"a}b": "literal"}}}, "literal"),
    ("Ticket ${issue.key}: ${ok}", {"issue": {"key": "KAN-1"}, "ok": True}, "Ticket KAN-1: true"),
    ("$${issue.key}", {}, "${issue.key}"),
    ("${value}", {"value": "${missing}"}, "${missing}"),
    ({"value": "${missing}"}, {}, "${missing}"),
    ("python", {}, "python"),
])
def test_short_bindings(text, payload, expected):
    rule = template_rule(text)
    assert render_action(rule.actions[0], payload)["variables"][0]["value"] == expected
    from app.dsl import Rule
    restored = Rule.model_validate(rule.model_dump())
    assert render_action(restored.actions[0], payload) == render_action(rule.actions[0], payload)


@pytest.mark.parametrize("value", [123, True, ["one"], {"key": 1}, None])
def test_complete_reference_preserves_json_type(value):
    from app.dsl import Rule
    from app.jira import bind
    raw = yaml.safe_load(EXAMPLE)
    raw["actions"] = [{"type": "jira.issue.edit", "issue": "${issue.key}",
                       "fields": {"description": "${input}"}}]
    rule = Rule.model_validate(raw)
    assert bind(rule.actions[0].fields["description"], {"input": value}) == value
    assert type(bind(rule.actions[0].fields["description"], {"input": value})) is type(value)


@pytest.mark.parametrize("text", ["${}", "${issue.key", "${issue.key.upper()}", "${issue[*]}",
                                  "${${issue.key}}", "${issue.key}" * 51, "a" * 4097])
def test_invalid_templates_rejected_before_execution(text):
    with pytest.raises(RuleError):
        template_rule(text)


@pytest.mark.parametrize("text,payload", [("${missing}", {}), ("prefix ${missing}", {}),
                                        ("prefix ${value}", {"value": []}),
                                        ("prefix ${value}", {"value": None}),
                                        ("prefix ${value}", {"value": "x" * 4096})])
def test_template_runtime_errors(text, payload):
    with pytest.raises(RuleError):
        render_action(template_rule(text).actions[0], payload)


def test_schema_advertises_short_bindings():
    from app.dsl import Rule
    schema = Rule.model_json_schema()
    options = schema["$defs"]["Action"]["properties"]["variables"]["additionalProperties"]["anyOf"]
    assert {"type": "string"} in options
    assert "${path}" in schema["$defs"]["TemplateBinding"]["properties"]["template"]["description"]


@pytest.mark.parametrize("example", sorted(Path("examples").glob("*.yaml")))
def test_all_published_yaml_examples_validate(example):
    parse_rule(example.read_text())


def test_literal_field_pipeline_example():
    rule, _ = parse_rule(Path("examples/literal-field-repository.yaml").read_text())
    event = {"issue": {"key": "DEMO-8", "fields": {"project": {"key": "DEMO"},
             "labels": ["automation-lab-repository"], "summary": "New repository",
             "repository.name": [{"value": "service-payments"}]}}}
    assert match(rule, "jira", "jira:issue_updated", event)["matched"]
    body = render_action(rule.actions[0], event)
    assert {v["key"]: v["value"] for v in body["variables"]}["REPOSITORY_NAME"] == "service-payments"
    event["issue"]["fields"]["repository.name"] = []
    assert not match(rule, "jira", "jira:issue_updated", event)["matched"]
    with pytest.raises(RuleError):
        render_action(rule.actions[0], event)


def test_legacy_checksum_and_pipeline_body_are_unchanged():
    import json
    # Digest independently computed with the old engine at source commit 4d60b26.
    old, digest = parse_rule(Path("tests/fixtures/legacy-repository-rule.yaml").read_text())
    assert digest == "19a96eea0daff819be805dbcaad024ee162366a603687730e42ca7e9137155b7"
    new, _ = parse_rule(EXAMPLE)
    event = json.loads(Path("examples/jira-event.json").read_text())
    assert match(old, "jira", event["webhookEvent"], event)["matched"] == match(new, "jira", event["webhookEvent"], event)["matched"]
    assert render_action(old.actions[0], event) == render_action(new.actions[0], event)


@pytest.mark.parametrize("key,from_id,to_id,expected", [
    ("DEMO-4", "10000", "10001", False),
    ("DEMO-4", "10001", "10003", True),
    ("DEMO-1", "10001", "10003", False),
])
def test_explicit_status_id_mapping(key, from_id, to_id, expected):
    # Synthetic status-ID fixture. IDs are examples, not a remote environment dependency.
    rule, _ = parse_rule(Path("examples/status-id-repository.yaml").read_text())
    payload = {"issue": {"key": key, "fields": {
        "project": {"key": "DEMO"}, "labels": ["automation-lab-repository"]}},
        "changelog": {"items": [{"field": "status", "from": from_id, "to": to_id}]}}
    assert match(rule, "jira", "jira:issue_updated", payload)["matched"] is expected


def test_example():
    import json
    rule, digest = parse_rule(EXAMPLE)
    event = json.loads(Path("examples/jira-event.json").read_text())
    assert match(rule, "jira", event["webhookEvent"], event)["matched"]
    assert not match(rule, "test", event["webhookEvent"], event)["matched"]
    assert len(digest) == 64
    body = render_action(rule.actions[0], event)
    assert len(body["variables"]) == 7
    assert body["variables"][-1]["value"] == "DEMO-1"


@pytest.mark.parametrize("text", ["x: 1\nx: 2", "x: &x [1]\ny: *x", "x: !!python/object:os.system {}",
                                 "x: .nan", "x: 2026-10-06", "["*30 + "0" + "]"*30, "x"*65537])
def test_unsafe_yaml(text):
    with pytest.raises(RuleError):
        parse_rule(text)


def test_unknown_keys_and_arbitrary_code():
    for field, value in [("shell", "touch /tmp/no"), ("url", "http://localhost")]:
        raw = yaml.safe_load(EXAMPLE)
        raw["actions"][0][field] = value
        with pytest.raises(RuleError):
            parse_rule(yaml.safe_dump(raw))


def test_typed_equality_and_missing():
    assert not evaluate({"path": "/x", "op": "eq", "value": True}, {"x": 1})["matched"]
    assert not evaluate({"path": "/x", "op": "ne", "value": "anything"}, {})["matched"]
    assert evaluate({"path": "/x", "op": "exists", "value": False}, {})["matched"]


@pytest.mark.parametrize("op,actual,expected,matched", [
    ("eq", "a", "a", True), ("ne", "a", "b", True), ("in", "a", ["a"], True),
    ("contains", ["lab"], "lab", True), ("contains", "hello", "ell", True),
    ("gt", 2, 1, True), ("gte", 2, 2, True), ("lt", 3, 1, False), ("lte", 1, 1, True),
])
def test_predicates(op, actual, expected, matched):
    node = {"path": "/x", "op": op, "value": expected}
    validate_filter(node)
    assert evaluate(node, {"x": actual})["matched"] == matched


def test_json_pointer():
    assert lookup({"a/b": [{"~": 2}]}, "/a~1b/0/~0") == 2


def test_filter_budget():
    node = {"some": {"path": "/items", "where": {"path": "", "op": "eq", "value": 2}}}
    with pytest.raises(RuleError):
        evaluate(node, {"items": [1]*10001})


def test_render_missing_never_interpolates_code():
    rule, _ = parse_rule(EXAMPLE)
    with pytest.raises(RuleError):
        render_action(rule.actions[0], {})
    value = "$(touch /tmp/never); {{ secrets.token }}"
    body = render_action(rule.actions[0], {"issue": {"key": "DEMO-1", "fields": {"summary": value}}})
    assert body["variables"][2]["value"] == value
