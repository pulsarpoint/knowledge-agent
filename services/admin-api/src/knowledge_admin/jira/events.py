"""Jira events: what the scheduled pull saw change. One event per new or changed ticket (Jira's `updated` moved),
judged against the pull's own latest event for that ticket — not the tickets table, which the manual fetch also
updates. Events carry the ticket's comments. An update whose only change is new comments that are all our own answers
(events/jira_replies.py) is not recorded, so a handler never answers itself. `for_assignee` says whether the event is
work for the ticket's assignee."""

from uuid import UUID

from psycopg.types.json import Jsonb

CREATED, UPDATED = 'jira.ticket.created', 'jira.ticket.updated'
COMPARED = ('summary', 'status', 'issue_type', 'priority')


def ticket(base_url: str, issue: dict) -> dict:
    fields = issue['fields']
    named = lambda value: (value or {}).get('name')
    person = lambda value: (value or {}).get('displayName') or (value or {}).get('key')
    description = fields.get('description')
    comments = [
        {
            'id': comment['id'],
            'author': person(comment.get('author')),
            'author_id': (comment.get('author') or {}).get('name') or (comment.get('author') or {}).get('key'),
            'created': comment['created'],
            'body': comment['body'],
        }
        for comment in (fields.get('comment') or {}).get('comments', [])
    ]
    return {
        'issue_id': issue['id'],
        'key': issue['key'],
        'url': f'{base_url}/browse/{issue["key"]}',
        'summary': fields['summary'],
        'description': description if isinstance(description, str) else '',
        'status': named(fields.get('status')),
        'issue_type': named(fields.get('issuetype')),
        'priority': named(fields.get('priority')),
        'assignee': person(fields.get('assignee')),
        'assignee_id': (fields.get('assignee') or {}).get('key'),
        'reporter': person(fields.get('reporter')),
        'labels': list(fields.get('labels') or []),
        'created': fields.get('created'),
        'updated': fields['updated'],
        'comments': comments,
        'comment_count': len(comments),
    }


def changes(before: dict, after: dict) -> dict:
    result = {
        field: {'from': before.get(field), 'to': after.get(field)}
        for field in COMPARED
        if before.get(field) != after.get(field)
    }
    if (before.get('assignee'), before.get('assignee_id')) != (after.get('assignee'), after.get('assignee_id')):
        result['assignee'] = {
            'from': before.get('assignee'),
            'to': after.get('assignee'),
            'from_id': before.get('assignee_id'),
            'to_id': after.get('assignee_id'),
        }
    old, new = before.get('labels') or [], after.get('labels') or []
    added, removed = [label for label in new if label not in old], [label for label in old if label not in new]
    if added or removed:
        result['labels'] = {'added': added, 'removed': removed}
    if before.get('description') != after.get('description'):
        result['description'] = {'changed': True}
    known = {comment['id'] for comment in before.get('comments') or []}
    if added := [comment['id'] for comment in after.get('comments') or [] if comment['id'] not in known]:
        result['comments'] = {'added': added}
    return result


def ours(comment: dict, posted: set[str], pending: set[str], username: str) -> bool:
    """An answer we posted (events/jira_replies.py): its id is a posted reply's, or — while the reply is still being
    posted and its id not yet recorded — the connection's user wrote it with exactly the text we are posting."""
    return comment['id'] in posted or (
        (comment['author_id'] or '').casefold() == username.casefold() and comment['body'] in pending
    )


def own_comment_ids(
    conn, project_id: UUID, connection_id: UUID, username: str, issue_id: str, new: list[dict]
) -> set[str]:
    """The ids of the new comments that are ours. Recording an update made only of them would make a handler answer
    its own comment."""
    from knowledge_admin.events.jira_replies import (
        comment_body,
    )  # events.jira_replies imports jira.sync, which imports this

    if not new:
        return set()
    posted = {
        row['jira_comment_id']
        for row in conn.execute(
            """SELECT jira_comment_id FROM ka.notifications WHERE project_id=%s AND jira_status='posted'
            AND jira_connection_id=%s AND jira_issue_id=%s AND jira_comment_id = ANY(%s)""",
            (project_id, connection_id, issue_id, [comment['id'] for comment in new]),
        )
    }
    pending = {
        comment_body(row['body'], row['agent_name'])
        for row in conn.execute(
            """SELECT n.body,coalesce(a.name,n.agent_id,'Agent') AS agent_name
            FROM ka.notifications n LEFT JOIN ka.agents a ON a.id=n.agent_id
            WHERE n.project_id=%s AND n.jira_status='pending' AND n.jira_connection_id=%s AND n.jira_issue_id=%s""",
            (project_id, connection_id, issue_id),
        )
    }
    return {comment['id'] for comment in new if ours(comment, posted, pending, username)}


def for_assignee(ticket: dict, changes: dict, first_seen: bool, own_comment_ids: set[str]) -> bool:
    """Work for the ticket's assignee: it is assigned and new, reassigned (by key), or a comment not ours was added.
    Other edits (status, labels, description, …) are not."""
    if ticket.get('assignee_id') is None:
        return False
    assignee = changes.get('assignee')
    added = (changes.get('comments') or {}).get('added', [])
    return (
        first_seen
        or (assignee is not None and assignee['from_id'] != assignee['to_id'])
        or any(comment_id not in own_comment_ids for comment_id in added)
    )


def record(conn, project_id: UUID, connection_id: UUID, base_url: str, username: str, issues: list[dict]) -> int:
    """Record events for the pulled issues inside the caller's transaction; returns how many were new."""
    latest = {
        row['issue_id']: row['payload']
        for row in conn.execute(
            """SELECT DISTINCT ON (payload->>'issue_id')
            payload->>'issue_id' AS issue_id, payload FROM ka.events
        WHERE project_id=%s AND source='jira' AND source_id=%s AND payload->>'issue_id' = ANY(%s)
        ORDER BY payload->>'issue_id', id DESC""",
            (project_id, connection_id, [issue['id'] for issue in issues]),
        )
    }
    recorded = 0
    for issue in issues:
        after, before = ticket(base_url, issue), latest.get(issue['id'])
        if before is not None and before.get('updated') == after['updated']:
            continue
        diff = {} if before is None else changes(before, after)
        added = (diff.get('comments') or {}).get('added', [])
        own = own_comment_ids(
            conn,
            project_id,
            connection_id,
            username,
            issue['id'],
            [comment for comment in after['comments'] if comment['id'] in added],
        )
        if (
            set(diff) == {'comments'}
            and before.get('comments') is not None  # an event recorded before events carried comments
            and own == set(added)
        ):
            continue
        payload = {**after, 'changes': diff, 'for_assignee': for_assignee(after, diff, before is None, own)}
        row = conn.execute(
            """INSERT INTO ka.events(project_id,source,source_id,type,subject,title,payload,dedupe_key,occurred_at)
            VALUES (%s,'jira',%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (project_id,dedupe_key) DO NOTHING RETURNING id""",
            (
                project_id,
                connection_id,
                CREATED if before is None else UPDATED,
                after['key'],
                after['summary'],
                Jsonb(payload),
                f'jira:{connection_id}:{issue["id"]}:{after["updated"]}',
                after['updated'],
            ),
        ).fetchone()
        recorded += row is not None
    return recorded
