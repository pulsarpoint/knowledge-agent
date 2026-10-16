"""Admin-only SSH settings of a server: read, save and test."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request

from knowledge_admin.capabilities import ssh
from knowledge_admin.identity import request_actor
from knowledge_admin.servers.operations_store import SSHInput, OperationsStore


def build_api(app_name, repository_store):
    router = APIRouter()

    def store_for():
        if repository_store is None:
            raise HTTPException(503, 'Operations storage is not configured.')
        return OperationsStore(repository_store, app_name)

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
                getattr(connection, name) == server[name] for name in ('username', 'port', 'allow_sudo', 'known_hosts')
            )
            actor = store.db.actor(conn, *request_actor(request))
            store.audit(
                conn,
                actor,
                'server.ssh.tested',
                server_id,
                {
                    'revision': value.revision,
                    'saved_settings': saved_settings,
                    'host_verification': bool(connection.known_hosts.strip()),
                },
            )
        try:
            result = await ssh.inspect_connection(connection)
        except ssh.SSHError as exc:
            result = {'ok': False, 'message': str(exc), 'host_key': None}
        if saved_settings:
            with store.db.connect() as conn:
                conn.execute(
                    'UPDATE ka.servers SET last_tested_at=now(),last_test_error=%s WHERE id=%s AND revision=%s',
                    (None if result['ok'] else result['message'], server_id, value.revision),
                )
        return result

    return router
