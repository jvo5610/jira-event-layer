"""Bounded data-only rule language. No eval, scripts, Jinja or arbitrary HTTP."""
import hashlib
import json
import re
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.paths import MAX_PATH, PATH_DESCRIPTION, PATH_EXAMPLES, PATH_SYNTAX, PathError, check_path as validate_path, resolve

MAX_YAML = 65536
MISSING = object()


class RuleError(ValueError):
    pass


class Loader(yaml.SafeLoader):
    def compose_node(self, parent, index):
        if self.check_event(yaml.AliasEvent):
            raise RuleError("YAML aliases are not permitted")
        depth = getattr(self, "depth", 0) + 1
        self.depth = depth
        if depth > 24:
            raise RuleError("YAML nesting limit exceeded")
        try:
            return super().compose_node(parent, index)
        finally:
            self.depth -= 1

    def construct_mapping(self, node, deep=False):
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in result:
                raise RuleError("YAML mapping keys must be unique strings")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


FieldPath = Annotated[str, Field(max_length=MAX_PATH, description=PATH_DESCRIPTION,
                                examples=PATH_EXAMPLES, json_schema_extra={"x-path-syntax": PATH_SYNTAX})]


class Binding(Strict):
    path: FieldPath | None = None
    value: str | None = None
    default: str | None = None

    @model_validator(mode="after")
    def valid(self):
        if (self.path is None) == (self.value is None):
            raise ValueError("Choose exactly one of path or value")
        if self.path is not None:
            check_path(self.path)
        if self.value is not None and len(self.value) > 4096:
            raise ValueError("Variable too long")
        return self


class Action(Strict):
    type: Literal["bitbucket.pipeline"]
    workspace: str
    repository: str
    branch: str = "master"
    pipeline: str
    variables: dict[str, Binding] = Field(default_factory=dict, max_length=50)

    @model_validator(mode="after")
    def valid(self):
        for value in (self.workspace, self.repository):
            if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,100}", value):
                raise ValueError("Invalid workspace or repository slug")
        for value in (self.branch, self.pipeline):
            if not value or len(value) > 200 or any(ord(c) < 32 for c in value):
                raise ValueError("Invalid branch/pipeline name")
        for key in self.variables:
            if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,99}", key):
                raise ValueError("Invalid variable name")
            if re.search(r"TOKEN|PASSWORD|SECRET|AUTHORIZATION|PRIVATE_KEY", key):
                raise ValueError("Secrets belong in connector configuration, not YAML variables")
        return self


class ValueBinding(Strict):
    path: FieldPath | None = None
    value: Any = None

    @model_validator(mode="before")
    @classmethod
    def has_binding(cls, raw):
        if isinstance(raw, dict) and raw.get("path") is None and "value" not in raw:
            raise ValueError("Choose path or value")
        return raw

    @model_validator(mode="after")
    def valid(self):
        if self.path is not None and self.value is not None:
            raise ValueError("Choose exactly one of path or value")
        if self.path is not None:
            check_path(self.path)
        if len(json.dumps(self.value, allow_nan=False).encode()) > 16000:
            raise ValueError("Field value exceeds size limit")
        return self


class JiraFields(Strict):
    fields: dict[str, ValueBinding] = Field(default_factory=dict, max_length=30)

    @model_validator(mode="after")
    def valid_fields(self):
        for key in self.fields:
            if key not in {"summary", "description", "labels", "priority", "assignee", "duedate"} and not re.fullmatch(r"customfield_[0-9]+", key):
                raise ValueError("Field not supported; project, type, status and permissions cannot be changed via fields")
        return self


class JiraCreate(JiraFields):
    type: Literal["jira.issue.create"]
    project: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,30}$")
    issue_type_id: str = Field(pattern=r"^[0-9]+$")
    parent: Binding | None = None


class JiraClone(JiraCreate):
    type: Literal["jira.issue.clone"]
    source: Binding
    copy_fields: list[str] = Field(default_factory=lambda: ["summary", "description"], min_length=1, max_length=30)

    @model_validator(mode="after")
    def valid_copy_fields(self):
        JiraFields(fields={key: ValueBinding(value=None) for key in self.copy_fields})
        return self


class JiraEdit(JiraFields):
    type: Literal["jira.issue.edit"]
    issue: Binding

    @model_validator(mode="after")
    def not_empty(self):
        if not self.fields:
            raise ValueError("At least one field is required")
        return self


class JiraTransition(JiraFields):
    type: Literal["jira.issue.transition"]
    issue: Binding
    to_status_id: str = Field(pattern=r"^[0-9]+$")
    from_status_id: str | None = Field(default=None, pattern=r"^[0-9]+$")


class JiraComment(Strict):
    type: Literal["jira.comment.add"]
    issue: Binding
    text: Binding


class JiraLink(Strict):
    type: Literal["jira.issue.link"]
    inward: Binding
    outward: Binding
    link_type_id: str = Field(pattern=r"^[0-9]+$")


class JiraGet(Strict):
    type: Literal["jira.issue.get"]
    issue: Binding
    # Guard evaluated against the current issue, not the webhook snapshot.
    require: dict | None = Field(default=None, description="Filter over the fetched current Jira issue, not the event. " + PATH_DESCRIPTION,
                                examples=[{"path": "fields.status.id", "op": "eq", "value": "10003"}],
                                json_schema_extra={"x-path-syntax": PATH_SYNTAX})

    @model_validator(mode="after")
    def valid_guard(self):
        if self.require is not None:
            validate_filter(self.require)
        return self


RuleAction = Annotated[Action | JiraCreate | JiraClone | JiraEdit | JiraTransition | JiraComment | JiraLink | JiraGet,
                       Field(discriminator="type")]


class Trigger(Strict):
    source: Literal["jira", "test"]
    event: str = Field(min_length=1, max_length=100)


class Rule(Strict):
    apiVersion: Literal["automation/v1"]
    kind: Literal["Rule"]
    name: str = Field(pattern=r"^[a-z][a-z0-9-]{2,79}$", description="Stable, meaningful rule slug. Created Jira issues receive automation-flujo-<name>; renaming creates a different rule.")
    description: str = Field(default="", max_length=2000)
    trigger: Trigger
    when: dict = Field(description="Filter over the triggering event; some.where uses the current list item. " + PATH_DESCRIPTION,
                       examples=[{"path": "issue.fields.project.key", "op": "eq", "value": "DEMO"},
                                 {"some": {"path": "changelog.items", "where": {"path": "field", "op": "eq", "value": "status"}}}],
                       json_schema_extra={"x-path-syntax": PATH_SYNTAX})
    actions: list[RuleAction] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def valid(self):
        validate_filter(self.when)
        return self


def check_path(path):
    try:
        return validate_path(path)
    except PathError as exc:
        raise RuleError(str(exc)) from None


def lookup(obj, path):
    try:
        return resolve(obj, path, MISSING)
    except PathError as exc:
        raise RuleError(str(exc)) from None


def validate_filter(node, depth=0, budget=None):
    budget = budget if budget is not None else [0]
    budget[0] += 1
    if depth > 12 or budget[0] > 128 or not isinstance(node, dict):
        raise RuleError("Filter must be a bounded object (depth <=12, nodes <=128)")
    if set(node) in ({"all"}, {"any"}):
        children = next(iter(node.values()))
        if not isinstance(children, list) or not 1 <= len(children) <= 32:
            raise RuleError("all/any require 1..32 filters")
        for child in children:
            validate_filter(child, depth + 1, budget)
    elif set(node) == {"not"}:
        validate_filter(node["not"], depth + 1, budget)
    elif set(node) == {"some"}:
        some = node["some"]
        if not isinstance(some, dict) or set(some) != {"path", "where"}:
            raise RuleError("some requires path and where")
        check_path(some["path"])
        validate_filter(some["where"], depth + 1, budget)
    else:
        if set(node) not in ({"path", "op", "value"}, {"path", "op"}):
            raise RuleError("Predicate requires path, op, value")
        check_path(node["path"])
        op = node["op"]
        if op not in {"eq", "ne", "in", "contains", "exists", "gt", "gte", "lt", "lte"}:
            raise RuleError("Unknown filter operator")
        if op != "exists" and "value" not in node:
            raise RuleError("Predicate value required")
        value = node.get("value", True)
        if op == "exists" and type(value) is not bool:
            raise RuleError("exists value must be boolean")
        if op == "in" and (not isinstance(value, list) or len(value) > 100):
            raise RuleError("in value must be a list of at most 100 items")
        if op in {"gt", "gte", "lt", "lte"} and type(value) not in {int, float}:
            raise RuleError("Numeric comparison requires numeric value")


def evaluate(node, payload, budget=None):
    budget = budget if budget is not None else [10000]
    budget[0] -= 1
    if budget[0] < 0:
        raise RuleError("Filter evaluation budget exceeded")
    if "all" in node or "any" in node:
        op = "all" if "all" in node else "any"
        children = [evaluate(child, payload, budget) for child in node[op]]
        matched = all(c["matched"] for c in children) if op == "all" else any(c["matched"] for c in children)
        return {"op": op, "matched": matched, "children": children}
    if "not" in node:
        child = evaluate(node["not"], payload, budget)
        return {"op": "not", "matched": not child["matched"], "children": [child]}
    if "some" in node:
        items = lookup(payload, node["some"]["path"])
        matched = False
        if isinstance(items, list):
            for item in items:
                if evaluate(node["some"]["where"], item, budget)["matched"]:
                    matched = True
                    break
        return {"op": "some", "path": node["some"]["path"], "matched": matched}
    actual = lookup(payload, node["path"])
    expected, op = node.get("value", True), node["op"]
    exists = actual is not MISSING
    matched = False
    if op == "exists":
        matched = exists == expected
    elif exists:
        equal = type(actual) is type(expected) and actual == expected
        if op == "eq":
            matched = equal
        elif op == "ne":
            matched = not equal
        elif op == "in":
            matched = any(type(actual) is type(v) and actual == v for v in expected)
        elif op == "contains":
            if isinstance(actual, list):
                matched = any(type(v) is type(expected) and v == expected for v in actual)
            elif isinstance(actual, str) and isinstance(expected, str):
                matched = expected in actual
        elif type(actual) in {int, float}:
            matched = {"gt": actual > expected, "gte": actual >= expected,
                       "lt": actual < expected, "lte": actual <= expected}[op]
    return {"op": op, "path": node["path"], "present": exists, "matched": matched}


def parse_rule(text):
    if len(text.encode()) > MAX_YAML:
        raise RuleError("YAML exceeds 64 KiB")
    try:
        raw = yaml.load(text, Loader=Loader)
        # Also rejects timestamps, sets, NaN and other non-JSON YAML values.
        json.dumps(raw, allow_nan=False)
        rule = Rule.model_validate(raw)
    except (yaml.YAMLError, ValueError, TypeError, RecursionError) as exc:
        raise RuleError("Invalid rule: " + str(exc)[:800]) from None
    canonical = json.dumps(rule.model_dump(), sort_keys=True, separators=(",", ":"), allow_nan=False)
    return rule, hashlib.sha256(canonical.encode()).hexdigest()


def match(rule, source, event_type, payload):
    if rule.trigger.source != source or rule.trigger.event != event_type:
        return {"matched": False, "reason": "trigger_mismatch"}
    return evaluate(rule.when, payload)


def render_action(action, payload):
    variables = []
    for key, binding in action.variables.items():
        value = binding.value if binding.path is None else lookup(payload, binding.path)
        if value is MISSING or value is None:
            value = binding.default
        if value is None or type(value) not in {str, int, float, bool}:
            raise RuleError(f"Variable {key} missing or not scalar")
        value = str(value)
        if len(value) > 4096:
            raise RuleError(f"Variable {key} exceeds size limit")
        variables.append({"key": key, "value": value, "secured": False})
    body = {"target": {"type": "pipeline_ref_target", "ref_type": "branch", "ref_name": action.branch,
                        "selector": {"type": "custom", "pattern": action.pipeline}}, "variables": variables}
    if len(json.dumps(body).encode()) > 10000:
        raise RuleError("Pipeline input exceeds 10 KB")
    return body
