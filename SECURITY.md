# Security

## Status

The current code is experimental, with no supported production release or response-time guarantee.
Security fixes target `main`. Review the production gates in `use-cases.json` before deployment.

## Reporting a vulnerability

Use GitHub's private vulnerability reporting:
https://github.com/jvo5610/jira-event-layer/security/advisories/new

Do not open a public issue containing credentials, private Jira data or exploit details.
Provide the commit, affected configuration, impact and a minimal redacted reproduction.

## Operating safely

- The single API serves management and signed webhooks on the same port. Use a TLS proxy/WAF
  to restrict management paths; application Bearer authorization and webhook HMAC remain independent.
- Use dedicated provider identities, Jira project permissions, scoped rules, repository allowlists and write budgets.
- Inject credentials at runtime, preferably through mounted secrets; never put them in YAML rules.
- Flow labels identify provenance, not authorization. No label is required to operate a ticket.
- Review ambiguous writes manually; never blindly replay them.
- Back up PostgreSQL and verify restores. Protect logs, event payloads and backups as private data.
- CI uses mocked providers; live provider tests require explicit operator configuration and authorization.

The project does not promise exactly-once external side effects, full Jira Automation parity,
general loop prevention, or safety against every concurrent change in Jira.
