ALTER TABLE events ADD COLUMN payload_redacted_at timestamptz;
CREATE INDEX retention_candidates ON events(received_at) WHERE payload_redacted_at IS NULL;
