"""Admin-only SSH configuration, operations policy, and durable run history."""
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request

from knowledge_admin.capabilities import ssh
from knowledge_admin.llm.mcp_settings import PUBLIC_COLUMNS
from knowledge_admin.access import request_actor
from knowledge_admin.operations_store import AgentSSHInput, DeleteAgentSSHInput, DeleteServerConfigInput, ServerConfigInput, SSHInput, SettingsInput, OperationsStore


def build_api(app_name, repository_store):
    router = APIRouter()

    def store_for(agent_id="operations"):
        if repository_store is None:
            raise HTTPException(503, 'Operations storage is not configured.')
        return OperationsStore(repository_store, app_name, agent_id)

    @router.get('/operations/settings')
    def settings(agent_id: str = "operations"):
        store = store_for(agent_id)
        with store.db.connect() as conn:
            return {'settings': store.settings(conn), 'servers': store.agent_servers(conn), 'configs': store.configs(conn),
                'mcp_servers': conn.execute(f'SELECT {PUBLIC_COLUMNS} FROM ka.mcp_servers WHERE project_id=%s ORDER BY name', (store.project(conn),)).fetchall(),
                'skills': conn.execute('SELECT id,name,description,enabled,revision FROM ka.agent_skills WHERE project_id=%s ORDER BY name', (store.project(conn),)).fetchall()}

    @router.put('/operations/settings')
    def save_settings(value: SettingsInput, request: Request, agent_id: str = "operations"):
        return store_for(agent_id).save_settings(value, request_actor(request))

    @router.put('/operations/configs/{server_id}')
    def save_server_config(server_id: UUID, value: ServerConfigInput, request: Request, agent_id: str = "operations"):
        return store_for(agent_id).save_server_config(server_id, value, request_actor(request))

    @router.delete('/operations/configs/{server_id}')
    def delete_server_config(server_id: UUID, value: DeleteServerConfigInput, request: Request, agent_id: str = "operations"):
        return store_for(agent_id).delete_server_config(server_id, value, request_actor(request))

    @router.get('/servers/{server_id}/ssh')
    def server_settings(server_id: UUID):
        store = store_for()
        with store.db.connect() as conn:
            return store.server(conn, server_id)

    @router.put('/servers/{server_id}/ssh')
    def save_server_settings(server_id: UUID, value: SSHInput, request: Request):
        return store_for().save_ssh(server_id, value, request_actor(request))

    @router.post('/servers/{server_id}/ssh/test')
    async def test_server(server_id: UUID, value: SSHInput, request: Request):
        store = store_for()
        with store.db.connect() as conn:
            server = store.server(conn, server_id)
            if server['revision'] != value.revision:
                raise HTTPException(409, 'Server settings changed. Reload before testing.')
            connection = store.connection(conn, server_id, test_settings=value)
            saved_settings = 'private_key' not in value.model_fields_set and all(
                getattr(connection, name) == server[name] for name in ('username', 'port', 'allow_sudo', 'known_hosts'))
            actor = store.db.actor(conn, *request_actor(request))
            store.audit(conn, actor, 'server.ssh.tested', server_id, {'revision': value.revision,
                'saved_settings': saved_settings, 'host_verification': bool(connection.known_hosts.strip())})
        try:
            result = await ssh.inspect_connection(connection)
        except ssh.SSHError as exc:
            result = {'ok': False, 'message': str(exc), 'host_key': None}
        if saved_settings:
            with store.db.connect() as conn:
                conn.execute('UPDATE ka.servers SET last_tested_at=now(),last_test_error=%s WHERE id=%s AND revision=%s',
                    (None if result['ok'] else result['message'], server_id, value.revision))
        return result

    @router.get('/agents/{agent_id}/servers/{server_id}/ssh')
    def agent_server_settings(agent_id: str, server_id: UUID):
        store = store_for(agent_id)
        with store.db.connect() as conn:
            return store.agent_server(conn, server_id)

    @router.put('/agents/{agent_id}/servers/{server_id}/ssh')
    def save_agent_server_settings(agent_id: str, server_id: UUID, value: AgentSSHInput, request: Request):
        return store_for(agent_id).save_agent_ssh(server_id, value, request_actor(request))

    @router.delete('/agents/{agent_id}/servers/{server_id}/ssh')
    def delete_agent_server_settings(agent_id: str, server_id: UUID, value: DeleteAgentSSHInput, request: Request):
        return store_for(agent_id).delete_agent_ssh(server_id, value, request_actor(request))

    @router.post('/agents/{agent_id}/servers/{server_id}/ssh/test')
    async def test_agent_server(agent_id: str, server_id: UUID, value: AgentSSHInput, request: Request):
        store = store_for(agent_id)
        with store.db.connect() as conn:
            server = store.agent_server(conn, server_id)
            connection = store.agent_connection(conn, server_id, test_settings=value)
            saved_settings = 'private_key' not in value.model_fields_set and all(
                getattr(connection, name) == server[name] for name in ('username','port','allow_sudo','known_hosts'))
            store.audit(conn, store.db.actor(conn, *request_actor(request)), 'agent.ssh.tested', server_id,
                {'agent_id': agent_id, 'revision': value.revision, 'saved_settings': saved_settings})
        try:
            result = await ssh.inspect_connection(connection)
        except ssh.SSHError as exc:
            result = {'ok': False, 'message': str(exc), 'host_key': None}
        if saved_settings:
            with store.db.connect() as conn:
                if server['connection_source'] == 'agent':
                    conn.execute('''UPDATE ka.agent_server_connections a SET last_tested_at=now(),last_test_error=%s
                        WHERE agent_id=%s AND server_id=%s AND revision=%s AND EXISTS (
                        SELECT 1 FROM ka.servers s WHERE s.id=a.server_id AND s.revision=%s)''',
                        (None if result['ok'] else result['message'],agent_id,server_id,value.revision,value.server_revision))
                else:
                    conn.execute('UPDATE ka.servers SET last_tested_at=now(),last_test_error=%s WHERE id=%s AND revision=%s',
                        (None if result['ok'] else result['message'],server_id,value.server_revision))
        return result

    return router
