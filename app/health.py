"""Platform-neutral container probe. Reports no credentials or database exceptions."""
import os
import socket
import sys
from urllib.request import urlopen

import psycopg
from app.config import secret


def worker_id():
    return os.getenv("WORKER_ID", socket.gethostname())


def worker_healthy(url, identity):
    with psycopg.connect(url, connect_timeout=5, options="-c statement_timeout=5000 -c search_path=public") as conn:
        row = conn.execute("SELECT last_seen>now()-interval '180 seconds' FROM worker_heartbeats WHERE worker_id=%s", (identity,)).fetchone()
        return bool(row and row[0])


def main():
    try:
        if len(sys.argv) == 2 and sys.argv[1] == "worker":
            return 0 if worker_healthy(secret("DATABASE_URL", required=True), worker_id()) else 1
        if len(sys.argv) == 2 and sys.argv[1] == "api":
            with urlopen("http://127.0.0.1:8080/readyz", timeout=5) as response:
                return 0 if response.status == 200 else 1
        return 2
    except Exception:
        return 1


if __name__ == "__main__":
    sys.exit(main())
