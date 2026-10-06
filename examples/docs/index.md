---
title: Knowledge Agent v3
---
# Knowledge Agent v3

## What is available?

The reader provides documentation, search and chat. The separate administration
application manages RAG sources, GitHub repositories, services, servers, Jira,
skills, model connections and agent tests. Keycloak provides login, Phoenix
provides tracing, and PostgreSQL stores configuration and searchable content.

## How do I process documentation?

Open http://localhost:8183/rag/files. Create a collection folder, upload Markdown
or add files in the mounted host folder, then process the selected folder.
RAG Settings controls parsing, chunking and embeddings. Unchanged file hashes
skip processing; successful runs replace changed content in the search index.
Full-text search works without an embedding model.

## How do I use my own content and source code?

Put sources in a folder inside DATA_ROOT (for example DATA_ROOT/docs/runbooks) and
add that folder to a RAG collection. RAG mounts DATA_ROOT read-only at /work/data and
only the Jira folder (JIRA_FOLDER) is writable. Add and pull
repositories through GitHub Projects. Configure model connections in LLM Settings
and test the search agent from Agents.
