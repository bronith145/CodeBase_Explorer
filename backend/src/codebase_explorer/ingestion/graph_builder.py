"""Assemble parsed files + resolver output into graph-ready rows (ADR-0004)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from codebase_explorer.ingestion.repo_source import scan_python_files
from codebase_explorer.models import Entity, ParsedFile, make_id
from codebase_explorer.parser.python_parser import PythonParser
from codebase_explorer.parser.resolver import STDLIB_MODULES, Resolver

GraphRow = dict[str, Any]


@dataclass
class GraphData:
    repo_id: str
    repository: GraphRow = field(default_factory=dict)
    files: list[GraphRow] = field(default_factory=list)
    classes: list[GraphRow] = field(default_factory=list)
    functions: list[GraphRow] = field(default_factory=list)
    routes: list[GraphRow] = field(default_factory=list)
    modules: list[GraphRow] = field(default_factory=list)
    contains: list[GraphRow] = field(default_factory=list)
    defines_class: list[GraphRow] = field(default_factory=list)
    defines_function: list[GraphRow] = field(default_factory=list)
    imports_file: list[GraphRow] = field(default_factory=list)
    imports_module: list[GraphRow] = field(default_factory=list)
    calls: list[GraphRow] = field(default_factory=list)
    inherits: list[GraphRow] = field(default_factory=list)
    handled_by: list[GraphRow] = field(default_factory=list)
    entities: list[Entity] = field(default_factory=list)  # kept for the vector stage

    def summary(self) -> dict[str, int]:
        count_keys = [
            "files", "classes", "functions", "routes", "modules",
            "imports_file", "imports_module", "calls", "inherits", "handled_by",
        ]
        counts = {key: len(getattr(self, key)) for key in count_keys}
        counts["calls_heuristic"] = sum(
            1 for call_row in self.calls if call_row["confidence"] == "heuristic"
        )
        return counts


def parse_repo(repo_id: str, root: Path) -> list[ParsedFile]:
    parser = PythonParser(repo_id)
    parsed_files: list[ParsedFile] = []
    for relative_path in scan_python_files(root):
        try:
            parsed_files.append(parser.parse_file(relative_path, (root / relative_path).read_bytes()))
        except Exception as exc:  # one bad file must not abort ingestion
            print(f"[warn] failed to parse {relative_path}: {exc}")
    return parsed_files


def build_graph_data(repo_id: str, root: Path, repo_name: str | None = None) -> GraphData:
    parsed_files = parse_repo(repo_id, root)
    resolver = Resolver(parsed_files)
    graph_data = GraphData(repo_id=repo_id)

    repository_node_id = make_id(repo_id, "Repository")
    graph_data.repository = {
        "id": repository_node_id,
        "name": repo_name or repo_id,
        "qualified_name": repo_id,
        "repo_id": repo_id,
        "root_path": str(root),
    }

    file_node_ids = {
        parsed_file.file_path: make_id(repo_id, "File", parsed_file.file_path)
        for parsed_file in parsed_files
    }
    for parsed_file in parsed_files:
        file_node_id = file_node_ids[parsed_file.file_path]
        graph_data.files.append({
            "id": file_node_id,
            "name": PurePosixPath(parsed_file.file_path).name,
            "qualified_name": parsed_file.module_qn or parsed_file.file_path,
            "file_path": parsed_file.file_path,
            "start_line": 1,
            "end_line": parsed_file.loc,
            "repo_id": repo_id,
            "hash": parsed_file.file_hash,
        })
        graph_data.contains.append({"src": repository_node_id, "dst": file_node_id})
        for entity in parsed_file.entities:
            graph_data.entities.append(entity)
            if entity.kind == "Class":
                graph_data.classes.append(entity.node_props())
                graph_data.defines_class.append({"src": file_node_id, "dst": entity.id})
            else:
                graph_data.functions.append(entity.node_props())
                graph_data.defines_function.append({"src": file_node_id, "dst": entity.id})

    _build_import_edges(graph_data, parsed_files, resolver, file_node_ids)
    _build_call_edges(graph_data, parsed_files, resolver)
    _build_inherits_edges(graph_data, parsed_files, resolver)
    _build_route_edges(graph_data, parsed_files, resolver)
    return graph_data


def _build_import_edges(
    graph_data: GraphData,
    parsed_files: list[ParsedFile],
    resolver: Resolver,
    file_node_ids: dict[str, str],
) -> None:
    seen_file_edges: set[tuple[str, str]] = set()
    seen_module_edges: set[tuple[str, str]] = set()
    third_party_modules: dict[str, GraphRow] = {}

    for parsed_file in parsed_files:
        source_file_id = file_node_ids[parsed_file.file_path]
        for import_ref in parsed_file.imports:
            target_module = resolver.import_target(parsed_file, import_ref)
            destination_file_id: str | None = None

            if target_module is not None:
                # Prefer the more specific submodule path when `from pkg import submodule`.
                module_candidates = [target_module]
                if import_ref.name:
                    submodule_qn = f"{target_module}.{import_ref.name}" if target_module else import_ref.name
                    module_candidates.insert(0, submodule_qn)
                for candidate_module in module_candidates:
                    if candidate_module in resolver.modules:
                        destination_file_id = file_node_ids[resolver.modules[candidate_module]]
                        break

            if destination_file_id is None and import_ref.name and target_module is None:
                fallback_module = resolver.resolve_module(
                    f"{import_ref.module}.{import_ref.name}".strip("."), parsed_file.module_qn
                )
                if fallback_module:
                    destination_file_id = file_node_ids[resolver.modules[fallback_module]]

            if destination_file_id is not None:
                edge_key = (source_file_id, destination_file_id)
                if destination_file_id != source_file_id and edge_key not in seen_file_edges:
                    seen_file_edges.add(edge_key)
                    graph_data.imports_file.append({"src": source_file_id, "dst": destination_file_id})
                continue

            if import_ref.level > 0 or not import_ref.module:
                continue  # unresolved relative import
            top_level_package = import_ref.module.split(".")[0]
            if top_level_package in STDLIB_MODULES:
                continue
            module_node_id = make_id(graph_data.repo_id, "Module", top_level_package)
            if top_level_package not in third_party_modules:
                third_party_modules[top_level_package] = {
                    "id": module_node_id,
                    "name": top_level_package,
                    "qualified_name": top_level_package,
                    "file_path": "",
                    "start_line": 0,
                    "end_line": 0,
                    "repo_id": graph_data.repo_id,
                }
            module_edge_key = (source_file_id, module_node_id)
            if module_edge_key not in seen_module_edges:
                seen_module_edges.add(module_edge_key)
                graph_data.imports_module.append({"src": source_file_id, "dst": module_node_id})

    graph_data.modules = list(third_party_modules.values())


def _build_call_edges(
    graph_data: GraphData,
    parsed_files: list[ParsedFile],
    resolver: Resolver,
) -> None:
    # Keep the best confidence per (caller_id, callee_id) pair: "resolved" wins over "heuristic".
    best_confidence: dict[tuple[str, str], str] = {}
    for parsed_file in parsed_files:
        functions_by_qn = {entity.qualified_name: entity for entity in parsed_file.entities if entity.kind == "Function"}
        for call_site in parsed_file.calls:
            caller_entity = functions_by_qn.get(call_site.caller_qn)
            if caller_entity is None:
                continue
            for target_entity, confidence in resolver.resolve_call(call_site.expr, caller_entity, parsed_file):
                edge_key = (caller_entity.id, target_entity.id)
                if best_confidence.get(edge_key) != "resolved":
                    best_confidence[edge_key] = confidence
    graph_data.calls = [
        {"src": caller_id, "dst": callee_id, "confidence": confidence}
        for (caller_id, callee_id), confidence in best_confidence.items()
    ]


def _build_inherits_edges(
    graph_data: GraphData,
    parsed_files: list[ParsedFile],
    resolver: Resolver,
) -> None:
    seen_inherits_edges: set[tuple[str, str]] = set()
    for parsed_file in parsed_files:
        for base_ref in parsed_file.bases:
            child_class = resolver.class_entity(base_ref.class_qn)
            if child_class is None:
                continue
            for parent_class_qn in resolver.base_classes(base_ref.class_qn):
                parent_class = resolver.class_entity(parent_class_qn)
                edge_key = (child_class.id, parent_class.id) if parent_class else None
                if parent_class and edge_key not in seen_inherits_edges:
                    seen_inherits_edges.add(edge_key)  # type: ignore[arg-type]
                    graph_data.inherits.append({"src": child_class.id, "dst": parent_class.id})


def _build_route_edges(
    graph_data: GraphData,
    parsed_files: list[ParsedFile],
    resolver: Resolver,
) -> None:
    for parsed_file in parsed_files:
        for route_def in parsed_file.routes:
            handler_entity = next(
                (entity for entity in resolver.by_qualified_name.get(route_def.handler_qn, [])
                 if entity.kind == "Function"),
                None,
            )
            if handler_entity is None:
                continue
            route_node_id = make_id(
                graph_data.repo_id, "Route",
                route_def.file_path, route_def.method, route_def.path, route_def.handler_qn,
            )
            graph_data.routes.append({
                "id": route_node_id,
                "name": f"{route_def.method} {route_def.path}",
                "qualified_name": f"{route_def.method} {route_def.path}",
                "file_path": route_def.file_path,
                "start_line": route_def.line,
                "end_line": route_def.line,
                "repo_id": graph_data.repo_id,
                "method": route_def.method,
                "path": route_def.path,
            })
            graph_data.handled_by.append({"src": route_node_id, "dst": handler_entity.id})
