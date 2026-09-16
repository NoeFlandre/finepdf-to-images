"""Architecture checks: the domain layer stays pure and the package stays acyclic.

The analysis lives in :mod:`tests.architecture.boundaries` and is itself regression-tested in
:mod:`tests.architecture.test_boundaries_selfcheck`.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from tests.architecture.boundaries import (
    find_cycle,
    forbidden_hits,
    internal_targets,
    layer_of,
    module_name,
    package_name,
)

SRC = pathlib.Path(__file__).resolve().parents[2] / "src" / "finepdf_to_images"

#: The domain sits at the bottom of the dependency arrows; it may import nothing above it.
LAYERS_ABOVE_DOMAIN = frozenset({"adapters", "cli", "pipeline", "__main__"})

pytestmark = pytest.mark.architecture


def _modules() -> dict[str, tuple[str, ast.Module]]:
    """Map dotted module name -> (containing package, parsed tree)."""
    modules: dict[str, tuple[str, ast.Module]] = {}
    for path in sorted(SRC.rglob("*.py")):
        parts = path.relative_to(SRC).with_suffix("").parts
        modules[module_name(parts)] = (
            package_name(parts),
            ast.parse(path.read_text(encoding="utf-8")),
        )
    return modules


def _graph() -> dict[str, set[str]]:
    return {
        name: internal_targets(tree, package=package)
        for name, (package, tree) in _modules().items()
    }


def test_source_tree_contains_the_expected_layers() -> None:
    """Guards against a rename silently turning every check below into a no-op."""
    modules = _modules()
    assert modules, "no python modules found under src/finepdf_to_images"
    assert {"domain", "adapters", "cli"} <= set(modules)


def test_domain_modules_import_no_io_libraries() -> None:
    offences = {
        name: sorted(forbidden_hits(tree))
        for name, (_package, tree) in _modules().items()
        if layer_of(name) == "domain"
    }
    assert {name: bad for name, bad in offences.items() if bad} == {}


def test_domain_never_imports_an_outer_layer() -> None:
    offences = {
        name: sorted(target for target in targets if layer_of(target) in LAYERS_ABOVE_DOMAIN)
        for name, targets in _graph().items()
        if layer_of(name) == "domain"
    }
    assert {name: bad for name, bad in offences.items() if bad} == {}


def test_package_import_graph_is_acyclic() -> None:
    cycle = find_cycle(_graph())
    assert cycle is None, f"import cycle: {' -> '.join(cycle or [])}"


def test_import_graph_has_real_edges() -> None:
    """A graph with no edges cannot contain a cycle, which would make the check above vacuous."""
    graph = _graph()
    known = set(graph)
    assert any(targets & known for targets in graph.values())
