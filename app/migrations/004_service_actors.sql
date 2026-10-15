-- Append-only identities: rotating credentials never re-enables older actors.
CREATE TABLE service_actors (
  provider text NOT NULL CHECK (provider='jira'),
  account_id text NOT NULL CHECK (length(account_id) BETWEEN 1 AND 255),
  registered_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(provider,account_id)
);
CREATE TABLE service_actor_credentials (
  provider text NOT NULL,
  cloud_id uuid NOT NULL,
  credential_sha256 text NOT NULL CHECK (credential_sha256 ~ '^[0-9a-f]{64}$'),
  account_id text NOT NULL,
  verified_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(provider,cloud_id,credential_sha256),
  FOREIGN KEY(provider,account_id) REFERENCES service_actors(provider,account_id)
);
