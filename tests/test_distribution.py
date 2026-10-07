from pathlib import Path

import pytest
import yaml

from tools.export_source import export
from tools.e2e_live import parse_args, rule_yaml, run
from app.dsl import parse_rule


def test_live_runner_destinations_are_arguments_not_environment(monkeypatch):
    monkeypatch.setenv("E2E_BITBUCKET_REPOSITORY", "unexpected/repo")
    args = parse_args(["--provider", "bitbucket", "--output", "unused",
                       "--bitbucket-repository", "explicit/repo",
                       "--bitbucket-branch", "main", "--bitbucket-pipeline", "noop"])
    assert args.bitbucket_repository == "explicit/repo"
    assert not args.allow_remote_writes
    monkeypatch.setenv("E2E_ALLOW_WRITES", "yes")
    with pytest.raises(RuntimeError, match="--allow-remote-writes"):
        run(args)


def test_live_runner_uses_v1_connector_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("BITBUCKET_EMAIL", "legacy@example.com")
    monkeypatch.setenv("BITBUCKET_TOKEN", "legacy-token")
    args = parse_args(["--provider", "bitbucket", "--output", str(tmp_path / "evidence"),
                       "--allow-remote-writes", "--bitbucket-repository", "explicit/repo",
                       "--bitbucket-branch", "main", "--bitbucket-pipeline", "noop"])
    with pytest.raises(RuntimeError, match="BITBUCKET_ACCOUNT_EMAIL"):
        run(args)


@pytest.mark.parametrize("provider", ["jira", "bitbucket", "all"])
def test_live_runner_requires_explicit_destinations(provider):
    with pytest.raises(SystemExit) as exc:
        parse_args(["--provider", provider, "--output", "unused", "--allow-remote-writes"])
    assert exc.value.code == 2


def test_development_compose_uses_role_specific_healthchecks():
    root = Path(__file__).resolve().parents[1]
    services = yaml.safe_load((root / "compose.dev.yaml").read_text())["services"]
    assert services["worker"]["healthcheck"]["test"] == ["CMD", "python", "-m", "app.health", "worker"]
    assert services["migrate"]["healthcheck"]["disable"] is True


def test_live_runner_emits_alias_free_rules_for_shared_bindings():
    binding = {"path": "/_steps/0/key"}
    spec = {"apiVersion": "automation/v1", "kind": "Rule", "name": "test-shared",
            "trigger": {"source": "test", "event": "automation.e2e"},
            "when": {"path": "/test_id", "op": "exists"},
            "actions": [{"type": "jira.issue.get", "issue": binding},
                        {"type": "jira.issue.get", "issue": binding}]}
    rule, _ = parse_rule(rule_yaml(spec))
    assert len(rule.actions) == 2


def test_source_export_excludes_private_state(tmp_path):
    destination = tmp_path / "source"
    files = export(Path(__file__).resolve().parents[1], destination)
    assert "Dockerfile" in files and "tools/selftest.py" in files
    assert "app/migrations/001_initial.sql" in files
    assert "app/paths.py" in files and "tests/test_paths.py" in files
    assert "examples/literal-field-repository.yaml" in files
    assert not (destination / ".git").exists()
    assert not (destination / ".env").exists()
    assert not (destination / "tools/lab").exists()
    assert not (destination / "work").exists()
    assert not (destination / "secrets").exists()
    assert not (destination / "release-readiness.json").exists()
    assert not (destination / ".venv").exists()
    with pytest.raises(ValueError, match="already exists"):
        export(Path(__file__).resolve().parents[1], destination)
