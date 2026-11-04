import copy
import hashlib
import hmac
import json
from pathlib import Path

import httpx
import pytest

from app.dsl import match, parse_rule, resolve_binding
from app.jira import Jira
from app.worker import Worker
from test_integration import activate, env


RULES = [Path("examples/forms/infra-s3.yaml"), Path("examples/forms/infra-sqs.yaml")]
ANSWERS = {
    "aws_account": "000000000000", "recursos": ["S3", "SQS"],
    "descripcion": "Conservar adjuntos de tickets y procesarlos en segundo plano.",
    "linea": "Core",
    "s3_name": "files-dev", "s3_why": "Guardar archivos hasta procesarlos.",
    "s3_read_who": "files-worker", "s3_write_who": "files-api",
    "sqs_name": "files-dev", "sqs_why": "Procesar los archivos en segundo plano.",
    "sqs_type": "Standard", "sqs_visibility": "120", "sqs_retention": "4 días",
    "sqs_producers": "files-api", "sqs_consumers": "files-worker",
}


def parent():
    return {"key": "DEMO-42", "fields": {
        "summary": "Almacenamiento y procesamiento de adjuntos en dev",
        "project": {"key": "DEMO"}, "issuetype": {"id": "10001"},
        "customfield_10010": {"requestType": {"id": "10000", "serviceDeskId": "10000"}},
    }}


def document():
    return {"source": "jira", "event_type": "jira:issue_created",
            "payload": {"issue": parent()}, "enrichment_status": "completed",
            "enrichment": {"forms": [{"submitted": True,
                "formTemplate": {"id": "11111111-1111-4111-8111-111111111111"},
                "answers": copy.deepcopy(ANSWERS)}]}}


@pytest.mark.parametrize("path", RULES)
def test_rules_accept_new_parents_and_render_only_form_values(path):
    rule, _ = parse_rule(path.read_text())
    event = document()
    assert match(rule, "jira", "jira:issue_created", event)["matched"]
    fields = {k: resolve_binding(v, event) for k, v in rule.actions[1].fields.items()}
    assert len(fields["description"]) < 1100
    assert "DEMO-42" in fields["description"]
    assert "files-api" in fields["description"]
    assert "files-worker" in fields["description"]
    assert not any(key + ":" in fields["description"] for key in ANSWERS)
    assert ANSWERS["descripcion"] in fields["description"]
    assert "Línea: Core" in fields["description"]
    assert "Otra información\nEscalado desde: DEMO-42 — " + parent()["fields"]["summary"] in fields["description"]
    stem = "s3" if "s3" in path.name else "sqs"
    assert ANSWERS[stem + "_why"] in fields["description"]
    assert "Nombre del bucket:" in fields["description"] if stem == "s3" else "Nombre de la cola:" in fields["description"]
    if stem == "sqs":
        assert "Productores:" in fields["description"] and "Consumidores:" in fields["description"]
    assert "Necesitamos una cola para procesar archivos" not in fields["description"]
    for missing in ("aws_account", "descripcion", "linea", stem + "_name", stem + "_why"):
        for value in (None, ""):
            altered = copy.deepcopy(event)
            altered["enrichment"]["forms"][0]["answers"][missing] = value
            assert not match(rule, "jira", "jira:issue_created", altered)["matched"]
    changed = copy.deepcopy(event)
    changed["enrichment"]["forms"][0]["answers"].update({stem + "_why": "Guardar comprobantes de operaciones.",
                                                       "descripcion": "Pedido de otro equipo.", "linea": "Emisor"})
    actual = resolve_binding(rule.actions[1].fields["description"], changed)
    assert "Guardar comprobantes de operaciones." in actual and "Pedido de otro equipo." in actual
    assert "Línea: Emisor" in actual and ANSWERS[stem + "_why"] not in actual
    for more in (False, True):
        altered = copy.deepcopy(event)
        altered["payload"]["issue"]["fields"]["project"]["key"] = "PRX" if more else "DEMO"
        altered["payload"]["issue"]["fields"]["issuetype"]["id"] = "10001" if more else "10002"
        assert not match(rule, "jira", "jira:issue_created", altered)["matched"]


def test_webhook_waits_for_forms_then_creates_and_links_children_without_replay(env):
    client, store, settings = env
    settings.jira_token = "fixture-token"
    settings.jira_cloud_id = "11111111-1111-4111-8111-111111111111"
    settings.allowed_jira_projects = frozenset({"DEMO"})
    store.remember_jira_actor(settings, "relay-fixture")
    for path in RULES:
        activate(client, path.read_text())

    created, links, reads = {}, [], []

    def provider(request):
        if request.method == "GET":
            key = request.url.path.rsplit("/", 1)[-1]
            return httpx.Response(200, json=parent() if key == "DEMO-42" else created[key])
        body = json.loads(request.content)
        if request.url.path.endswith("/issue"):
            assert body["fields"]["project"]["key"] == "DEMO"
            key = "DEMO-" + str(100 + len(created))
            created[key] = {"key": key, "fields": body["fields"]}
            return httpx.Response(201, json={"key": key})
        assert request.url.path.endswith("/issueLink")
        assert body["inwardIssue"]["key"] == "DEMO-42"
        links.append(body)
        return httpx.Response(201)

    class FormsJira(Jira):
        def forms(self, key, progress=None):
            reads.append(key)
            submitted = len(reads) > 1
            questions, answers = {}, {}
            for number, (field, value) in enumerate(ANSWERS.items()):
                qid = str(number)
                questions[qid] = {"questionKey": field, "type": "ts"}
                answers[qid] = {"value": value}
            return ([{"id": "form", "submitted": submitted,
                      "formTemplate": {"id": "11111111-1111-4111-8111-111111111111"}}],
                    [{"design": {"questions": questions}, "state": {"answers": answers}}])

    worker = Worker(store, settings, jira=FormsJira(settings,
        httpx.Client(transport=httpx.MockTransport(provider))))
    payload = {"webhookEvent": "jira:issue_created", "user": {"accountId": "human-fixture"},
               "issue": parent()}
    raw = json.dumps(payload).encode()
    headers = {"X-Atlassian-Webhook-Identifier": "routing-parent",
               "X-Hub-Signature": "sha256=" + hmac.new(settings.webhook_secret.encode(), raw,
                                                        hashlib.sha256).hexdigest()}
    response = client.post("/webhooks/jira", content=raw, headers=headers)
    assert response.status_code == 202
    assert worker.tick()
    assert not created and not links
    assert client.get("/v1/runs").json() == []
    store.query("UPDATE events SET enrichment_next_at=now() RETURNING id")
    assert worker.tick()
    for _ in range(6):
        assert worker.tick()
    runs = client.get("/v1/runs").json()
    assert len(runs) == 2 and all(run["status"] == "succeeded" for run in runs)
    assert len(created) == len(links) == 2
    assert {link["outwardIssue"]["key"] for link in links} == set(created)
    assert all("infra" in issue["fields"]["labels"] for issue in created.values())
    duplicate = client.post("/webhooks/jira", content=raw, headers=headers)
    assert duplicate.status_code == 202 and duplicate.json()["duplicate"]
    assert not worker.tick()
    assert len(created) == 2
