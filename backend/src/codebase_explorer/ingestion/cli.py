"""CLI: python -m codebase_explorer.ingestion.cli <path-or-git-url> [--repo-id ID]"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from codebase_explorer.config import get_settings
from codebase_explorer.graph.loader import GraphLoader
from codebase_explorer.ingestion.graph_builder import build_graph_data
from codebase_explorer.ingestion.repo_source import resolve_source


def main() -> None:
    ap = argparse.ArgumentParser(description="Ingest a Python repo into Neo4j")
    ap.add_argument("source", help="Local path or Git URL")
    ap.add_argument("--repo-id", default=None)
    ap.add_argument("--workspace", default="workspace", help="Clone destination for Git URLs")
    ap.add_argument("--dry-run", action="store_true", help="Parse only; skip Neo4j")
    args = ap.parse_args()

    root, default_id = resolve_source(args.source, Path(args.workspace))
    repo_id = args.repo_id or default_id
    data = build_graph_data(repo_id, root)
    print(json.dumps({"repo_id": repo_id, **data.summary()}, indent=2))
    if args.dry_run:
        return

    s = get_settings()
    loader = GraphLoader(s.neo4j_uri, s.neo4j_user, s.neo4j_password)
    try:
        loader.load(data)
    finally:
        loader.close()
    print("Loaded into Neo4j.")


if __name__ == "__main__":
    main()

