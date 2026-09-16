"""Regression tests for the architecture analyser itself.

These exist because the first version of the import-boundary check was reviewed and found to be
partly vacuous: it inspected only ``from X import Y`` and never plain ``import X``, and it keyed
``__init__`` modules inconsistently so the import graph lost every package-level edge. A domain
module could import an adapter, and a real cycle could exist, with all checks green.

Every case below is a bug that shipped once. None of them may ship again.
"""

from __future__ import annotations

import ast

import pytest

from tests.architecture.boundaries import (
    external_modules,
    find_cycle,
    forbidden_hits,
    internal_targets,
    layer_of,
    module_name,
)

pytestmark = pytest.mark.architecture


def parse(source: str) -> ast.Module:
    return ast.parse(source)


# --------------------------------------------------------------------------- module naming


@pytest.mark.parametrize(
    ("parts", "expected"),
    [
        (("domain", "__init__"), "domain"),
        (("domain", "scoring"), "domain.scoring"),
        (("__init__",), ""),
        (("cli",), "cli"),
        (("adapters", "sub", "__init__"), "adapters.sub"),
    ],
)
def test_init_modules_collapse_onto_their_package(parts: tuple[str, ...], expected: str) -> None:
    """REGRESSION: keying ``domain/__init__.py`` as ``domain.__init__`` orphaned package edges."""
    assert module_name(parts) == expected


# --------------------------------------------------------------------------- external imports


@pytest.mark.parametrize(
    "source",
    [
        "import os",
        "import os.path",
        "from os import path",
        "import httpx, json",
        "from pypdf import PdfReader",
    ],
)
def test_forbidden_io_libraries_are_detected_in_both_import_forms(source: str) -> None:
    assert forbidden_hits(parse(source))


@pytest.mark.parametrize(
    "source",
    [
        "import io",
        "from io import BytesIO",
        "import json",
        "",
        # urllib.parse is pure string manipulation; only the I/O submodules are banned.
        "from urllib.parse import urlsplit",
        "import urllib.parse",
    ],
)
def test_pure_stdlib_is_not_flagged(source: str) -> None:
    assert not forbidden_hits(parse(source))


@pytest.mark.parametrize(
    "source", ["import urllib.request", "from urllib.request import urlopen", "import urllib.error"]
)
def test_the_io_submodules_of_an_otherwise_allowed_package_are_flagged(source: str) -> None:
    """REGRESSION: banning the whole `urllib` package would push the domain into hand-rolling a
    URL parser, which is worse than the rule was protecting against."""
    assert forbidden_hits(parse(source))


def test_a_submodule_of_a_banned_package_is_flagged() -> None:
    assert forbidden_hits(parse("import os.path"))


def test_the_package_itself_is_not_an_external_module() -> None:
    assert external_modules(parse("from finepdf_to_images import __version__")) == set()
    assert external_modules(parse("import finepdf_to_images.domain.source")) == set()


# --------------------------------------------------------------------------- internal imports


@pytest.mark.parametrize(
    "source",
    [
        "import finepdf_to_images.adapters",
        "import finepdf_to_images.adapters.retrieval",
        "import finepdf_to_images.adapters as a",
        "from finepdf_to_images.adapters import retrieval",
        "from finepdf_to_images import adapters",
    ],
)
def test_domain_reaching_into_adapters_is_detected_in_every_import_form(source: str) -> None:
    """REGRESSION: plain ``import finepdf_to_images.adapters`` was invisible to the checker."""
    targets = internal_targets(parse(source), package="domain")
    assert any(layer_of(target) == "adapters" for target in targets), targets


def test_relative_sibling_import_is_resolved() -> None:
    assert "domain.models" in internal_targets(parse("from . import models"), package="domain")


def test_relative_parent_import_is_resolved() -> None:
    targets = internal_targets(parse("from ..adapters import retrieval"), package="domain")
    assert "adapters" in targets
    assert "adapters.retrieval" in targets


def test_bare_package_import_resolves_to_the_package_root() -> None:
    """REGRESSION: ``from finepdf_to_images import X`` produced no target at all."""
    assert internal_targets(parse("from finepdf_to_images import cli"), package="") == {"cli"}


def test_unrelated_third_party_imports_are_not_internal_targets() -> None:
    assert internal_targets(parse("import httpx\nfrom os import path"), package="") == set()


# --------------------------------------------------------------------------- cycles


def test_direct_cycle_is_reported() -> None:
    cycle = find_cycle({"a": ["b"], "b": ["a"]})
    assert cycle is not None
    assert cycle[0] == cycle[-1]


def test_indirect_cycle_is_reported() -> None:
    """REGRESSION: a real ``cli -> domain.leak -> cli`` cycle passed the original check."""
    assert find_cycle({"cli": ["domain.leak"], "domain.leak": ["cli"], "x": []}) is not None


def test_self_cycle_is_reported() -> None:
    assert find_cycle({"a": ["a"]}) is not None


def test_acyclic_graph_is_accepted() -> None:
    assert find_cycle({"cli": ["domain", "adapters"], "adapters": ["domain"], "domain": []}) is None


def test_diamond_is_not_a_cycle() -> None:
    assert find_cycle({"a": ["b", "c"], "b": ["d"], "c": ["d"], "d": []}) is None
