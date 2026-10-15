"""Disposable Docker acceptance test; no personal credentials, cloud or provider calls.

Creates only uniquely named test containers/network and removes those in finally.
Checks fresh install, unmanaged-schema rejection, restricted DB role, backup/restore, probes,
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
    admin_bootstrap_token, hook_secret = secrets.token_urlsafe(40), secrets.token_urlsafe(40)
    active_api_token = admin_bootstrap_token
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
            owner_database = {"host": "127.0.0.1", "port": port_of(db, 5432), "dbname": "automation_test",
                              "user": "owner", "password": db_password}

            def database_ready():
                with psycopg.connect(**owner_database, connect_timeout=2) as conn:
                    return conn.execute("SELECT 1").fetchone()[0] == 1
            eventually(database_ready)

            def one_shot(label, values, *args):
                return command("docker", "run", "--rm", "--network", name, "--read-only",
                               "--cap-drop=ALL", "--security-opt=no-new-privileges:true",
                               "--env-file", envfile(label, values), "--no-healthcheck", parser_args_image,
                               *args)
            parser_args_image = args.image
            migration = {"DB_HOST": db, "DB_PORT": "5432", "DB_NAME": "automation_test", "DB_USER": "owner", "DB_PASSWORD": db_password, "TENANT_ID": "portable-smoke"}
            one_shot("migrate", migration, "python", "-m", "app.migrate")
            one_shot("migrate-again", migration, "python", "-m", "app.migrate")
            passed.append("fresh_install_and_idempotent_migrations")
            with psycopg.connect(**owner_database, autocommit=True) as conn:
                conn.execute(sql.SQL("CREATE ROLE runtime LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE").format(sql.Literal(runtime_password)))
                conn.execute("GRANT USAGE ON SCHEMA public TO runtime")
                conn.execute("GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO runtime")
                conn.execute("GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO runtime")
                conn.execute("REVOKE INSERT,UPDATE,DELETE ON schema_migrations,installation FROM runtime")
                conn.execute("REVOKE UPDATE,DELETE ON auth_bootstrap_state FROM runtime")
                conn.execute("REVOKE UPDATE,DELETE ON service_actors,service_actor_credentials FROM runtime")
                conn.execute("CREATE DATABASE automation_restore")
                conn.execute("CREATE DATABASE automation_legacy")
            with psycopg.connect(**{**owner_database, "dbname": "automation_legacy"}) as conn:
                conn.execute(Path("app/migrations/001_initial.sql").read_text())
                conn.execute("INSERT INTO events(id,tenant,source,delivery_id,event_type,payload,raw_body,sha256) VALUES(%s,'portable-smoke','test','legacy','test','{}',%s,%s)",
                             (uuid.uuid4(), b"{}", hashlib.sha256(b"{}").hexdigest()))
            rejected = command("docker", "run", "--rm", "--network", name,
                               "--env-file", envfile("unmanaged", {**migration, "DB_NAME": "automation_legacy"}),
                               "--no-healthcheck", args.image, "python", "-m", "app.migrate", check=False)
            assert rejected.returncode != 0 and b"automatic adoption is not supported" in rejected.stderr
            with psycopg.connect(**{**owner_database, "dbname": "automation_legacy"}) as conn:
                assert conn.execute("SELECT count(*) FROM events WHERE delivery_id='legacy'").fetchone()[0] == 1
            passed.append("unmanaged_schema_rejected_without_data_loss")

            common = {**migration, "DB_USER": "runtime", "DB_PASSWORD": runtime_password}
            management = container("api", {**common, "ADMIN_BOOTSTRAP_TOKEN": admin_bootstrap_token,
                                          "JIRA_WEBHOOK_SECRET": hook_secret}, port=8080)
            ingress = management
            worker = container("worker", common, "python", "-m", "app.worker")
            admin_url = f"http://127.0.0.1:{port_of(management, 8080)}"
            ingress_url = f"http://127.0.0.1:{port_of(ingress, 8080)}"
            with httpx.Client(timeout=5, trust_env=False) as client:
                eventually(lambda: client.get(admin_url + "/readyz").status_code == 200)
                eventually(lambda: client.get(ingress_url + "/readyz").status_code == 200)
                eventually(lambda: command("docker", "exec", worker, "python", "-m", "app.health", "worker", check=False).returncode == 0)
                with psycopg.connect(**{**owner_database, "user": "runtime", "password": runtime_password}) as conn:
                    assert not conn.execute("SELECT has_schema_privilege(current_user,'public','CREATE')").fetchone()[0]
                    assert not conn.execute("SELECT has_table_privilege(current_user,'auth_bootstrap_state','DELETE')").fetchone()[0]
                    assert not conn.execute("SELECT has_table_privilege(current_user,'auth_bootstrap_state','UPDATE')").fetchone()[0]
                passed.append("nonroot_readonly_runtime_without_ddl_permissions")
                assert client.get(admin_url + "/v1/schema").status_code == 401
                assert client.get(admin_url + "/openapi.json").status_code == 401
                auth = {"Authorization": "Bearer " + admin_bootstrap_token}
                assert client.get(admin_url + "/v1/schema", headers=auth).status_code == 200
                assert client.get(ingress_url + "/v1/schema", headers=auth).status_code == 200
                assert client.post(admin_url + "/webhooks/jira", headers=auth).status_code == 401
                passed.append("unified_api_with_independent_bearer_and_hmac_auth")
                raw = b'{"webhookEvent":"jira:issue_updated","user":{"accountId":"human-fixture"},"issue":{"key":"TEST-1"},"sample.data":[{"ok":true}]}'
                headers = {"X-Atlassian-Webhook-Identifier": "acceptance-event",
                           "X-Hub-Signature": "sha256=" + hmac.new(hook_secret.encode(), raw, hashlib.sha256).hexdigest()}
                assert client.post(ingress_url + "/webhooks/jira", content=raw).status_code == 401
                first = client.post(ingress_url + "/webhooks/jira", content=raw, headers=headers)
                assert first.status_code == 202 and not first.json()["duplicate"]
                assert client.post(ingress_url + "/webhooks/jira", content=raw, headers=headers).json()["duplicate"]
                # Registry is shared across independent API/worker processes.
                with psycopg.connect(**{**owner_database, 'user': 'runtime', 'password': runtime_password}) as conn:
                    conn.execute("INSERT INTO service_actors(provider,account_id) VALUES('jira','relay-service-smoke')")
                    assert not conn.execute("SELECT has_table_privilege(current_user,'service_actors','DELETE')").fetchone()[0]
                    assert not conn.execute("SELECT has_table_privilege(current_user,'service_actor_credentials','UPDATE')").fetchone()[0]
                own = {'webhookEvent': 'jira:issue_created', 'user': {'accountId': 'relay-service-smoke'}, 'issue': {'key': 'TEST-2'}}
                own_raw = json.dumps(own).encode()
                own_headers = {'X-Atlassian-Webhook-Identifier': 'self-created',
                    'X-Hub-Signature': 'sha256=' + hmac.new(hook_secret.encode(), own_raw, hashlib.sha256).hexdigest()}
                own_event = client.post(ingress_url + '/webhooks/jira', content=own_raw, headers=own_headers).json()
                assert own_event['ignored_reason'] == 'ignored_service_actor' and own_event['runs'] == []
                assert client.post(admin_url + '/v1/events/' + own_event['event_id'] + '/execute', headers=auth).status_code == 409
                through_api = client.post(admin_url + '/v1/events', headers={**auth, 'Idempotency-Key': 'self-api'},
                    json={'source': 'jira', 'event_type': 'comment_created', 'payload': own}).json()
                assert through_api['ignored_reason'] == 'ignored_service_actor'
                no_actor = client.post(admin_url + '/v1/events', headers={**auth, 'Idempotency-Key': 'unknown-actor'},
                    json={'source': 'jira', 'event_type': 'jira:issue_created', 'payload': {}}).json()
                assert no_actor['ignored_reason'] == 'held_actor_unknown'
                assert client.post(admin_url + '/v1/events/' + no_actor['event_id'] + '/execute', headers=auth).status_code == 409
                passed.append('global_service_actor_exclusion_and_unknown_actor_fail_closed')
                command("docker", "restart", ingress)
                ingress_url = f"http://127.0.0.1:{port_of(ingress, 8080)}"
                admin_url = ingress_url
                eventually(lambda: client.get(ingress_url + "/readyz").status_code == 200)
                assert client.post(ingress_url + "/webhooks/jira", content=raw, headers=headers).json()["duplicate"]
                assert client.post(ingress_url + '/webhooks/jira', content=own_raw, headers=own_headers).json()['ignored_reason'] == 'ignored_service_actor'
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
                one_shot("verify-restore", {**migration, "DB_NAME": "automation_restore"}, "python", "-m", "app.migrate")
                with psycopg.connect(**{**owner_database, "dbname": "automation_restore"}) as conn:
                    row = conn.execute("SELECT id,sha256 FROM events WHERE delivery_id='acceptance-event'").fetchone()
                    assert str(row[0]) == first.json()["event_id"] and row[1] == hashlib.sha256(raw).hexdigest()
                passed.append("backup_restore_preserves_event_identity_and_schema")
                command("docker", "stop", "--time", "20", worker)
                exit_code = command("docker", "inspect", "--format", "{{.State.ExitCode}}", worker).stdout.strip()
                assert exit_code == b"0"
                passed.append("worker_health_and_graceful_sigterm")

                pending = client.post(admin_url + "/v1/auth/tokens/self/rotate", headers=auth)
                assert pending.status_code == 200
                replacement = pending.json()
                with psycopg.connect(**owner_database) as conn:
                    conn.execute("UPDATE auth_tokens SET pending_until=now()-interval '1 second' WHERE id=%s", (replacement["id"],))
                assert client.get(admin_url + "/v1/schema", headers={"Authorization": "Bearer " + replacement["token"]}).status_code == 401
                assert client.get(admin_url + "/v1/schema", headers=auth).status_code == 200
                replacement = client.post(admin_url + "/v1/auth/tokens/self/rotate", headers=auth).json()
                new_auth = {"Authorization": "Bearer " + replacement["token"]}
                assert client.post(admin_url + "/v1/auth/tokens/self/confirm", headers=new_auth).status_code == 200
                assert client.get(admin_url + "/v1/schema", headers=auth).status_code == 401
                command("docker", "restart", "--time", "20", management)
                admin_url = f"http://127.0.0.1:{port_of(management, 8080)}"
                eventually(lambda: client.get(admin_url + "/readyz").status_code == 200)
                assert client.get(admin_url + "/v1/schema", headers=auth).status_code == 401
                assert client.get(admin_url + "/v1/schema", headers=new_auth).status_code == 200
                delegated = client.post(admin_url + "/v1/auth/tokens", headers=new_auth,
                    json={"name": "docker-readonly", "permissions": ["api:read"], "expires_in_seconds": 3600}).json()
                delegated_auth = {"Authorization": "Bearer " + delegated["token"]}
                assert client.get(admin_url + "/v1/schema", headers=delegated_auth).status_code == 200
                assert client.get(admin_url + "/v1/auth/tokens", headers=delegated_auth).status_code == 403
                assert client.post(admin_url + "/v1/auth/tokens/" + delegated["id"] + "/revoke", headers=new_auth).status_code == 200
                assert client.get(admin_url + "/v1/schema", headers=delegated_auth).status_code == 401
                active_api_token = replacement["token"]
                passed.append("db_tokens_rotation_expiry_revocation_and_no_bootstrap_resurrection")

            # Exercise the shipped Compose file, not merely equivalent docker run commands.
            secret_directory = Path(temporary) / "secrets"
            initialize(secret_directory, runtime_password, db_password)
            config = envfile("compose.env", {"TENANT_ID": "portable-smoke", "AUTOMATION_IMAGE": args.image,
                             "AUTOMATION_SECRETS_DIR": secret_directory, "API_PORT": "0",
                             "DB_HOST": db, "DB_PORT": "5432", "DB_NAME": "automation_test", "DB_USER": "runtime", "DB_MIGRATION_USER": "owner"})
            override = Path(temporary) / "network.json"
            override.write_text(json.dumps({"networks": {"default": {"external": True, "name": name}}}))
            compose = ["docker", "compose", "--project-name", name + "-compose", "--env-file", config,
                       "-f", str(Path("compose.yaml").resolve()), "-f", str(override)]
            def compose_diagnostic():
                diagnostic = command(*compose, "ps", "-a", check=False).stdout.decode(errors="replace")
                diagnostic += command(*compose, "logs", "--no-color", "--tail", "20", check=False).stdout.decode(errors="replace")
                sensitive = [db_password, runtime_password, admin_bootstrap_token, active_api_token, hook_secret]
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
                    sensitive = [db_password, runtime_password, admin_bootstrap_token, active_api_token, hook_secret]
                    sensitive += [p.read_text().strip() for p in secret_directory.iterdir()]
                    for value in sensitive:
                        if value:
                            diagnostic = diagnostic.replace(value, "[redacted]")
                    raise RuntimeError("Compose acceptance failed: " + diagnostic[-5000:])
                address = command(*compose, "port", "api", "8080").stdout.decode().strip()
                public_address = address
                with httpx.Client(timeout=5, trust_env=False) as client:
                    eventually(lambda: client.get("http://" + address + "/readyz").status_code == 200)
                    eventually(lambda: client.get("http://" + public_address + "/readyz").status_code == 200)
                    token = active_api_token  # Persisted rotated identity; the new bootstrap env is ignored.
                    assert client.get("http://" + address + "/v1/schema", headers={"Authorization": "Bearer " + token}).status_code == 200
                    assert client.get("http://" + public_address + "/v1/schema").status_code == 401
                    assert client.post("http://" + address + "/webhooks/jira").status_code == 401
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
