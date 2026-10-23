"""Documents in the 0103 shape (ka.documents + ka.document_versions), for migration tests that roll 0104 back to test
an older migration. Today's save_document writes the 0104 shape and cannot be used there."""

from pathlib import Path
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb

MIGRATIONS = Path(__file__).resolve().parents[3] / 'db/migrations'
# 0104 keeps documents and drafts in one table; tests of older migrations use the 0103 tables, so each starts with
# 0104 rolled back and goes forward through it again where it rolls forward.
DOCUMENT_MODEL = ('0104_document_model.down.sql', '0104_document_model.up.sql')
# 0106 turns discovery runs into resource runs: rolled back first, so older migrations see discovery runs.
RESOURCE_RUNS = ('0106_resource_runs.down.sql', '0106_resource_runs.up.sql')
# 0107 adds documents.inventory over 0104's tables: rolled back before 0106 (0109, replacing it with a tag, first).
TAGS_NOT_FLAGS = ('0109_tags_not_flags.down.sql', '0109_tags_not_flags.up.sql')
# 0126 drops the old inventory structures (service_assets, inventory_links, ...) that 0109 and older migrations change:
# rolled back first.
INVENTORY_DROP = ('0126_inventory_drop_old.down.sql', '0126_inventory_drop_old.up.sql')
# 0129 narrows discovery_proposals to documents (service proposals are gone): rolled back first.
SERVICE_PROPOSALS = ('0129_service_proposals_gone.down.sql', '0129_service_proposals_gone.up.sql')
# 0127 makes environment_servers again, which 0126 down makes: rolled back before 0126.
WORKSPACE = ('0127_environment_workspace.down.sql', '0127_environment_workspace.up.sql')
INVENTORY_DOCUMENT = ('0107_inventory_document.down.sql', '0107_inventory_document.up.sql')


def roll_back_0104(store) -> None:
    with store.connect() as conn:
        conn.execute((MIGRATIONS / SERVICE_PROPOSALS[0]).read_text(), prepare=False)
        conn.execute((MIGRATIONS / WORKSPACE[0]).read_text(), prepare=False)
        conn.execute((MIGRATIONS / INVENTORY_DROP[0]).read_text(), prepare=False)
        conn.execute((MIGRATIONS / TAGS_NOT_FLAGS[0]).read_text(), prepare=False)
        conn.execute((MIGRATIONS / INVENTORY_DOCUMENT[0]).read_text(), prepare=False)
        conn.execute((MIGRATIONS / RESOURCE_RUNS[0]).read_text(), prepare=False)
        conn.execute((MIGRATIONS / DOCUMENT_MODEL[0]).read_text(), prepare=False)
        conn.commit()


@pytest.fixture
def before_0104(store):
    """Use with `pytestmark = pytest.mark.usefixtures('before_0104')` (import it into the test module)."""
    roll_back_0104(store)


def old_document(
    conn, project, slug: str, title: str, markdown: str, *, folder: str | None = None, origin: str = 'manual'
) -> dict:
    """A live document with version 1 in the 0103 tables: {'id', 'revision', 'version_id'}."""
    document, version = uuid4(), uuid4()
    conn.execute(
        'INSERT INTO ka.documents(id,project_id,slug,folder,current_version_id) VALUES (%s,%s,%s,%s,%s)',
        (document, project, slug, folder, version),
    )
    conn.execute(
        """INSERT INTO ka.document_versions(id,document_id,project_id,version,title,description,purpose,audience,
        markdown,content_hash,source_snapshot,origin) VALUES (%s,%s,%s,1,%s,'','explanation','both',%s,
        encode(sha256(convert_to(%s,'UTF8')),'hex'),%s,%s)""",
        (
            version,
            document,
            project,
            title,
            markdown,
            markdown,
            Jsonb({'code_projects': [], 'services': [], 'servers': [], 'deployments': [], 'environments': []}),
            origin,
        ),
    )
    return {'id': document, 'revision': 1, 'version_id': version}
