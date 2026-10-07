# Product Requirements Document: Agentic GraphRAG Codebase Explorer

**Status:** Draft v1.0 | **Date:** October 6, 2026 | **Type:** Open-source portfolio project

---

## 1. Overview

Agentic GraphRAG Codebase Explorer ingests a code repository, builds a knowledge graph of its structure alongside a vector index of its code, and lets developers ask natural-language questions about architecture and dependencies. An agent routes each question to semantic search (Qdrant), structural graph queries (Neo4j), or both, then synthesizes a grounded answer. Every step is traced in Langfuse so users can see exactly which nodes were retrieved and traversed.

**One-line pitch:** Ask "If I change the auth middleware, what breaks?" and get an answer backed by real graph traversal, with a visible trace.

## 2. Problem Statement

Plain vector search over code finds *similar* text but cannot follow relationships. Developers' hardest questions are relational: what calls this, what inherits from that, what is affected by this change. Answering them today means manual grep, IDE "find usages," and tribal knowledge. Existing RAG tools also act as black boxes, so developers cannot verify why an answer was given.

## 3. Goals and Non-Goals

### Goals

1. Answer semantic ("where is the auth logic?") and structural ("what calls `JWTGenerator`?") questions accurately.
2. Answer multi-hop impact-analysis questions by combining both retrieval modes.
3. Make every answer auditable via traces of tool calls, retrieved chunks, and traversed graph nodes.
4. Run fully self-hosted with `docker compose up`, with a pluggable LLM provider.

### Non-Goals (v1)

- Multi-language support (v1 is **Python only**).
- Code editing, refactoring, or PR generation.
- Multi-user auth, tenancy, or SaaS hosting.
- Exact runtime call-graph resolution (static analysis is approximate by design).
- Repositories above \~200k LOC.

## 4. Target Users

| Persona | Need |
| --- | --- |
| **Onboarding developer** | Quickly understand an unfamiliar codebase's structure. |
| **Maintainer / reviewer** | Assess the blast radius of a change before merging. |
| **Tech lead** | Verify architectural boundaries and find coupling hotspots. |

## 5. User Stories

- As a developer, I can point the system at a local repo or Git URL and have it indexed.
- As a developer, I can ask a question in chat and get an answer with source references (file, line range).
- As a developer, I can ask "what is affected if I change X?" and see the dependency chain.
- As a developer, I can see an interactive graph of the nodes involved in an answer.
- As a developer, I can open the trace for any answer and inspect each agent step.
- As an operator, I can switch the LLM between Ollama, vLLM, and a cloud provider (e.g., AWS Bedrock) via configuration.

## 6. Functional Requirements

Priority: **P0** = required for v1, **P1** = should have, **P2** = later.

### 6.1 Ingestion Pipeline

| ID | Requirement | Priority |
| --- | --- | --- |
| ING-1 | Accept a local path or Git URL and clone/scan it, respecting `.gitignore`. | P0 |
| ING-2 | Parse Python files with Tree-sitter to extract files, classes, functions, methods, and imports. | P0 |
| ING-3 | Extract relationships: `DEFINED_IN`, `IMPORTS`, `CALLS`, `INHERITS`. | P0 |
| ING-4 | Add special-case extractors for FastAPI route decorators, creating `Route` nodes with `HANDLED_BY` edges. | P0 |
| ING-5 | Chunk code per function/class (with docstring and signature) and generate embeddings via Nomic Embed Text. | P0 |
| ING-6 | Store each vector in Qdrant with a payload containing the Neo4j node ID, file path, and line range (dual-linking). | P0 |
| ING-7 | Support incremental re-indexing by file hash; only changed files are reprocessed. | P1 |
| ING-8 | Mark each `CALLS` edge with a confidence attribute (`resolved` or `heuristic`). | P1 |
| ING-9 | Run indexing as a background job with progress reporting. | P1 |
| ING-10 | Optional SCIP/LSIF integration to improve call resolution. | P2 |

### 6.2 Graph Schema (Neo4j)

- **Nodes:** `Repository`, `File`, `Class`, `Function` (includes methods), `Route`, `Module`
- **Edges:** `(File)-[:DEFINES]->(Class|Function)`, `(File)-[:IMPORTS]->(Module|File)`, `(Function)-[:CALLS {confidence}]->(Function)`, `(Class)-[:INHERITS]->(Class)`, `(Route)-[:HANDLED_BY]->(Function)`
- **Properties (all code nodes):** `id`, `name`, `qualified_name`, `file_path`, `start_line`, `end_line`, `repo_id`
- Constraints on `id` uniqueness and indexes on `qualified_name`.

### 6.3 Agent and Retrieval

| ID | Requirement | Priority |
| --- | --- | --- |
| AGT-1 | LangGraph agent with an explicit state graph: plan, tool-call loop, synthesize. | P0 |
| AGT-2 | **Semantic tool:** `semantic_search(query, top_k)` over Qdrant, returning chunks with node IDs. | P0 |
| AGT-3 | **Structural tools (parameterized, no free-form Cypher):** `get_callers(name, depth)`, `get_callees(name, depth)`, `get_dependents(name, depth)`, `get_inheritance_chain(name)`, `get_routes_affected(name)`. | P0 |
| AGT-4 | Support entity resolution: semantic search to find the node ID, then graph traversal from that node. | P0 |
| AGT-5 | Cap traversal depth and result size to protect context limits; report truncation to the user. | P0 |
| AGT-6 | Final answers must cite files and line ranges for every claim about code. | P0 |
| AGT-7 | Agent states uncertainty when only `heuristic` edges support a conclusion. | P1 |
| AGT-8 | Optional read-only guarded `run_cypher` tool, disabled by default. | P2 |
| AGT-9 | Maintain conversation memory within a session. | P1 |

### 6.4 LLM and Embedding Layer

| ID | Requirement | Priority |
| --- | --- | --- |
| LLM-1 | Provider abstraction through LangChain; selected via environment config. | P0 |
| LLM-2 | Default to Ollama; support vLLM and AWS Bedrock as alternatives. | P0 |
| LLM-3 | Document tested models and expected quality (e.g., 8B vs. 32B+ class). | P1 |
| LLM-4 | Tool-calling failures retry once with an error message, then degrade gracefully. | P1 |

### 6.5 Frontend (Next.js 15 + Tailwind)

| ID | Requirement | Priority |
| --- | --- | --- |
| UI-1 | Repository management page: add repo, view index status and stats. | P0 |
| UI-2 | Chat interface with streaming responses and clickable code citations. | P0 |
| UI-3 | Inline code viewer for cited snippets with syntax highlighting. | P1 |
| UI-4 | Interactive graph view highlighting nodes retrieved and traversed for the current answer. | P1 |
| UI-5 | "View trace" link on each answer that opens the matching Langfuse trace. | P0 |
| UI-6 | Step timeline in the UI showing which tools the agent called. | P1 |
| UI-7 | Full codebase graph explorer with filters. | P2 |

### 6.6 Observability (Standout Feature)

| ID | Requirement | Priority |
| --- | --- | --- |
| OBS-1 | Self-hosted Langfuse captures one trace per question. | P0 |
| OBS-2 | Spans for planning, each tool call (inputs, outputs, latency), retrieval results, and synthesis. | P0 |
| OBS-3 | Attach the list of Neo4j node IDs retrieved and traversed as span metadata. | P0 |
| OBS-4 | Record token usage and latency per step. | P1 |
| OBS-5 | User feedback (thumbs up/down) written back to the trace as a score. | P1 |
| OBS-6 | Evaluation runs scored in Langfuse (see Section 9). | P1 |

### 6.7 API (FastAPI)

- `POST /repos` (start ingestion), `GET /repos/{id}/status`
- `POST /chat` (SSE streaming; returns answer, citations, trace ID, and traversed node IDs)
- `GET /graph/subgraph?node_ids=...` (data for the graph view)
- `GET /health`

## 7. Non-Functional Requirements

| Category | Requirement |
| --- | --- |
| **Performance** | Index a 50k LOC Python repo in under 10 minutes. Structural-only queries return in under 5 s; agentic multi-step queries under 30 s on the reference setup. |
| **Reliability** | Ingestion is idempotent; a failed run can be resumed without duplicating nodes. |
| **Security** | LLM cannot execute arbitrary Cypher by default; all graph tools are read-only and parameterized. Secrets via environment variables. Cloned repos are never executed. |
| **Privacy** | In the default configuration, no code leaves the user's machine. |
| **Portability** | Single `docker-compose.yml`; a CPU-only path (smaller model or remote LLM) documented for machines without a GPU. |
| **Maintainability** | Clear module boundaries (parser, graph, vectors, agent, API); typed Python; CI with lint and tests. |

## 8. System Architecture Summary

| Layer | Technology |
| --- | --- |
| Frontend | Next.js 15, Tailwind CSS |
| Backend | FastAPI (async) |
| Agent | LangGraph / LangChain |
| LLM inference | Ollama or vLLM (Bedrock optional) |
| Embeddings | Nomic Embed Text |
| Vector store | Qdrant |
| Graph store | Neo4j Community Edition |
| Parsing | Tree-sitter |
| Observability | Langfuse (self-hosted, with its Postgres and supporting services) |

**Ingestion flow:** Repo, then Tree-sitter AST, then entities and edges into Neo4j, then code chunks embedded into Qdrant with the Neo4j ID in the payload.

**Query flow:** Question, then agent plan, then semantic and/or structural tools, then graph expansion from resolved nodes, then synthesis with citations, with a Langfuse trace throughout.

## 9. Success Metrics

| Metric | Target (v1) |
| --- | --- |
| Entity extraction accuracy (functions/classes) vs. a manually verified sample | ≥ 95% |
| `CALLS` edge precision on a labeled sample | ≥ 80% |
| Answer correctness on an evaluation set of 30+ questions (semantic, structural, multi-hop) | ≥ 70% with default model; ≥ 85% with a stronger model |
| Tool-call success rate (valid args, no errors) | ≥ 90% |
| Answers with valid file/line citations | ≥ 95% |
| Time from `docker compose up` to first answered question on a sample repo | \< 20 minutes |

An evaluation set with expected answers and expected graph nodes is a deliverable, enabling model comparison and regression testing.

## 10. Milestones

| Phase | Scope | Exit criteria |
| --- | --- | --- |
| **1. Foundations** | Repo setup, docker-compose with Neo4j and Qdrant, Tree-sitter parser, graph schema | Sample repo ingested into Neo4j; manual Cypher queries return correct callers |
| **2. Vector layer** | Chunking, embeddings, Qdrant storage, dual-linking | Semantic search returns chunks with valid node IDs |
| **3. Agent** | Parameterized tools, LangGraph loop, LLM abstraction, FastAPI `/chat` | Agent answers semantic, structural, and hybrid questions end to end |
| **4. Observability** | Langfuse integration, node-level metadata | Every answer produces a complete, inspectable trace |
| **5. Frontend** | Chat UI, citations, trace links, graph view | Full demo flow works in the browser |
| **6. Hardening** | Evaluation suite, incremental indexing, docs, demo video | Metrics in Section 9 measured and published in the README |

## 11. Risks and Mitigations

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Small local LLMs fail at planning and tool use | Poor answer quality | Parameterized tools instead of free-form Cypher; pluggable provider; document stronger models |
| Inaccurate `CALLS` edges (dynamic dispatch, decorators, aliasing) | Wrong impact analysis | Scope to Python; confidence flags on edges; route-specific extractors; optional SCIP later |
| Framework "magic" hides dependencies (DI, middleware registration) | Missed relationships | Dedicated FastAPI extractors; state limitations in docs and in answers |
| GPU requirement limits who can run it | Reduced adoption | CPU-only and remote-LLM paths documented |
| Operational complexity (8+ containers) | Setup friction | Single compose file, health checks, staged development |
| Context overflow from large traversals | Degraded or failed answers | Depth and result caps, summarization of large subgraphs |
| Scope creep | Project stalls | Strict P0 list; multi-language support deferred |

## 12. Open Questions

1. Should the default LLM be a local model, or should the README recommend a hosted model for best demo quality?
2. Which reference repositories will be used for evaluation (e.g., a mid-size FastAPI open-source project)?
3. Is a full-codebase graph explorer (UI-7) worth including in v1 for demo impact?
4. Should ingestion support private Git repos (credentials handling) in v1?
5. Which Langfuse version and dependency set will be pinned, given its evolving infrastructure requirements?

## 13. Future Roadmap

- TypeScript/JavaScript and Java support
- Git-history awareness (who changed what, change coupling)
- PR impact analysis as a GitHub App
- Architecture-rule checks (e.g., "UI must not import DB layer")
- Community-detection summaries of modules