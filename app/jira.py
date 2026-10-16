"""Bounded Jira REST v3 adapter. No arbitrary URL, scripts, bulk writes or blind retries."""
import json
import re
import threading
import time
from copy import deepcopy
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.dsl import MISSING, RuleError, evaluate, lookup
from app.transport import bounded_request

FLOW_LABEL_PREFIX = "automation-flujo-"


class ExecutionIdentity(BaseModel):
    """Trusted worker metadata from the immutable revision, never event bindings."""
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    rule_name: str = Field(pattern=r"^[a-z][a-z0-9-]{2,79}$")
    rule_version: int = Field(ge=1)
    rule_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    run_id: UUID
    step: int = Field(ge=0, le=9)

    @property
    def operation_id(self):
        return f"{self.run_id}:{self.step}"

    @property
    def flow_label(self):
        return FLOW_LABEL_PREFIX + self.rule_name

    def provenance(self):
        return {"schema_version": 1, "id": self.operation_id, **self.model_dump(mode="json")}


class PreflightRetry(Exception):
    """A read failed before any remote mutation. Retrying is safe."""
    def __init__(self, delay=30):
        self.delay = delay


class AmbiguousWrite(Exception):
    pass


class WriteRateLimited(Exception):
    def __init__(self, delay):
        self.delay = delay


def retry_delay(response):
    try:
        return max(1, min(int(response.headers.get("Retry-After", "30")), 3600))
    except ValueError:
        return 30


def bind(binding, payload):
    from app.dsl import resolve_binding
    value = resolve_binding(binding, payload)
    if value is MISSING:
        value = getattr(binding, "default", None)
        if value is None:
            raise RuleError("jira_binding_missing")
    if len(json.dumps(value, allow_nan=False).encode()) > 16000:
        raise RuleError("jira_binding_too_large")
    return value


def adf(text):
    if not isinstance(text, str) or not text.strip() or len(text) > 8000:
        raise RuleError("jira_text_invalid")
    return {"type": "doc", "version": 1, "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": text}]}]}


def append_description(original, text):
    paragraph = adf(text)['content'][0]
    if original is None:
        document = {'type':'doc','version':1,'content':[]}
    elif isinstance(original, str):
        document = adf(original) if original.strip() else {'type':'doc','version':1,'content':[]}
    elif (isinstance(original, dict) and original.get('type') == 'doc'
          and original.get('version') == 1 and isinstance(original.get('content'), list)):
        document = deepcopy(original)
    else:
        raise RuleError('jira_description_invalid')
    document['content'].append(paragraph)
    return document


class Jira:
    def __init__(self, settings, client=None):
        self.settings = settings
        self.client = client or httpx.Client(timeout=20, follow_redirects=False)
        self._token_lock = threading.Lock()
        self._access_token = ""
        self._token_expires_at = 0.0
        self._oauth_credential = None

    def access_token(self):
        s = self.settings
        if not s.jira_client_id and not s.jira_client_secret:
            if not s.jira_token:
                raise RuleError("jira_credentials_missing")
            return s.jira_token
        if not s.jira_client_id or not s.jira_client_secret:
            raise RuleError("jira_oauth_credentials_incomplete")
        from app.actors import credential_key
        credential = credential_key(s)
        with self._token_lock:
            if (self._access_token and self._oauth_credential == credential
                    and time.monotonic() < self._token_expires_at):
                return self._access_token
            started = time.monotonic()
            try:
                response = bounded_request(self.client, "POST", "https://auth.atlassian.com/oauth/token",
                    max_bytes=32768, data={"grant_type": "client_credentials",
                        "client_id": s.jira_client_id, "client_secret": s.jira_client_secret},
                    headers={"Accept": "application/json"}, follow_redirects=False)
            except httpx.HTTPError:
                raise PreflightRetry() from None
            if response.status_code == 429 or response.status_code >= 500:
                raise PreflightRetry(retry_delay(response))
            if response.status_code != 200:
                raise RuleError(f"jira_oauth_http_{response.status_code}")
            try:
                data = response.json()
                token, expires = data["access_token"], data["expires_in"]
                if (not isinstance(token, str) or not 1 <= len(token) <= 16384
                        or any(ord(c) < 33 or ord(c) > 126 for c in token)
                        or type(expires) is not int or not 1 <= expires <= 86400
                        or str(data.get("token_type", "")).lower() != "bearer"):
                    raise ValueError()
            except (ValueError, KeyError, TypeError):
                raise RuleError("jira_oauth_invalid_response") from None
            self._access_token = token
            self._oauth_credential = credential
            self._token_expires_at = started + expires - min(60, expires / 10)
            return token

    def request(self, method, path, body=None):
        s = self.settings
        try:
            cloud_id = str(UUID(s.jira_cloud_id))
        except ValueError:
            raise RuleError("jira_cloud_id_invalid") from None
        # OAuth and scoped API tokens use the fixed gateway; rules cannot change the host.
        url = f"https://api.atlassian.com/ex/jira/{cloud_id}/rest/api/3/{path}"
        token = self.access_token()
        response = bounded_request(self.client, method, url, json=body,
            headers={"Accept": "application/json", "Authorization": f"Bearer {token}"}, follow_redirects=False)
        if response.status_code == 401 and s.jira_client_id:
            with self._token_lock:
                if self._access_token == token:
                    self._access_token = ""
            # A read can safely refresh once. Never automatically replay a mutation.
            if method == "GET":
                response = bounded_request(self.client, method, url, json=body,
                    headers={"Accept": "application/json", "Authorization": f"Bearer {self.access_token()}"}, follow_redirects=False)
        return response

    def read(self, path):
        try:
            response = self.request("GET", path)
        except httpx.HTTPError:
            raise PreflightRetry() from None
        if response.status_code == 429 or response.status_code >= 500:
            raise PreflightRetry(retry_delay(response))
        if response.status_code != 200:
            raise RuleError(f"jira_preflight_http_{response.status_code}")
        if len(response.content) > 256000:
            raise RuleError("jira_response_too_large")
        try:
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError()
            return data
        except ValueError:
            raise RuleError("jira_invalid_read_response") from None

    def issue_key(self, value):
        if not isinstance(value, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{1,30}-[1-9][0-9]*", value):
            raise RuleError("jira_issue_key_invalid")
        return value

    def service_account(self):
        from app.actors import account_id
        profile = self.read('myself')
        identifier = account_id(profile.get('accountId'))
        if identifier is None or profile.get('active') is False:
            raise RuleError('jira_service_identity_invalid')
        return identifier

    def issue(self, value):
        key = self.issue_key(value)
        issue = self.read(f"issue/{key}?fields=*all")
        actual = issue.get("fields", {})
        if issue.get("key") != key or actual.get("project", {}).get("key") != key.rsplit("-", 1)[0]:
            raise RuleError("jira_issue_scope_mismatch")
        return issue

    def fields(self, bindings, payload):
        fields = {key: bind(binding, payload) for key, binding in bindings.items()}
        self.validate_fields(fields)
        return fields

    def validate_fields(self, fields):
        if "summary" in fields and (not isinstance(fields["summary"], str) or not 1 <= len(fields["summary"].strip()) <= 255):
            raise RuleError("jira_summary_invalid")
        if isinstance(fields.get("description"), str):
            fields["description"] = adf(fields["description"])
        if "labels" in fields:
            labels = fields["labels"]
            if not isinstance(labels, list) or len(labels) > 30 or any(not isinstance(x, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", x) for x in labels):
                raise RuleError("jira_labels_invalid")
        if len(json.dumps(fields, allow_nan=False).encode()) > 32000:
            raise RuleError("jira_fields_too_large")

    def write(self, method, path, body, expected, reserve):
        # Persist a budget reservation BEFORE the network call. Ambiguous outcomes consume it.
        reserve()
        try:
            response = self.request(method, path, body)
        except httpx.HTTPError:
            raise AmbiguousWrite("jira_ambiguous_network_error") from None
        if response.status_code == 429:
            raise WriteRateLimited(retry_delay(response))
        if response.status_code >= 500 or response.status_code in {408, 409}:
            raise AmbiguousWrite(f"jira_ambiguous_http_{response.status_code}")
        if response.status_code != expected:
            # Do not expose provider bodies: they can contain private issue content.
            raise RuleError(f"jira_write_http_{response.status_code}")
        return response

    @staticmethod
    def identifier(response, name):
        try:
            value = response.json()[name]
            if not isinstance(value, str) or not value or len(value) > 100:
                raise ValueError()
            return value
        except (ValueError, KeyError, TypeError):
            raise AmbiguousWrite("jira_invalid_write_response") from None

    def execute(self, action, payload, identity: ExecutionIdentity, reserve):
        if not isinstance(identity, ExecutionIdentity):
            raise RuleError("jira_execution_identity_required")
        operation_id = identity.operation_id
        kind = action.type
        if kind in {"jira.issue.create", "jira.issue.clone"}:
            fields = {}
            if kind == "jira.issue.clone":
                source = self.issue(bind(action.source, payload))
                fields = {k: source["fields"][k] for k in action.copy_fields if k in source["fields"]}
                if "labels" in fields:
                    # A clone belongs to its creating flow, not its source's flow.
                    fields["labels"] = [x for x in fields["labels"] if not x.startswith(FLOW_LABEL_PREFIX)]
            supplied = self.fields(action.fields, payload)
            if any(x.startswith(FLOW_LABEL_PREFIX) for x in supplied.get("labels", [])):
                raise RuleError("jira_flow_labels_reserved")
            fields.update(supplied)
            fields["labels"] = sorted(set(fields.get("labels", [])) | {identity.flow_label})
            self.validate_fields(fields)
            if not fields.get("summary"):
                raise RuleError("jira_summary_required")
            fields.update(project={"key": action.project}, issuetype={"id": action.issue_type_id})
            if action.parent is not None:
                parent = self.issue(bind(action.parent, payload))
                if parent["fields"]["project"]["key"] != action.project:
                    raise RuleError("jira_parent_project_mismatch")
                fields["parent"] = {"key": parent["key"]}
            body = {"fields": fields, "properties": [{"key": "automation.operation", "value": identity.provenance()}]}
            response = self.write("POST", "issue", body, 201, reserve)
            key = self.identifier(response, "key")
            if not re.fullmatch(re.escape(action.project) + r"-[1-9][0-9]*", key):
                raise AmbiguousWrite("jira_created_issue_scope_mismatch")
            return {"key": key, "operation_id": operation_id}

        if kind == "jira.issue.link":
            inward = self.issue(bind(action.inward, payload))["key"]
            outward = self.issue(bind(action.outward, payload))["key"]
            if inward == outward:
                raise RuleError("jira_self_link_not_allowed")
            self.write("POST", "issueLink", {"type": {"id": action.link_type_id},
                       "inwardIssue": {"key": inward}, "outwardIssue": {"key": outward}}, 201, reserve)
            return {"inward": inward, "outward": outward, "link_type_id": action.link_type_id}

        issue = self.issue(bind(action.issue, payload))
        key = issue["key"]
        if kind == "jira.issue.get":
            if action.require is not None and not evaluate(action.require, issue)["matched"]:
                return {"key": key, "guard_matched": False, "stop": True}
            return {"key": key, "fields": issue["fields"], "guard_matched": True}
        if kind == "jira.issue.edit":
            fields = self.fields(action.fields, payload)
            if action.append_description is not None:
                if 'description' not in issue['fields']:
                    raise RuleError('jira_description_missing')
                fields['description'] = append_description(issue['fields']['description'], bind(action.append_description, payload))
                self.validate_fields(fields)
            self.check_protected_labels(fields, issue)
            if all(issue["fields"].get(k, MISSING) == v for k, v in fields.items()):
                return {"key": key, "no_change": True}
            self.write("PUT", f"issue/{key}", {"fields": fields}, 204, reserve)
            return {"key": key, "updated_fields": sorted(fields)}
        if kind == "jira.comment.add":
            body = {"body": adf(bind(action.text, payload)),
                    "properties": [{"key": "automation.operation", "value": identity.provenance()}]}
            response = self.write("POST", f"issue/{key}/comment", body, 201, reserve)
            return {"key": key, "comment_id": self.identifier(response, "id"), "operation_id": operation_id}
        if kind == "jira.issue.transition":
            status = issue["fields"].get("status", {}).get("id")
            if status == action.to_status_id:
                if action.fields:
                    raise RuleError("jira_already_in_status_with_fields")
                return {"key": key, "status_id": status, "no_change": True}
            if action.from_status_id is not None and status != action.from_status_id:
                raise RuleError("jira_source_status_changed")
            available = self.read(f"issue/{key}/transitions").get("transitions", [])
            matching = [t for t in available if t.get("to", {}).get("id") == action.to_status_id]
            if len(matching) != 1 or not re.fullmatch(r"[0-9]+", str(matching[0].get("id", ""))):
                raise RuleError("jira_transition_missing_or_ambiguous")
            fields = self.fields(action.fields, payload)
            self.check_protected_labels(fields, issue)
            body = {"transition": {"id": matching[0]["id"]}, "fields": fields}
            self.write("POST", f"issue/{key}/transitions", body, 204, reserve)
            return {"key": key, "status_id": action.to_status_id}
        raise RuleError("jira_action_not_supported")

    def check_protected_labels(self, fields, issue):
        if "labels" not in fields:
            return
        existing = {x for x in issue["fields"].get("labels", []) if x.startswith(FLOW_LABEL_PREFIX)}
        requested = {x for x in fields["labels"] if x.startswith(FLOW_LABEL_PREFIX)}
        if requested != existing:
            raise RuleError("jira_flow_labels_cannot_be_changed")
