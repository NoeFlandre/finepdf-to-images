"""Pure import-graph analysis used by the architecture checks.

Kept separate from the tests that consume it so the analyser itself can be regression-tested
against synthetic sources. An architecture check that silently matches nothing is worse than no
check at all, because it buys confidence without paying for it.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Mapping

TOP_PACKAGE = "finepdf_to_images"

#: Third-party and standard-library roots that perform network, filesystem, PDF or Hub I/O.
#: ``io`` is deliberately absent: ``BytesIO`` over in-memory bytes is pure.
FORBIDDEN_IN_DOMAIN = frozenset(
    {
        "PIL",
        "aiohttp",
        "datasets",
        "fitz",
        "fsspec",
        "httpx",
        "huggingface_hub",
        "os",
        "pathlib",
        "pdf2image",
        "pdfminer",
        "pyarrow",
        "pymupdf",
        "pypdf",
        "requests",
        "shutil",
        "socket",
        "subprocess",
        "tempfile",
        "urllib",
    }
)


def module_name(parts: Iterable[str]) -> str:
    """Normalise a path relative to the package root into a dotted module name.

    ``__init__`` collapses onto its package so that ``domain/__init__.py`` and an import of
    ``finepdf_to_images.domain`` name the same node. Without this the import graph loses every
    edge that points at a package and the cycle check becomes vacuous.
    """
    segments = [part for part in parts if part]
    if segments and segments[-1] == "__init__":
        segments.pop()
    return ".".join(segments)


def package_name(parts: Iterable[str]) -> str:
    """The dotted package a module lives in, used to resolve its relative imports.

    ``domain/scoring.py`` and ``domain/__init__.py`` both live in ``domain``: Python resolves a
    relative import against ``__package__``, which is the same for a module and its package's
    ``__init__``.
    """
    segments = [part for part in parts if part]
    if segments and segments[-1] == "__init__":
        segments.pop()
    else:
        segments = segments[:-1]
    return ".".join(segments)


def external_roots(tree: ast.Module) -> set[str]:
    """Top-level names of every non-relative import that leaves the package."""
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return {root for root in roots if root != TOP_PACKAGE}


def internal_targets(tree: ast.Module, *, package: str) -> set[str]:
    """Modules inside the package that ``module`` imports, as dotted names without the prefix.

    ``package`` is the dotted package the module lives in (see :func:`package_name`); relative
    imports are resolved against it exactly as Python resolves them against ``__package__``.

    Handles every import form that can cross a layer:

    - ``import finepdf_to_images.adapters.retrieval``
    - ``from finepdf_to_images.adapters import retrieval`` (both the package and the attribute)
    - ``from finepdf_to_images import domain``
    - ``from . import sibling`` and ``from ..domain import models``
    """
    targets: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == TOP_PACKAGE or alias.name.startswith(f"{TOP_PACKAGE}."):
                    targets.add(alias.name.removeprefix(TOP_PACKAGE).lstrip("."))
        elif isinstance(node, ast.ImportFrom):
            base = _absolute_base(node, package=package)
            if base is None:
                continue
            targets.add(base)
            targets.update(f"{base}.{alias.name}".lstrip(".") for alias in node.names)
    return {target for target in targets if target}


def _absolute_base(node: ast.ImportFrom, *, package: str) -> str | None:
    """Resolve an ``ImportFrom`` to a package-relative dotted base, or ``None`` if it leaves us."""
    if node.level == 0:
        if node.module == TOP_PACKAGE:
            return ""
        if node.module and node.module.startswith(f"{TOP_PACKAGE}."):
            return node.module.removeprefix(f"{TOP_PACKAGE}.")
        return None
    # level 1 is the module's own package, level 2 its parent, and so on.
    anchor = [part for part in package.split(".") if part]
    ascend = node.level - 1
    trimmed = anchor[: len(anchor) - ascend] if ascend <= len(anchor) else []
    return ".".join([*trimmed, *(node.module.split(".") if node.module else [])])


def layer_of(module: str) -> str:
    """The architectural layer a dotted module name belongs to."""
    return module.split(".", 1)[0] if module else ""


def find_cycle(graph: Mapping[str, Iterable[str]]) -> list[str] | None:
    """Return one import cycle as a path, or ``None`` when the graph is acyclic."""
    known = set(graph)
    edges = {node: sorted(set(targets) & known) for node, targets in graph.items()}
    state: dict[str, int] = {}

    def visit(node: str, trail: tuple[str, ...]) -> list[str] | None:
        if state.get(node) == 2:
            return None
        if state.get(node) == 1:
            start = trail.index(node)
            return [*trail[start:], node]
        state[node] = 1
        for nxt in edges[node]:
            cycle = visit(nxt, (*trail, node))
            if cycle is not None:
                return cycle
        state[node] = 2
        return None

    for node in sorted(edges):
        cycle = visit(node, ())
        if cycle is not None:
            return cycle
    return None
