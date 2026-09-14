"""Persisted HTTP MCP connections shared by administration and code analysis."""
from uuid import UUID


PUBLIC_COLUMNS = '''id,name,description,url,enabled,tools,revision,last_tested_at,last_test_error,
    (token_ciphertext IS NOT NULL) AS has_token,updated_at'''


def credential_aad(server_id: UUID) -> bytes:
    return f'mcp:{server_id}:v1'.encode()


def save_selection(conn, project, config_id, ids):
    rows = conn.execute('''SELECT id FROM ka.mcp_servers WHERE project_id=%s AND id=ANY(%s)
        AND enabled AND last_tested_at IS NOT NULL AND last_test_error IS NULL AND jsonb_array_length(tools)>0 FOR SHARE''', (project, ids)).fetchall()
    if len(rows) != len(ids):
        raise ValueError('Select enabled MCP servers with a successful connection test and available tools.')
    conn.execute('DELETE FROM ka.agent_config_mcp_servers WHERE config_id=%s', (config_id,))
    for server_id in ids:
        conn.execute('INSERT INTO ka.agent_config_mcp_servers VALUES (%s,%s,%s)', (config_id, project, server_id))
