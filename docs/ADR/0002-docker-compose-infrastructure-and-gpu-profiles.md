# ADR-0002: Docker Compose infrastructure with CPU/GPU profiles

- **Status:** Accepted
- **Date:** 2026-10-07
- **Section:** 1 – Scaffold & Infrastructure

## Context
The MVP needs Neo4j (graph), Qdrant (vectors), and an LLM/embedding runtime (Ollama). The user wants to run both with and without an NVIDIA GPU. The PRD lists Langfuse as P0, but it needs Postgres, ClickHouse, Redis and S3-compatible storage (8+ containers), which adds heavy setup friction.

## Decision
1. `docker-compose.yml` defines **neo4j**, **qdrant**, and Ollama as **two mutually exclusive profiles**:
   - `cpu` → service `ollama-cpu`
   - `gpu` → service `ollama-gpu` (NVIDIA device reservation)
   Both expose the same network alias `ollama`, so the backend config is identical.
2. Neo4j and Qdrant have no profile (always started) and include **health checks**.
3. Neo4j **Community Edition** with the **APOC** plugin disabled by default (not needed for MVP); data persisted in named volumes.
4. **Langfuse is deferred** out of the MVP compose file. MVP tracing = structured tool-call logging with node IDs (Section 4). Langfuse added as a separate `docker-compose.langfuse.yml` later.
5. A one-shot **`ollama-pull`** helper is NOT included; models are pulled via a documented `make pull-models` target to keep compose simple.
6. All ports/credentials come from `.env` (template: `.env.example`).

## Rationale
- Profiles with a shared alias give a single compose file for both hardware paths without conditional config in code.
- Health checks let the backend `depends_on: condition: service_healthy`, reducing flaky first runs (success metric: <20 min to first answer).
- Deferring Langfuse keeps the MVP at 3 containers; the tracing interface will be abstracted so Langfuse plugs in later.

## Alternatives considered
- *Single Ollama service with GPU optional via override file*: users must remember to pass `-f`. Profiles are more discoverable.
- *Include Langfuse now*: contradicts the agreed slim MVP.
- *Run Ollama on the host*: the user can still do this by setting `OLLAMA_BASE_URL`; documented as an option.

## Consequences
- Run: `docker compose --profile cpu up -d` or `--profile gpu up -d`.
- GPU profile requires the NVIDIA Container Toolkit / Docker Desktop WSL2 GPU support.
- OBS-1..3 (Langfuse) are consciously postponed; recorded as a deviation from the PRD.

