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

## TD-004 — the pilot ships no curated allow-list entries

**State.** `ALLOWED_LICENSES` is populated, but nothing in the pipeline produces a
`curated-allowlist` declaration, so every artifact in the current run resolves to `metadata-only`.
The published dataset therefore contains provenance and hashes and **no third-party bytes**.

**Why.** Clearing a document for redistribution means a human establishing its terms. For an
arbitrary sample of the open web that is per-document work, and guessing is precisely what
[ADR-0005](adr/0005-conservative-publication-policy.md) refuses to do. Shipping the policy without
entries is the honest state, not a misconfiguration.

**Trigger.** Add entries — as reviewed, source-attributed records in this repository — when there
is a concrete set of documents whose terms have actually been established. Until then the dataset
card must say plainly that no source bytes are republished.

## TD-005 — the policy is not wired into a pipeline stage yet

**State.** `domain.policy.decide()` is complete and tested, but nothing calls it: there is no
retrieval or extraction stage yet to produce artifacts for it to judge, and no publication stage to
consume its verdicts or to write `policy_summary()` into a dataset card.

**Why.** The policy is deliberately a standalone pure function, delivered ahead of the stages that
need it so that those stages are built against a decided rule rather than inventing one. Wiring it
into a stage that does not exist would mean writing that stage here.

**Trigger.** Issues #3 and #4 pass `require_artifact_hash=True` when judging retrieved bytes; issue
#2 writes the card from `policy_summary()` and publishes only what `decide()` permits. Until all
three land, the statement "this project republishes no third-party bytes" is true because no bytes
are published at all — not because the policy refused them.

## TD-006 — DNS rebinding and hostnames resolving to private addresses

**State.** `validate_url` refuses IP **literals** that name a loopback, private, link-local,
reserved, multicast or unspecified address. A *hostname* that resolves to one of those is not
caught, and neither is a host that resolves differently between the check and the request.

**Why.** Resolving a name is I/O, and the URL rules live in the pure domain precisely so they can
be tested without a network. Closing this properly means resolving in the adapter, checking the
resolved address, and pinning the connection to it — real work, and more than a bounded pilot that
fetches a few dozen public documents needs.

**Trigger.** Before this pipeline is ever pointed at untrusted URLs from inside a network with
anything worth reaching, or run as a service. Until then the exposure is a developer machine
fetching public PDFs.
