ALTER TABLE events
  ADD COLUMN enrichment_status text NOT NULL DEFAULT 'not_required'
    CHECK (enrichment_status IN ('not_required','pending','enriching','completed','failed')),
  ADD COLUMN enrichment jsonb,
  ADD COLUMN enrichment_attempts integer NOT NULL DEFAULT 0,
  ADD COLUMN enrichment_next_at timestamptz NOT NULL DEFAULT now(),
  ADD COLUMN enrichment_lease_until timestamptz,
  ADD COLUMN enrichment_lease_token uuid,
  ADD COLUMN enrichment_error text,
  ADD COLUMN enriched_at timestamptz;

CREATE INDEX event_enrichment_runnable ON events(enrichment_next_at,received_at)
  WHERE enrichment_status='pending';
