from pathlib import Path

import pytest

from codebase_explorer.ingestion.graph_builder import GraphData, build_graph_data

FIXTURE = Path(__file__).parent / "fixtures" / "sample_app"


@pytest.fixture(scope="module")
def graph() -> GraphData:
    return build_graph_data("sample", FIXTURE)


def _names(graph: GraphData) -> dict[str, str]:
    return {f["id"]: f["qualified_name"] for f in graph.functions} | {
        c["id"]: c["qualified_name"] for c in graph.classes
    }


def _calls(graph: GraphData) -> set[tuple[str, str, str]]:
    n = _names(graph)
    return {(n[c["src"]], n[c["dst"]], c["confidence"]) for c in graph.calls}


def test_entities_extracted(graph: GraphData) -> None:
    n = set(_names(graph).values())
    assert {"app.models.User", "app.models.Base", "app.auth.JWTGenerator"} <= n
    assert {"app.models.User.greet", "app.auth.create_token", "app.main.token"} <= n


def test_deterministic_ids() -> None:
    a = build_graph_data("sample", FIXTURE)
    b = build_graph_data("sample", FIXTURE)
    assert {f["id"] for f in a.functions} == {f["id"] for f in b.functions}


def test_calls_resolved(graph: GraphData) -> None:
    calls = _calls(graph)
    assert ("app.main.token", "app.auth.create_token", "resolved") in calls
    assert ("app.main.token", "app.main.get_user", "resolved") in calls  # Depends()
    assert ("app.auth.JWTGenerator.generate", "app.utils.helper", "resolved") in calls
    assert ("app.models.User.greet", "app.models.Base.save", "resolved") in calls  # inherited
    assert ("app.models.User.__init__", "app.models.Base.__init__", "resolved") in calls  # super()


def test_calls_heuristic(graph: GraphData) -> None:
    assert (
        "app.auth.create_token", "app.auth.JWTGenerator.generate", "heuristic"
    ) in _calls(graph)


def test_inherits_and_route(graph: GraphData) -> None:
    n = _names(graph)
    assert {(n[i["src"]], n[i["dst"]]) for i in graph.inherits} == {
        ("app.models.User", "app.models.Base")
    }
    assert [r["name"] for r in graph.routes] == ["GET /token"]
    assert n[graph.handled_by[0]["dst"]] == "app.main.token"


def test_imports(graph: GraphData) -> None:
    files = {f["id"]: f["file_path"] for f in graph.files}
    edges = {(files[i["src"]], files[i["dst"]]) for i in graph.imports_file}
    assert ("app/auth.py", "app/models.py") in edges
    assert ("app/auth.py", "app/utils.py") in edges
    assert ("app/main.py", "app/auth.py") in edges
    assert {m["name"] for m in graph.modules} == {"fastapi"}  # os is stdlib -> dropped

