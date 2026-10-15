"""Authentication boundary: credentials become identities, endpoints require permissions.

Local database tokens are implemented. A future OAuth validator can implement
Authenticator without changing endpoints, rules, workers or audit attribution.
"""
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

PERMISSIONS = frozenset({"api:read", "rules:write", "flows:operate", "auth:admin"})


@dataclass(frozen=True)
class Identity:
    subject: str
    permissions: frozenset[str]
    token_id: UUID | None = None
    pending: bool = False
    pending_administrator: bool = False


class Authenticator(Protocol):
    async def authenticate(self, authorization: str) -> Identity | None: ...


def required_permission(method, route):
    if route in {"/v1/auth/me", "/v1/auth/tokens/self/rotate", "/v1/auth/tokens/self/confirm"}:
        return None
    if route.startswith("/v1/auth/"):
        return "auth:admin"
    readonly = method == "GET" or route in {"/v1/evaluate", "/v1/compare", "/v1/rules/validate"}
    if readonly:
        return "api:read"
    if route == "/v1/rules" and method == "POST":
        return "rules:write"
    if method == "POST" and route in {"/v1/rules/{name}/activate", "/v1/rules/{name}/disable",
            "/v1/events", "/v1/events/{event_id}/execute", "/v1/runs/{run_id}/reconcile"}:
        return "flows:operate"
    return "auth:admin"  # New mutation routes are administrator-only unless explicitly classified.
