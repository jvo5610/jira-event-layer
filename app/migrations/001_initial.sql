CREATE TABLE IF NOT EXISTS rules (
  name text PRIMARY KEY, active_version integer, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS rule_versions (
  rule_name text NOT NULL REFERENCES rules(name), version integer NOT NULL,
  sha256 text NOT NULL, yaml text NOT NULL, spec jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(rule_name, version)
);
CREATE TABLE IF NOT EXISTS events (
  id uuid PRIMARY KEY, tenant text NOT NULL, source text NOT NULL, delivery_id text NOT NULL,
  event_type text NOT NULL, payload jsonb NOT NULL, raw_body bytea NOT NULL, sha256 text NOT NULL,
  received_at timestamptz NOT NULL DEFAULT now(), processed_at timestamptz,
  attempts integer NOT NULL DEFAULT 0, last_error text,
  UNIQUE(tenant, source, delivery_id)
);
CREATE TABLE IF NOT EXISTS evaluations (
  event_id uuid NOT NULL REFERENCES events(id), rule_name text NOT NULL, version integer NOT NULL,
  matched boolean NOT NULL, trace jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(event_id, rule_name, version),
  FOREIGN KEY(rule_name, version) REFERENCES rule_versions(rule_name, version)
);
CREATE TABLE IF NOT EXISTS runs (
  id uuid PRIMARY KEY, event_id uuid NOT NULL REFERENCES events(id),
  rule_name text NOT NULL, version integer NOT NULL,
  status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','dispatching','waiting','succeeded','failed','needs_review')),
  step integer NOT NULL DEFAULT 0, attempts integer NOT NULL DEFAULT 0,
  next_at timestamptz NOT NULL DEFAULT now(), lease_until timestamptz,
  external_uuid text, external_result jsonb, last_error text,
  created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(event_id, rule_name, version),
  FOREIGN KEY(rule_name, version) REFERENCES rule_versions(rule_name, version)
);
CREATE INDEX IF NOT EXISTS runnable ON runs(next_at) WHERE status IN ('pending','waiting');
ALTER TABLE runs ADD COLUMN IF NOT EXISTS lease_token uuid;
CREATE TABLE IF NOT EXISTS jira_write_attempts (
  id bigserial PRIMARY KEY, run_id uuid NOT NULL REFERENCES runs(id), step integer NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS jira_write_budget ON jira_write_attempts(created_at);
CREATE TABLE IF NOT EXISTS run_log (
  id bigserial PRIMARY KEY, run_id uuid NOT NULL REFERENCES runs(id), step integer NOT NULL,
  status text NOT NULL, detail jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS audit (
  id bigserial PRIMARY KEY, operation text NOT NULL, target text NOT NULL, detail jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
