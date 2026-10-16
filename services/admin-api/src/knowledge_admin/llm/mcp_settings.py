"""Persisted HTTP MCP connections shared by administration and code analysis."""

from uuid import UUID


PUBLIC_COLUMNS = """id,name,description,url,enabled,tools,revision,last_tested_at,last_test_error,
    (token_ciphertext IS NOT NULL) AS has_token,updated_at"""


def credential_aad(server_id: UUID) -> bytes:
    return f'mcp:{server_id}:v1'.encode()
