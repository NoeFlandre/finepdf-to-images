# ADR-0003: A dependency-free argparse CLI

Status: accepted (2026-09-16)

## Context

The CLI needs a small number of subcommands, a documented `--help`, and stable exit codes. Typer and Click are the obvious alternatives.

## Decision

Use `argparse` from the standard library. At bootstrap, the package ships with an empty list of runtime dependencies.

## Consequences

- You install nothing to run `--help`. The Docker smoke path stays simple and fast.
- There is no rich help formatting and no shell completion. The wiring of subcommands is longer than the decorators of Typer. This is acceptable for a small number of commands in a proof of concept.
- If a subcommand has more than about twelve options, write a new ADR to review this decision. Do not fight `argparse`.
