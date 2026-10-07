# ADR-0003: Python parsing and call resolution strategy

- **Status:** Accepted
- **Date:** 2026-10-07
- **Section:** 2 – Parser & Graph

## Context
Requirements ING-2/3/4/8 need entities (files, classes, functions, methods, imports) and relationships (`IMPORTS`, `CALLS`, `INHERITS`, FastAPI `Route`→handler) from Python source. The PRD accepts approximate static analysis but wants a `resolved` vs `heuristic` flag on `CALLS`. `CALLS` precision (≥80%) is the biggest quality risk, and the PRD did not define how names are resolved.

## Decision
1. **Tree-sitter (`tree-sitter` + `tree-sitter-python`)** produces the AST. Extraction is a single recursive walk per file that tracks scope (class / enclosing function).
2. **Two phases:**
   - *Phase A – per-file extraction* (no cross-file knowledge): entities, import refs, raw call expressions (`foo`, `self.bar`, `mod.func`, `?.method`, `super.m`), base-class expressions, route decorators.
   - *Phase B – repo-wide `Resolver`*: builds module and symbol indexes and resolves the raw expressions.
3. **Module qualified names come from the repo-relative path** (`backend/app/core/db.py` → `backend.app.core.db`), not from package detection. Absolute imports (`app.core.db`) are matched by **dotted suffix** against known modules, with ties broken by common path prefix with the importing file. Relative imports resolve exactly.
4. **Resolution order** for a call expression: nested/local function → import bindings (following `__init__.py` re-exports, depth ≤ 5) → `self`/`cls`/`super` via class + resolved bases → heuristic fallback.
5. **Confidence:** `resolved` when the target is found by scope/import/class rules. `heuristic` when only the method name matches: at most 3 repo-wide candidates, and common names (`get`, `add`, `commit`, …) are excluded to cut false positives.
6. **Class instantiation** `Foo()` maps to `Foo.__init__` (resolved through inheritance). If none exists, no edge is created, because `CALLS` is Function→Function only.
7. **FastAPI special cases (ING-4):** decorators `@x.get/post/put/delete/patch/options/head("<literal path>")` create `Route` nodes with `HANDLED_BY`; `Depends(f)` / `Security(f)` is recorded as a call from the enclosing function to `f` (a dependency-injection edge a plain parser would miss).
8. Imports of stdlib modules are dropped. Third-party imports become `Module` nodes (top-level package only). First-party imports become File→File edges.

## Rationale
- Two-phase design keeps the AST walker simple and makes the resolver unit-testable without Tree-sitter.
- Path-based module names work for src-layouts, namespace packages and monorepos (e.g. `backend/app/...`) without guessing source roots.
- Heuristic edges with a hard cap favour precision over recall; the flag lets the agent hedge (AGT-7).
- `Depends()` handling covers the single most common "framework magic" in FastAPI projects cheaply.

## Alternatives considered
- *Jedi / pyright / SCIP for resolution*: far more accurate, but heavy, slower, and need an environment. Deferred (ING-10, P2).
- *Python `ast` module*: simpler, but the PRD mandates Tree-sitter, and it keeps the door open for multi-language support.
- *Package-root detection via `__init__.py`*: fails for namespace packages.

## Consequences
- Known blind spots (documented, surfaced to users later): dynamic dispatch, aliasing of instances (`x = foo.bar; x()`), decorators that wrap functions, `router.include_router` prefixes (route paths are the decorator literal only), attribute chains like `self.repo.save()` (heuristic only).
- Suffix matching can mis-resolve when two modules share a suffix; the prefix tiebreaker mitigates this.

