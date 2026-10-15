"""Bounded, on-demand discovery. Never scan history or persist payload samples."""
import json
import re
from app.paths import MAX_PATH


SAFE_VALUES = {
    "issue.fields.project.key", "issue.fields.status.id", "issue.fields.status.name",
    "issue.fields.issuetype.id", "issue.fields.issuetype.name", "issue.fields.priority.id",
    "issue.fields.priority.name", "webhookEvent",
    "issue.fields.parent.key", "issue.fields.parent.fields.issuetype.name",
}


def discover(payloads, *, max_fields=500, budget=10000):
    fields = {}
    remaining = [budget]

    def visit(value, path, depth):
        if remaining[0] <= 0 or depth > 16 or len(fields) >= max_fields or len(path) > MAX_PATH:
            return
        remaining[0] -= 1
        kind = ("null" if value is None else "boolean" if isinstance(value, bool) else
                "object" if isinstance(value, dict) else "array" if isinstance(value, list) else
                "number" if isinstance(value, (int, float)) else "string")
        if path:
            entry = fields.setdefault(path, {"path": path, "types": set(), "values": []})
            entry["types"].add(kind)
            if path in SAFE_VALUES and kind in {"string", "number", "boolean"}:
                if len(str(value)) <= 120 and value not in entry["values"] and len(entry["values"]) < 8:
                    entry["values"].append(value)
            if path == 'issue.fields.labels' and isinstance(value,list):
                for item in value[:20]:
                    if isinstance(item,str) and len(item)<=120 and '@' not in item and item not in entry['values'] and len(entry['values'])<20:
                        entry['values'].append(item)
        if isinstance(value, dict):
            for key, child in value.items():
                if remaining[0] <= 0 or len(fields) >= max_fields:
                    break
                if not isinstance(key, str) or len(key) > 120:
                    continue
                part = ("." if path else "") + key if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) else "[" + json.dumps(key) + "]"
                visit(child, path + part, depth + 1)
        elif isinstance(value, list):
            # A concrete, executable path, not a made-up wildcard DSL.
            for index, child in enumerate(value[:3]):
                visit(child, f"{path}[{index}]", depth + 1)

    for payload in payloads:
        visit(payload, "", 0)
    return [{**entry, "types": sorted(entry["types"])} for _, entry in sorted(fields.items())]
