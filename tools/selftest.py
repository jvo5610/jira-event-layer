"""Run the complete provider-free suite from a fresh checkout with disposable PostgreSQL.

Prerequisites: Python 3.12+, uv, Docker Engine and Compose v2. No .env or cloud
credentials are read. --full also builds the image and tests its shipped Compose.
"""
import argparse
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time
import uuid


def run(args, *, env=None, quiet=False, check=True):
    result = subprocess.run(args, env=env, capture_output=True if quiet else False, timeout=600)
    if check and result.returncode:
        raise RuntimeError(f"Verification command failed ({args[0]}, exit {result.returncode})")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    name = "automation-selftest-" + uuid.uuid4().hex[:12]
    password = secrets.token_urlsafe(40)
    image = "automation-api:selftest-" + uuid.uuid4().hex[:12]
    with tempfile.TemporaryDirectory(prefix=name) as directory:
        config = Path(directory) / "postgres.env"
        with open(config, "x", opener=lambda p, f: os.open(p, f, 0o600)) as stream:
            stream.write(f"POSTGRES_USER=automation\nPOSTGRES_PASSWORD={password}\nPOSTGRES_DB=automation_test\n")
        try:
            run(["docker", "run", "-d", "--name", name, "--env-file", str(config),
                 "-p", "127.0.0.1::5432", "postgres:17"], quiet=True)
            deadline = time.monotonic() + 40
            while time.monotonic() < deadline:
                if run(["docker", "exec", name, "pg_isready", "-U", "automation", "-d", "automation_test"], quiet=True, check=False).returncode == 0:
                    break
                time.sleep(.5)
            else:
                raise RuntimeError("Disposable PostgreSQL did not become ready")
            port = run(["docker", "port", name, "5432/tcp"], quiet=True).stdout.decode().strip().rsplit(":", 1)[1]
            env = {k: v for k, v in os.environ.items() if k in {"PATH", "HOME", "TMPDIR", "SYSTEMROOT", "SSL_CERT_FILE", "SSL_CERT_DIR"}}
            env["TEST_DATABASE_URL"] = f"postgresql://automation:{password}@127.0.0.1:{port}/automation_test"
            run(["uv", "sync", "--frozen"], env=env)
            run(["uv", "run", "--frozen", "pytest", "-q"], env=env)
            if args.full:
                run(["docker", "build", "-t", image, "."])
                run(["uv", "run", "--frozen", "python", "tools/smoke_container.py", "--image", image], env=env)
        finally:
            run(["docker", "rm", "-f", "-v", name], quiet=True, check=False)
            if args.full:
                run(["docker", "image", "rm", image], quiet=True, check=False)
    print("Fresh-checkout selftest passed; disposable resources removed. No provider writes.")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, FileNotFoundError) as exc:
        print(f"Selftest failed: {exc}", file=sys.stderr)
        sys.exit(1)
