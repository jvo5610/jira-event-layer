"""Mounted dotenv support without databases, network calls or operator secrets."""
import os

import pytest

from app.config import Settings, allowed_jira_projects_from_env, database_config, load_environment


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    monkeypatch.setattr(os, "environ", {})


def test_no_implicit_dotenv(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("DB_PASSWORD=not-loaded\n")
    monkeypatch.chdir(tmp_path)
    load_environment()
    assert "DB_PASSWORD" not in os.environ


@pytest.mark.parametrize("mode", ["api", "worker"])
def test_mounted_file_configures_runtime(mode, tmp_path, monkeypatch):
    path = tmp_path / "mounted.env"
    path.write_text('TENANT_ID=jira-relay-dev\nDB_HOST=postgres\nDB_NAME=relay\n'
                    'DB_USER=runtime\nDB_PASSWORD="literal${VALUE}@$()+=#"\n'
                    'JIRA_WEBHOOK_SECRET=' + 'w' * 40 + '\n')
    monkeypatch.setenv("ENV_FILE", str(path))
    cfg = Settings.from_env(mode)
    assert cfg.tenant == "jira-relay-dev"
    assert cfg.database == {"host": "postgres", "port": 5432, "dbname": "relay",
                            "user": "runtime", "password": "literal${VALUE}@$()+=#"}
    assert cfg.webhook_secret == "w" * 40


def test_database_entrypoints_load_file_and_direct_values_win(tmp_path, monkeypatch):
    path = tmp_path / "mounted.env"
    path.write_text("DB_HOST=from-file\nDB_NAME=relay\nDB_USER=runtime\n"
                    "DB_PASSWORD=from-file\nDB_PORT=5433\n")
    monkeypatch.setenv("ENV_FILE", str(path))
    monkeypatch.setenv("DB_HOST", "from-process")
    monkeypatch.setenv("DB_PASSWORD", "direct-secret")
    assert database_config() == {"host": "from-process", "port": 5433, "dbname": "relay",
                                 "user": "runtime", "password": "direct-secret"}
    monkeypatch.setenv("DB_PASSWORD", "")
    with pytest.raises(RuntimeError, match="DB_PASSWORD"):
        database_config()


def test_individual_secret_file_still_supported(tmp_path, monkeypatch):
    secret_path = tmp_path / "password"
    secret_path.write_text("secret-from-volume\n")
    path = tmp_path / "mounted.env"
    path.write_text(f"DB_HOST=postgres\nDB_NAME=relay\nDB_USER=runtime\nDB_PASSWORD_FILE={secret_path}\n")
    monkeypatch.setenv("ENV_FILE", str(path))
    assert database_config()["password"] == "secret-from-volume"


@pytest.mark.parametrize("content", [b"\xff", b"A" * 1048577, b"BAD-KEY=secret\n", b"BARE_KEY\n", b"SECRET=a\x00b\n", b'VALID=ok\nSECRET="unterminated\n'])
def test_invalid_files_fail_without_disclosing_contents(content, tmp_path, monkeypatch):
    path = tmp_path / "mounted.env"
    path.write_bytes(content)
    monkeypatch.setenv("ENV_FILE", str(path))
    with pytest.raises(RuntimeError, match="^Unable to load ENV_FILE as a bounded UTF-8 dotenv file$"):
        load_environment()
    assert "VALID" not in os.environ


@pytest.mark.parametrize("filename", ["", "/nonexistent/env-file"])
def test_explicit_file_must_be_readable(filename, monkeypatch):
    monkeypatch.setenv("ENV_FILE", filename)
    with pytest.raises(RuntimeError, match="Unable to load ENV_FILE"):
        load_environment()


def test_dotenv_quotes_comments_bom_and_empty_values(tmp_path, monkeypatch):
    path = tmp_path / "mounted.env"
    path.write_text("\ufeff# comment\nexport DB_HOST='postgres'\nEMPTY=\nQUOTED=\"line1\\nline2\"\n")
    monkeypatch.setenv("ENV_FILE", str(path))
    load_environment()
    assert os.environ["DB_HOST"] == "postgres"
    assert os.environ["EMPTY"] == ""
    assert os.environ["QUOTED"] == "line1\nline2"


@pytest.mark.parametrize("value", [None, "", " ", "*", "123", "ops", "OPS,", "OPS,,SRE", "OP S", "OPS\nSRE", "A" * 32])
def test_jira_allowlist_is_required_and_validated(value, monkeypatch):
    if value is not None:
        monkeypatch.setenv("ALLOWED_JIRA_PROJECTS", value)
    with pytest.raises(RuntimeError, match="ALLOWED_JIRA_PROJECTS"):
        allowed_jira_projects_from_env(required=True)


@pytest.mark.parametrize("mode", ["api", "worker"])
@pytest.mark.parametrize("oauth", [False, True])
def test_jira_credentials_require_allowlist(mode, oauth, monkeypatch):
    values = {"TENANT_ID": "test", "DB_HOST": "postgres", "DB_NAME": "relay",
              "DB_USER": "runtime", "DB_PASSWORD": "test-only", "JIRA_WEBHOOK_SECRET": "w" * 40}
    if oauth:
        values.update(JIRA_CLIENT_ID="client-test", JIRA_CLIENT_SECRET="secret-test",
                      JIRA_CLOUD_ID="11111111-1111-4111-8111-111111111111")
    else:
        values["JIRA_API_TOKEN"] = "token-test"
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    for invalid in (None, "", "*", "OPS,"):
        monkeypatch.delenv("ALLOWED_JIRA_PROJECTS", raising=False)
        if invalid is not None:
            monkeypatch.setenv("ALLOWED_JIRA_PROJECTS", invalid)
        with pytest.raises(RuntimeError, match="ALLOWED_JIRA_PROJECTS"):
            Settings.from_env(mode)
    monkeypatch.setenv("ALLOWED_JIRA_PROJECTS", " OPS, SRE,OPS ")
    assert Settings.from_env(mode).allowed_jira_projects == frozenset({"OPS", "SRE"})


def test_allowlist_from_env_file_and_direct_value_wins(tmp_path, monkeypatch):
    path = tmp_path / "mounted.env"
    path.write_text("ALLOWED_JIRA_PROJECTS=OPS,SRE\n")
    monkeypatch.setenv("ENV_FILE", str(path))
    load_environment()
    assert allowed_jira_projects_from_env(required=True) == frozenset({"OPS", "SRE"})
    monkeypatch.setenv("ALLOWED_JIRA_PROJECTS", "OTHER")
    load_environment()
    assert allowed_jira_projects_from_env(required=True) == frozenset({"OTHER"})
