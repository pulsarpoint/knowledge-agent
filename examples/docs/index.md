---
title: Knowledge Agent v3
---
# Knowledge Agent v3

## What is available?

The administration application manages documents, code projects, services, servers, Jira,
skills, model connections and agent tests. Keycloak provides login, Phoenix
provides tracing, and PostgreSQL stores configuration, documents and memory.

## How do I write documentation?

Open http://localhost:8183/documents. Create a document by hand, or choose **New** →
**Write with an agent** and tell the agent what to write; it saves a draft that you
publish. Every published version is kept. Agents find documents with full-text search.

## How do I use my own source code?

Put code in a folder inside DATA_ROOT (for example DATA_ROOT/code/payments), or clone a
Git repository, and map it in Code projects. Every service mounts DATA_ROOT read-only at
/work/data. Configure model connections in LLM Settings and test agents from Agents.
