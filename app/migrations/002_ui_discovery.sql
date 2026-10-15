-- Bounded discovery and keyset navigation; no payload-wide GIN index.
CREATE INDEX events_discovery ON events(source,event_type,received_at DESC,id DESC) WHERE payload_redacted_at IS NULL;
CREATE INDEX events_recent ON events(received_at DESC,id DESC);
CREATE INDEX runs_recent ON runs(created_at DESC,id DESC);
