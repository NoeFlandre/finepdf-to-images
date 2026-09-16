"""Architecture checks: the domain layer stays pure and the package stays acyclic."""

from __future__ import annotations

import ast
import pathlib

import pytest

SRC = pathlib.Path(__file__).resolve().parents[2] / "src" / "finepdf_to_images"

#: Anything that performs network, filesystem, PDF or Hub I/O must stay out of the domain.
FORBIDDEN_IN_DOMAIN = {
    "datasets",
    "fsspec",
    "httpx",
    "huggingface_hub",
    "os",
    "pathlib",
    "pyarrow",
    "pypdf",
    "requests",
    "shutil",
    "socket",
    "subprocess",
    "tempfile",
    "urllib",
}

pytestmark = pytest.mark.architecture


def _modules() -> dict[str, ast.Module]:
    return {
        str(path.relative_to(SRC).with_suffix("")).replace("/", "."): ast.parse(
            path.read_text(encoding="utf-8")
        )
        for path in sorted(SRC.rglob("*.py"))
    }


def _imported_roots(tree: ast.Module) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_source_tree_is_not_empty() -> None:
    assert _modules(), "no python modules found under src/finepdf_to_images"


def test_domain_modules_import_no_io_libraries() -> None:
    offences = {
        name: sorted(_imported_roots(tree) & FORBIDDEN_IN_DOMAIN)
        for name, tree in _modules().items()
        if name.startswith("domain")
    }
    assert {k: v for k, v in offences.items() if v} == {}


def test_domain_never_imports_adapters_or_cli() -> None:
    offences: dict[str, list[str]] = {}
    for name, tree in _modules().items():
        if not name.startswith("domain"):
            continue
        bad = sorted(
            target
            for target in _internal_targets(tree)
            if target.startswith(("adapters", "cli", "pipeline"))
        )
        if bad:
            offences[name] = bad
    assert offences == {}


def _internal_targets(tree: ast.Module) -> set[str]:
    targets: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            if node.module.startswith("finepdf_to_images."):
                targets.add(node.module.split(".", 1)[1])
        elif isinstance(node, ast.ImportFrom) and node.level and node.module:
            targets.add(node.module)
    return targets


def test_package_import_graph_is_acyclic() -> None:
    graph = {name: _internal_targets(tree) for name, tree in _modules().items()}
    known = set(graph)
    graph = {name: {t for t in targets if t in known} for name, targets in graph.items()}

    state: dict[str, int] = {}

    def visit(node: str, trail: tuple[str, ...]) -> None:
        if state.get(node) == 2:
            return
        assert state.get(node) != 1, f"import cycle: {' -> '.join([*trail, node])}"
        state[node] = 1
        for nxt in sorted(graph[node]):
            visit(nxt, (*trail, node))
        state[node] = 2

    for node in sorted(graph):
        visit(node, ())
