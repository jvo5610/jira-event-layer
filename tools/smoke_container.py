"""Disposable Docker acceptance test; no personal credentials, cloud or provider calls.

Creates only uniquely named test containers/network and removes those in finally.
Checks fresh install, legacy adoption, restricted DB role, backup/restore, probes,
signed ingress, deduplication, private API, restart and graceful worker shutdown.
"""
import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time
import uuid

import httpx
import psycopg
from psycopg import sql
if __package__:
    from .init_installation import initialize
else:
    from init_installation import initialize


def command(*args, input=None, check=True):
    result = subprocess.run(args, input=input, capture_output=True, timeout=60)
    if check and result.returncode:
        # Arguments/output may contain credentials: deliberately do not include them.
        raise RuntimeError(f"Acceptance command failed: {args[0]} (exit {result.returncode})")
    return result


def eventually(check, seconds=35):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if check():
                return
        except (httpx.HTTPError, psycopg.OperationalError):
            pass
        time.sleep(.5)
    raise RuntimeError("Acceptance readiness deadline exceeded")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default="automation-api:1.0.0")
    args = parser.parse_args()
    name = "automation-smoke-" + uuid.uuid4().hex[:12]
    containers = []
    admin_token, hook_secret = secrets.token_urlsafe(40), secrets.token_urlsafe(40)
    db_password, runtime_password = secrets.token_urlsafe(40), secrets.token_urlsafe(40)
    passed = []
    command("docker", "network", "create", name)
    try:
        with tempfile.TemporaryDirectory(prefix="automation-smoke-") as temporary:
            def envfile(label, values):
                path = Path(temporary) / label
                with open(path, "x", opener=lambda p, f: os.open(p, f, 0o600)) as handle:
                    handle.write("\n".join(f"{k}={v}" for k, v in values.items()) + "\n")
                return str(path)

            def container(label, env, *command_args, image=None, port=None, hardened=True):
                target = name + "-" + label
                run = ["docker", "run", "-d", "--name", target, "--network", name,
                       "--env-file", envfile(label, env)]
                if hardened:
                    run += ["--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges:true",
                            "--tmpfs", "/tmp:size=16m,mode=1777", "--memory", "512m", "--pids-limit", "128"]
                if port:
                    run += ["-p", f"127.0.0.1::{port}"]
                containers.append(target)
                command(*run, image or args.image, *command_args)
                return target

            def port_of(target, port):
                return command("docker", "port", target, f"{port}/tcp").stdout.decode().strip().rsplit(":", 1)[1]

            db = container("db", {"POSTGRES_USER": "owner", "POSTGRES_PASSWORD": db_password,
                                  "POSTGRES_DB": "automation_test"}, image="postgres:17", port=5432, hardened=False)
            host = f"127.0.0.1:{port_of(db, 5432)}"
            owner_url = f"postgresql://owner:{db_password}@{host}/automation_test"
            owner_internal = f"postgresql://owner:{db_password}@{db}:5432/automation_test"
            runtime_internal = f"postgresql://runtime:{runtime_password}@{db}:5432/automation_test"

            def database_ready():
                with psycopg.connect(owner_url, connect_timeout=2) as conn:
                    return conn.execute("SELECT 1").fetchone()[0] == 1
            eventually(database_ready)

            def one_shot(label, values, *args):
                return command("docker", "run", "--rm", "--network", name, "--read-only",
                               "--cap-drop=ALL", "--security-opt=no-new-privileges:true",
                               "--env-file", envfile(label, values), "--no-healthcheck", parser_args_image,
                               *args)
            parser_args_image = args.image
            migration = {"DATABASE_URL": owner_internal, "TENANT_ID": "portable-smoke"}
            one_shot("migrate", migration, "python", "-m", "app.migrate")
            one_shot("migrate-again", migration, "python", "-m", "app.migrate")
            passed.append("fresh_install_and_idempotent_migrations")
            with psycopg.connect(owner_url, autocommit=True) as conn:
                conn.execute(sql.SQL("CREATE ROLE runtime LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE").format(sql.Literal(runtime_password)))
                conn.execute("GRANT USAGE ON SCHEMA public TO runtime")
                conn.execute("GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO runtime")
                conn.execute("GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO runtime")
                conn.execute("REVOKE INSERT,UPDATE,DELETE ON schema_migrations,installation FROM runtime")
                conn.execute("CREATE DATABASE automation_restore")
                conn.execute("CREATE DATABASE automation_legacy")
            with psycopg.connect(owner_url.replace("/automation_test", "/automation_legacy")) as conn:
                conn.execute(Path("app/migrations/001_initial.sql").read_text())
                conn.execute("INSERT INTO events(id,tenant,source,delivery_id,event_type,payload,raw_body,sha256) VALUES(%s,'portable-smoke','test','legacy','test','{}',%s,%s)",
                             (uuid.uuid4(), b"{}", hashlib.sha256(b"{}").hexdigest()))
            one_shot("migrate-legacy", {**migration, "DATABASE_URL": owner_internal.replace("/automation_test", "/automation_legacy")}, "python", "-m", "app.migrate")
            with psycopg.connect(owner_url.replace("/automation_test", "/automation_legacy")) as conn:
                assert conn.execute("SELECT count(*) FROM events WHERE delivery_id='legacy'").fetchone()[0] == 1
            passed.append("legacy_upgrade_preserves_events")

            common = {"TENANT_ID": "portable-smoke", "DATABASE_URL": runtime_internal}
            management = container("api", {**common, "API_SURFACE": "management", "ADMIN_TOKEN": admin_token,
                                           "ALLOWED_JIRA_PROJECTS": "DEMO"}, port=8080)
            ingress = container("webhooks", {**common, "API_SURFACE": "webhooks", "JIRA_WEBHOOK_SECRET": hook_secret}, port=8080)
            worker = container("worker", common, "python", "-m", "app.worker")
            admin_url = f"http://127.0.0.1:{port_of(management, 8080)}"
            ingress_url = f"http://127.0.0.1:{port_of(ingress, 8080)}"
            with httpx.Client(timeout=5, trust_env=False) as client:
                eventually(lambda: client.get(admin_url + "/readyz").status_code == 200)
                eventually(lambda: client.get(ingress_url + "/readyz").status_code == 200)
                eventually(lambda: command("docker", "exec", worker, "python", "-m", "app.health", "worker", check=False).returncode == 0)
                with psycopg.connect(f"postgresql://runtime:{runtime_password}@{host}/automation_test") as conn:
                    assert not conn.execute("SELECT has_schema_privilege(current_user,'public','CREATE')").fetchone()[0]
                passed.append("nonroot_readonly_runtime_without_ddl_permissions")
                assert client.get(admin_url + "/v1/schema").status_code == 401
                assert client.get(admin_url + "/openapi.json").status_code == 401
                auth = {"Authorization": "Bearer " + admin_token}
                assert client.get(admin_url + "/v1/schema", headers=auth).status_code == 200
                assert client.get(ingress_url + "/v1/schema", headers=auth).status_code == 404
                assert client.post(admin_url + "/webhooks/jira").status_code == 404
                passed.append("management_and_webhook_network_isolation")
                raw = b'{"webhookEvent":"jira:issue_updated","issue":{"key":"TEST-1"},"sample.data":[{"ok":true}]}'
                headers = {"X-Atlassian-Webhook-Identifier": "acceptance-event",
                           "X-Hub-Signature": "sha256=" + hmac.new(hook_secret.encode(), raw, hashlib.sha256).hexdigest()}
                assert client.post(ingress_url + "/webhooks/jira", content=raw).status_code == 401
                first = client.post(ingress_url + "/webhooks/jira", content=raw, headers=headers)
                assert first.status_code == 202 and not first.json()["duplicate"]
                assert client.post(ingress_url + "/webhooks/jira", content=raw, headers=headers).json()["duplicate"]
                command("docker", "restart", ingress)
                ingress_url = f"http://127.0.0.1:{port_of(ingress, 8080)}"
                eventually(lambda: client.get(ingress_url + "/readyz").status_code == 200)
                assert client.post(ingress_url + "/webhooks/jira", content=raw, headers=headers).json()["duplicate"]
                passed.append("signed_ingress_and_deduplication_survive_restart")
                # Exercise the shipped image's grammar, not just an in-process parser.
                text = '''apiVersion: automation/v1
kind: Rule
name: literal-path-acceptance
trigger: {source: jira, event: "jira:issue_updated"}
when: {path: '["sample.data"][0].ok', op: eq, value: true}
actions:
  - type: jira.issue.get
    issue: {path: issue.key}
'''
                pointer = text.replace('["sample.data"][0].ok', '/sample.data/0/ok')
                response = client.post(admin_url + "/v1/rules", content=text, headers=auth)
                assert response.status_code == 201
                revision = response.json()
                event_ids = [first.json()["event_id"]]
                evaluated = client.post(admin_url + "/v1/evaluate", headers=auth,
                                        json={"yaml": text, "event_ids": event_ids}).json()
                assert evaluated["executed"] is False and evaluated["results"][0]["trace"]["matched"]
                assert client.post(admin_url + "/v1/rules/validate", content=pointer, headers=auth).status_code == 422
                invalid = text.replace('["sample.data"][0].ok', 'sample[*]')
                assert client.post(admin_url + "/v1/rules/validate", content=invalid, headers=auth).status_code == 422
                command("docker", "restart", management)
                admin_url = f"http://127.0.0.1:{port_of(management, 8080)}"
                eventually(lambda: client.get(admin_url + "/readyz").status_code == 200)
                restored = client.get(admin_url + "/v1/rules/literal-path-acceptance/versions/1", headers=auth).json()
                assert restored["yaml"] == text and restored["sha256"] == revision["sha256"]
                assert client.get(admin_url + "/v1/runs", headers=auth).json() == []
                passed.append("v1_field_paths_persist_reject_json_pointer_and_survive_restart")
                backup = command("docker", "exec", db, "pg_dump", "-U", "owner", "-d", "automation_test", "-Fc").stdout
                command("docker", "exec", "-i", db, "pg_restore", "-U", "owner", "-d", "automation_restore", "--no-owner", "--no-privileges", input=backup)
                one_shot("verify-restore", {**migration, "DATABASE_URL": owner_internal.replace("/automation_test", "/automation_restore")}, "python", "-m", "app.migrate")
                with psycopg.connect(owner_url.replace("/automation_test", "/automation_restore")) as conn:
                    row = conn.execute("SELECT id,sha256 FROM events WHERE delivery_id='acceptance-event'").fetchone()
                    assert str(row[0]) == first.json()["event_id"] and row[1] == hashlib.sha256(raw).hexdigest()
                passed.append("backup_restore_preserves_event_identity_and_schema")
                command("docker", "stop", "--time", "20", worker)
                exit_code = command("docker", "inspect", "--format", "{{.State.ExitCode}}", worker).stdout.strip()
                assert exit_code == b"0"
                passed.append("worker_health_and_graceful_sigterm")

            # Exercise the shipped Compose file, not merely equivalent docker run commands.
            secret_directory = Path(temporary) / "secrets"
            initialize(secret_directory, runtime_internal, owner_internal)
            config = envfile("compose.env", {"TENANT_ID": "portable-smoke", "AUTOMATION_IMAGE": args.image,
                             "AUTOMATION_SECRETS_DIR": secret_directory, "ADMIN_PORT": "0", "WEBHOOK_PORT": "0"})
            override = Path(temporary) / "network.json"
            override.write_text(json.dumps({"networks": {"default": {"external": True, "name": name}}}))
            compose = ["docker", "compose", "--project-name", name + "-compose", "--env-file", config,
                       "-f", str(Path("compose.yaml").resolve()), "-f", str(override)]
            def compose_diagnostic():
                diagnostic = command(*compose, "ps", "-a", check=False).stdout.decode(errors="replace")
                diagnostic += command(*compose, "logs", "--no-color", "--tail", "20", check=False).stdout.decode(errors="replace")
                sensitive = [db_password, runtime_password, admin_token, hook_secret]
                sensitive += [p.read_text().strip() for p in secret_directory.iterdir()]
                for value in sensitive:
                    if value:
                        diagnostic = diagnostic.replace(value, "[redacted]")
                return diagnostic[-5000:]
            try:
                started = command(*compose, "up", "-d", "--no-build", check=False)
                if started.returncode:
                    diagnostic = started.stderr.decode(errors="replace")
                    diagnostic += command(*compose, "logs", "--no-color", "--tail", "20", check=False).stdout.decode(errors="replace")
                    sensitive = [db_password, runtime_password, admin_token, hook_secret]
                    sensitive += [p.read_text().strip() for p in secret_directory.iterdir()]
                    for value in sensitive:
                        if value:
                            diagnostic = diagnostic.replace(value, "[redacted]")
                    raise RuntimeError("Compose acceptance failed: " + diagnostic[-5000:])
                address = command(*compose, "port", "api", "8080").stdout.decode().strip()
                public_address = command(*compose, "port", "webhooks", "8080").stdout.decode().strip()
                with httpx.Client(timeout=5, trust_env=False) as client:
                    eventually(lambda: client.get("http://" + address + "/readyz").status_code == 200)
                    eventually(lambda: client.get("http://" + public_address + "/readyz").status_code == 200)
                    token = (secret_directory / "admin_token").read_text().strip()
                    assert client.get("http://" + address + "/v1/schema", headers={"Authorization": "Bearer " + token}).status_code == 200
                    assert client.get("http://" + public_address + "/v1/schema").status_code == 404
                    eventually(lambda: command(*compose, "exec", "-T", "worker", "python", "-m", "app.health", "worker", check=False).returncode == 0)
                passed.append("shipped_compose_installation_and_mounted_secrets")
            except RuntimeError as exc:
                raise RuntimeError(str(exc) + "\nCompose diagnostics:\n" + compose_diagnostic()) from None
            finally:
                command(*compose, "down", "--volumes", "--timeout", "20", check=False)
            print(json.dumps({"passed": passed, "provider_calls": 0, "image": args.image}, indent=2))
    finally:
        for target in reversed(containers):
            command("docker", "rm", "-f", "-v", target, check=False)
        command("docker", "network", "rm", name, check=False)


if __name__ == "__main__":
    main()
