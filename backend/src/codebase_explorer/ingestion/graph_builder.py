"""Assemble parsed files + resolver output into graph-ready rows (ADR-0004)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from codebase_explorer.ingestion.repo_source import scan_python_files
from codebase_explorer.models import Entity, ParsedFile, make_id
from codebase_explorer.parser.python_parser import PythonParser
from codebase_explorer.parser.resolver import STDLIB, Resolver

Row = dict[str, Any]


@dataclass
class GraphData:
    repo_id: str
    repository: Row = field(default_factory=dict)
    files: list[Row] = field(default_factory=list)
    classes: list[Row] = field(default_factory=list)
    functions: list[Row] = field(default_factory=list)
    routes: list[Row] = field(default_factory=list)
    modules: list[Row] = field(default_factory=list)
    contains: list[Row] = field(default_factory=list)
    defines_class: list[Row] = field(default_factory=list)
    defines_function: list[Row] = field(default_factory=list)
    imports_file: list[Row] = field(default_factory=list)
    imports_module: list[Row] = field(default_factory=list)
    calls: list[Row] = field(default_factory=list)
    inherits: list[Row] = field(default_factory=list)
    handled_by: list[Row] = field(default_factory=list)
    entities: list[Entity] = field(default_factory=list)  # kept for the vector stage

    def summary(self) -> dict[str, int]:
        keys = ["files", "classes", "functions", "routes", "modules", "imports_file",
                "imports_module", "calls", "inherits", "handled_by"]
        out = {k: len(getattr(self, k)) for k in keys}
        out["calls_heuristic"] = sum(1 for c in self.calls if c["confidence"] == "heuristic")
        return out


def parse_repo(repo_id: str, root: Path) -> list[ParsedFile]:
    parser = PythonParser(repo_id)
    parsed: list[ParsedFile] = []
    for rel in scan_python_files(root):
        try:
            parsed.append(parser.parse_file(rel, (root / rel).read_bytes()))
        except Exception as exc:  # one bad file must not abort ingestion
            print(f"[warn] failed to parse {rel}: {exc}")
    return parsed


def build_graph_data(repo_id: str, root: Path, repo_name: str | None = None) -> GraphData:
    parsed = parse_repo(repo_id, root)
    resolver = Resolver(parsed)
    data = GraphData(repo_id=repo_id)

    repo_node_id = make_id(repo_id, "Repository")
    data.repository = {"id": repo_node_id, "name": repo_name or repo_id,
                       "qualified_name": repo_id, "repo_id": repo_id,
                       "root_path": str(root)}

    file_id = {pf.file_path: make_id(repo_id, "File", pf.file_path) for pf in parsed}
    for pf in parsed:
        fid = file_id[pf.file_path]
        data.files.append({
            "id": fid, "name": PurePosixPath(pf.file_path).name,
            "qualified_name": pf.module_qn or pf.file_path, "file_path": pf.file_path,
            "start_line": 1, "end_line": pf.loc, "repo_id": repo_id, "hash": pf.file_hash,
        })
        data.contains.append({"src": repo_node_id, "dst": fid})
        for e in pf.entities:
            data.entities.append(e)
            if e.kind == "Class":
                data.classes.append(e.node_props())
                data.defines_class.append({"src": fid, "dst": e.id})
            else:
                data.functions.append(e.node_props())
                data.defines_function.append({"src": fid, "dst": e.id})

    _imports(data, parsed, resolver, file_id)
    _calls(data, parsed, resolver)
    _inherits(data, parsed, resolver)
    _routes(data, parsed, resolver)
    return data


def _imports(data: GraphData, parsed: list[ParsedFile], r: Resolver, file_id: dict[str, str]) -> None:
    seen_file: set[tuple[str, str]] = set()
    seen_mod: set[tuple[str, str]] = set()
    modules: dict[str, Row] = {}
    for pf in parsed:
        src = file_id[pf.file_path]
        for imp in pf.imports:
            target = r.import_target(pf, imp)
            dst_file: str | None = None
            if target is not None:
                candidates = [target]
                if imp.name:
                    candidates.insert(0, f"{target}.{imp.name}" if target else imp.name)
                for c in candidates:  # prefer submodule when `from pkg import submodule`
                    if c in r.modules:
                        dst_file = file_id[r.modules[c]]
                        break
            if dst_file is None and imp.name and target is None:
                sub = r.resolve_module(f"{imp.module}.{imp.name}".strip("."), pf.module_qn)
                if sub:
                    dst_file = file_id[r.modules[sub]]
            if dst_file is not None:
                if dst_file != src and (src, dst_file) not in seen_file:
                    seen_file.add((src, dst_file))
                    data.imports_file.append({"src": src, "dst": dst_file})
                continue
            if imp.level > 0 or not imp.module:
                continue  # unresolved relative import
            top = imp.module.split(".")[0]
            if top in STDLIB:
                continue
            mid = make_id(data.repo_id, "Module", top)
            if top not in modules:
                modules[top] = {"id": mid, "name": top, "qualified_name": top,
                                "file_path": "", "start_line": 0, "end_line": 0,
                                "repo_id": data.repo_id}
            if (src, mid) not in seen_mod:
                seen_mod.add((src, mid))
                data.imports_module.append({"src": src, "dst": mid})
    data.modules = list(modules.values())


def _calls(data: GraphData, parsed: list[ParsedFile], r: Resolver) -> None:
    best: dict[tuple[str, str], str] = {}
    for pf in parsed:
        local = {e.qualified_name: e for e in pf.entities if e.kind == "Function"}
        for cs in pf.calls:
            caller = local.get(cs.caller_qn)
            if caller is None:
                continue
            for target, conf in r.resolve_call(cs.expr, caller, pf):
                key = (caller.id, target.id)
                if best.get(key) != "resolved":
                    best[key] = conf
    data.calls = [{"src": s, "dst": d, "confidence": c} for (s, d), c in best.items()]


def _inherits(data: GraphData, parsed: list[ParsedFile], r: Resolver) -> None:
    seen: set[tuple[str, str]] = set()
    for pf in parsed:
        for b in pf.bases:
            child = r.class_entity(b.class_qn)
            if child is None:
                continue
            for parent_qn in r.base_classes(b.class_qn):
                parent = r.class_entity(parent_qn)
                if parent and (child.id, parent.id) not in seen:
                    seen.add((child.id, parent.id))
                    data.inherits.append({"src": child.id, "dst": parent.id})


def _routes(data: GraphData, parsed: list[ParsedFile], r: Resolver) -> None:
    for pf in parsed:
        for rt in pf.routes:
            handler = next((e for e in r.by_qn.get(rt.handler_qn, []) if e.kind == "Function"), None)
            if handler is None:
                continue
            rid = make_id(data.repo_id, "Route", rt.file_path, rt.method, rt.path, rt.handler_qn)
            data.routes.append({
                "id": rid, "name": f"{rt.method} {rt.path}",
                "qualified_name": f"{rt.method} {rt.path}", "file_path": rt.file_path,
                "start_line": rt.line, "end_line": rt.line, "repo_id": data.repo_id,
                "method": rt.method, "path": rt.path,
            })
            data.handled_by.append({"src": rid, "dst": handler.id})

