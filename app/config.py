import os
import re
from io import StringIO
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID
from dotenv.parser import parse_stream
from psycopg.conninfo import conninfo_to_dict


LEGACY_CONNECTOR_ENV = (
    "JIRA_EMAIL",
    "JIRA_TOKEN",
    "JIRA_TOKEN_FILE",
    "BITBUCKET_EMAIL",
    "BITBUCKET_TOKEN",
    "BITBUCKET_TOKEN_FILE",
)


def load_environment():
    """Load only an explicitly selected ENV_FILE; process variables win per key."""
    filename = os.getenv("ENV_FILE")
    if filename is None:
        return
    try:
        if not filename.strip():
            raise ValueError()
        with Path(filename).open("rb") as handle:
            raw = handle.read(1048577)
        if len(raw) > 1048576:
            raise ValueError()
        values = {}
        # Parse bindings without interpolation; reject malformed lines atomically.
        for binding in parse_stream(StringIO(raw.decode("utf-8-sig"))):
            if binding.error:
                raise ValueError()
            if binding.key is None:
                continue  # Blank line or comment.
            if (not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", binding.key)
                    or binding.value is None or "\x00" in binding.value):
                raise ValueError()
            values[binding.key] = binding.value
    except (OSError, UnicodeError, ValueError):
        raise RuntimeError("Unable to load ENV_FILE as a bounded UTF-8 dotenv file") from None
    for key, value in values.items():
        # Never chain another file through the file's own contents.
        if key != "ENV_FILE":
            os.environ.setdefault(key, value)


def reject_legacy_connector_env():
    configured = [name for name in LEGACY_CONNECTOR_ENV if name in os.environ]
    if configured:
        raise RuntimeError(
            "Legacy connector environment variables are not supported in v1: "
            + ", ".join(configured)
        )


def secret(name, *, required=False):
    """Direct environment value takes precedence over a mounted file (even if empty)."""
    value, filename = os.getenv(name), os.getenv(name + "_FILE")
    if value is None and filename is not None:
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


def database_config(prefix=""):
    """Explicit database parameters; URL-based deployment configuration is not supported."""
    load_environment()
    if any(prefix + name in os.environ for name in ("DATABASE_URL", "DATABASE_URL_FILE")):
        raise RuntimeError("DATABASE_URL and DATABASE_URL_FILE are not supported; use DB_HOST, DB_PORT, DB_NAME, DB_USER and DB_PASSWORD or DB_PASSWORD_FILE")
    values = {}
    for name, parameter in (("DB_HOST", "host"), ("DB_NAME", "dbname"), ("DB_USER", "user")):
        name = prefix + name
        value = os.getenv(name, "")
        if not value or value != value.strip() or any(ord(c) < 32 for c in value):
            raise RuntimeError(f"{name} is required and must be nonempty without surrounding whitespace or control characters")
        values[parameter] = value
    values["port"] = integer(prefix + "DB_PORT", 5432, 1, 65535)
    values["password"] = secret(prefix + "DB_PASSWORD", required=True)
    return values


def database_options(database):
    # Internal test/administration helpers may pass libpq conninfo. Environment
    # entrypoints always use database_config() and pass named driver parameters.
    return conninfo_to_dict(database) if isinstance(database, str) else dict(database)


def tenant_id():
    value = os.getenv("TENANT_ID", "")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,99}", value):
        raise RuntimeError("TENANT_ID is required (1-100 letters, digits, '.', '_' or '-')")
    return value


def jira_configured(settings):
    return bool(getattr(settings, "jira_token", "") or
                (getattr(settings, "jira_client_id", "") and getattr(settings, "jira_client_secret", "")))


@dataclass
class Settings:
    database: dict = field(repr=False)
    admin_bootstrap_token: str | None = field(repr=False)
    webhook_secret: str = field(repr=False)
    bitbucket_token: str = field(default="", repr=False)
    tenant: str = "default"
    max_body: int = 10485760
    jira_token: str = field(default="", repr=False)
    jira_cloud_id: str = ""
    jira_client_id: str = field(default="", repr=False)
    jira_client_secret: str = field(default="", repr=False)
    jira_max_daily_writes: int = 100
    bitbucket_max_daily_writes: int = 100
    auth_rotation_ttl: int = 3600

    @classmethod
    def from_env(cls, mode="api"):
        load_environment()
        reject_legacy_connector_env()
        obsolete = [name for base in ("API_TOKEN", "ADMIN_TOKEN", "READER_TOKEN", "WRITER_TOKEN", "OPERATOR_TOKEN")
                    for name in (base, base + "_FILE") if name in os.environ]
        if obsolete:
            raise RuntimeError("Legacy API tokens are not supported; use ADMIN_BOOTSTRAP_TOKEN or ADMIN_BOOTSTRAP_TOKEN_FILE: " + ", ".join(obsolete))
        if mode not in {"api", "worker"}:
            raise ValueError("Unknown service entrypoint")
        settings = cls(database_config(),
                       None,  # Read the bootstrap secret only if the database has never been initialized.
                       secret("JIRA_WEBHOOK_SECRET", required=mode == "api"),
                       bitbucket_token=secret("BITBUCKET_API_TOKEN"),
                       tenant=tenant_id())
        if settings.webhook_secret and (len(settings.webhook_secret) < 32 or not settings.webhook_secret.isascii()):
            raise RuntimeError("Webhook secret must contain at least 32 ASCII characters")
        settings.auth_rotation_ttl = integer("AUTH_ROTATION_TTL_SECONDS", 3600, 60, 86400)
        settings.max_body = integer("MAX_BODY_BYTES", 10485760, 1024, 10485760)
        settings.jira_token = secret("JIRA_API_TOKEN")
        settings.jira_cloud_id = os.getenv("JIRA_CLOUD_ID", "")
        settings.jira_client_id = secret("JIRA_CLIENT_ID")
        settings.jira_client_secret = secret("JIRA_CLIENT_SECRET")
        if bool(settings.jira_client_id) != bool(settings.jira_client_secret):
            raise RuntimeError("JIRA_CLIENT_ID and JIRA_CLIENT_SECRET must be configured together")
        if settings.jira_client_id:
            if settings.jira_token:
                raise RuntimeError("Choose Jira OAuth credentials or JIRA_API_TOKEN, not both")
            try:
                settings.jira_cloud_id = str(UUID(settings.jira_cloud_id))
            except ValueError:
                raise RuntimeError("JIRA_CLOUD_ID must be a valid UUID when Jira OAuth is configured") from None
        settings.jira_max_daily_writes = integer("JIRA_MAX_DAILY_WRITES", 100, 1, 100000)
        settings.bitbucket_max_daily_writes = integer("BITBUCKET_MAX_DAILY_WRITES", 100, 1, 100000)
        return settings
