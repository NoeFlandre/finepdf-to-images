# ADR-0003 — A dependency-free argparse CLI

Status: accepted (2026-09-16)

## Context

The CLI needs a handful of subcommands, a documented `--help`, and stable exit codes. Typer and
Click are the obvious alternatives.

## Decision

Use `argparse` from the standard library. The package ships with an empty runtime dependency list
at bootstrap.

## Consequences

- Nothing to install to run `--help`; the Docker smoke path stays trivial and fast.
- No rich help formatting, no shell completion, and subcommand wiring is more verbose than Typer's
  decorators. Acceptable for a handful of commands in a proof of concept.
- If the command surface grows past roughly a dozen options per subcommand, revisit this with a new
  ADR rather than fighting `argparse`.
