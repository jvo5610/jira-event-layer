"""Bounded, on-demand discovery. Never scan history or persist payload samples."""
import json
import re
from collections import deque
from app.paths import MAX_PATH


SAFE_VALUES = {
    "issue.fields.project.key", "issue.fields.status.id", "issue.fields.status.name",
    "issue.fields.issuetype.id", "issue.fields.issuetype.name", "issue.fields.priority.id",
    "issue.fields.priority.name", "webhookEvent",
    "issue.fields.parent.key", "issue.fields.parent.fields.issuetype.name",
}
SAMPLE_LIMIT = 100
VISITS_PER_EVENT = 2000
SENSITIVE_KEY = re.compile(r'password|passwd|secret|token|authorization|cookie|apikey|credential|email|phone')
SENSITIVE_TEXT = re.compile(
    r'\bBearer\s+\S+|-----BEGIN .*PRIVATE KEY|\bAKIA[A-Z0-9]{16}\b|\bATATT[A-Za-z0-9_-]{20,}'
    r'|\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+'
    r'|(?:password|secret|token|api[_-]?key)\s*[:=]\s*\S+'
    r'|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', re.IGNORECASE)


def example(entry, value, blocked):
    if blocked:
        entry['example_state'] = 'protected'
        return
    if entry['example_state'] == 'present' or value is None:
        return
    if isinstance(value, str):
        if not value[:512].strip():
            return
        if SENSITIVE_TEXT.search(value[:512]):
            entry['example_state'] = 'protected'
            return
        entry.update(example=value[:120], example_truncated=len(value) > 120, example_kind='value')
    elif isinstance(value, (dict, list)):
        if not value:
            return
        # Show structure, never copy whole objects or unredacted nested data.
        entry.update(example_kind='object' if isinstance(value, dict) else 'array', example_count=len(value))
    elif isinstance(value, (int, float, bool)):
        entry.update(example=value, example_kind='value')
    else:
        return
    entry['example_state'] = 'present'


def discover(payloads, *, max_fields=500, budget=VISITS_PER_EVENT, breadth_first=False):
    fields = {}
    remaining = [budget]
    pending = deque()

    def descend(value, path, depth, blocked):
        if breadth_first:
            # Bound the queue itself, not just the eventual traversal.
            if len(pending) < remaining[0]:
                pending.append((value, path, depth, blocked))
        else:
            visit(value, path, depth, blocked)

    def visit(value, path, depth, blocked=False):
        if remaining[0] <= 0 or depth > 16 or len(path) > MAX_PATH:
            return
        remaining[0] -= 1
        kind = ("null" if value is None else "boolean" if isinstance(value, bool) else
                "object" if isinstance(value, dict) else "array" if isinstance(value, list) else
                "number" if isinstance(value, (int, float)) else "string")
        if path and (path in fields or len(fields) < max_fields):
            entry = fields.setdefault(path, {"path": path, "types": set(), "values": [], "example_state": "empty"})
            entry["types"].add(kind)
            example(entry, value, blocked)
            if not blocked and path in SAFE_VALUES and kind in {"string", "number", "boolean"}:
                if len(str(value)) <= 120 and not SENSITIVE_TEXT.search(str(value)) and value not in entry["values"] and len(entry["values"]) < 8:
                    entry["values"].append(value)
            if path == 'issue.fields.labels' and isinstance(value,list):
                for item in value[:20]:
                    if isinstance(item,str) and len(item)<=120 and not SENSITIVE_TEXT.search(item) and item not in entry['values'] and len(entry['values'])<20:
                        entry['values'].append(item)
        if isinstance(value, dict):
            for key, child in value.items():
                if remaining[0] <= 0:
                    break
                if not isinstance(key, str) or len(key) > 120:
                    continue
                part = ("." if path else "") + key if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) else "[" + json.dumps(key) + "]"
                sensitive = bool(SENSITIVE_KEY.search(re.sub(r'[^a-z0-9]', '', key.lower())))
                descend(child, path + part, depth + 1, blocked or sensitive)
        elif isinstance(value, list):
            # A concrete, executable path, not a made-up wildcard DSL.
            for index, child in enumerate(value[:3]):
                descend(child, f"{path}[{index}]", depth + 1, blocked)

    for index, payload in enumerate(payloads):
        if index >= SAMPLE_LIMIT:
            break
        remaining[0] = budget
        pending.clear()
        visit(payload, "", 0)
        while pending and remaining[0] > 0:
            visit(*pending.popleft())
    return [{**entry, "types": sorted(entry["types"])} for _, entry in sorted(fields.items())]
