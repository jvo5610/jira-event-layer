import hashlib
import hmac
import json
from contextlib import asynccontextmanager
from uuid import UUID
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field
from psycopg import OperationalError
from psycopg_pool import PoolTimeout
from starlette.concurrency import run_in_threadpool

from app.config import Settings, secret
from app.auth import required_permission
from app.tokens import TokenManager, DatabaseAuthenticator
from app.catalog import SAMPLE_LIMIT, VISITS_PER_EVENT, discover
from app.dsl import Rule, RuleError, match, parse_rule
from app.safety import validate_safety
from app.actors import exclusion_reason
from app.store import Conflict, Store, principal

YAML_BODY = {"requestBody": {"required": True, "content": {
    "application/yaml": {"schema": {"type": "string", "maxLength": 65536}}
}}}


def strict_json(raw):
    def reject_constant(value):
        raise ValueError("Non-JSON numeric constant")
    return json.loads(raw, parse_constant=reject_constant)


class Activation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class Evaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    yaml: str = Field(max_length=65536)
    event_ids: list[UUID] = Field(default_factory=list, max_length=100)
    source: str = "jira"
    event_type: str = "jira:issue_updated"
    payload: dict | None = None


class Comparison(BaseModel):
    model_config = ConfigDict(extra="forbid")
    left_yaml: str = Field(max_length=65536)
    right_yaml: str = Field(max_length=65536)
    event_ids: list[UUID] = Field(min_length=1, max_length=100)


class Reconciliation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    external_uuid: UUID


class TokenCreation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100, pattern=r"\S")
    permissions: list[Literal["api:read", "rules:write", "flows:operate", "auth:admin"]] = Field(min_length=1, max_length=4)
    expires_in_seconds: int = Field(ge=60, le=31536000, strict=True)


def create_app(settings=None, store=None, authenticator=None):
    @asynccontextmanager
    async def lifespan(app):
        app.state.settings = settings or Settings.from_env("api")
        app.state.store = store or Store(app.state.settings.database, app.state.settings.tenant)
        await run_in_threadpool(app.state.store.open)
        try:
            cfg = app.state.settings
            app.state.tokens = TokenManager(app.state.store, cfg.auth_rotation_ttl)
            if authenticator is None:
                supplier = lambda: cfg.admin_bootstrap_token if cfg.admin_bootstrap_token is not None else secret("ADMIN_BOOTSTRAP_TOKEN")
                await run_in_threadpool(app.state.tokens.bootstrap, supplier, cfg.webhook_secret)
            app.state.authenticator = authenticator or DatabaseAuthenticator(app.state.tokens)
            yield
        finally:
            await run_in_threadpool(app.state.store.close)

    app = FastAPI(title="Jira Relay API", version="1.0.0", lifespan=lifespan,
                  openapi_url=None, docs_url=None, redoc_url=None,
                  description="Single-tenant deterministic automation engine. Separate API, workers and PostgreSQL; no cloud dependency.")
    bearer = HTTPBearer(auto_error=False)

    def authorize(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        identity = request.state.identity
        if identity is None:
            raise HTTPException(401, "Valid API bearer token required", headers={"WWW-Authenticate": "Bearer"})
        permission = required_permission(request.method, request.scope["route"].path)
        if permission is not None and permission not in identity.permissions:
            raise HTTPException(403, "Credential does not permit this operation")

    @app.middleware("http")
    async def bounds(request, call_next):
        cfg = request.app.state.settings
        path = request.url.path
        management = path.startswith("/v1/") or path in {"/metrics", "/openapi.json", "/docs", "/redoc"}
        # Set audit attribution in the async context before sync handlers are dispatched.
        try:
            identity = await request.app.state.authenticator.authenticate(request.headers.get("Authorization", "")) if management else None
        except (OperationalError, PoolTimeout):
            return JSONResponse({"detail": "Authentication persistence unavailable"}, status_code=503)
        request.state.identity = identity
        actor = identity.subject if identity else "anonymous"
        # Stream-bound before parsing JSON; prevents unbounded chunked request bodies too.
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > cfg.max_body:
                return JSONResponse({"detail": "Body exceeds configured limit"}, status_code=413)
        request._body = bytes(data)
        context = principal.set(actor)
        try:
            response = await call_next(request)
        finally:
            principal.reset(context)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(RuleError)
    async def invalid(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(Conflict)
    async def conflict(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        return JSONResponse({"detail": "Resource not found"}, status_code=404)

    @app.exception_handler(OperationalError)
    @app.exception_handler(PoolTimeout)
    async def database_unavailable(request, exc):
        return JSONResponse({"detail": "Persistence unavailable; retry delivery"}, status_code=503)

    @app.get("/healthz")
    def health():
        return {"status": "ok"}

    @app.get("/readyz")
    def ready(request: Request):
        request.app.state.store.query("SELECT 1")
        return {"status": "ready"}

    @app.get("/openapi.json", dependencies=[Depends(authorize)], include_in_schema=False)
    def openapi():
        return app.openapi()

    @app.get("/v1/auth/me", dependencies=[Depends(authorize)])
    def current_identity(request: Request):
        identity = request.state.identity
        return {"subject": identity.subject, "permissions": sorted(identity.permissions),
                "token_id": identity.token_id, "pending": identity.pending,
                "pending_administrator": identity.pending_administrator}

    @app.get("/v1/auth/tokens", dependencies=[Depends(authorize)])
    def list_tokens(request: Request, limit: int = Query(100, ge=1, le=100), offset: int = Query(0, ge=0, le=1000000)):
        return request.app.state.tokens.list(request.state.identity, limit, offset)

    @app.post("/v1/auth/tokens", status_code=201, dependencies=[Depends(authorize)])
    def create_token(body: TokenCreation, request: Request):
        return request.app.state.tokens.create(request.state.identity, body.name, body.permissions, body.expires_in_seconds)

    @app.post("/v1/auth/tokens/self/rotate", dependencies=[Depends(authorize)])
    def rotate_token(request: Request):
        return request.app.state.tokens.rotate(request.state.identity)

    @app.post("/v1/auth/tokens/self/confirm", dependencies=[Depends(authorize)])
    def confirm_token(request: Request):
        return request.app.state.tokens.confirm(request.state.identity)

    @app.post("/v1/auth/tokens/{token_id}/revoke", dependencies=[Depends(authorize)])
    def revoke_token(token_id: UUID, request: Request):
        return request.app.state.tokens.revoke(request.state.identity, token_id)

    def checked(text, request):
        rule, digest = parse_rule(text)
        try:
            request.app.state.settings.check_targets(rule)
        except ValueError as exc:
            raise RuleError(str(exc)) from None
        return rule, digest

    @app.get("/v1/schema", dependencies=[Depends(authorize)])
    def schema():
        return Rule.model_json_schema()

    @app.get("/v1/catalog", dependencies=[Depends(authorize)])
    def catalog(request: Request):
        cfg = request.app.state.settings
        return {
            "schema": Rule.model_json_schema(),
            "events": request.app.state.store.query("SELECT DISTINCT source,event_type FROM (SELECT source,event_type FROM events ORDER BY received_at DESC LIMIT 200) recent ORDER BY source,event_type"),
            "connectors": {
                "jira": {"configured": bool(cfg.jira_token and cfg.jira_cloud_id)},
                "bitbucket": {"configured": True if cfg.bitbucket_token and cfg.bitbucket_email else None if cfg.allowed_repositories else False, "repositories": cfg.allowed_repositories},
            },
            "limits": {"actions": 10, "filter_depth": 12, "filter_nodes": 128, "filter_children": 32},
        }

    @app.get("/v1/catalog/fields", dependencies=[Depends(authorize)])
    def catalog_fields(request: Request, source: str = Query(max_length=100), event_type: str = Query(max_length=100)):
        rows = request.app.state.store.query("WITH recent AS MATERIALIZED (SELECT id,payload,received_at FROM events WHERE source=%s AND event_type=%s AND payload_redacted_at IS NULL ORDER BY received_at DESC,id DESC LIMIT %s), ranked AS (SELECT *,row_number() OVER (PARTITION BY coalesce(payload#>>'{issue,fields,issuetype,id}','') ORDER BY received_at DESC,id DESC) AS rank FROM recent) SELECT payload FROM ranked ORDER BY rank,received_at DESC,id DESC", (source, event_type, SAMPLE_LIMIT))
        return {"sample_count": len(rows), "sample_limit": SAMPLE_LIMIT, "candidate_limit": SAMPLE_LIMIT,
                "sampling": "latest events, issue-type interleaved", "fields": discover(row["payload"] for row in rows),
                "limits": {"fields": 500, "visits_per_event": VISITS_PER_EVENT, "depth": 16, "array_items": 3},
                "notice": "Observed fields, not a complete provider schema. Previews prefer non-empty values; sensitive fields are withheld and text is truncated. Objects/lists show structure only."}

    @app.post("/v1/rules/validate", dependencies=[Depends(authorize)], openapi_extra=YAML_BODY)
    async def validate_yaml(request: Request, targets: bool = True):
        try:
            text = (await request.body()).decode("utf-8")
        except UnicodeDecodeError:
            raise RuleError("YAML must be UTF-8") from None
        rule, digest = checked(text, request) if targets else parse_rule(text)
        validate_safety(rule)
        return {"valid": True, "sha256": digest, "spec": rule.model_dump(), "targets_checked": targets, "safety_checked": True}

    @app.post("/v1/rules", status_code=201, dependencies=[Depends(authorize)], openapi_extra=YAML_BODY)
    async def save_yaml(request: Request):
        try:
            text = (await request.body()).decode("utf-8")
        except UnicodeDecodeError:
            raise RuleError("YAML must be UTF-8") from None
        rule, digest = checked(text, request)
        return await run_in_threadpool(request.app.state.store.save_rule, rule, digest, text)

    @app.get("/v1/rules", dependencies=[Depends(authorize)])
    def rules(request: Request):
        return request.app.state.store.query("SELECT r.name,r.active_version,max(v.version) latest_version FROM rules r JOIN rule_versions v ON v.rule_name=r.name GROUP BY r.name ORDER BY r.name LIMIT 1000")

    @app.get("/v1/rules/{name}/versions", dependencies=[Depends(authorize)])
    def versions(name: str, request: Request):
        return request.app.state.store.query("SELECT rule_name,version,sha256,created_at FROM rule_versions WHERE rule_name=%s ORDER BY version DESC LIMIT 100", (name,))

    @app.get("/v1/rules/{name}/versions/{version}", dependencies=[Depends(authorize)])
    def version(name: str, version: int, request: Request):
        rows = request.app.state.store.query("SELECT * FROM rule_versions WHERE rule_name=%s AND version=%s", (name, version))
        if not rows:
            raise KeyError(name)
        return rows[0]

    @app.get("/v1/rules/{name}/versions/{version}/context", dependencies=[Depends(authorize)])
    def editor_context(name: str, version: int, request: Request, before_step: int = Query(ge=0, le=10)):
        store = request.app.state.store
        revisions = store.query('SELECT spec FROM rule_versions WHERE rule_name=%s AND version=%s', (name, version))
        if not revisions:
            raise KeyError(name)
        actions = revisions[0]['spec']['actions']
        if before_step > len(actions):
            raise HTTPException(422, 'Step is outside this rule version')
        # One coherent run, never a union of outputs from unrelated executions.
        # Redacted events cannot be used to reconstruct historical context.
        runs = store.query('''SELECT r.id,r.created_at,r.status,e.id AS event_id FROM runs r
            JOIN events e ON e.id=r.event_id WHERE r.rule_name=%s AND r.version=%s
            AND e.payload_redacted_at IS NULL ORDER BY r.created_at DESC,r.id DESC LIMIT 10''', (name, version))
        for run in runs:
            rows = store.query('''SELECT DISTINCT ON (step) step,detail->'result' AS result FROM run_log
                WHERE run_id=%s AND step<%s AND status IN ('pending','succeeded')
                AND detail->'result' IS NOT NULL ORDER BY step,id DESC''', (run['id'], before_step))
            if before_step and not rows:
                continue
            fields = []
            for row in rows:
                step = row['step']
                if not 0 <= step < min(before_step, len(actions)):
                    continue
                # Existing discovery applies the same sensitive-key/value protections,
                # 120-character previews, depth, field and visit budgets as event data.
                for field in discover([{'_steps': {str(step): row['result']}}], max_fields=100, breadth_first=True):
                    if field['path'] != '_steps':
                        fields.append({**field, 'step': step, 'action_type': actions[step]['type']})
            events = store.query('SELECT payload FROM events WHERE id=%s AND payload_redacted_at IS NULL', (run['event_id'],))
            if not events:
                continue
            payload = {k:v for k,v in events[0]['payload'].items() if k != '_steps'}
            return {'run_id': run['id'], 'created_at': run['created_at'], 'status': run['status'],
                    'version': version, 'before_step': before_step, 'fields': fields[:900],
                    'event_fields': discover([payload]), 'sample': True,
                    'notice': 'Historical example from one run of this exact version; not live values or an execution preview. No provider calls.'}
        return {'run_id': None, 'version': version, 'before_step': before_step,
                'fields': [], 'event_fields': [], 'sample': False,
                'notice': 'No retained prior outputs in the latest 10 runs of this version. No actions were executed.'}

    @app.post("/v1/rules/{name}/activate", dependencies=[Depends(authorize)])
    def activate(name: str, body: Activation, request: Request):
        rows = request.app.state.store.query("SELECT spec FROM rule_versions WHERE rule_name=%s AND version=%s", (name, body.version))
        if not rows:
            raise KeyError(name)
        try:
            request.app.state.settings.check_targets(Rule.model_validate(rows[0]["spec"]))
        except ValueError as exc:
            raise RuleError(str(exc)) from None
        return request.app.state.store.activate(name, body.version, body.sha256)

    @app.post("/v1/rules/{name}/disable", dependencies=[Depends(authorize)])
    def disable(name: str, request: Request):
        return request.app.state.store.disable(name)

    def sample(ids, request):
        rows = request.app.state.store.query("SELECT id,source,event_type,payload,payload_redacted_at FROM events WHERE id=ANY(%s)", (ids,))
        if len(rows) != len(set(ids)):
            raise KeyError("event")
        if any(row["payload_redacted_at"] is not None for row in rows):
            raise Conflict("Selected event payload has been redacted")
        return rows

    @app.post("/v1/evaluate", dependencies=[Depends(authorize)])
    def evaluate(body: Evaluation, request: Request):
        rule, digest = checked(body.yaml, request)
        rows = sample(body.event_ids, request)
        if body.payload is not None:
            rows.append({"id": None, "source": body.source, "event_type": body.event_type, "payload": body.payload})
        return {"sha256": digest, "executed": False, "results": [
            {"event_id": row["id"], "trace": match(rule, row["source"], row["event_type"], row["payload"])} for row in rows]}

    @app.post("/v1/compare", dependencies=[Depends(authorize)])
    def compare(body: Comparison, request: Request):
        left, ld = checked(body.left_yaml, request)
        right, rd = checked(body.right_yaml, request)
        results = []
        for event in sample(body.event_ids, request):
            a = match(left, event["source"], event["event_type"], event["payload"])
            b = match(right, event["source"], event["event_type"], event["payload"])
            results.append({"event_id": event["id"], "left": a, "right": b, "changed": a["matched"] != b["matched"]})
        return {"executed": False, "left_sha256": ld, "right_sha256": rd,
                "changed": sum(r["changed"] for r in results), "results": results}

    @app.post("/webhooks/jira", status_code=202)
    async def jira(request: Request):
        raw = await request.body()
        cfg = request.app.state.settings
        expected = "sha256=" + hmac.new(cfg.webhook_secret.encode(), raw, hashlib.sha256).hexdigest()
        if not cfg.webhook_secret or not hmac.compare_digest(expected.encode(), request.headers.get("X-Hub-Signature", "").encode()):
            raise HTTPException(401, "Invalid webhook signature")
        delivery = request.headers.get("X-Atlassian-Webhook-Identifier", "")
        if not delivery or len(delivery) > 250:
            raise HTTPException(400, "Stable webhook identifier required")
        try:
            payload = strict_json(raw)
            event_type = payload["webhookEvent"]
            if not isinstance(event_type, str) or not 1 <= len(event_type) <= 100:
                raise ValueError()
        except (ValueError, KeyError, TypeError, RecursionError):
            raise HTTPException(400, "Invalid Jira event") from None
        return await run_in_threadpool(request.app.state.store.ingest, cfg.tenant, "jira", delivery, event_type, payload, raw)

    @app.post("/v1/events", status_code=202, dependencies=[Depends(authorize)])
    async def ingest(request: Request):
        raw = await request.body()
        delivery = request.headers.get("Idempotency-Key", "")
        if not delivery or len(delivery) > 250:
            raise HTTPException(400, "Idempotency-Key required (max 250 characters)")
        try:
            body = strict_json(raw)
            if set(body) != {"source", "event_type", "payload"} or body["source"] not in {"jira", "test"}:
                raise ValueError()
            if not isinstance(body["payload"], dict) or not isinstance(body["event_type"], str) or not 1 <= len(body["event_type"]) <= 100:
                raise ValueError()
        except (ValueError, TypeError, RecursionError):
            raise HTTPException(400, "Expected source, event_type and object payload") from None
        return await run_in_threadpool(request.app.state.store.ingest, request.app.state.settings.tenant,
                                       body["source"], delivery, body["event_type"], body["payload"], raw)

    @app.get("/v1/events", dependencies=[Depends(authorize)])
    def events(request: Request, limit: int = Query(50, ge=1, le=100), source: str | None = Query(None, max_length=100), event_type: str | None = Query(None, max_length=100), before: UUID | None = None):
        return request.app.state.store.query("SELECT id,source,event_type,delivery_id,received_at,processed_at,attempts,last_error,payload#>>'{issue,key}' AS issue_key,payload#>>'{issue,fields,issuetype,name}' AS issue_type,left(payload#>>'{issue,fields,summary}',200) AS issue_summary FROM events WHERE (%s::text IS NULL OR source=%s) AND (%s::text IS NULL OR event_type=%s) AND (%s::uuid IS NULL OR (received_at,id)<(SELECT received_at,id FROM events WHERE id=%s)) ORDER BY received_at DESC,id DESC LIMIT %s", (source, source, event_type, event_type, before, before, limit))

    @app.get("/v1/events/{event_id}", dependencies=[Depends(authorize)])
    def event(event_id: UUID, request: Request):
        rows = request.app.state.store.query("SELECT id,source,event_type,payload,received_at,processed_at,payload_redacted_at,last_error FROM events WHERE id=%s", (event_id,))
        if not rows:
            raise KeyError(str(event_id))
        with request.app.state.store.pool.connection() as conn:
            blocked = exclusion_reason(conn, rows[0])
        return {**rows[0], 'execution_blocked_reason': blocked, "evaluations": request.app.state.store.query("SELECT * FROM evaluations WHERE event_id=%s", (event_id,))}

    @app.post("/v1/events/{event_id}/execute", dependencies=[Depends(authorize)])
    def execute(event_id: UUID, request: Request):
        return request.app.state.store.execute(event_id)

    @app.get("/v1/runs", dependencies=[Depends(authorize)])
    def runs(request: Request, limit: int = Query(50, ge=1, le=100), before: UUID | None = None):
        return request.app.state.store.query("SELECT id,event_id,rule_name,version,status,step,attempts,next_at,external_uuid,last_error,created_at,updated_at FROM runs WHERE (%s::uuid IS NULL OR (created_at,id)<(SELECT created_at,id FROM runs WHERE id=%s)) ORDER BY created_at DESC,id DESC LIMIT %s", (before, before, limit))

    @app.get("/v1/runs/{run_id}", dependencies=[Depends(authorize)])
    def run(run_id: UUID, request: Request):
        rows = request.app.state.store.query("SELECT * FROM runs WHERE id=%s", (run_id,))
        if not rows:
            raise KeyError(str(run_id))
        return {**rows[0], "log": request.app.state.store.query("SELECT * FROM run_log WHERE run_id=%s ORDER BY id", (run_id,))}

    @app.get("/v1/audit", dependencies=[Depends(authorize)])
    def audit(request: Request):
        return request.app.state.store.query("SELECT * FROM audit ORDER BY id DESC LIMIT 100")

    @app.post("/v1/runs/{run_id}/reconcile", dependencies=[Depends(authorize)])
    def reconcile(run_id: UUID, body: Reconciliation, request: Request):
        # Operator supplies the observed pipeline UUID. This never retries the POST.
        from app.worker import Bitbucket
        store = request.app.state.store
        rows = store.query("SELECT r.*,v.spec FROM runs r JOIN rule_versions v ON v.rule_name=r.rule_name AND v.version=r.version WHERE r.id=%s", (run_id,))
        if not rows:
            raise KeyError(str(run_id))
        run = rows[0]
        if run["status"] != "needs_review":
            raise Conflict("Only needs_review runs may be reconciled")
        action = Rule.model_validate(run["spec"]).actions[run["step"]]
        if action.type != "bitbucket.pipeline":
            raise HTTPException(422, "This reconciliation operation supports Bitbucket pipelines only")
        external_uuid = "{" + str(body.external_uuid) + "}"
        connector = Bitbucket(request.app.state.settings)
        try:
            response = connector.call("GET", action, external_uuid=external_uuid)
            if response.status_code != 200 or response.json().get("uuid") != external_uuid:
                raise HTTPException(422, "Pipeline UUID not verified in target repository")
        finally:
            connector.client.close()
        with store.pool.connection() as conn:
            updated = conn.execute("UPDATE runs SET status='waiting',external_uuid=%s,attempts=0,lease_until=NULL,next_at=now(),updated_at=now() WHERE id=%s AND status='needs_review' RETURNING id,status", (external_uuid, run_id)).fetchone()
            if not updated:
                raise Conflict("Run changed during reconciliation")
            store.audit(conn, "attach_existing_pipeline", str(run_id), {"external_uuid": external_uuid})
        return updated

    @app.get("/metrics", dependencies=[Depends(authorize)], response_class=PlainTextResponse)
    def metrics(request: Request):
        rows = request.app.state.store.query("SELECT status,count(*) count FROM runs GROUP BY status")
        gauges = request.app.state.store.query("SELECT (SELECT count(*) FROM worker_heartbeats WHERE last_seen>now()-interval '180 seconds') workers, COALESCE((SELECT extract(epoch FROM now()-min(created_at)) FROM runs WHERE status='pending'),0) oldest_pending")[0]
        return ("# TYPE automation_runs gauge\n" + "".join(f'automation_runs{{status="{r["status"]}"}} {r["count"]}\n' for r in rows)
                + "# TYPE automation_workers_healthy gauge\n" + f'automation_workers_healthy {gauges["workers"]}\n'
                + "# TYPE automation_oldest_pending_seconds gauge\n" + f'automation_oldest_pending_seconds {gauges["oldest_pending"]}\n')

    return app


app = create_app()
