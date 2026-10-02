# ADR-0014: A publication removes what it does not contain

Status: accepted (2026-09-17)

## Context

`HuggingFaceHub.upload` emitted only `CommitOperationAdd`. Thus the published repository could only grow. Across successive layouts, the pilot collected **203 files that no current plan mentions**. These were 190 loose images in `images/ab/cd/<digest>.png`, 3 PDFs, and four `data/*.jsonl` files from earlier schemas. A reader who opens the dataset sees all of them. The reader cannot know which files are current.

`is_noop` had the matching gap. It compared only the *planned* files with the remote. A publication that existed only to remove files reported "already published and identical". It removed nothing.

## Decision

1. **A publication states what the dataset is.** It is not a list of additions. Delete the remote files that the plan does not contain.
2. **Only the paths that this stage writes can be deleted.** `OWNED_PREFIXES` is `data/`, `images/`, `pdfs/`, `README.md`, and `manifest.json`. Do not touch anything else. This includes a `LICENSE`, a `.gitignore`, an asset that the card links to, and a file that a maintainer added through the web UI of the Hub. "Delete everything that the plan does not name" is the wrong default for a repository that other people can also write to.
3. **Never delete `.gitattributes`.** The Hub manages it. If the stage removes it, the stage fights the Hub over the LFS tracking rules. It is outside the owned prefixes, so this rule is an extra safety measure.
4. **Put the deletions in the same commit as the writes.** Two commits leave a revision that is neither the old dataset nor the new dataset. A reader can fetch exactly that revision.
5. **Make `is_noop` count the extra owned files.** Otherwise it mistakes a cleanup publication for a no-op. An extra file that the stage does not own does not block a no-op. Thus the `LICENSE` of a maintainer does not make each run look like it has work to do.
6. **Make the verification cover both halves of the commit.** A deletion that did not happen is a failed publication, in the same way as a write that did not happen. The dataset continues to serve the old shape.
7. **Treat a missing artifact root as an error. It is not a smaller plan.** See the Consequences.

## Consequences

- The published tree can shrink. This makes a minimal republished layout possible. Without deletion, a cleaner dataset can only be added next to the mess that it replaces.
- **A forgotten CLI flag became destructive. The team had to close this gap.** `--pdf-root` and `--image-root` were optional. If you omitted one, the plan silently lost those artifacts. This was acceptable while a publication could only add files. The run simply did not upload the bytes. With deletion, the same forgotten flag *removes bytes that the stage already published to a public dataset*. The exit code is 0 and there is no warning. The `--image-root` case was worse than the `--pdf-root` case. The PDFs still satisfied the check for the agreement of the card and the payload. Thus no other check fired. A root is now required when the policy cleared anything. The code derives what the policy cleared from the run. It does not derive it from the flags that the caller passed. When the code derived it from the flag, a forgotten argument looked like "nothing was cleared".
- You can still publish metadata only. To do so, clear nothing. Do not omit an argument.
- `FakeHub` models deletion. It records what the code asked it to remove. Thus the dry-run tests and the idempotency tests use the real behavior. A dry run deletes nothing. A test asserts this explicitly. Deletion is the operation that is easiest to let escape this guarantee.

## Addendum (2026-09-17): the stage refuses stale stage inputs

The required `--image-root` closed the *forgotten argument* gap. It did not close the neighboring gap. If you pointed `--documents`, `--images`, `--scored`, or `--retrieved` at a **truncated, empty, or stale** file, the plan still shrank. A publication deletes what it does not contain. Thus the corresponding published bytes were removed, with exit code 0.

Issue #33 weighed several options. The code now implements the first option. It cross-checks the stage inputs against `--extract-manifest`. This manifest already records a content digest of exactly those row sets. Thus the check is an equality and not a heuristic. It names the file that is wrong. It does not guess that "too much" is disappearing. It costs nothing at runtime.

The team did not add the backstop "refuse a large shrink unless `--allow-shrink`". It guards the same gap less precisely. A digest that the extract stage already publishes now covers each input that can shrink the plan. The stage still allows a run with no extract manifest. A publication without an extraction step is documented behavior.
