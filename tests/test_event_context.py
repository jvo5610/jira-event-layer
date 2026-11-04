from datetime import datetime, timezone
from uuid import UUID

from app.event_context import action_context, event_document


def test_public_document_has_json_types_and_excludes_database_internals():
    identifier = UUID("11111111-1111-4111-8111-111111111111")
    now = datetime(2026, 10, 29, tzinfo=timezone.utc)
    row = {"id": identifier, "source": "jira", "event_type": "jira:issue_created",
           "payload": {"issue": {"key": "DEMO-8"}, "enrichment": {"forged": True}},
           "enrichment": {"forms": [{"answers": {"engine": "PostgreSQL"}}]},
           "received_at": now, "raw_body": b"not part of the public event",
           "enrichment_lease_token": "private", "tenant": "internal"}

    document = event_document(row)

    assert document["id"] == str(identifier)
    assert document["received_at"] == now.isoformat()
    assert event_document(document) == document
    assert document["enrichment"] == row["enrichment"]
    assert document["payload"] == row["payload"]
    assert not {"raw_body", "enrichment_lease_token", "tenant", "_relay"} & document.keys()


def test_action_results_are_a_separate_namespace_without_changing_the_event():
    event = event_document({"payload": {"_steps": {"0": {"key": "FORGED-1"}}}})
    context = action_context(event, [{"step": 0, "result": {"key": "DEMO-9"}},
                                     {"step": 1, "result": None}])

    assert context == {**event, "_steps": {"0": {"key": "DEMO-9"}}}
    assert event["payload"]["_steps"]["0"]["key"] == "FORGED-1"
    assert "_steps" not in event
