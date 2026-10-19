"""Which agent a Send-to-agent handler runs for an event: its fixed agent, or the agent mapped to the Jira ticket's
assignee (ka.jira_user_agents by the event's connection and payload.assignee_id, the current assignee). The choice is
pick_agent, a pure function; the processor and Preview load the mapping rows with mapping_rows."""

from typing import NamedTuple

from knowledge_admin.jira.events import CREATED, UPDATED

NOT_ASSIGNED = 'The ticket is not assigned.'
DISABLED = 'Agent is disabled.'
JIRA_TYPES = (CREATED, UPDATED)  # every jira.ticket.* type the connection records; add new ones here


class Target(NamedTuple):
    """The agent to run, or why none runs (exactly one of agent_id and refusal is set). `fires` is False when the event
    is not for this handler (no assignee, or nobody mapped to them): the processor then skips it and records nothing;
    a disabled mapped agent fires and fails, like a disabled fixed agent."""

    agent_id: str | None
    refusal: str | None
    fires: bool = True


def pick_agent(handler: dict, event: dict, rows: list[dict]) -> Target:
    """`rows` are mapping rows of the event's connection: {jira_user_key, agent_id, enabled (the agent's)}."""
    if handler['agent_from'] != 'assignee':
        return Target(handler['agent_id'], None)
    payload = event.get('payload') or {}
    key = payload.get('assignee_id')
    if not key:
        return Target(None, NOT_ASSIGNED, fires=False)
    row = next((row for row in rows if row['jira_user_key'] == key), None)
    if row is None:
        return Target(None, f'No agent is mapped to Jira user {payload.get("assignee") or key} ({key}).', fires=False)
    if not row['enabled']:
        return Target(None, DISABLED)
    return Target(row['agent_id'], None)


def mapping_rows(conn, handler: dict, event: dict) -> list[dict]:
    """The mapping row of the event's assignee on the event's Jira connection; none for a fixed handler."""
    key = (event.get('payload') or {}).get('assignee_id')
    if handler['agent_from'] != 'assignee' or event['source'] != 'jira' or not event['source_id'] or not key:
        return []
    return conn.execute(
        """SELECT m.jira_user_key,m.agent_id,a.enabled FROM ka.jira_user_agents m JOIN ka.agents a ON a.id=m.agent_id
        WHERE m.connection_id=%s AND m.jira_user_key=%s""",
        (event['source_id'], key),
    ).fetchall()
