"""Create portable Compose secret files. No overwrites and no secret output."""
import argparse
import getpass
import os
from pathlib import Path
import secrets


def initialize(directory, runtime_dsn, migration_dsn):
    if not runtime_dsn or not migration_dsn or "\n" in runtime_dsn or "\n" in migration_dsn:
        raise ValueError("Two nonempty single-line database connection strings are required")
    directory = Path(directory)
    # Parent directory protects the files on the host; per-service bind mounts let
    # the nonroot container read only the secrets explicitly granted in Compose.
    directory.mkdir(mode=0o700, parents=False, exist_ok=False)
    values = {"database_url": runtime_dsn, "migration_database_url": migration_dsn,
              "bitbucket_token": "", "jira_token": "", "reader_token": "", "writer_token": "", "operator_token": "",
              "admin_token": secrets.token_urlsafe(48), "jira_webhook_secret": secrets.token_urlsafe(48)}
    for name, value in values.items():
        path = directory / name
        with open(path, "x", opener=lambda p, f: os.open(p, f, 0o600)) as output:
            output.write(value + "\n")
        path.chmod(0o444)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", default="secrets")
    args = parser.parse_args()
    runtime_dsn = getpass.getpass("Runtime database DSN (hidden): ")
    migration_dsn = getpass.getpass("Migration-owner database DSN (hidden): ")
    initialize(args.directory, runtime_dsn, migration_dsn)
    print("Created private secret directory. Existing directories are never overwritten.")


if __name__ == "__main__":
    main()
