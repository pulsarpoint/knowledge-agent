"""The Jira ticket pull shared by Fetch tickets (Jira → Tickets) and the scheduled auto pull (target jira.pull)."""
from uuid import UUID

import httpx
from cryptography.exceptions import InvalidTag
from fastapi import HTTPException
from psycopg.types.json import Jsonb

from knowledge_admin.jira import events as jira_events
from knowledge_admin.jira.client import JiraClient
from knowledge_admin.repositories import RepositoryStore
from knowledge_admin.events.schedules import TargetMissing

FETCH_CLASH = "Jira settings or tickets changed during the fetch. Fetch tickets again"
CLASH_SKIPPED = 'A manual fetch ran at the same time.'
DEFAULT_LIMIT = 25


def credentials(store: RepositoryStore, project_id: UUID) -> tuple[dict, str]:
    """The project's primary Jira connection and its decrypted token."""
    with store.connect() as conn:
        config = conn.execute("SELECT * FROM ka.jira_connections WHERE project_id=%s AND name='primary' FOR SHARE", (project_id,)).fetchone()
        if config is None or config["credential_id"] is None or config["username"] is None:
            raise HTTPException(400, "Save a Jira URL, username and token in Jira Settings first")
        if not config["enabled"] or config["deployment"] != "data_center":
            raise HTTPException(400, "This Jira Data Center connection is disabled or unsupported")
        secret = conn.execute("SELECT ciphertext,key_version FROM ka.credentials WHERE id=%s AND project_id=%s AND kind='jira_pat'", (config["credential_id"], project_id)).fetchone()
        try:
            if secret is None or secret["key_version"] != "v1":
                raise ValueError("Unsupported credential")
            blob = bytes(secret["ciphertext"])
            token = store.cipher.decrypt(blob[:12], blob[12:], f"{project_id}:{config['credential_id']}:jira_pat:v1".encode()).decode()
        except (InvalidTag, ValueError, UnicodeDecodeError):
            raise HTTPException(503, "Stored Jira token could not be read. Re-enter the token in Jira Settings") from None
    return config, token


def pull_tickets(store: RepositoryStore, project_id: UUID, jql: str, limit: int, actor: tuple[str, str] | None,
                 transport: httpx.BaseTransport | None = None, action: str = 'jira.tickets_fetched', events: bool = False) -> dict:
    """Fetch up to `limit` tickets for `jql` and replace the saved selection with them. `actor` is the signed-in
    (subject, email), or None for the scheduler (the audit row then has no actor). With `events`, the pull also
    records ticket events in the same transaction, so a clashing or failing pull records none."""
    config, token = credentials(store, project_id)
    with JiraClient(config["base_url"], token, transport) as jira:
        jira.current_user(config["username"])
        issues, total = jira.tickets(limit, jql)
    with store.connect() as conn:
        current = conn.execute("SELECT revision,fetch_revision FROM ka.jira_connections WHERE id=%s AND project_id=%s FOR UPDATE", (config["id"], config["project_id"])).fetchone()
        if current is None or current["revision"] != config["revision"] or current["fetch_revision"] != config["fetch_revision"]:
            raise HTTPException(409, FETCH_CLASH)
        actor_id = None if actor is None else store.actor(conn, *actor)
        conn.execute("UPDATE ka.jira_tickets SET out_of_scope_at=now() WHERE connection_id=%s AND out_of_scope_at IS NULL", (config["id"],))
        for issue in issues:
            fields = issue["fields"]
            conn.execute("""INSERT INTO ka.jira_tickets(project_id,connection_id,remote_id,issue_key,jira_project_key,
                summary,description,issue_type,status_id,status_name,priority,assignee_id,assignee_name,reporter_id,
                labels,raw_payload,source_created_at,source_updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(connection_id,remote_id) DO UPDATE SET issue_key=EXCLUDED.issue_key,
                jira_project_key=EXCLUDED.jira_project_key,summary=EXCLUDED.summary,description=EXCLUDED.description,
                issue_type=EXCLUDED.issue_type,status_id=EXCLUDED.status_id,status_name=EXCLUDED.status_name,
                priority=EXCLUDED.priority,assignee_id=EXCLUDED.assignee_id,assignee_name=EXCLUDED.assignee_name,
                reporter_id=EXCLUDED.reporter_id,labels=EXCLUDED.labels,raw_payload=EXCLUDED.raw_payload,
                source_created_at=EXCLUDED.source_created_at,source_updated_at=EXCLUDED.source_updated_at,
                imported_at=now(),last_seen_at=now(),out_of_scope_at=NULL""",
                (config["project_id"], config["id"], issue["id"], issue["key"], fields["project"]["key"],
                 fields["summary"], Jsonb(fields.get("description")), (fields.get("issuetype") or {}).get("name"),
                 (fields.get("status") or {}).get("id"), (fields.get("status") or {}).get("name"),
                 (fields.get("priority") or {}).get("name"), (fields.get("assignee") or {}).get("key"),
                 (fields.get("assignee") or {}).get("displayName"), (fields.get("reporter") or {}).get("key"),
                 fields.get("labels", []), Jsonb(issue), fields.get("created"), fields["updated"]))
        new_events = jira_events.record(conn, config["project_id"], config["id"], config["base_url"], issues) if events else 0
        if events:  # the scheduled pull keeps the manual Fetch query and limit shown on Jira → Tickets
            conn.execute("""UPDATE ka.jira_connections SET last_successful_sync_at=now(),last_fetch_total=%s,
                fetch_revision=fetch_revision+1 WHERE id=%s""", (total, config["id"]))
        else:
            conn.execute("""UPDATE ka.jira_connections SET last_successful_sync_at=now(),last_fetch_limit=%s,
                last_fetch_total=%s,jql=%s,fetch_revision=fetch_revision+1 WHERE id=%s""", (limit, total, jql, config["id"]))
        conn.execute("""INSERT INTO ka.audit_events(project_id,actor_id,action,resource_type,resource_id,details)
            VALUES (%s,%s,%s,'jira_connection',%s,%s)""",
            (config["project_id"], actor_id, action, config["id"], Jsonb({"requested": limit, "fetched": len(issues), "total": total, **({"events": new_events} if events else {})})))
    return {'fetched': len(issues), 'total': total, 'events': new_events}


def auto_pull(store: RepositoryStore, schedule: dict, transport: httpx.BaseTransport | None = None) -> tuple[str, str]:
    """Target jira.pull: pull with the schedule's own query and limit (params), else the connection's last fetch."""
    with store.connect() as conn:
        connection = conn.execute("""SELECT jql,last_fetch_limit FROM ka.jira_connections
            WHERE id=%s AND project_id=%s AND name='primary'""", (schedule['target_id'], schedule['project_id'])).fetchone()
    if connection is None:
        raise TargetMissing()
    params = schedule.get('params') or {}
    jql = params.get('jql') or connection['jql']
    limit = params.get('limit') or connection['last_fetch_limit'] or DEFAULT_LIMIT
    try:
        result = pull_tickets(store, schedule['project_id'], jql, limit, None, transport, action='jira.tickets_auto_pulled', events=True)
    except HTTPException as exc:
        if exc.status_code == 409 and exc.detail == FETCH_CLASH:
            return 'skipped', CLASH_SKIPPED
        raise
    count = result['events']
    return 'succeeded', f"Pulled {result['fetched']} of {result['total']} tickets · {'1 new event' if count == 1 else f'{count} new events'}."
