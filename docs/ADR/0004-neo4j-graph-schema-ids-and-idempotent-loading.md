# ADR-0004: Neo4j graph schema, deterministic IDs, and idempotent loading

- **Status:** Accepted
- **Date:** 2026-10-07
- **Section:** 2 – Parser & Graph

## Context
The PRD defines the node/edge schema (6.2) and requires idempotent, resumable ingestion (NFR Reliability) and dual-linking where Qdrant payloads hold Neo4j node IDs (ING-6). Graph tools must be parameterized and read-only.

## Decision
1. **Schema** follows PRD 6.2: nodes `Repository`, `File`, `Class`, `Function` (methods included, flagged `is_method`), `Route`, `Module`; edges `DEFINES`, `IMPORTS`, `CALLS {confidence}`, `INHERITS`, `HANDLED_BY`. **One addition:** `(Repository)-[:CONTAINS]->(File)` so a repo can be scoped and deleted cleanly.
2. **Deterministic IDs:** `id = sha1(repo_id | kind | file_path | local_qualified_name)[:20]`. The same code always yields the same ID, so Qdrant payloads stay valid across re-indexing and `MERGE` is idempotent.
3. All code nodes carry `id, name, qualified_name, file_path, start_line, end_line, repo_id`. Functions/classes also carry `signature`, truncated `docstring`; functions carry `is_method`, `class_qn`. Source code text is **not** stored in Neo4j (it lives in the Qdrant payload and on disk) to keep the graph light.
4. **Uniqueness constraints** on `id` for every label; **indexes** on `name` and `qualified_name` (Class, Function, File, Route).
5. **Loading** uses batched `UNWIND … MERGE` queries (1000 rows) with fixed, hand-written Cypher per relationship type (labels/types cannot be parameters, and they are never built from user input).
6. **Re-index strategy for the MVP:** delete all nodes with the `repo_id` (batched `CALL … IN TRANSACTIONS`), then reload. Per-file incremental updates (ING-7) are P1 and deferred; the deterministic IDs make it a non-breaking later addition.
7. Duplicate `CALLS` pairs are collapsed in Python; if both `resolved` and `heuristic` exist, `resolved` wins.
8. The synchronous official `neo4j` driver is used by the ingestion CLI; the API will use an async wrapper or threadpool later.

## Rationale
- Deterministic IDs are what makes dual-linking, idempotency, and later incremental indexing possible without a separate mapping table.
- Delete-and-reload is simplest and correct; at ≤200k LOC the cost is acceptable.
- Not storing code in Neo4j avoids bloating the property store and duplicate sources of truth.

## Alternatives considered
- *Random UUIDs + mapping table*: breaks idempotency; extra state.
- *Neo4j-generated internal IDs*: unstable and not meant for external references.
- *`neomodel`/OGM*: extra abstraction for a small, fixed schema.

## Consequences
- Renaming or moving a function changes its ID (acceptable; the file is re-embedded anyway).
- Only one-repo-at-a-time full reindex is supported until ING-7 is implemented.

