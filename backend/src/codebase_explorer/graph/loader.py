"""Neo4j schema + idempotent batched loader (ADR-0004)."""

from __future__ import annotations

from typing import Any

from neo4j import Driver, GraphDatabase

from codebase_explorer.ingestion.graph_builder import GraphData

LABELS = ["Repository", "File", "Class", "Function", "Route", "Module"]
INDEXED = ["File", "Class", "Function", "Route"]
BATCH = 1000

# (rel type, src label, dst label, GraphData attribute, extra SET clause)
EDGES: list[tuple[str, str, str, str, str]] = [
    ("CONTAINS", "Repository", "File", "contains", ""),
    ("DEFINES", "File", "Class", "defines_class", ""),
    ("DEFINES", "File", "Function", "defines_function", ""),
    ("IMPORTS", "File", "File", "imports_file", ""),
    ("IMPORTS", "File", "Module", "imports_module", ""),
    ("CALLS", "Function", "Function", "calls", "SET rel.confidence = r.confidence"),
    ("INHERITS", "Class", "Class", "inherits", ""),
    ("HANDLED_BY", "Route", "Function", "handled_by", ""),
]
NODES: list[tuple[str, str]] = [
    ("File", "files"), ("Class", "classes"), ("Function", "functions"),
    ("Route", "routes"), ("Module", "modules"),
]


class GraphLoader:
    def __init__(self, uri: str, user: str, password: str) -> None:
        self.driver: Driver = GraphDatabase.driver(uri, auth=(user, password))

    def close(self) -> None:
        self.driver.close()

    def ensure_schema(self) -> None:
        with self.driver.session() as s:
            for label in LABELS:
                s.run(
                    f"CREATE CONSTRAINT {label.lower()}_id IF NOT EXISTS "
                    f"FOR (n:{label}) REQUIRE n.id IS UNIQUE"
                ).consume()
            for label in INDEXED:
                for prop in ("qualified_name", "name"):
                    s.run(
                        f"CREATE INDEX {label.lower()}_{prop} IF NOT EXISTS "
                        f"FOR (n:{label}) ON (n.{prop})"
                    ).consume()

    def clear_repo(self, repo_id: str) -> None:
        with self.driver.session() as s:
            s.run(
                "MATCH (n {repo_id: $repo_id}) "
                "CALL { WITH n DETACH DELETE n } IN TRANSACTIONS OF 5000 ROWS",
                repo_id=repo_id,
            ).consume()

    def _batched(self, query: str, rows: list[dict[str, Any]]) -> None:
        with self.driver.session() as s:
            for i in range(0, len(rows), BATCH):
                s.run(query, rows=rows[i:i + BATCH]).consume()

    def load(self, data: GraphData, clear: bool = True) -> None:
        self.ensure_schema()
        if clear:
            self.clear_repo(data.repo_id)
        self._batched(
            "UNWIND $rows AS r MERGE (n:Repository {id: r.id}) SET n += r", [data.repository]
        )
        for label, attr in NODES:
            self._batched(
                f"UNWIND $rows AS r MERGE (n:{label} {{id: r.id}}) SET n += r",
                getattr(data, attr),
            )
        for rel, src, dst, attr, extra in EDGES:
            self._batched(
                f"UNWIND $rows AS r MATCH (a:{src} {{id: r.src}}) MATCH (b:{dst} {{id: r.dst}}) "
                f"MERGE (a)-[rel:{rel}]->(b) {extra}",
                getattr(data, attr),
            )

