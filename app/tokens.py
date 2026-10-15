"""Transactional opaque credentials: only hashes are stored, no in-process auth cache."""
import hashlib
import secrets
from uuid import uuid4

from fastapi import HTTPException
from psycopg.types.json import Jsonb
from starlette.concurrency import run_in_threadpool

from app.auth import Identity, PERMISSIONS

PUBLIC = "id,name,permissions,created_at,expires_at,revoked_at,rotation_parent_id,pending_until"


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


class TokenManager:
    def __init__(self, store, rotation_ttl=3600):
        self.store = store
        self.rotation_ttl = rotation_ttl

    @staticmethod
    def lock(conn):
        conn.execute("SELECT pg_advisory_xact_lock(9824330)")

    @staticmethod
    def audit(conn, operation, target, actor, **detail):
        conn.execute("INSERT INTO audit(operation,target,detail) VALUES(%s,%s,%s)",
                     (operation, str(target), Jsonb({"principal": actor, **detail})))

    def bootstrap(self, token_supplier, webhook_secret=""):
        with self.store.pool.connection() as conn:
            self.lock(conn)
            if conn.execute("SELECT singleton FROM auth_bootstrap_state").fetchone():
                return
            token = token_supplier()
            if (not token or len(token) < 32 or len(token) > 16384
                    or any(ord(c) < 33 or ord(c) > 126 for c in token)):
                raise RuntimeError("ADMIN_BOOTSTRAP_TOKEN or ADMIN_BOOTSTRAP_TOKEN_FILE is required for first initialization (32+ printable ASCII characters without spaces)")
            if token == webhook_secret:
                raise RuntimeError("Bootstrap token and webhook secret must be distinct")
            identifier = uuid4()
            conn.execute("INSERT INTO auth_tokens(id,name,token_hash,permissions) VALUES(%s,%s,%s,%s)",
                         (identifier, "initial-administrator", digest(token), sorted(PERMISSIONS)))
            conn.execute("INSERT INTO auth_bootstrap_state(singleton) VALUES(true)")
            self.audit(conn, "auth_bootstrap", identifier, "bootstrap")

    @staticmethod
    def active(conn, *, token_hash=None, token_id=None):
        return conn.execute("""SELECT t.* FROM auth_tokens t
            LEFT JOIN auth_tokens p ON p.id=t.rotation_parent_id
            WHERE (t.token_hash=%s OR t.id=%s) AND t.revoked_at IS NULL
            AND (t.expires_at IS NULL OR t.expires_at>now())
            AND (t.pending_until IS NULL OR (t.pending_until>now() AND p.revoked_at IS NULL
              AND p.id IS NOT NULL AND p.pending_until IS NULL AND (p.expires_at IS NULL OR p.expires_at>now())))""",
            (token_hash, token_id)).fetchone()

    def lookup(self, credential):
        if not credential or len(credential) > 16384:
            return None
        with self.store.pool.connection() as conn:
            row = self.active(conn, token_hash=digest(credential))
        if not row:
            return None
        permissions = frozenset(row["permissions"])
        if row["pending_until"] is not None:
            permissions = permissions - {"auth:admin"}
        return Identity("token:" + str(row["id"]), permissions, row["id"], row["pending_until"] is not None,
                        row["pending_until"] is not None and "auth:admin" in row["permissions"])

    def actor(self, conn, identity, *, admin=False):
        row = self.active(conn, token_id=identity.token_id) if identity.token_id else None
        if not row:
            raise HTTPException(401, "Active local credential required", headers={"WWW-Authenticate": "Bearer"})
        if admin and (row["pending_until"] is not None or "auth:admin" not in row["permissions"]):
            raise HTTPException(403, "Administrator credential required")
        return row

    def create(self, identity, name, permissions, expires_in_seconds):
        token = "jrl_" + secrets.token_urlsafe(48)
        identifier = uuid4()
        granted = sorted(PERMISSIONS if "auth:admin" in permissions else set(permissions))
        with self.store.pool.connection() as conn:
            self.lock(conn)
            self.actor(conn, identity, admin=True)
            row = conn.execute(f"""INSERT INTO auth_tokens(id,name,token_hash,permissions,expires_at)
                VALUES(%s,%s,%s,%s,now()+%s*interval '1 second') RETURNING {PUBLIC}""",
                (identifier, name, digest(token), granted, expires_in_seconds)).fetchone()
            self.audit(conn, "auth_token_created", identifier, identity.subject, permissions=granted)
        return {**row, "token": token}

    def list(self, identity, limit=100, offset=0):
        with self.store.pool.connection() as conn:
            self.actor(conn, identity, admin=True)
            return conn.execute(f"SELECT {PUBLIC} FROM auth_tokens ORDER BY created_at,id LIMIT %s OFFSET %s", (limit, offset)).fetchall()

    def revoke(self, identity, token_id):
        with self.store.pool.connection() as conn:
            self.lock(conn)
            self.actor(conn, identity, admin=True)
            row = conn.execute("SELECT * FROM auth_tokens WHERE id=%s", (token_id,)).fetchone()
            if not row:
                raise HTTPException(404, "Token not found")
            if row["revoked_at"] is None:
                if "auth:admin" in row["permissions"] and row["pending_until"] is None and self.active(conn, token_id=token_id):
                    other = conn.execute("""SELECT id FROM auth_tokens WHERE id<>%s AND revoked_at IS NULL
                        AND pending_until IS NULL AND (expires_at IS NULL OR expires_at>now())
                        AND 'auth:admin'=ANY(permissions) LIMIT 1""", (token_id,)).fetchone()
                    if not other:
                        raise HTTPException(409, "Cannot revoke the last active administrator")
                conn.execute("UPDATE auth_tokens SET revoked_at=now() WHERE id=%s OR (rotation_parent_id=%s AND pending_until IS NOT NULL)", (token_id, token_id))
                self.audit(conn, "auth_token_revoked", token_id, identity.subject)
        return {"revoked": True, "id": token_id}

    def rotate(self, identity):
        token = "jrl_" + secrets.token_urlsafe(48)
        with self.store.pool.connection() as conn:
            self.lock(conn)
            parent = self.actor(conn, identity)
            if parent["pending_until"] is not None:
                raise HTTPException(409, "Confirm the pending rotation first")
            conn.execute("""UPDATE auth_tokens SET revoked_at=now() WHERE rotation_parent_id=%s
                AND pending_until IS NOT NULL AND revoked_at IS NULL AND pending_until<=now()""", (parent["id"],))
            pending = conn.execute("SELECT id FROM auth_tokens WHERE rotation_parent_id=%s AND pending_until IS NOT NULL AND revoked_at IS NULL", (parent["id"],)).fetchone()
            if pending:
                raise HTTPException(409, "A rotation is already pending")
            row = conn.execute(f"""INSERT INTO auth_tokens(id,name,token_hash,permissions,expires_at,rotation_parent_id,pending_until)
                VALUES(%s,%s,%s,%s,%s,%s,LEAST(now()+%s*interval '1 second',%s::timestamptz)) RETURNING {PUBLIC}""",
                (uuid4(), parent["name"], digest(token), parent["permissions"], parent["expires_at"], parent["id"], self.rotation_ttl, parent["expires_at"])).fetchone()
            self.audit(conn, "auth_rotation_started", row["id"], identity.subject, parent_id=str(parent["id"]))
        return {**row, "token": token}

    def confirm(self, identity):
        with self.store.pool.connection() as conn:
            self.lock(conn)
            replacement = self.actor(conn, identity)
            if replacement["pending_until"] is None:
                raise HTTPException(409, "Use the new pending token to confirm rotation")
            conn.execute("UPDATE auth_tokens SET revoked_at=now() WHERE id=%s", (replacement["rotation_parent_id"],))
            row = conn.execute(f"UPDATE auth_tokens SET pending_until=NULL WHERE id=%s RETURNING {PUBLIC}", (replacement["id"],)).fetchone()
            self.audit(conn, "auth_rotation_confirmed", row["id"], identity.subject, parent_id=str(row["rotation_parent_id"]))
        return {"confirmed": True, **row}


class DatabaseAuthenticator:
    def __init__(self, manager):
        self.manager = manager

    async def authenticate(self, authorization):
        scheme, separator, credential = authorization.partition(" ")
        if not separator or scheme.lower() != "bearer":
            return None
        return await run_in_threadpool(self.manager.lookup, credential)
