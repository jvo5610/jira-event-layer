import hashlib
import json
import logging
from contextvars import ContextVar
from datetime import datetime, timezone


log = logging.getLogger("jira_relay")
request_id = ContextVar("request_id", default=None)


def configure_logging():
    log.setLevel(logging.INFO)
    log.propagate = False
    if not log.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        log.addHandler(handler)


def fingerprint(value):
    return hashlib.sha256(value.encode()).hexdigest() if value else None


def log_event(event, *, level=logging.INFO, **fields):
    configure_logging()
    record = {"timestamp": datetime.now(timezone.utc).isoformat(),
              "level": logging.getLevelName(level), "event": event}
    if request_id.get():
        record["request_id"] = request_id.get()
    record.update(fields)
    log.log(level, json.dumps(record, default=str, ensure_ascii=True))
