# Technical debt

Debt is recorded here explicitly rather than hidden. Each entry states what is missing, why it was
acceptable to ship without it, and what would trigger paying it down.

## TD-001 — no property, acceptance, coverage or mutation gates at bootstrap

**State.** The bootstrap commit ships lint, types, unit tests and architecture checks only.

**Why.** There is no domain logic yet to property-test or mutate; adding Hypothesis, `pytest-bdd`,
`radon` and a mutation runner now would mean committing unused dependencies, which the bootstrap
acceptance criteria forbid.

**Trigger.** Issue #5 wires the full gauntlet once the pipeline stages exist.

## TD-002 — `ty` is pre-1.0

**State.** Type checking uses Astral's `ty`, which is an alpha tool with an unstable configuration
surface and incomplete inference.

**Why.** It is the checker this project standardises on, it is fast, and it already catches real
errors. The alternative would be a second type checker in CI for no additional signal.

**Trigger.** Re-evaluate the pin when `ty` reaches a stable release, or if a false positive forces
a suppression that hides a genuine defect.

## TD-003 — branch protection does not include administrators

**State.** `main` requires `Deterministic quality gates`, `Docker smoke` and `Docs build` to pass,
requires branches to be up to date, forbids force pushes and deletions, and requires linear
history. It does **not** set `enforce_admins`, so a repository administrator can still merge past a
red gate.

**Why.** Locking administrators out of their own single-maintainer repository trades a real
recovery path for a theoretical guarantee.

**Trigger.** Enable `enforce_admins` if a second maintainer joins, or if a red gate is ever merged
past in practice.
