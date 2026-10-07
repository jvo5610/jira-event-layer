"""Generate an owner-only development configuration without disclosing secrets."""
import os
import secrets

fd = os.open(".env", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as output:
    output.write("TENANT_ID=development\n")
    output.write("DATABASE_URL=postgresql://automation:local-test-only@127.0.0.1:55439/automation\n")
    output.write("ADMIN_TOKEN=" + secrets.token_urlsafe(48) + "\n")
    output.write("JIRA_WEBHOOK_SECRET=" + secrets.token_urlsafe(48) + "\n")
    output.write("BITBUCKET_EMAIL=\nBITBUCKET_TOKEN=\n")
print("Generated .env (owner-only); existing files are never overwritten.")
