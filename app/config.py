import os
import re
from dataclasses import dataclass, field
from pathlib import Path


def secret(name, *, required=False):
    """Environment or mounted file, never both. Errors never include secret values."""
    value, filename = os.getenv(name), os.getenv(name + "_FILE")
    if value is not None and filename is not None:
        raise RuntimeError(f"Set only {name} or {name}_FILE")
    if filename is not None:
        try:
            with Path(filename).open("rb") as handle:
                raw = handle.read(16385)
            if len(raw) > 16384:
                raise ValueError()
            value = raw.decode("utf-8").rstrip("\r\n")
        except (OSError, UnicodeError, ValueError):
            raise RuntimeError(f"Unable to read bounded UTF-8 secret {name}_FILE") from None
    if required and not value:
        raise RuntimeError(f"{name} or {name}_FILE is required")
    return value or ""


def integer(name, default, minimum, maximum):
    try:
        value = int(os.getenv(name, str(default)))
        if not minimum <= value <= maximum:
            raise ValueError()
        return value
    except ValueError:
        raise RuntimeError(f"{name} must be an integer between {minimum} and {maximum}") from None


def csv(name):
    return tuple(filter(None, (v.strip() for v in os.getenv(name, "").split(","))))


def tenant_id():
    value = os.getenv("TENANT_ID", "")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,99}", value):
        raise RuntimeError("TENANT_ID is required (1-100 letters, digits, '.', '_' or '-')")
    return value


@dataclass
class Settings:
    database_url: str = field(repr=False)
    admin_token: str = field(repr=False)
    webhook_secret: str = field(repr=False)
    bitbucket_email: str = ""
    bitbucket_token: str = field(default="", repr=False)
    allowed_repositories: tuple[str, ...] = ()
    tenant: str = "default"
    max_body: int = 1048576
    jira_email: str = ""
    jira_token: str = field(default="", repr=False)
    jira_cloud_id: str = ""
    allowed_jira_projects: tuple[str, ...] = ()
    allowed_jira_fields: tuple[str, ...] = ("summary", "description", "labels", "priority", "assignee", "duedate")
    jira_required_label: str = "automation-managed"
    jira_max_daily_writes: int = 100
    reader_token: str = field(default="", repr=False)
    writer_token: str = field(default="", repr=False)
    operator_token: str = field(default="", repr=False)
    api_surface: str = "all"
    jira_ignored_actor_ids: tuple[str, ...] = ()
    bitbucket_max_daily_writes: int = 100

    @classmethod
    def from_env(cls, mode="api"):
        surface = os.getenv("API_SURFACE", "all")
        if surface not in {"all", "management", "webhooks"}:
            raise RuntimeError("API_SURFACE must be all, management or webhooks")
        settings = cls(secret("DATABASE_URL", required=True),
                       secret("ADMIN_TOKEN", required=mode == "api" and surface != "webhooks"),
                       secret("JIRA_WEBHOOK_SECRET", required=mode == "api" and surface != "management"),
                       os.getenv("BITBUCKET_EMAIL", ""), secret("BITBUCKET_TOKEN"), csv("ALLOWED_REPOSITORIES"),
                       tenant=tenant_id(), api_surface=surface)
        for name in ("reader_token", "writer_token", "operator_token"):
            setattr(settings, name, secret(name.upper()))
        tokens = [settings.admin_token, settings.reader_token, settings.writer_token, settings.operator_token]
        present = [v for v in tokens if v]
        if any(len(v) < 32 or not v.isascii() for v in present + ([settings.webhook_secret] if settings.webhook_secret else [])):
            raise RuntimeError("API and webhook secrets must contain at least 32 ASCII characters")
        if len(set(present)) != len(present) or (settings.webhook_secret and settings.webhook_secret in present):
            raise RuntimeError("API roles and webhook must use distinct secrets")
        settings.max_body = integer("MAX_BODY_BYTES", 1048576, 1024, 10485760)
        settings.jira_email = os.getenv("JIRA_EMAIL", "")
        settings.jira_token = secret("JIRA_TOKEN")
        settings.jira_cloud_id = os.getenv("JIRA_CLOUD_ID", "")
        settings.allowed_jira_projects = csv("ALLOWED_JIRA_PROJECTS")
        settings.allowed_jira_fields = tuple(filter(None, os.getenv("ALLOWED_JIRA_FIELDS", ",".join(settings.allowed_jira_fields)).split(",")))
        settings.jira_required_label = os.getenv("JIRA_REQUIRED_LABEL", "automation-managed")
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", settings.jira_required_label):
            raise RuntimeError("JIRA_REQUIRED_LABEL must be a nonempty safe label")
        if settings.jira_required_label.startswith("automation-flujo-"):
            raise RuntimeError("JIRA_REQUIRED_LABEL cannot use the reserved flow-label namespace")
        settings.jira_max_daily_writes = integer("JIRA_MAX_DAILY_WRITES", 100, 1, 100000)
        settings.bitbucket_max_daily_writes = integer("BITBUCKET_MAX_DAILY_WRITES", 100, 1, 100000)
        settings.jira_ignored_actor_ids = csv("JIRA_IGNORED_ACTOR_IDS")
        return settings

    def check_targets(self, rule):
        for action in rule.actions:
            if action.type == "bitbucket.pipeline":
                if f"{action.workspace}/{action.repository}" not in self.allowed_repositories:
                    raise ValueError("Action target is not in connector allowlist")
            else:
                if not self.allowed_jira_projects:
                    raise ValueError("Jira connector disabled: project allowlist is empty")
                if hasattr(action, "project") and action.project not in self.allowed_jira_projects:
                    raise ValueError("Jira project not in connector allowlist")
                fields = set(getattr(action, "fields", {})) | set(getattr(action, "copy_fields", []))
                if not fields <= set(self.allowed_jira_fields):
                    raise ValueError("Jira field not in connector allowlist")
