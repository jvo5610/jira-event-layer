-- Bounded, on-demand lookup of coherent execution examples in the rule editor.
CREATE INDEX runs_rule_context ON runs(rule_name,version,created_at DESC,id DESC);
CREATE INDEX run_log_context ON run_log(run_id,step,id DESC)
    WHERE status IN ('pending','succeeded') AND detail->'result' IS NOT NULL;
