"""Neo4j schema + idempotent batched loader (ADR-0004)."""

from __future__ import annotations

from typing import Any

from neo4j import Driver, GraphDatabase

from codebase_explorer.ingestion.graph_builder import GraphData

ALL_NODE_LABELS = ["Repository", "File", "Class", "Function", "Route", "Module"]
INDEXED_NODE_LABELS = ["File", "Class", "Function", "Route"]
BATCH_SIZE = 1000

# Each tuple: (relationship_type, source_label, destination_label, GraphData_attribute, extra_SET_clause)
EDGE_DEFINITIONS: list[tuple[str, str, str, str, str]] = [
    ("CONTAINS",   "Repository", "File",     "contains",          ""),
    ("DEFINES",    "File",       "Class",    "defines_class",     ""),
    ("DEFINES",    "File",       "Function", "defines_function",  ""),
    ("IMPORTS",    "File",       "File",     "imports_file",      ""),
    ("IMPORTS",    "File",       "Module",   "imports_module",    ""),
    ("CALLS",      "Function",   "Function", "calls",             "SET rel.confidence = r.confidence"),
    ("INHERITS",   "Class",      "Class",    "inherits",          ""),
    ("HANDLED_BY", "Route",      "Function", "handled_by",        ""),
]
NODE_DEFINITIONS: list[tuple[str, str]] = [
    ("File",     "files"),
    ("Class",    "classes"),
    ("Function", "functions"),
    ("Route",    "routes"),
    ("Module",   "modules"),
]


class GraphLoader:
    def __init__(self, uri: str, user: str, password: str) -> None:
        self.driver: Driver = GraphDatabase.driver(uri, auth=(user, password))

    def close(self) -> None:
        self.driver.close()

    def ensure_schema(self) -> None:
        with self.driver.session() as session:
            for label in ALL_NODE_LABELS:
                session.run(
                    f"CREATE CONSTRAINT {label.lower()}_id IF NOT EXISTS "
                    f"FOR (n:{label}) REQUIRE n.id IS UNIQUE"
                ).consume()
            for label in INDEXED_NODE_LABELS:
                for property_name in ("qualified_name", "name"):
                    session.run(
                        f"CREATE INDEX {label.lower()}_{property_name} IF NOT EXISTS "
                        f"FOR (n:{label}) ON (n.{property_name})"
                    ).consume()

    def clear_repo(self, repo_id: str) -> None:
        with self.driver.session() as session:
            session.run(
                "MATCH (n {repo_id: $repo_id}) "
                "CALL { WITH n DETACH DELETE n } IN TRANSACTIONS OF 5000 ROWS",
                repo_id=repo_id,
            ).consume()

    def _execute_batched(self, query: str, rows: list[dict[str, Any]]) -> None:
        with self.driver.session() as session:
            for batch_start in range(0, len(rows), BATCH_SIZE):
                session.run(query, rows=rows[batch_start:batch_start + BATCH_SIZE]).consume()

    def load(self, graph_data: GraphData, clear: bool = True) -> None:
        self.ensure_schema()
        if clear:
            self.clear_repo(graph_data.repo_id)
        self._execute_batched(
            "UNWIND $rows AS r MERGE (n:Repository {id: r.id}) SET n += r",
            [graph_data.repository],
        )
        for node_label, data_attribute in NODE_DEFINITIONS:
            self._execute_batched(
                f"UNWIND $rows AS r MERGE (n:{node_label} {{id: r.id}}) SET n += r",
                getattr(graph_data, data_attribute),
            )
        for rel_type, src_label, dst_label, data_attribute, extra_set in EDGE_DEFINITIONS:
            self._execute_batched(
                f"UNWIND $rows AS r "
                f"MATCH (source:{src_label} {{id: r.src}}) "
                f"MATCH (destination:{dst_label} {{id: r.dst}}) "
                f"MERGE (source)-[rel:{rel_type}]->(destination) {extra_set}",
                getattr(graph_data, data_attribute),
            )
