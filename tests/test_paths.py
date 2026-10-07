"""Path grammar, type distinctions and security boundaries."""
import json

import pytest

from app.dsl import Binding, MISSING, Rule, RuleError, ValueBinding, check_path, evaluate, lookup, validate_filter
from app.paths import compile_path


@pytest.mark.parametrize("path,payload,expected", [
    ("issue.fields.summary", {"issue": {"fields": {"summary": "Repository"}}}, "Repository"),
    ('this["some.value"].is_literal', {"this": {"some.value": {"is_literal": True}}}, True),
    ('issue.fields["Nombre del repositorio"]', {"issue": {"fields": {"Nombre del repositorio": "repo"}}}, "repo"),
    ("issue.components[0].name", {"issue": {"components": [{"name": "Backend"}]}}, "Backend"),
    ('["root.key"][1]["a/b~c"]', {"root.key": [None, {"a/b~c": 3}]}, 3),
    ('["日本語"]', {"日本語": 5}, 5),
    (r'["quote\"and\\slash"]', {'quote"and\\slash': "literal"}, "literal"),
    (r'["line\nbreak"]', {"line\nbreak": "literal"}, "literal"),
    (r'["\u0061"]', {"a": "escaped"}, "escaped"),
    (r'["\ud83d\ude00"]', {"😀": "unicode"}, "unicode"),
    ('[""]', {"": "empty key"}, "empty key"),
    ('_steps["0"].key', {"_steps": {"0": {"key": "DEMO-2"}}}, "DEMO-2"),
    ("[0][0]", [["nested"]], "nested"),
    ("", {"a": 1}, {"a": 1}),
    ("null_field", {"null_field": None}, None),
    ("false_field", {"false_field": False}, False),
    ("zero", {"zero": 0}, 0),
    ("__class__", {"__class__": "data only"}, "data only"),
])
def test_valid_paths(path, payload, expected):
    assert check_path(path) == path
    assert lookup(payload, path) == expected
    assert Binding(path=path).path == ValueBinding(path=path).path == path


@pytest.mark.parametrize("path,payload", [
    ("missing", {}), ("parent.child", {"parent": None}),
    ("parent.child", {"parent": "not an object"}),
    ("items[0]", {"items": []}), ("items[999999999999999999999]", {"items": [1]}),
    ('items["0"]', {"items": [1]}), ("items[0]", {"items": {"0": 1}}),
    ("items.name", {"items": [{"name": "not implicit fanout"}]}),
    ("__class__.__name__", "never Python attribute lookup"),
])
def test_missing_and_wrong_container_types(path, payload):
    assert lookup(payload, path) is MISSING


@pytest.mark.parametrize("path", [
    "$", "$.issue.key", ".issue", "issue.", "issue..key", "issue/key",
    'this."some.value".ok', "issue[", "issue[]", "issue[-1]", "issue[01]",
    "issue[1.0]", "issue[+1]", "issue[ 0]", "issue[0 ]", "issue[0]tail",
    "issue['key']", 'issue["key" ]', 'issue["key"]..name', 'issue.["key"]',
    'issue["key"]()', 'issue["unterminated]', r'issue["bad\q"]',
    r'issue["\ud800"]', r'issue["\udc00"]', 'issue["line\nbreak"]',
    "issue[*]", "issue[0:2]", "issue[0,1]", "issue[?(@.ok)]", "issue..*",
    "issue.get()", 'issue[__import__("os")]', "issue + other", "issue; code",
    " issue", "issue ", "issue\x00key", "éclair", "a" * 501,
    "/issue/key", "/a~1b/0/~0", "/", "/a~2b",
])
def test_invalid_paths_rejected_everywhere(path):
    with pytest.raises(RuleError):
        check_path(path)
    with pytest.raises(ValueError):
        Binding(path=path)
    with pytest.raises(ValueError):
        ValueBinding(path=path)
    with pytest.raises(RuleError):
        validate_filter({"path": path, "op": "exists"})
    with pytest.raises(RuleError):
        validate_filter({"some": {"path": path, "where": {"path": "", "op": "exists"}}})


@pytest.mark.parametrize("path", [None, 1, True, [], {}])
def test_nonstring_paths_rejected(path):
    with pytest.raises(RuleError):
        check_path(path)


def test_some_uses_current_item_and_distinguishes_null_from_missing():
    predicate = {"path": '["is.ok"]', "op": "eq", "value": True}
    node = {"some": {"path": "changelog.items", "where": predicate}}
    validate_filter(node)
    assert evaluate(node, {"changelog": {"items": [{"is.ok": False}, {"is.ok": True}]}})["matched"]
    assert not evaluate(node, {"is.ok": True, "changelog": {"items": [{}]}})["matched"]
    assert evaluate({"path": "x", "op": "exists"}, {"x": None})["matched"]
    assert not evaluate({"path": "x", "op": "exists"}, {})["matched"]
    assert evaluate({"path": "", "op": "eq", "value": 2}, 2)["matched"]


def test_path_compilation_cache_is_bounded():
    for index in range(1100):
        check_path(f'fields["key{index}"]')
    assert compile_path.cache_info().currsize <= 1024


def test_literal_keys_roundtrip_using_json_string_escapes():
    import random
    rng = random.Random(5610)
    for _ in range(100):
        key = ''.join(rng.choice('ab. /~[]"\\\n\t😀') for _ in range(rng.randrange(0, 20)))
        path = 'root[' + json.dumps(key) + '][0].value'
        payload = {"root": {key: [{"value": "data"}]}}
        assert lookup(payload, path) == "data"


def test_schema_describes_paths_in_bindings_filters_and_guards():
    schema = Rule.model_json_schema()
    for name in ("Binding", "ValueBinding"):
        field = schema["$defs"][name]["properties"]["path"]
        serialized = json.dumps(field)
        assert "500" in serialized and "x-path-syntax" in serialized
        assert 'issue.fields.summary' in serialized
        assert "without $" in serialized
    assert schema["properties"]["when"]["x-path-syntax"]["root"] == "implicit-current-object"
    assert "x-path-syntax" in schema["$defs"]["JiraGet"]["properties"]["require"]
