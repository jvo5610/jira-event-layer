import hashlib
import hmac
import json
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field
from psycopg import OperationalError
from psycopg_pool import PoolTimeout
from starlette.concurrency import run_in_threadpool

from app.config import Settings
from app.dsl import Rule, RuleError, match, parse_rule
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


def create_app(settings=None, store=None):
    @asynccontextmanager
    async def lifespan(app):
        app.state.settings = settings or Settings.from_env()
        app.state.store = store or Store(app.state.settings.database_url, app.state.settings.tenant)
        await run_in_threadpool(app.state.store.open)
        try:
            yield
        finally:
            await run_in_threadpool(app.state.store.close)

    app = FastAPI(title="Automation API", version="0.2.0", lifespan=lifespan,
                  openapi_url=None, docs_url=None, redoc_url=None,
                  description="Single-tenant deterministic automation engine. Separate API, workers and PostgreSQL; no cloud dependency.")
    bearer = HTTPBearer(auto_error=False)

    def admin(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        cfg = request.app.state.settings
        role = None
        for candidate in ("admin", "reader", "writer", "operator"):
            token = getattr(cfg, candidate + "_token")
            if token and credentials and hmac.compare_digest(credentials.credentials.encode(), token.encode()):
                role = candidate
        if role is None:
            raise HTTPException(401, "Valid API bearer token required", headers={"WWW-Authenticate": "Bearer"})
        path = request.scope["route"].path
        readonly = request.method == "GET" or path in {"/v1/evaluate", "/v1/compare", "/v1/rules/validate"}
        permitted = (role == "admin" or readonly or (role == "writer" and path == "/v1/rules")
                     or (role == "operator" and path in {"/v1/rules/{name}/activate", "/v1/rules/{name}/disable",
                         "/v1/events", "/v1/events/{event_id}/execute", "/v1/runs/{run_id}/reconcile"}))
        if not permitted:
            raise HTTPException(403, "Credential does not permit this operation")

    @app.middleware("http")
    async def bounds(request, call_next):
        cfg = request.app.state.settings
        path = request.url.path
        management = path.startswith("/v1/") or path in {"/metrics", "/openapi.json", "/docs", "/redoc"}
        if ((cfg.api_surface == "webhooks" and management)
                or (cfg.api_surface == "management" and path.startswith("/webhooks/"))):
            return JSONResponse({"detail": "Not found"}, status_code=404)
        # Set audit attribution in the async context before sync handlers are dispatched.
        credential = request.headers.get("Authorization", "")
        actor = "anonymous"
        for role in ("admin", "reader", "writer", "operator"):
            token = getattr(cfg, role + "_token")
            if token and hmac.compare_digest(credential.encode(), ("Bearer " + token).encode()):
                actor = role
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

    @app.get("/openapi.json", dependencies=[Depends(admin)], include_in_schema=False)
    def openapi():
        return app.openapi()

    def checked(text, request):
        rule, digest = parse_rule(text)
        try:
            request.app.state.settings.check_targets(rule)
        except ValueError as exc:
            raise RuleError(str(exc)) from None
        return rule, digest

    @app.get("/v1/schema", dependencies=[Depends(admin)])
    def schema():
        return Rule.model_json_schema()

    @app.post("/v1/rules/validate", dependencies=[Depends(admin)], openapi_extra=YAML_BODY)
    async def validate_yaml(request: Request):
        try:
            text = (await request.body()).decode("utf-8")
        except UnicodeDecodeError:
            raise RuleError("YAML must be UTF-8") from None
        rule, digest = checked(text, request)
        return {"valid": True, "sha256": digest, "spec": rule.model_dump()}

    @app.post("/v1/rules", status_code=201, dependencies=[Depends(admin)], openapi_extra=YAML_BODY)
    async def save_yaml(request: Request):
        try:
            text = (await request.body()).decode("utf-8")
        except UnicodeDecodeError:
            raise RuleError("YAML must be UTF-8") from None
        rule, digest = checked(text, request)
        return await run_in_threadpool(request.app.state.store.save_rule, rule, digest, text)

    @app.get("/v1/rules", dependencies=[Depends(admin)])
    def rules(request: Request):
        return request.app.state.store.query("SELECT r.name,r.active_version,max(v.version) latest_version FROM rules r JOIN rule_versions v ON v.rule_name=r.name GROUP BY r.name ORDER BY r.name LIMIT 1000")

    @app.get("/v1/rules/{name}/versions", dependencies=[Depends(admin)])
    def versions(name: str, request: Request):
        return request.app.state.store.query("SELECT rule_name,version,sha256,created_at FROM rule_versions WHERE rule_name=%s ORDER BY version DESC LIMIT 100", (name,))

    @app.get("/v1/rules/{name}/versions/{version}", dependencies=[Depends(admin)])
    def version(name: str, version: int, request: Request):
        rows = request.app.state.store.query("SELECT * FROM rule_versions WHERE rule_name=%s AND version=%s", (name, version))
        if not rows:
            raise KeyError(name)
        return rows[0]

    @app.post("/v1/rules/{name}/activate", dependencies=[Depends(admin)])
    def activate(name: str, body: Activation, request: Request):
        rows = request.app.state.store.query("SELECT spec FROM rule_versions WHERE rule_name=%s AND version=%s", (name, body.version))
        if not rows:
            raise KeyError(name)
        try:
            request.app.state.settings.check_targets(Rule.model_validate(rows[0]["spec"]))
        except ValueError as exc:
            raise RuleError(str(exc)) from None
        return request.app.state.store.activate(name, body.version, body.sha256)

    @app.post("/v1/rules/{name}/disable", dependencies=[Depends(admin)])
    def disable(name: str, request: Request):
        return request.app.state.store.disable(name)

    def sample(ids, request):
        rows = request.app.state.store.query("SELECT id,source,event_type,payload,payload_redacted_at FROM events WHERE id=ANY(%s)", (ids,))
        if len(rows) != len(set(ids)):
            raise KeyError("event")
        if any(row["payload_redacted_at"] is not None for row in rows):
            raise Conflict("Selected event payload has been redacted")
        return rows

    @app.post("/v1/evaluate", dependencies=[Depends(admin)])
    def evaluate(body: Evaluation, request: Request):
        rule, digest = checked(body.yaml, request)
        rows = sample(body.event_ids, request)
        if body.payload is not None:
            rows.append({"id": None, "source": body.source, "event_type": body.event_type, "payload": body.payload})
        return {"sha256": digest, "executed": False, "results": [
            {"event_id": row["id"], "trace": match(rule, row["source"], row["event_type"], row["payload"])} for row in rows]}

    @app.post("/v1/compare", dependencies=[Depends(admin)])
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
        user = payload.get("user")
        suppress = isinstance(user, dict) and user.get("accountId") in cfg.jira_ignored_actor_ids
        return await run_in_threadpool(request.app.state.store.ingest, cfg.tenant, "jira", delivery, event_type, payload, raw, suppress)

    @app.post("/v1/events", status_code=202, dependencies=[Depends(admin)])
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

    @app.get("/v1/events", dependencies=[Depends(admin)])
    def events(request: Request, limit: int = Query(50, ge=1, le=100)):
        return request.app.state.store.query("SELECT id,source,event_type,delivery_id,received_at,processed_at,attempts,last_error FROM events ORDER BY received_at DESC LIMIT %s", (limit,))

    @app.get("/v1/events/{event_id}", dependencies=[Depends(admin)])
    def event(event_id: UUID, request: Request):
        rows = request.app.state.store.query("SELECT id,source,event_type,payload,received_at,processed_at,payload_redacted_at FROM events WHERE id=%s", (event_id,))
        if not rows:
            raise KeyError(str(event_id))
        return {**rows[0], "evaluations": request.app.state.store.query("SELECT * FROM evaluations WHERE event_id=%s", (event_id,))}

    @app.post("/v1/events/{event_id}/execute", dependencies=[Depends(admin)])
    def execute(event_id: UUID, request: Request):
        return request.app.state.store.execute(event_id)

    @app.get("/v1/runs", dependencies=[Depends(admin)])
    def runs(request: Request, limit: int = Query(50, ge=1, le=100)):
        return request.app.state.store.query("SELECT * FROM runs ORDER BY created_at DESC LIMIT %s", (limit,))

    @app.get("/v1/runs/{run_id}", dependencies=[Depends(admin)])
    def run(run_id: UUID, request: Request):
        rows = request.app.state.store.query("SELECT * FROM runs WHERE id=%s", (run_id,))
        if not rows:
            raise KeyError(str(run_id))
        return {**rows[0], "log": request.app.state.store.query("SELECT * FROM run_log WHERE run_id=%s ORDER BY id", (run_id,))}

    @app.get("/v1/audit", dependencies=[Depends(admin)])
    def audit(request: Request):
        return request.app.state.store.query("SELECT * FROM audit ORDER BY id DESC LIMIT 100")

    @app.post("/v1/runs/{run_id}/reconcile", dependencies=[Depends(admin)])
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

    @app.get("/metrics", dependencies=[Depends(admin)], response_class=PlainTextResponse)
    def metrics(request: Request):
        rows = request.app.state.store.query("SELECT status,count(*) count FROM runs GROUP BY status")
        gauges = request.app.state.store.query("SELECT (SELECT count(*) FROM worker_heartbeats WHERE last_seen>now()-interval '180 seconds') workers, COALESCE((SELECT extract(epoch FROM now()-min(created_at)) FROM runs WHERE status='pending'),0) oldest_pending")[0]
        return ("# TYPE automation_runs gauge\n" + "".join(f'automation_runs{{status="{r["status"]}"}} {r["count"]}\n' for r in rows)
                + "# TYPE automation_workers_healthy gauge\n" + f'automation_workers_healthy {gauges["workers"]}\n'
                + "# TYPE automation_oldest_pending_seconds gauge\n" + f'automation_oldest_pending_seconds {gauges["oldest_pending"]}\n')

    return app


app = create_app()
