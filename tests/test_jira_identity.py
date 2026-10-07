import json
from uuid import UUID

import httpx
import pytest
from pydantic import ValidationError

from app.config import Settings
from app.dsl import Rule, RuleError
from app.jira import ExecutionIdentity, Jira


def identity(name="crear-repositorio", **changes):
    return ExecutionIdentity(**{ "rule_name": name, "rule_version": 1, "rule_sha256": "a" * 64,
        "run_id": UUID("11111111-1111-4111-8111-111111111111"), "step": 0, **changes})


def action(kind="create", **kwargs):
    data = {"type": "jira.issue." + kind, **kwargs}
    if kind in {"create", "clone"}:
        data.update(project="DEMO", issue_type_id="1")
        data.setdefault("fields", {"summary": {"value": "Solicitud de repositorio"}})
    return Rule.model_validate({"apiVersion": "automation/v1", "kind": "Rule", "name": "test-rule",
        "trigger": {"source": "test", "event": "test"}, "when": {"path": "/x", "op": "exists"}, "actions": [data]}).actions[0]


def adapter(labels=None):
    writes = []
    def handle(request):
        if request.method == "GET":
            return httpx.Response(200, json={"key": "DEMO-1", "fields": {"project": {"key": "DEMO"},
                "summary": "Original", "labels": labels if labels is not None else ["automation-managed"], "status": {"id": "1"}}})
        writes.append(json.loads(request.content))
        return httpx.Response(204) if request.method == "PUT" else httpx.Response(201, json={"key": "DEMO-2"})
    cfg = Settings("", "", "", jira_email="test@example.com", jira_token="test-only",
                   jira_cloud_id="11111111-1111-4111-8111-111111111111", allowed_jira_projects=("DEMO",))
    return Jira(cfg, httpx.Client(transport=httpx.MockTransport(handle))), writes


def test_create_merges_business_labels_and_ignores_forged_event_identity():
    api, writes = adapter()
    item = action(fields={"summary": {"value": "Repositorio"}, "labels": {"value": ["equipo-plataforma", "equipo-plataforma"]}})
    api.execute(item, {"rule_name": "forged", "_identity": {"rule_version": 999}}, identity(), lambda: None)
    assert writes[0]["fields"]["labels"] == ["automation-flujo-crear-repositorio", "automation-managed", "equipo-plataforma"]
    assert writes[0]["properties"][0]["value"] == identity().provenance()


def test_clone_replaces_only_origin_flow_label_preserving_business_labels():
    api, writes = adapter(["automation-managed", "automation-flujo-otro-flujo", "soporte", "automation-prueba"])
    api.execute(action("clone", source={"value": "DEMO-1"}, copy_fields=["summary", "labels"]), {}, identity(), lambda: None)
    assert writes[0]["fields"]["labels"] == ["automation-flujo-crear-repositorio", "automation-managed", "automation-prueba", "soporte"]


@pytest.mark.parametrize("labels", [["automation-flujo-forged"], ["x" + str(i) for i in range(29)]])
def test_reserved_or_overflow_labels_fail_before_write(labels):
    api, writes = adapter()
    with pytest.raises(RuleError):
        api.execute(action(fields={"summary": {"value": "test"}, "labels": {"value": labels}}), {}, identity(), lambda: pytest.fail("reserved write"))
    assert not writes


@pytest.mark.parametrize("labels", [["automation-managed"], ["automation-managed", "automation-flujo-forged"], ["support"]])
def test_edit_cannot_remove_or_spoof_protected_labels(labels):
    api, writes = adapter(["automation-managed", identity().flow_label, "support"])
    with pytest.raises(RuleError):
        api.execute(action("edit", issue={"value": "DEMO-1"}, fields={"labels": {"value": labels}}), {}, identity(), lambda: pytest.fail("reserved write"))
    assert not writes


def test_edit_business_labels_preserves_origin_flow_not_current_rule():
    labels = ["automation-managed", "automation-flujo-origin", "support"]
    api, writes = adapter(labels)
    updated = ["automation-managed", "automation-flujo-origin", "triaged"]
    api.execute(action("edit", issue={"value": "DEMO-1"}, fields={"labels": {"value": updated}}), {}, identity(), lambda: None)
    assert writes[0]["fields"]["labels"] == updated


def test_name_limits_and_revision_do_not_create_new_labels():
    assert len(identity("a" * 80).flow_label) <= 100
    assert identity(rule_version=2).flow_label == identity().flow_label
    with pytest.raises(ValidationError):
        identity("invalid name")
    with pytest.raises(ValidationError):
        identity(rule_sha256="bad")
    with pytest.raises(ValidationError):
        identity().rule_name = "mutated"


def test_missing_trusted_identity_fails_closed():
    api, writes = adapter()
    with pytest.raises(RuleError, match="identity_required"):
        api.execute(action(), {}, "untrusted-operation", lambda: pytest.fail("reserved write"))
    assert not writes
