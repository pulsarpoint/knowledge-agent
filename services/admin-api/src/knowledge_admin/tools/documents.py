"""Search and read the RAG knowledge base within the agent's document collections, recording citable excerpts."""

from agents import RunContextWrapper, function_tool

from knowledge_admin.capabilities import documents
from knowledge_admin.tools import AgentContext


@function_tool
async def search_documents(ctx: RunContextWrapper[AgentContext], query: str, collection: str | None = None) -> str:
    """Search the knowledge base and return numbered excerpts to cite as [n].

    Args:
        query: Words or a question to search for.
        collection: Optional collection name, such as "jira", to search only that collection.
    """
    return await documents.search_documents(ctx.context, query, collection)


@function_tool
async def read_document(ctx: RunContextWrapper[AgentContext], collection: str, path: str) -> str:
    """Read one knowledge-base document returned by search_documents; cite it with its [n].

    Args:
        collection: The collection name shown before the path, such as "jira".
        path: The document's full path as shown after the collection, such as "jira/DEMO-1.md".
    """
    return await documents.read_document(ctx.context, collection, path)


@function_tool
async def report_not_covered(ctx: RunContextWrapper[AgentContext], reason: str) -> str:
    """Record that the retrieved documents do not answer the question.

    Args:
        reason: What information is missing from the documentation.
    """
    return await documents.report_not_covered(ctx.context, reason)
