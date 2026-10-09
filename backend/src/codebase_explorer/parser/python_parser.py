"""Phase A (ADR-0003): per-file Tree-sitter extraction for Python."""

from __future__ import annotations

import hashlib
import re
from pathlib import PurePosixPath

import tree_sitter_python as tspython
from tree_sitter import Language, Node, Parser

from codebase_explorer.models import (
    BaseRef,
    CallSite,
    Entity,
    ImportRef,
    ParsedFile,
    RouteDef,
    join_qn,
    make_id,
)

PYTHON_LANGUAGE = Language(tspython.language())
DOTTED_NAME_PATTERN = re.compile(r"^[A-Za-z_]\w*(\.[A-Za-z_]\w*)*$")
HTTP_METHODS = {"get", "post", "put", "delete", "patch", "options", "head"}
DEPENDENCY_INJECTION_FUNCS = {"Depends", "Security"}


def module_qn_for(path: str) -> tuple[str, bool]:
    parts = list(PurePosixPath(path).with_suffix("").parts)
    is_package = bool(parts) and parts[-1] == "__init__"
    if is_package:
        parts = parts[:-1]
    return ".".join(parts), is_package


def node_text(node: Node) -> str:
    """Decode a Tree-sitter node's source bytes to a string."""
    return (node.text or b"").decode("utf-8", "replace")


def extract_string_value(node: Node | None) -> str | None:
    """Return the inner text of a Tree-sitter string node, or None."""
    if node is None or node.type != "string":
        return None
    return "".join(node_text(child) for child in node.children if child.type == "string_content")


class PythonParser:
    def __init__(self, repo_id: str) -> None:
        self.repo_id = repo_id
        self._parser = Parser(PYTHON_LANGUAGE)

    def parse_file(self, file_path: str, source: bytes) -> ParsedFile:
        tree = self._parser.parse(source)
        module_qn, is_package = module_qn_for(file_path)
        parsed_file = ParsedFile(
            file_path=file_path,
            module_qn=module_qn,
            is_package=is_package,
            file_hash=hashlib.sha256(source).hexdigest(),
            loc=source.count(b"\n") + 1,
        )
        self._current_file = parsed_file
        self._source = source
        self._walk(tree.root_node, (), None, None, False)
        return parsed_file

    # ------------------------------------------------------------------ walk
    def _walk(
        self,
        node: Node,
        scope: tuple[str, ...],
        enclosing_class_qn: str | None,
        caller_qn: str | None,
        in_class_body: bool,
    ) -> None:
        node_type = node.type
        if node_type == "decorated_definition":
            decorators = [child for child in node.children if child.type == "decorator"]
            definition = node.child_by_field_name("definition")
            if definition is not None and definition.type == "function_definition":
                self._handle_function(definition, node, decorators, scope, enclosing_class_qn, in_class_body)
                return
            if definition is not None and definition.type == "class_definition":
                self._handle_class(definition, node, scope)
                return
        elif node_type == "function_definition":
            self._handle_function(node, node, [], scope, enclosing_class_qn, in_class_body)
            return
        elif node_type == "class_definition":
            self._handle_class(node, node, scope)
            return
        elif node_type == "import_statement":
            self._handle_import(node)
            return
        elif node_type == "import_from_statement":
            self._handle_import_from(node)
            return
        elif node_type == "call":
            self._handle_call(node, caller_qn)
        for child in node.children:
            self._walk(child, scope, enclosing_class_qn, caller_qn, False)

    # ------------------------------------------------------------ definitions
    def _make_entity(
        self,
        kind: str,
        definition_node: Node,
        outer_node: Node,
        scope: tuple[str, ...],
        enclosing_class_qn: str | None,
        is_method: bool,
    ) -> Entity:
        name_node = definition_node.child_by_field_name("name")
        assert name_node is not None
        name = node_text(name_node)
        local_qn = ".".join((*scope, name))
        body_node = definition_node.child_by_field_name("body")
        signature_end_byte = body_node.start_byte if body_node is not None else definition_node.end_byte
        raw_signature = self._source[definition_node.start_byte:signature_end_byte].decode("utf-8", "replace").strip()
        clean_signature = " ".join(raw_signature.rstrip(":").strip().split())
        return Entity(
            id=make_id(self.repo_id, kind, self._current_file.file_path, local_qn),
            kind=kind,
            name=name,
            qualified_name=join_qn(self._current_file.module_qn, local_qn),
            local_qn=local_qn,
            file_path=self._current_file.file_path,
            start_line=outer_node.start_point[0] + 1,
            end_line=outer_node.end_point[0] + 1,
            repo_id=self.repo_id,
            signature=clean_signature,
            docstring=self._extract_docstring(body_node),
            code=self._source[outer_node.start_byte:outer_node.end_byte].decode("utf-8", "replace"),
            class_qn=enclosing_class_qn,
            is_method=is_method,
        )

    @staticmethod
    def _extract_docstring(body_node: Node | None) -> str:
        if body_node is None or not body_node.named_children:
            return ""
        first_statement = body_node.named_children[0]
        if first_statement.type == "expression_statement" and first_statement.named_children:
            string_value = extract_string_value(first_statement.named_children[0])
            if string_value is not None:
                return string_value.strip()
        return ""

    def _handle_function(
        self,
        definition_node: Node,
        outer_node: Node,
        decorators: list[Node],
        scope: tuple[str, ...],
        enclosing_class_qn: str | None,
        in_class_body: bool,
    ) -> None:
        entity = self._make_entity(
            "Function", definition_node, outer_node, scope,
            enclosing_class_qn, in_class_body and enclosing_class_qn is not None,
        )
        self._current_file.entities.append(entity)
        for decorator in decorators:
            self._handle_route_decorator(decorator, entity)
        inner_scope = (*scope, entity.name)
        for field_name in ("parameters", "body"):
            child_node = definition_node.child_by_field_name(field_name)
            if child_node is not None:
                self._walk(child_node, inner_scope, enclosing_class_qn, entity.qualified_name, False)

    def _handle_class(self, definition_node: Node, outer_node: Node, scope: tuple[str, ...]) -> None:
        entity = self._make_entity("Class", definition_node, outer_node, scope, None, False)
        self._current_file.entities.append(entity)
        superclasses_node = definition_node.child_by_field_name("superclasses")
        if superclasses_node is not None:
            for child in superclasses_node.named_children:
                if child.type in ("identifier", "attribute") and DOTTED_NAME_PATTERN.match(node_text(child)):
                    self._current_file.bases.append(BaseRef(entity.qualified_name, node_text(child)))
        body_node = definition_node.child_by_field_name("body")
        if body_node is not None:
            for child in body_node.children:
                self._walk(child, (*scope, entity.name), entity.qualified_name, None, True)

    # ----------------------------------------------------------------- routes
    def _handle_route_decorator(self, decorator_node: Node, handler_entity: Entity) -> None:
        if not decorator_node.named_children:
            return
        decorator_call = decorator_node.named_children[0]
        if decorator_call.type != "call":
            return
        function_node = decorator_call.child_by_field_name("function")
        if function_node is None or function_node.type != "attribute":
            return
        attribute_node = function_node.child_by_field_name("attribute")
        if attribute_node is None or node_text(attribute_node) not in HTTP_METHODS:
            return
        arguments_node = decorator_call.child_by_field_name("arguments")
        first_argument = arguments_node.named_children[0] if arguments_node is not None and arguments_node.named_children else None
        route_path = extract_string_value(first_argument)
        if route_path is None:
            return
        self._current_file.routes.append(
            RouteDef(
                node_text(attribute_node).upper(),
                route_path,
                handler_entity.qualified_name,
                self._current_file.file_path,
                decorator_node.start_point[0] + 1,
            )
        )

    # ------------------------------------------------------------------ calls
    def _normalise_call_expression(self, node: Node) -> str | None:
        """Normalise a call-target node to a dotted string suitable for resolution."""
        text = node_text(node)
        if node.type in ("identifier", "attribute") and DOTTED_NAME_PATTERN.match(text):
            return text
        if node.type == "attribute":
            attribute_node = node.child_by_field_name("attribute")
            object_node = node.child_by_field_name("object")
            if attribute_node is None:
                return None
            if object_node is not None and object_node.type == "call":
                function_node = object_node.child_by_field_name("function")
                if function_node is not None and node_text(function_node) == "super":
                    return f"super.{node_text(attribute_node)}"
            return f"?.{node_text(attribute_node)}"
        return None

    def _handle_call(self, call_node: Node, caller_qn: str | None) -> None:
        function_node = call_node.child_by_field_name("function")
        if caller_qn is None or function_node is None:
            return
        call_line = call_node.start_point[0] + 1
        call_expression = self._normalise_call_expression(function_node)
        if call_expression is None:
            return
        self._current_file.calls.append(CallSite(caller_qn, call_expression, call_line))
        # Capture FastAPI Depends()/Security() dependency as an additional call edge
        if call_expression.split(".")[-1] in DEPENDENCY_INJECTION_FUNCS:
            arguments_node = call_node.child_by_field_name("arguments")
            if arguments_node is not None:
                for argument in arguments_node.named_children:
                    if argument.type in ("identifier", "attribute"):
                        dependency_expr = self._normalise_call_expression(argument)
                        if dependency_expr and not dependency_expr.startswith("?."):
                            self._current_file.calls.append(CallSite(caller_qn, dependency_expr, call_line))
                        break

    # ---------------------------------------------------------------- imports
    def _handle_import(self, import_node: Node) -> None:
        line = import_node.start_point[0] + 1
        for child in import_node.named_children:
            if child.type == "dotted_name":
                self._current_file.imports.append(ImportRef(0, node_text(child), None, None, line))
            elif child.type == "aliased_import":
                name_node = child.child_by_field_name("name")
                alias_node = child.child_by_field_name("alias")
                if name_node is not None:
                    self._current_file.imports.append(
                        ImportRef(0, node_text(name_node), None,
                                  node_text(alias_node) if alias_node is not None else None, line)
                    )

    def _handle_import_from(self, import_from_node: Node) -> None:
        line = import_from_node.start_point[0] + 1
        module_name_node = import_from_node.child_by_field_name("module_name")
        relative_level, module_name = 0, ""
        if module_name_node is not None:
            if module_name_node.type == "relative_import":
                import_prefix = next(
                    (child for child in module_name_node.children if child.type == "import_prefix"), None
                )
                relative_level = node_text(import_prefix).count(".") if import_prefix is not None else 1
                dotted_name_node = next(
                    (child for child in module_name_node.children if child.type == "dotted_name"), None
                )
                module_name = node_text(dotted_name_node) if dotted_name_node is not None else ""
            else:
                module_name = node_text(module_name_node)
        for imported_name in import_from_node.children_by_field_name("name"):
            if imported_name.type == "dotted_name":
                self._current_file.imports.append(
                    ImportRef(relative_level, module_name, node_text(imported_name), None, line)
                )
            elif imported_name.type == "aliased_import":
                name_node = imported_name.child_by_field_name("name")
                alias_node = imported_name.child_by_field_name("alias")
                if name_node is not None:
                    self._current_file.imports.append(
                        ImportRef(relative_level, module_name, node_text(name_node),
                                  node_text(alias_node) if alias_node is not None else None, line)
                    )
