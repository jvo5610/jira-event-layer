-- Run after each migration as the owner on a DEDICATED installation database.
-- Configure psql with PGHOST, PGPORT, PGDATABASE, PGUSER (migrator) and a private PGPASSFILE.
-- psql -v runtime_role=automation_runtime -f deploy/runtime-grants.sql
-- The login role must already exist; credentials are provisioned separately.
BEGIN;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE CREATE ON SCHEMA public FROM :"runtime_role";
GRANT USAGE ON SCHEMA public TO :"runtime_role";
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO :"runtime_role";
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO :"runtime_role";
REVOKE INSERT, UPDATE, DELETE ON schema_migrations, installation FROM :"runtime_role";
REVOKE UPDATE, DELETE ON auth_bootstrap_state FROM :"runtime_role";
REVOKE UPDATE, DELETE ON service_actors, service_actor_credentials FROM :"runtime_role";
COMMIT;
