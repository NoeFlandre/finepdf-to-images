"""Pure import-graph analysis used by the architecture checks.

Kept separate from the tests that consume it so the analyser itself can be regression-tested
against synthetic sources. An architecture check that silently matches nothing is worse than no
check at all, because it buys confidence without paying for it.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Mapping

TOP_PACKAGE = "finepdf_to_images"

#: Modules that perform network, filesystem, PDF or Hub I/O and must stay out of the domain.
#:
#: Entries are matched as dotted **prefixes**, not top-level names, because the rule is about I/O
#: rather than about packages. ``urllib.request`` opens sockets and is banned; ``urllib.parse`` is
#: pure string manipulation and is not. Banning the whole ``urllib`` package would push the domain
#: into hand-rolling a URL parser, which is a worse outcome than the rule was protecting against.
#:
#: ``io`` is deliberately absent for the same reason: ``BytesIO`` over in-memory bytes is pure.
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
        "urllib.error",
        "urllib.request",
        "urllib.response",
        "urllib.robotparser",
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


def external_modules(tree: ast.Module) -> set[str]:
    """Dotted names of every non-relative import that leaves the package.

    Full dotted names, not just roots: the forbidden list distinguishes ``urllib.request`` from
    ``urllib.parse``, and truncating to the root would erase exactly that distinction.
    """
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module)
    return {
        module
        for module in modules
        if module != TOP_PACKAGE and not module.startswith(f"{TOP_PACKAGE}.")
    }


def forbidden_hits(tree: ast.Module, forbidden: Iterable[str] = FORBIDDEN_IN_DOMAIN) -> set[str]:
    """Imported modules matching a forbidden dotted prefix.

    ``os`` matches ``os`` and ``os.path``; ``urllib.request`` matches itself but not
    ``urllib.parse``.
    """
    banned = set(forbidden)
    return {
        module
        for module in external_modules(tree)
        if any(module == entry or module.startswith(f"{entry}.") for entry in banned)
    }


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
            targets.update(_plain_import_targets(node))
        elif isinstance(node, ast.ImportFrom):
            targets.update(_from_import_targets(node, package=package))
    return {target for target in targets if target}


def _plain_import_targets(node: ast.Import) -> set[str]:
    """``import finepdf_to_images.adapters.retrieval`` -> ``{"adapters.retrieval"}``."""
    return {
        alias.name.removeprefix(TOP_PACKAGE).lstrip(".")
        for alias in node.names
        if alias.name == TOP_PACKAGE or alias.name.startswith(f"{TOP_PACKAGE}.")
    }


def _from_import_targets(node: ast.ImportFrom, *, package: str) -> set[str]:
    """Both the package imported from and each name imported out of it.

    ``from finepdf_to_images.adapters import retrieval`` crosses a layer whether the analyser
    reads it as an import of ``adapters`` or of ``adapters.retrieval``, so both are recorded.
    """
    base = _absolute_base(node, package=package)
    if base is None:
        return set()
    return {base, *(f"{base}.{alias.name}".lstrip(".") for alias in node.names)}


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


def _visit(
    node: str,
    trail: tuple[str, ...],
    *,
    edges: Mapping[str, list[str]],
    state: dict[str, int],
) -> list[str] | None:
    """One depth-first step of the cycle search.

    ``state`` is the classic three-colour marking: absent is unvisited, 1 is on the current path,
    2 is finished. Meeting a node that is on the current path *is* the cycle, and the trail from
    its first appearance is the path to report.

    Lifted out of :func:`find_cycle` so each function stays inside the complexity limit; the
    recursion is unchanged.
    """
    if state.get(node) == 2:
        return None
    if state.get(node) == 1:
        start = trail.index(node)
        return [*trail[start:], node]
    state[node] = 1
    for nxt in edges[node]:
        cycle = _visit(nxt, (*trail, node), edges=edges, state=state)
        if cycle is not None:
            return cycle
    state[node] = 2
    return None


def find_cycle(graph: Mapping[str, Iterable[str]]) -> list[str] | None:
    """Return one import cycle as a path, or ``None`` when the graph is acyclic."""
    known = set(graph)
    edges = {node: sorted(set(targets) & known) for node, targets in graph.items()}
    state: dict[str, int] = {}

    def visit(node: str, trail: tuple[str, ...]) -> list[str] | None:
        return _visit(node, trail, edges=edges, state=state)

    for node in sorted(edges):
        cycle = visit(node, ())
        if cycle is not None:
            return cycle
    return None
