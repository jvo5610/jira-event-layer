"""Explicit bounded payload retention; delivery identity survives redaction forever."""
import argparse
import sys

from app.config import secret, tenant_id
from app.store import Store


def redact(store, days=90, limit=100, apply=False):
    if not 1 <= days <= 3650 or not 1 <= limit <= 1000:
        raise ValueError("Retention days must be 1..3650 and batch size 1..1000")
    with store.pool.connection() as conn:
        rows = conn.execute("""SELECT e.id FROM events e
            WHERE payload_redacted_at IS NULL AND received_at<now()-%s*interval '1 day'
            AND NOT EXISTS (SELECT 1 FROM runs r WHERE r.event_id=e.id AND r.status NOT IN ('succeeded','failed'))
            ORDER BY received_at FOR UPDATE OF e SKIP LOCKED LIMIT %s""", (days, limit)).fetchall()
        ids = [r["id"] for r in rows]
        if apply and ids:
            conn.execute("UPDATE events SET payload='{}',raw_body=''::bytea,payload_redacted_at=now() WHERE id=ANY(%s)", (ids,))
            conn.execute("UPDATE evaluations SET trace='{\"redacted\":true}' WHERE event_id=ANY(%s)", (ids,))
            conn.execute("UPDATE runs SET external_result=NULL WHERE event_id=ANY(%s)", (ids,))
            conn.execute("UPDATE run_log SET detail='{\"redacted\":true}' WHERE run_id IN (SELECT id FROM runs WHERE event_id=ANY(%s))", (ids,))
            store.audit(conn, "redact_event_payloads", "retention", {"days": days, "count": len(ids)})
        return {"applied": apply, "count": len(ids)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--apply", action="store_true", help="Irreversibly redact eligible payloads; default is dry run")
    args = parser.parse_args()
    store = None
    try:
        store = Store(secret("DATABASE_URL", required=True), tenant_id())
        store.open()
        print(redact(store, args.days, args.limit, args.apply))
        return 0
    except Exception as exc:
        print(f"Retention failed ({type(exc).__name__})", file=sys.stderr)
        return 1
    finally:
        if store:
            store.close()


if __name__ == "__main__":
    sys.exit(main())
