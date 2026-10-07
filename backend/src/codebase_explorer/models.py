"""Shared dataclasses describing parsed code (see ADR-0003 / ADR-0004)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any


def make_id(*parts: str) -> str:
    """Deterministic node id (ADR-0004)."""
    return hashlib.sha1("\x1f".join(parts).encode("utf-8")).hexdigest()[:20]


def join_qn(module_qn: str, local: str) -> str:
    if not module_qn:
        return local
    return f"{module_qn}.{local}" if local else module_qn


@dataclass
class Entity:
    id: str
    kind: str  # "Class" | "Function"
    name: str
    qualified_name: str
    local_qn: str
    file_path: str
    start_line: int
    end_line: int
    repo_id: str
    signature: str = ""
    docstring: str = ""
    code: str = ""
    class_qn: str | None = None  # enclosing class (full qn) – for self/super resolution
    is_method: bool = False

    def node_props(self) -> dict[str, Any]:
        props: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "qualified_name": self.qualified_name,
            "file_path": self.file_path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "repo_id": self.repo_id,
            "signature": self.signature,
            "docstring": self.docstring[:1000],
        }
        if self.kind == "Function":
            props["is_method"] = self.is_method
            props["class_qn"] = self.class_qn or ""
        return props


@dataclass
class ImportRef:
    level: int  # number of leading dots for relative imports
    module: str  # dotted module text ("" for `from . import x`)
    name: str | None  # imported symbol for `from m import name`; None for `import m`
    alias: str | None
    line: int


@dataclass
class CallSite:
    caller_qn: str
    expr: str  # "foo", "self.bar", "mod.func", "super.m", "?.method"
    line: int


@dataclass
class BaseRef:
    class_qn: str
    expr: str


@dataclass
class RouteDef:
    method: str
    path: str
    handler_qn: str
    file_path: str
    line: int


@dataclass
class ParsedFile:
    file_path: str
    module_qn: str
    is_package: bool
    file_hash: str
    loc: int
    entities: list[Entity] = field(default_factory=list)
    imports: list[ImportRef] = field(default_factory=list)
    calls: list[CallSite] = field(default_factory=list)
    bases: list[BaseRef] = field(default_factory=list)
    routes: list[RouteDef] = field(default_factory=list)

