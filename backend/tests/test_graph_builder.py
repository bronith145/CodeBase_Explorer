from pathlib import Path

import pytest

from codebase_explorer.ingestion.graph_builder import GraphData, build_graph_data

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sample_app"


@pytest.fixture(scope="module")
def graph() -> GraphData:
    return build_graph_data("sample", FIXTURE_PATH)


def _id_to_qualified_name(graph_data: GraphData) -> dict[str, str]:
    return (
        {function["id"]: function["qualified_name"] for function in graph_data.functions}
        | {class_row["id"]: class_row["qualified_name"] for class_row in graph_data.classes}
    )


def _call_triples(graph_data: GraphData) -> set[tuple[str, str, str]]:
    id_to_qn = _id_to_qualified_name(graph_data)
    return {
        (id_to_qn[call["src"]], id_to_qn[call["dst"]], call["confidence"])
        for call in graph_data.calls
    }


def test_entities_extracted(graph: GraphData) -> None:
    all_qualified_names = set(_id_to_qualified_name(graph).values())
    assert {"app.models.User", "app.models.Base", "app.auth.JWTGenerator"} <= all_qualified_names
    assert {"app.models.User.greet", "app.auth.create_token", "app.main.token"} <= all_qualified_names


def test_deterministic_ids() -> None:
    first_run = build_graph_data("sample", FIXTURE_PATH)
    second_run = build_graph_data("sample", FIXTURE_PATH)
    assert {fn["id"] for fn in first_run.functions} == {fn["id"] for fn in second_run.functions}


def test_calls_resolved(graph: GraphData) -> None:
    call_triples = _call_triples(graph)
    assert ("app.main.token", "app.auth.create_token", "resolved") in call_triples
    assert ("app.main.token", "app.main.get_user", "resolved") in call_triples          # Depends()
    assert ("app.auth.JWTGenerator.generate", "app.utils.helper", "resolved") in call_triples
    assert ("app.models.User.greet", "app.models.Base.save", "resolved") in call_triples   # inherited
    assert ("app.models.User.__init__", "app.models.Base.__init__", "resolved") in call_triples  # super()


def test_calls_heuristic(graph: GraphData) -> None:
    assert ("app.auth.create_token", "app.auth.JWTGenerator.generate", "heuristic") in _call_triples(graph)


def test_inherits_and_route(graph: GraphData) -> None:
    id_to_qn = _id_to_qualified_name(graph)
    assert {
        (id_to_qn[inherit["src"]], id_to_qn[inherit["dst"]])
        for inherit in graph.inherits
    } == {("app.models.User", "app.models.Base")}
    assert [route["name"] for route in graph.routes] == ["GET /token"]
    assert id_to_qn[graph.handled_by[0]["dst"]] == "app.main.token"


def test_imports(graph: GraphData) -> None:
    file_id_to_path = {file_row["id"]: file_row["file_path"] for file_row in graph.files}
    import_edges = {
        (file_id_to_path[edge["src"]], file_id_to_path[edge["dst"]])
        for edge in graph.imports_file
    }
    assert ("app/auth.py", "app/models.py") in import_edges
    assert ("app/auth.py", "app/utils.py") in import_edges
    assert ("app/main.py", "app/auth.py") in import_edges
    assert {module["name"] for module in graph.modules} == {"fastapi"}  # os is stdlib → dropped
