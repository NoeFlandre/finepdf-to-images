# ADR-0002: uv, Ruff, ty and a committed lockfile

Status: accepted (2026-09-16)

## Context

A proof of concept has a published public dataset as its result. If nobody can reproduce the run, the result has no value. Reproducibility starts with the environment.

## Decision

Manage the project with `uv`. Commit `uv.lock`. Pin the interpreter with `.python-version` (3.13). Use Ruff for linting and formatting. Use `ty` for type checking. CI and Docker install with `uv sync --locked`. Thus a lockfile that drifted fails the build. The build does not silently resolve something new.

## Consequences

- One tool replaces pip, virtualenv, pip-tools, and a pair of formatter and linter. There is less configuration to keep consistent between local runs, Docker, and CI.
- The project follows the release cadence of Astral. This includes the pre-1.0 instability of `ty` (see [TD-002](../technical-debt.md)).
- The choice of Python 3.13 and not 3.14 is deliberate and conservative. The quality tools that issue #5 added have better support there. It is cheap to change this choice later.
