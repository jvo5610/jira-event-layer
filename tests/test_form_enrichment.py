from types import SimpleNamespace

import pytest

from app.dsl import RuleError
from app.form_enrichment import build, candidate, normalize
from app.worker import Worker


def jsm_payload(field_name="customfield_98765"):
    return {
        "issue": {
            "id": "12345",
            "key": "DEMO-7",
            "fields": {
                field_name: {
                    "requestType": {
                        "id": "9000",
                        "serviceDeskId": "8000",
                    }
                }
            },
        }
    }


def test_candidate_uses_request_metadata_shape_not_installation_field_ids():
    expected = {"issue_key": "DEMO-7", "request_type_id": "9000", "service_desk_id": "8000"}
    assert candidate("jira", "jira:issue_created", jsm_payload()) == expected
    assert candidate("jira", "jira:issue_created", jsm_payload("customfield_10010")) == expected
    assert candidate("jira", "jira:issue_updated", jsm_payload()) is None
    assert candidate("jira", "jira:issue_created", {"issue": {"key": "DEMO-7", "fields": {}}}) is None


def test_dynamic_normalizer_preserves_json_types_and_unknown_shapes():
    form = {
        "design": {
            "questions": {
                "1": {"questionKey": "name", "type": "ts"},
                "2": {"questionKey": "entity", "type": "cd",
                      "choices": [{"id": "6", "label": "global"}]},
                "3": {"questionKey": "resources", "type": "cm",
                      "choices": [{"id": "1", "label": "S3"}, {"id": "2", "label": "RDS"}]},
                "4": {"questionKey": "count", "type": "number"},
                "5": {"questionKey": "enabled", "type": "boolean"},
                "6": {"questionKey": "configuration", "type": "future-json-type"},
                "7": {"questionKey": "external_choices", "type": "cm"},
            }
        },
        "state": {
            "answers": {
                "1": {"text": "service"},
                "2": {"choices": ["6"]},
                "3": {"choices": ["1", "2"]},
                "4": {"number": 3},
                "5": {"value": True},
                "6": {"value": {"nested": [1, "two", False]}},
                "7": {"choices": ["external-a", "external-b"]},
                "999": {"newShape": {"kept": "raw"}},
            }
        },
    }

    result = normalize(form)

    assert result["answers"] == {
        "name": "service",
        "entity": "global",
        "resources": ["S3", "RDS"],
        "count": 3,
        "enabled": True,
        "configuration": {"nested": [1, "two", False]},
        "external_choices": ["external-a", "external-b"],
        "question_999": {"newShape": {"kept": "raw"}},
    }
    assert "raw" not in result
    assert result["warnings"] == [{"question_id": "999", "reason": "definition_missing"}]


def test_duplicate_or_missing_keys_never_overwrite_answers():
    form = {
        "design": {"questions": {
            "1": {"questionKey": "same", "type": "ts"},
            "2": {"questionKey": "same", "type": "ts"},
            "3": {"type": "ts"},
        }},
        "state": {"answers": {
            "1": {"text": "first"},
            "2": {"text": "second"},
            "3": {"text": "third"},
        }},
    }

    result = normalize(form)

    assert result["answers"] == {
        "same": "first",
        "question_2": "second",
        "question_3": "third",
    }
    assert [warning["reason"] for warning in result["warnings"]] == [
        "question_key_duplicate",
        "question_key_missing",
    ]


def test_multiple_forms_are_normalized_independently():
    forms = [
        {"design": {"questions": {"1": {"questionKey": "a", "type": "ts"}}},
         "state": {"answers": {"1": {"text": "one"}}}},
        {"design": {"questions": {"1": {"questionKey": "b", "type": "ts"}}},
         "state": {"answers": {"1": {"text": "two"}}}},
    ]
    index = [
        {"id": "form-a", "name": "A", "submitted": True},
        {"id": "form-b", "name": "B", "submitted": True},
    ]

    result = build(index, forms)

    assert [item["id"] for item in result["forms"]] == ["form-a", "form-b"]
    assert result["forms"][0]["answers"] == {"a": "one"}
    assert result["forms"][1]["answers"] == {"b": "two"}
    assert all("raw" not in item for item in result["forms"])


def test_form_layout_does_not_consume_persisted_enrichment_budget(monkeypatch):
    monkeypatch.setattr("app.form_enrichment.MAX_ENRICHMENT_BYTES", 256)
    form = {
        "design": {
            "questions": {"1": {"questionKey": "engine", "type": "ts"}},
            "layout": [{"text": "unused layout " * 1000}],
        },
        "state": {"answers": {"1": {"text": "PostgreSQL"}}},
    }

    result = build([{"id": "form-a", "name": "Database", "submitted": True}], [form])

    assert result == {"forms": [{"id": "form-a", "name": "Database", "submitted": True,
                                 "answers": {"engine": "PostgreSQL"}}]}
    assert form["design"]["layout"] == [{"text": "unused layout " * 1000}]


def test_normalized_answers_still_have_a_storage_limit(monkeypatch):
    monkeypatch.setattr("app.form_enrichment.MAX_ENRICHMENT_BYTES", 256)
    form = {"design": {"questions": {"1": {"questionKey": "description"}}},
            "state": {"answers": {"1": {"text": "x" * 300}}}}

    with pytest.raises(RuleError, match="jira_form_enrichment_too_large"):
        normalize(form)


def test_missing_definition_cannot_overwrite_an_explicit_question_key():
    form = {"design": {"questions": {"1": {"questionKey": "question_999"}}},
            "state": {"answers": {"1": {"text": "keep"}, "999": {"text": "other"}}}}
    with pytest.raises(RuleError, match="jira_form_duplicate_question_key"):
        normalize(form)


def test_worker_renews_lease_and_heartbeat_while_reading_forms():
    event = {"id": "event", "source": "jira", "event_type": "jira:issue_created",
             "payload": jsm_payload(), "enrichment_attempts": 1}
    calls = []
    class Store:
        def claim_enrichment(self):
            return event
        def renew_enrichment(self, current):
            assert current is event
            calls.append("lease")
            return True
        def complete_enrichment(self, current, result):
            calls.append("completed")
            return True
    class Jira:
        def forms(self, key, progress):
            for _ in range(3):
                progress()
            return [{"id": "form", "submitted": True}], [{"design": {"questions": {}}, "state": {"answers": {}}}]
    worker = Worker.__new__(Worker)
    worker.store, worker.jira = Store(), Jira()
    worker.settings = SimpleNamespace(jira_forms_empty_retries=3)
    worker.ensure_service_identity = lambda: None
    worker.heartbeat = lambda: calls.append("heartbeat")
    assert worker.tick_enrichment()
    assert calls == ["lease", "heartbeat"] * 3 + ["completed"]


@pytest.mark.parametrize("attempt,expected", [(1, "retry"), (3, "complete")])
def test_empty_form_indexes_use_configured_consistency_retries(attempt, expected):
    event = {
        "id": "event-id",
        "source": "jira",
        "event_type": "jira:issue_created",
        "payload": jsm_payload(),
        "enrichment_attempts": attempt,
        "enrichment_lease_token": "lease",
    }
    outcomes = []

    class Store:
        @staticmethod
        def claim_enrichment():
            return event

        @staticmethod
        def retry_enrichment(current, error, delay):
            outcomes.append(("retry", error, delay))

        @staticmethod
        def complete_enrichment(current, enrichment):
            outcomes.append(("complete", enrichment))
            return True

        @staticmethod
        def fail_enrichment(current, error):
            outcomes.append(("fail", error))

    class Jira:
        @staticmethod
        def forms(issue_key, progress=None):
            assert issue_key == "DEMO-7"
            return [], []

    worker = Worker.__new__(Worker)
    worker.store = Store()
    worker.jira = Jira()
    worker.settings = SimpleNamespace(jira_forms_empty_retries=3)
    worker.ensure_service_identity = lambda: None

    assert worker.tick_enrichment()
    assert outcomes[0][0] == expected
    if expected == "complete":
        assert outcomes[0][1] == {"forms": []}


@pytest.mark.parametrize("submitted", [False, None, "true", 1, "missing"])
@pytest.mark.parametrize("attempt,expected", [(1, "retry"), (3, "failed")])
def test_worker_waits_for_explicit_submission(submitted, attempt, expected):
    event = {"id": "event", "source": "jira", "event_type": "jira:issue_created",
             "payload": jsm_payload(), "enrichment_attempts": attempt}
    outcomes = []

    class Store:
        def claim_enrichment(self):
            return event

        def retry_enrichment(self, current, error, delay):
            outcomes.append(("retry", error, delay))

        def fail_enrichment(self, current, error):
            outcomes.append(("failed", error))

        def complete_enrichment(self, *args):
            pytest.fail("An unsubmitted form must not trigger rule evaluation")

    class Jira:
        def forms(self, key, progress=None):
            metadata = {"id": "form"}
            if submitted != "missing":
                metadata["submitted"] = submitted
            return ([metadata],
                    [{"design": {"questions": {}}, "state": {"answers": {}}}])

    worker = Worker.__new__(Worker)
    worker.store, worker.jira = Store(), Jira()
    worker.settings = SimpleNamespace(jira_forms_empty_retries=3)
    worker.ensure_service_identity = lambda: None

    assert worker.tick_enrichment()
    assert outcomes[0][:2] == (expected, "jira_forms_not_submitted")


@pytest.mark.parametrize("form", [
    {},
    {"design": {}, "state": {}},
    {"design": {"questions": []}, "state": {"answers": {}}},
])
def test_invalid_form_structures_are_rejected(form):
    with pytest.raises(RuleError, match="jira_form_invalid"):
        normalize(form)
