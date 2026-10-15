"""Fingerprint of public table structure, independent of data, grants and owners.

Catalog definitions can change across PostgreSQL major versions; rehearse major
upgrades and compare definitions rather than automatically replacing this hash.
"""
import hashlib
import json


def fingerprint(conn):
    queries = {
        "relations": """
            SELECT c.relname, c.relkind, c.relrowsecurity, c.relforcerowsecurity,
                   CASE WHEN c.relkind IN ('v','m') THEN pg_get_viewdef(c.oid, false) END AS view
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','f')
            ORDER BY c.relname""",
        "columns": """
            SELECT c.relname, row_number() OVER (PARTITION BY c.relname ORDER BY a.attnum) AS position,
                   a.attname,
                   format_type(a.atttypid,a.atttypmod) AS type,
                   a.attnotnull, a.attidentity, a.attgenerated,
                   pg_get_expr(d.adbin,d.adrelid,false) AS default_expr
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            JOIN pg_attribute a ON a.attrelid=c.oid
            LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum
            WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','f')
              AND a.attnum>0 AND NOT a.attisdropped
            ORDER BY c.relname,a.attnum""",
        "constraints": """
            SELECT c.relname, k.conname, k.contype, k.convalidated,
                   pg_get_constraintdef(k.oid,false) AS definition
            FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' ORDER BY c.relname,k.conname""",
        "indexes": """
            SELECT c.relname, i.relname AS index_name, x.indisvalid, x.indisready,
                   pg_get_indexdef(i.oid,0,false) AS definition
            FROM pg_index x JOIN pg_class c ON c.oid=x.indrelid
            JOIN pg_class i ON i.oid=x.indexrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' ORDER BY c.relname,i.relname""",
        "sequences": """
            SELECT sequencename, data_type::text, start_value, min_value, max_value,
                   increment_by, cycle, cache_size
            FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename""",
        "triggers": """
            SELECT c.relname,t.tgname,t.tgenabled,pg_get_triggerdef(t.oid,false) AS definition
            FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND NOT t.tgisinternal ORDER BY c.relname,t.tgname""",
        "policies": """
            SELECT tablename,policyname,permissive,roles,cmd,qual,with_check
            FROM pg_policies WHERE schemaname='public' ORDER BY tablename,policyname""",
    }
    state = {key: conn.execute(query).fetchall() for key, query in queries.items()}
    canonical = json.dumps(state, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()
