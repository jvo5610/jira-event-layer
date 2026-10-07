CREATE TABLE installation (
  singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton), tenant text NOT NULL
);
CREATE TABLE worker_heartbeats (
  worker_id text PRIMARY KEY, last_seen timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE connector_write_attempts (
  id bigserial PRIMARY KEY, connector text NOT NULL, run_id uuid NOT NULL REFERENCES runs(id),
  step integer NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX connector_write_budget ON connector_write_attempts(connector, created_at);
