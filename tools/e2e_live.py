"""Opt-in API -> PostgreSQL -> worker -> real provider acceptance tests.

Requires E2E_ALLOW_WRITES=yes and explicit provider credentials/targets. Creates
labelled test issues and/or runs a configured NO-OP pipeline. Never edits existing
issues or deletes remote evidence. No native webhook registration is changed.
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import tempfile
import time
import uuid

import httpx
import yaml

if __package__:
    from .smoke_container import command, eventually
else:
    from smoke_container import command, eventually


class DataOnlyDumper(yaml.SafeDumper):
    def ignore_aliases(self, data):
        # Shared Python bindings must not become YAML aliases (forbidden by DSL).
        return True


def rule_yaml(spec):
    return yaml.dump(spec, Dumper=DataOnlyDumper)


def required(name):
    value = os.getenv(name, "")
    if not value:
        raise RuntimeError(f"Set {name} explicitly for live E2E")
    return value


def execute_case(client, actions, label, marker, timeout=240):
    spec = {"apiVersion": "automation/v1", "kind": "Rule", "name": label,
            "trigger": {"source": "test", "event": "automation.e2e"},
            "when": {"path": "test_id", "op": "eq", "value": marker}, "actions": actions}
    saved = client.post("/v1/rules", content=rule_yaml(spec), headers={"Content-Type": "application/yaml"})
    saved.raise_for_status()
    version = saved.json()
    response = client.post(f"/v1/rules/{label}/activate", json={"version": version["version"], "sha256": version["sha256"]})
    response.raise_for_status()
    body = {"source": "test", "event_type": "automation.e2e", "payload": {"test_id": marker}}
    key = {"Idempotency-Key": marker}
    event = client.post("/v1/events", json=body, headers=key)
    event.raise_for_status()
    data = event.json()
    assert len(data["runs"]) == 1
    run_id = data["runs"][0]["id"]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get("/v1/runs/" + run_id)
        response.raise_for_status()
        run = response.json()
        if run["status"] in {"failed", "needs_review"}:
            raise RuntimeError(f"Live action stopped: {run['status']} ({run['last_error']}); never blindly retry")
        if run["status"] == "succeeded":
            break
        time.sleep(2)
    else:
        raise RuntimeError("Live E2E timeout; inspect provider before rerunning")
    duplicate = client.post("/v1/events", json=body, headers=key)
    duplicate.raise_for_status()
    assert duplicate.json()["duplicate"] and duplicate.json()["runs"][0]["id"] == run_id
    negative = client.post("/v1/events", headers={"Idempotency-Key": marker + "-negative"},
                           json={**body, "payload": {"test_id": "does-not-match"}})
    negative.raise_for_status()
    assert negative.json()["runs"] == []
    client.post(f"/v1/rules/{label}/disable").raise_for_status()
    return run


def run(args):
    if os.getenv("E2E_ALLOW_WRITES") != "yes":
        raise RuntimeError("Live E2E creates remote test resources. Set E2E_ALLOW_WRITES=yes explicitly")
    stamp = uuid.uuid4().hex[:12]
    name = "automation-live-" + stamp
    config = {"TENANT_ID": name, "ADMIN_TOKEN": secrets.token_urlsafe(48), "API_SURFACE": "management"}
    if args.provider in {"bitbucket", "all"}:
        for key in ("BITBUCKET_EMAIL", "BITBUCKET_TOKEN"):
            config[key] = required(key)
        repo = required("E2E_BITBUCKET_REPOSITORY")
        if repo.count("/") != 1:
            raise RuntimeError("E2E_BITBUCKET_REPOSITORY must be workspace/repository")
        config["ALLOWED_REPOSITORIES"] = repo
        pipeline = required("E2E_BITBUCKET_PIPELINE")
        branch = required("E2E_BITBUCKET_BRANCH")
    if args.provider in {"jira", "all"}:
        for key in ("JIRA_EMAIL", "JIRA_TOKEN", "JIRA_CLOUD_ID"):
            config[key] = required(key)
        project = required("E2E_JIRA_PROJECT")
        issue_type = required("E2E_JIRA_ISSUE_TYPE_ID")
        status = required("E2E_JIRA_TO_STATUS_ID")
        link_type = required("E2E_JIRA_LINK_TYPE_ID")
        config.update(ALLOWED_JIRA_PROJECTS=project, JIRA_REQUIRED_LABEL="automation-managed", JIRA_MAX_DAILY_WRITES="20")
    report = {"test_id": name, "candidate_image": args.image, "ingress": "authenticated API event, not a native Jira webhook",
              "provider_calls": "real", "cases": [], "remote_resources_deleted": False}
    evidence = Path(args.output)
    evidence.mkdir(parents=True, exist_ok=False, mode=0o700)
    containers = []
    command("docker", "network", "create", name)
    try:
        with tempfile.TemporaryDirectory(prefix=name) as temporary:
            def envfile(label, values):
                path = Path(temporary) / label
                with open(path, "x", opener=lambda p, flags: os.open(p, flags, 0o600)) as handle:
                    handle.write("\n".join(f"{k}={v}" for k, v in values.items()) + "\n")
                return str(path)
            db_password = secrets.token_urlsafe(40)
            db = name + "-db"
            containers.append(db)
            command("docker", "run", "-d", "--name", db, "--network", name,
                    "--env-file", envfile("db.env", {"POSTGRES_PASSWORD": db_password, "POSTGRES_DB": "automation_test"}), "postgres:17")
            eventually(lambda: command("docker", "exec", db, "pg_isready", "-U", "postgres", "-d", "automation_test", check=False).returncode == 0)
            config["DATABASE_URL"] = f"postgresql://postgres:{db_password}@{db}/automation_test"
            env_path = envfile("runtime.env", config)
            command("docker", "run", "--rm", "--network", name, "--env-file", env_path,
                    args.image, "python", "-m", "app.migrate")
            api, worker = name + "-api", name + "-worker"
            for target, worker_mode in [(api, False), (worker, True)]:
                containers.append(target)
                invocation = ["docker", "run", "-d", "--name", target, "--network", name,
                              "--env-file", env_path, "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges:true"]
                if not worker_mode:
                    invocation += ["-p", "127.0.0.1::8080"]
                command(*invocation, args.image, *(["python", "-m", "app.worker"] if worker_mode else []))
            port = command("docker", "port", api, "8080/tcp").stdout.decode().strip().rsplit(":", 1)[1]
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=15,
                              headers={"Authorization": "Bearer " + config["ADMIN_TOKEN"]}, trust_env=False) as client:
                eventually(lambda: client.get("/readyz").status_code == 200)
                if args.provider in {"bitbucket", "all"}:
                    workspace, repository = repo.split("/")
                    action = {"type": "bitbucket.pipeline", "workspace": workspace, "repository": repository,
                              "branch": branch, "pipeline": pipeline,
                              "variables": {key: {"value": value} for key, value in {
                                  "PROJECT_KEY": "E2E", "REPOSITORY_NAME": name, "DESCRIPTION": "Portable live E2E NO-OP",
                                  "LANGUAGE": "python", "DEFAULT_REVIEWERS": "", "AWS_ACCOUNT_ID": "", "JIRA_ISSUE_KEY": "E2E-1"}.items()}}
                    run = execute_case(client, [action], "live-bitbucket", stamp + "-bb")
                    with httpx.Client(timeout=20, auth=(config["BITBUCKET_EMAIL"], config["BITBUCKET_TOKEN"])) as provider:
                        observed = provider.get(f"https://api.bitbucket.org/2.0/repositories/{repo}/pipelines/{run['external_uuid']}")
                        observed.raise_for_status()
                        data = observed.json()
                    assert data["state"]["result"]["name"] == "SUCCESSFUL"
                    report["cases"].append({"provider": "bitbucket", "result": "passed", "run_id": run["id"],
                                           "pipeline_uuid": run["external_uuid"], "build_number": data["build_number"],
                                           "url": f"https://bitbucket.org/{repo}/pipelines/results/{data['build_number']}",
                                           "deduplication": "passed", "negative_filter": "passed"})
                    print("Real Bitbucket pipeline succeeded; duplicate and negative cases passed", flush=True)
                if args.provider in {"jira", "all"}:
                    source = {"path": '_steps["0"].key'}
                    flow_name = "prueba-ciclo-de-tickets"
                    fields = {"summary": {"value": "[Prueba de automatizacion] Ticket original"},
                              "description": {"value": "Created only by the portable E2E worker"},
                              "labels": {"value": ["automation-prueba"]}}
                    actions = [
                        {"type": "jira.issue.create", "project": project, "issue_type_id": issue_type, "fields": fields},
                        {"type": "jira.comment.add", "issue": source, "text": {"value": "Prueba del flujo: comentario publicado por el motor."}},
                        {"type": "jira.issue.edit", "issue": source, "fields": {"summary": {"value": "[Prueba de automatizacion] Ticket original actualizado"}}},
                        {"type": "jira.issue.transition", "issue": source, "to_status_id": status},
                        {"type": "jira.issue.clone", "source": source, "project": project, "issue_type_id": issue_type,
                         "copy_fields": ["summary", "description", "labels"], "fields": {"summary": {"value": "[Prueba de automatizacion] Copia del ticket original"}}},
                        {"type": "jira.issue.link", "inward": source, "outward": {"path": '_steps["4"].key'}, "link_type_id": link_type},
                        {"type": "jira.issue.get", "issue": source, "require": {"path": "fields.status.id", "op": "eq", "value": status}},
                        # Transition the clone only after checking the original's current status.
                        {"type": "jira.issue.transition", "issue": {"path": '_steps["4"].key'}, "to_status_id": status},
                        {"type": "jira.issue.transition", "issue": {"path": '_steps["4"].key'}, "to_status_id": status},
                        {"type": "jira.issue.get", "issue": {"path": '_steps["4"].key'},
                         "require": {"path": "fields.status.id", "op": "eq", "value": status}}
                    ]
                    run = execute_case(client, actions, flow_name, stamp + "-jira")
                    results = {str(row["step"]): row["detail"]["result"] for row in run["log"]
                               if row["detail"].get("result") is not None}
                    created, cloned = results["0"]["key"], results["4"]["key"]
                    with httpx.Client(base_url=f"https://api.atlassian.com/ex/jira/{config['JIRA_CLOUD_ID']}/rest/api/3/",
                                      auth=(config["JIRA_EMAIL"], config["JIRA_TOKEN"]), timeout=20) as provider:
                        original = provider.get("issue/" + created, params={"fields": "summary,status,labels,issuelinks", "properties": "automation.operation"})
                        original.raise_for_status()
                        current = original.json()["fields"]
                        comments = provider.get("issue/" + created + "/comment")
                        comments.raise_for_status()
                        copy = provider.get("issue/" + cloned, params={"fields": "summary,labels,description,status"})
                        copy.raise_for_status()
                    assert current["summary"] == "[Prueba de automatizacion] Ticket original actualizado"
                    assert current["status"]["id"] == status
                    expected_labels = {"automation-managed", "automation-prueba", "automation-flujo-" + flow_name}
                    assert set(current["labels"]) == set(copy.json()["fields"]["labels"]) == expected_labels
                    provenance = original.json()["properties"]["automation.operation"]
                    assert provenance["rule_name"] == flow_name and provenance["run_id"] == run["id"]
                    assert provenance["rule_version"] == run["version"] and provenance["step"] == 0
                    assert "comentario publicado por el motor" in json.dumps(comments.json()) and cloned in json.dumps(current["issuelinks"])
                    assert copy.json()["fields"]["summary"] == "[Prueba de automatizacion] Copia del ticket original"
                    assert copy.json()["fields"]["status"]["id"] == status
                    assert results["6"]["guard_matched"] and results["8"]["no_change"]
                    assert results["9"]["guard_matched"]
                    stopped = execute_case(client, [
                        {"type": "jira.issue.get", "issue": {"value": created},
                         "require": {"path": "fields.status.id", "op": "eq", "value": "never-matching-status"}},
                        {"type": "jira.comment.add", "issue": {"value": created},
                         "text": {"value": "SHOULD NOT EXECUTE"}}
                    ], "prueba-validar-condicion", stamp + "-guard")
                    stopped_results = [row["detail"]["result"] for row in stopped["log"] if row["detail"].get("result")]
                    assert len(stopped_results) == 1 and stopped_results[0]["stop"]
                    with httpx.Client(base_url=f"https://api.atlassian.com/ex/jira/{config['JIRA_CLOUD_ID']}/rest/api/3/",
                                      auth=(config["JIRA_EMAIL"], config["JIRA_TOKEN"]), timeout=20) as provider:
                        after = provider.get("issue/" + created + "/comment")
                        after.raise_for_status()
                    assert after.json()["total"] == comments.json()["total"]
                    assert "SHOULD NOT EXECUTE" not in json.dumps(after.json())
                    report["cases"].append({"provider": "jira", "result": "passed", "run_id": run["id"],
                                           "issues": [created, cloned], "actions": [a["type"] for a in actions],
                                           "deduplication": "passed", "negative_filter": "passed",
                                           "transition_based_on_other_issue": "passed", "already_in_status_noop": "passed",
                                           "false_guard_prevents_write": "passed", "guard_run_id": stopped["id"],
                                           "flow_labels_and_provenance": "passed"})
                    print("Real Jira actions verified with independent provider reads", flush=True)
            report["result"] = "passed"
            backup = command("docker", "exec", db, "pg_dump", "-U", "postgres", "-d", "automation_test", "-Fc").stdout
            (evidence / "database.dump").write_bytes(backup)
            (evidence / "result.json").write_text(json.dumps(report, indent=2) + "\n")
            print(json.dumps(report, indent=2))
    except Exception as exc:
        report["result"] = "failed"
        report["error_class"] = type(exc).__name__
        raise
    finally:
        # Preserve ambiguous/partial outcomes before removing disposable local services.
        if containers:
            dump = command("docker", "exec", containers[0], "pg_dump", "-U", "postgres", "-d", "automation_test", "-Fc", check=False)
            if dump.returncode == 0:
                (evidence / "database.dump").write_bytes(dump.stdout)
        (evidence / "result.json").write_text(json.dumps(report, indent=2) + "\n")
        for target in reversed(containers):
            command("docker", "rm", "-f", "-v", target, check=False)
        command("docker", "network", "rm", name, check=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=["bitbucket", "jira", "all"], required=True)
    parser.add_argument("--image", default="automation-api:0.2.0")
    parser.add_argument("--output", required=True, help="New private evidence directory, never an existing directory")
    run(parser.parse_args())
