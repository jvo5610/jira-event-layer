# Contributing

This project is experimental. Discuss larger changes in an issue before implementing them.
Keep the core portable: API + worker + PostgreSQL, no cloud service required.

## Development

Use Python 3.12+, uv and Docker with Compose v2. Run `python3 tools/selftest.py --full`
from a clean checkout. It creates and removes its own disposable test database and containers.
No provider credentials are needed. Do not point the test suite at a shared database.

Add regression tests for bug fixes, negative authorization cases, malformed input and ambiguous
remote outcomes. Keep execution deterministic; YAML must not execute arbitrary code or resolve secrets.
Preserve immutable rule versions, durable event identity and manual review of ambiguous writes.

Real Jira/Bitbucket tests are opt-in. Use a dedicated disposable project/repository and least-privilege
credentials. Never run them against someone else's resources. Provider test evidence is private and
must not be committed. Do not submit tokens, `.env` files, database dumps or personal issue payloads.

## Pull requests

Explain the behavior, compatibility impact and tests run. Update schemas, examples and documentation
when changing the contract. Do not mix unrelated formatting or deployment changes into a fix.
CI checks unit/integration tests, dependency vulnerabilities and container installation. Passing CI
does not mean the application is certified for production.

Contributions are distributed under this repository's MIT license. No CLA is required.
