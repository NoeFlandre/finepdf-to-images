"""Bounded proof-of-concept pipeline from FinePDFs rows to published PDF and image artifacts.

Layering contract enforced by ``tests/architecture``:

``domain``
    Pure decision logic. No network, filesystem, PDF or Hugging Face imports.
``adapters``
    Every side effect, kept thin and injectable so tests can drive local fixtures.
``cli`` / ``pipeline``
    Composition roots that wire adapters into the pure domain.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
