"""Session-wide temporary directories for the integration tests.

The publication helpers used to build their image trees with ``tempfile.mkdtemp`` under a
``functools.cache``, which leaked a directory into the system temp on every run and did not
cooperate with pytest's own cleanup or with parallel runs. ``tmp_path_factory`` does both.

Autouse and session-scoped so the helpers can stay plain functions: making them take a fixture
would mean threading a parameter through every test that calls ``publish()``, which is a lot of
churn for a directory.
"""

from __future__ import annotations

import pathlib

import pytest


@pytest.fixture(scope="session", autouse=True)
def _pytest_managed_tmp_root(tmp_path_factory: pytest.TempPathFactory) -> pathlib.Path:
    """Hand the publication helpers a directory pytest owns and cleans up."""
    from tests.integration import publication_support

    root = tmp_path_factory.mktemp("publication")
    publication_support.TMP_ROOT = root
    return root
