# ADR-0014 — A publication removes what it does not contain

Status: accepted (2026-09-17)

## Context

`HuggingFaceHub.upload` emitted only `CommitOperationAdd`, so the published repository could only
grow. Across successive layouts the pilot accumulated **203 files no current plan mentions**: 190
loose images under `images/ab/cd/<digest>.png`, 3 PDFs, four `data/*.jsonl` from earlier schemas.
A reader opening the dataset sees all of it, with no way to tell which files are current.

`is_noop` had the matching gap: it compared only the *planned* files against the remote. A
publication whose entire purpose was removing files would have reported "already published and
identical" and removed nothing.

## Decision

1. **A publication is a statement of what the dataset is**, not a list of additions. Remote files
   the plan does not contain are deleted.
2. **Only paths this stage writes are candidates.** `OWNED_PREFIXES` is `data/`, `images/`,
   `pdfs/`, plus `README.md` and `manifest.json`. Anything else — a `LICENSE`, a `.gitignore`, an
   asset the card links to, a file a maintainer added through the Hub's web UI — is left alone.
   "Delete everything the plan does not name" is the wrong default for a repository other people
   can also write to.
3. **`.gitattributes` is never deleted.** The Hub manages it; removing it would fight the Hub over
   LFS tracking rules. It is outside the owned prefixes anyway, so this is belt and braces.
4. **Deletions ride in the same commit as the writes.** Two commits would leave a revision that is
   neither the old dataset nor the new one, and a reader could fetch exactly that revision.
5. **`is_noop` accounts for extra owned files**, or a cleanup publication is mistaken for a no-op.
   An unowned extra does not block a no-op, so a maintainer's `LICENSE` does not make every run
   look like it has work to do.
6. **Verification covers both halves of the commit.** A deletion that did not happen is as much a
   failed publication as a write that did not: the dataset would still be serving the old shape.
7. **A missing artifact root is an error, not a smaller plan.** See Consequences.

## Consequences

- The published tree can shrink, which is what makes the minimal layout of ADR-0015 possible.
- **Forgetting a CLI flag became destructive, and had to be closed.** `--pdf-root` and
  `--image-root` were optional: omitting one silently dropped those artifacts from the plan. That
  was survivable while publication could only add files — the bytes simply were not uploaded that
  run. With deletion, the same forgotten flag *removes already-published bytes from a public
  dataset*, with exit code 0 and no warning. The `--image-root` case was worse than the
  `--pdf-root` one: the PDFs still satisfied the card/payload agreement check, so nothing else
  fired. A root is now required whenever the policy cleared anything, and what the policy cleared
  is derived from the run rather than from which flags the caller passed — deriving it from the
  flag was what made a forgotten argument look like "nothing was cleared".
- Publishing metadata only is still possible. It is expressed by clearing nothing, not by omitting
  an argument.
- `FakeHub` models deletion and records what it was asked to remove, so the dry-run and idempotency
  tests exercise the real behaviour. A dry run deletes nothing, asserted explicitly: deletion is
  exactly the operation that would be easiest to let escape that guarantee.
