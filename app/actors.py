"""Global actor exclusion. Never infer the actor from issue creator/reporter/assignee."""
import hashlib
import re
from uuid import UUID

EXCLUDED = {'ignored_service_actor', 'held_actor_unknown', 'ignored_jira_actor'}


def account_id(value):
    return (value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9:_-]{1,255}', value)
            and value not in {'unknown', 'anonymous'} else None)


def credential_key(settings):
    if not settings.jira_token:
        raise ValueError('Jira credentials required to verify service identity')
    return str(UUID(settings.jira_cloud_id)), hashlib.sha256(settings.jira_token.encode()).hexdigest()


def actor_ids(event_type, payload):
    if not isinstance(payload, dict):
        return set()
    candidates = [payload.get('user')]
    kind = event_type.rsplit(':', 1)[-1]
    # Only actor-bearing fields for the operation, never the original author of
    # an updated/deleted comment or worklog. Some webhook types have no actor.
    fallback = {'comment_created': ('comment', 'author'),
                'comment_updated': ('comment', 'updateAuthor'),
                'worklog_created': ('worklog', 'author'),
                'worklog_updated': ('worklog', 'updateAuthor')}.get(kind)
    if fallback:
        obj = payload.get(fallback[0])
        candidates.append(obj.get(fallback[1]) if isinstance(obj, dict) else None)
    return {identifier for obj in candidates if isinstance(obj, dict)
            if (identifier := account_id(obj.get('accountId'))) is not None}


def exclusion_reason(conn, event):
    if event.get('last_error') in EXCLUDED:
        return event['last_error']
    # Redacted historical data has its own irreversible replay protection.
    if event.get('payload_redacted_at') is not None:
        return None
    ids = actor_ids(event['event_type'], event['payload'])
    if ids and conn.execute("SELECT 1 FROM service_actors WHERE provider='jira' AND account_id=ANY(%s) LIMIT 1",
                            (list(ids),)).fetchone():
        return 'ignored_service_actor'
    if event['source'] == 'jira' and len(ids) != 1:
        return 'held_actor_unknown'
    return None
