-- Token deletion never reopens bootstrap. Runtime cannot delete this marker.
CREATE TABLE auth_bootstrap_state (
  singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
  initialized_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE auth_tokens (
  id uuid PRIMARY KEY,
  name text NOT NULL CHECK (length(name) BETWEEN 1 AND 100),
  token_hash text NOT NULL UNIQUE CHECK (token_hash ~ '^[0-9a-f]{64}$'),
  permissions text[] NOT NULL CHECK (cardinality(permissions)>0 AND permissions <@ ARRAY['api:read','rules:write','flows:operate','auth:admin']::text[]),
  created_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz,
  revoked_at timestamptz,
  rotation_parent_id uuid REFERENCES auth_tokens(id),
  pending_until timestamptz,
  CHECK (pending_until IS NULL OR rotation_parent_id IS NOT NULL)
);
CREATE UNIQUE INDEX auth_one_pending_rotation ON auth_tokens(rotation_parent_id)
  WHERE pending_until IS NOT NULL AND revoked_at IS NULL;
