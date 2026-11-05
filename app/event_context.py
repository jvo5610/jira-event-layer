"""The public event document is also the input to rules and action bindings."""
from datetime import date, datetime
from uuid import UUID


EVENT_FIELDS = (
    "id", "source", "event_type", "payload", "enrichment",
    "enrichment_status", "enrichment_attempts", "enrichment_error", "enriched_at",
    "received_at", "processed_at", "payload_redacted_at", "last_error",
)


def json_value(value):
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    return value


def event_document(row):
    return {key: json_value(row.get(key)) for key in EVENT_FIELDS}


def action_context(event, results):
    return {**event, "_steps": {str(row["step"]): json_value(row["result"])
                              for row in results if row.get("result") is not None}}
