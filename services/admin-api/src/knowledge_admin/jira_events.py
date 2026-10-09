"""Jira events: what the scheduled pull saw change. One event per new or changed ticket (Jira's `updated` moved),
judged against the pull's own latest event for that ticket — not the tickets table, which the manual fetch also
updates."""
from uuid import UUID

from psycopg.types.json import Jsonb

CREATED, UPDATED = 'jira.ticket.created', 'jira.ticket.updated'
COMPARED = ('summary', 'status', 'issue_type', 'priority', 'assignee')


def ticket(base_url: str, issue: dict) -> dict:
    fields = issue['fields']
    named = lambda value: (value or {}).get('name')
    person = lambda value: (value or {}).get('displayName') or (value or {}).get('key')
    description = fields.get('description')
    return {'issue_id': issue['id'], 'key': issue['key'], 'url': f"{base_url}/browse/{issue['key']}",
            'summary': fields['summary'], 'description': description if isinstance(description, str) else '',
            'status': named(fields.get('status')), 'issue_type': named(fields.get('issuetype')),
            'priority': named(fields.get('priority')), 'assignee': person(fields.get('assignee')),
            'reporter': person(fields.get('reporter')), 'labels': list(fields.get('labels') or []),
            'created': fields.get('created'), 'updated': fields['updated']}


def changes(before: dict, after: dict) -> dict:
    result = {field: {'from': before.get(field), 'to': after.get(field)} for field in COMPARED if before.get(field) != after.get(field)}
    old, new = before.get('labels') or [], after.get('labels') or []
    added, removed = [label for label in new if label not in old], [label for label in old if label not in new]
    if added or removed:
        result['labels'] = {'added': added, 'removed': removed}
    if before.get('description') != after.get('description'):
        result['description'] = {'changed': True}
    return result


def record(conn, project_id: UUID, connection_id: UUID, base_url: str, issues: list[dict]) -> int:
    """Record events for the pulled issues inside the caller's transaction; returns how many were new."""
    latest = {row['issue_id']: row['payload'] for row in conn.execute("""SELECT DISTINCT ON (payload->>'issue_id')
            payload->>'issue_id' AS issue_id, payload FROM ka.events
        WHERE project_id=%s AND source='jira' AND source_id=%s AND payload->>'issue_id' = ANY(%s)
        ORDER BY payload->>'issue_id', id DESC""", (project_id, connection_id, [issue['id'] for issue in issues]))}
    recorded = 0
    for issue in issues:
        after, before = ticket(base_url, issue), latest.get(issue['id'])
        if before is not None and before.get('updated') == after['updated']:
            continue
        payload = {**after, 'changes': {} if before is None else changes(before, after)}
        row = conn.execute("""INSERT INTO ka.events(project_id,source,source_id,type,subject,title,payload,dedupe_key,occurred_at)
            VALUES (%s,'jira',%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (project_id,dedupe_key) DO NOTHING RETURNING id""",
            (project_id, connection_id, CREATED if before is None else UPDATED, after['key'], after['summary'], Jsonb(payload),
             f"jira:{connection_id}:{issue['id']}:{after['updated']}", after['updated'])).fetchone()
        recorded += row is not None
    return recorded
