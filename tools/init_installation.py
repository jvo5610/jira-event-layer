"""Create portable Compose secret files. No overwrites and no secret output."""
import argparse
import getpass
import os
from pathlib import Path
import secrets


def initialize(directory, runtime_password, migration_password):
    if not runtime_password or not migration_password or any(c in runtime_password + migration_password for c in "\r\n"):
        raise ValueError("Two nonempty single-line database passwords are required")
    directory = Path(directory)
    # Parent directory protects the files on the host; per-service bind mounts let
    # the nonroot container read only the secrets explicitly granted in Compose.
    directory.mkdir(mode=0o700, parents=False, exist_ok=False)
    values = {"db_runtime_password": runtime_password, "db_migrator_password": migration_password,
              "bitbucket_api_token": "", "jira_api_token": "", "gui_api_token": "",
              "admin_bootstrap_token": secrets.token_urlsafe(48), "jira_webhook_secret": secrets.token_urlsafe(48)}
    for name, value in values.items():
        path = directory / name
        with open(path, "x", opener=lambda p, f: os.open(p, f, 0o600)) as output:
            output.write(value + "\n")
        path.chmod(0o444)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", default="secrets")
    args = parser.parse_args()
    runtime_password = getpass.getpass("Runtime database password (hidden): ")
    migration_password = getpass.getpass("Migrator database password (hidden): ")
    initialize(args.directory, runtime_password, migration_password)
    print("Created private secret directory. Existing directories are never overwritten.")


if __name__ == "__main__":
    main()
