# ADR-0002 — uv, Ruff, ty and a committed lockfile

Status: accepted (2026-09-16)

## Context

A proof of concept whose result is a published public dataset is worthless if the run cannot be
reproduced. Reproducibility starts with the environment.

## Decision

Manage the project with `uv`, commit `uv.lock`, and pin the interpreter with `.python-version`
(3.13). Use Ruff for both linting and formatting, and `ty` for type checking. CI and Docker install
with `uv sync --locked`, so a drifted lockfile fails the build instead of silently resolving
something new.

## Consequences

- One tool replaces pip, virtualenv, pip-tools and a formatter/linter pair; less configuration to
  keep consistent between local runs, Docker and CI.
- The project inherits Astral's release cadence, including `ty`'s pre-1.0 instability (see
  [TD-002](../technical-debt.md)).
- Python 3.13 rather than 3.14 is a deliberate conservative choice: the quality tooling added in
  issue #5 has better support there. This is cheap to revisit.
