# ADR-0001: Monorepo layout and Python tooling

- **Status:** Accepted
- **Date:** 2026-10-07
- **Section:** 1 – Scaffold & Infrastructure

## Context
The PRD requires clear module boundaries (parser, graph, vectors, agent, API), typed Python, and CI with lint and tests. The frontend (Next.js) and backend (FastAPI) ship together via one `docker compose`. The dev machine has Python 3.14, but many native wheels (tree-sitter grammars, qdrant/neo4j clients) lag behind newest Python releases.

## Decision
1. **Monorepo** with `backend/` and `frontend/` (added in a later section), plus `docs/`, `docker-compose.yml`, `.env.example` at the root.
2. Backend is a single installable package `codebase_explorer` under `backend/src/` (src-layout), with sub-packages: `config`, `parser`, `graph`, `vectors`, `ingestion`, `agent`, `api`.
3. **Python 3.12** is the pinned runtime (Docker image `python:3.12-slim`; `requires-python >=3.11,<3.14`).
4. Dependencies managed with **`pyproject.toml`** (PEP 621) using **uv** locally, plain `pip` inside Docker. **Ruff** for lint/format, **mypy** for typing, **pytest** for tests.
5. Configuration via **pydantic-settings** reading environment variables / `.env`.

## Rationale
- One repo keeps the demo (`docker compose up`) simple and atomic versioning easy for a portfolio project.
- src-layout prevents accidental imports from the working directory and enforces real package boundaries.
- Pinning 3.12 avoids wheel-availability problems with 3.14 and is stable for all dependencies.
- pydantic-settings gives typed, validated config and is the natural fit for FastAPI and the provider-switching requirement (LLM-1).

## Alternatives considered
- *Polyrepo*: more overhead, harder to run together. Rejected.
- *Poetry*: fine, but uv is faster and PEP 621-standard. Rejected for simplicity.
- *Python 3.14 locally and in Docker*: wheel risk. Rejected.

## Consequences
- Developers running locally should use Python 3.12 (`uv venv --python 3.12`); Docker is the reference environment.
- Adding a new module requires respecting the package boundaries listed above.

