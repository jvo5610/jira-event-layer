import pytest

from test_integration import env, webhook_client
from test_service_actors import read_rule, send


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("event_type", ["filter_created", "filter_updated", "filter_deleted",
                                      "jira:filter_created", "jira:filter_updated", "jira:filter_deleted"])
def test_jira_filter_events_are_acknowledged_without_persistence(env, webhook_client, native, event_type):
    client, store, _ = env
    read_rule(client, event_type)
    payload = {"filter": {"id": "123", "name": "private-filter-name"}}

    for _ in range(2):
        result = send(env, webhook_client if native else None, event_type, payload)
        assert result["event_id"] is None
        assert result["ignored_reason"] == "ignored_event_type"
        assert result["persisted"] is False
        assert result["runs"] == []

    assert client.get("/v1/events").json() == []
    assert store.query("SELECT count(*) n FROM evaluations")[0]["n"] == 0
    assert store.query("SELECT count(*) n FROM runs")[0]["n"] == 0


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("event_type", ["issue_property_set", "issuelink_created", "issuelink_deleted"])
def test_required_actorless_events_remain_retained(env, webhook_client, native, event_type):
    client, _, _ = env

    result = send(env, webhook_client if native else None, event_type, {"timestamp": 123})

    assert result["event_id"] is not None
    assert result["ignored_reason"] == "held_actor_unknown"
    assert client.get("/v1/events/" + result["event_id"]).status_code == 200


def test_filter_drop_does_not_bypass_webhook_authentication(env):
    client, store, _ = env

    response = client.post("/webhooks/jira", json={"webhookEvent": "filter_updated"},
                           headers={"X-Atlassian-Webhook-Identifier": "untrusted-filter"})

    assert response.status_code == 401
    assert store.query("SELECT count(*) n FROM events")[0]["n"] == 0


def test_synthetic_filter_event_is_not_subject_to_jira_ingress_policy(env):
    client, _, _ = env

    response = client.post("/v1/events", headers={"Idempotency-Key": "synthetic-filter"},
                           json={"source": "test", "event_type": "filter_updated", "payload": {}})

    assert response.status_code == 202
    assert response.json()["event_id"] is not None
    assert response.json()["ignored_reason"] is None
