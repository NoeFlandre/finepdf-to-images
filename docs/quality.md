# Quality gates

The deterministic gauntlet, in order:

```
baseline -> Ruff -> ty -> tests -> property tests -> acceptance tests
         -> architecture checks -> CRAP -> mutation tests -> smoke test -> diff review
```

| # | Gate | Command | Blocks merge |
| --- | --- | --- | --- |
| 1 | Baseline | `uv sync --locked --all-groups` | yes |
| 2 | Lint & format | `uv run ruff check .` · `uv run ruff format --check .` | yes |
| 3 | Types | `uv run ty check` | yes |
| 4 | Tests | `uv run pytest -m "not architecture and not acceptance" --cov --cov-report=xml` | yes |
| 5 | Property tests | `uv run pytest -m property` | yes |
| 6 | Acceptance scenarios | `uv run pytest -m acceptance` | yes |
| 7 | Architecture checks | `uv run pytest -m architecture` | yes |
| 8 | CRAP | `uv run python scripts/crap.py` | yes |
| 9 | Mutation tests | `uv run mutmut run` | **no — advisory** |
| 10 | Smoke (CLI + Docker) | `bash scripts/smoke.sh` | yes |
| 11 | Diff review | a human, and an independent agent | by convention |

`main` is protected: the gate jobs are required status checks, branches must be up to date, history
stays linear, and force pushes and deletion are refused.

The ordering is **enforced, not merely described**: gates 1–8 run in one job, and the smoke and
mutation jobs declare `needs: quality`, so they cannot start until the earlier gates pass. Without
that the jobs run concurrently and the "gauntlet" is a list rather than a sequence.

## What each gate is for

### Property tests

Hypothesis, over the invariants that are easier to state than to enumerate: canonical
serialization round-trips and is idempotent, selection is bounded and ordered and a subset, no
input turns an unknown licence into an allowed one, evidence is always a real vocabulary term, no
matched term is subsumed by another, arbitrary Unicode never raises.

### Acceptance scenarios

`tests/acceptance/features/pipeline.feature` in Gherkin, driven by `pytest-bdd`. The steps call the
**real** stage functions; only the shard reader and the HTTP transport are substituted, so the
scenarios are deterministic and reach no third-party site.

The headline scenario is the whole flow: FinePDF row → relevance decision → PDF retrieval → image
extraction → manifest. The rest are failures: an unsafe URL, HTML served as a PDF, a malformed PDF,
a PDF with no images, blank text, and publication refused without a licence.

### Architecture checks

An AST walk over `src/`. The domain must not import a network, filesystem, PDF or Hub library, must
not import an adapter or the CLI, and the package must stay acyclic. The analyser carries its own
regression tests, because an earlier version of it silently matched nothing.

### CRAP

```
CRAP(f) = complexity(f)² × (1 − coverage(f))³ + complexity(f)
```

A function with **no** recorded statements is treated as *unmeasured* rather than fully covered,
and an unmeasured function **with real branching** (complexity > 1) fails the gate. That
distinction is the whole difference between a gate and a formality: an empty coverage report, or
one generated before a file grew, otherwise scores every function at 100% and passes having
measured nothing.

The `complexity > 1` qualifier is deliberate and worth knowing: a straight-line function with no
coverage data still passes quietly. Every realistic stale or empty report also strips branching
functions, so the gate fires — but it is a filter, not a total check.

How dangerous a function is to change. High when it is both complicated and poorly covered;
collapses toward raw complexity as coverage approaches 100%. **Threshold 6** — at full coverage a
function may be as complex as 6; at 80% coverage the ceiling is about 3.

This one does real work. Bringing the project under it forced the HTTP transport to become
testable (its client is now injectable, and `httpx.MockTransport` exercises the genuine streaming
and redirect code offline), and split a dozen functions that had quietly grown a branch at a time.

It is a design constraint, **not a target**: raising coverage on a monster to get under the line is
exactly the move the number exists to discourage. Read the complexity column too.

### Mutation tests — advisory, and why

`mutmut` over the pure domain — where the decisions live, and where a surviving mutant says
something real. Current result:

| | |
| --- | --- |
| mutants | 923 |
| killed | 766 |
| survived | 157 |
| kill rate | **83%** |

It is **not** a merge gate. A kill rate is a conversation, not a pass/fail line: the honest
response to a surviving mutant is sometimes a new test and sometimes "that mutant is equivalent",
and blocking on a percentage rewards writing assertions that mirror the source rather than the
behaviour.

Being advisory has a cost worth stating: `continue-on-error` makes the job **neutral** in the
checks UI, so a crashed `mutmut` does not stand out from a clean advisory run. The `|| true` that
used to hide it as well is gone, and the surviving-mutant list is uploaded as an artifact, so the
evidence is there for anyone who looks — but nobody is forced to.

The survivors were classified rather than ignored. Two classes mattered and were killed:

- **Schema key names.** A mutant renaming `"dataset"` to `"DATASET"` in a manifest survived,
  because nothing asserted the exact keys — and those keys are the published contract.
- **Boundaries.** `< 1` → `<= 1`, `>= MIN` → `> MIN`. Only the far side of each boundary was
  tested, so a limit that *rejects a legitimate value* was invisible. One of these would have
  rejected every connect timeout of a second or less.

Both are now covered in `tests/unit/test_mutation_survivors.py`, which names the mutant each test
kills. That took the kill rate from 72% to 83%.

The remaining 157 are overwhelmingly **diagnostic-string mutations** — upper-casing a message,
replacing it with `None`. Chasing those would turn the tests into a transcription of the source.
Message *content* is asserted where it matters: a reason a caller matches on, a field name a user
needs to act.

### Smoke

`scripts/smoke.sh` runs the real CLI end to end over the committed fixtures, offline, plus the
Docker image in CI. Every stage is asserted on its **counts**, not on "a manifest exists":

- `score` must find relevant rows, or the later stages would be vacuous.
- `retrieve`, offline, must fail every attempt **with a recorded reason** — the reason histogram
  is checked to sum to the attempt count.
- `extract` runs over the committed fixture PDFs and must decode **2 images from 3 documents**,
  with one zero-image success and one recorded failure, and must write the artifacts.
- `select`, `score` and `extract` outputs are compared byte-for-byte across two runs.

An earlier version ran `extract` over an empty retrieval, so the stage never opened a PDF. It
could not have caught the pypdf `DependencyError` this gate is credited with catching — a gate that
cannot fail is not a gate.

This gate has earned its place twice. Two bugs reached the repository past a fully green suite and
were caught only on the real path:

- `httpx.Timeout` constructed with two of its four required arguments — unreachable through the
  fixture transport.
- `pypdf.errors.DependencyError: jbig2dec binary is not available`, which inherits from nothing
  PDF-specific and aborted an entire run — unreachable through hand-built fixture PDFs.

A fixture suite cannot fail in ways its fixtures cannot express.

## Running the lot

```bash
uv sync --locked --all-groups
uv run ruff check . && uv run ruff format --check .
uv run ty check
uv run pytest -m "not architecture and not acceptance" --cov --cov-report=xml
uv run pytest -m property
uv run pytest -m acceptance
uv run pytest -m architecture
uv run python scripts/crap.py
uv run mutmut run          # advisory, minutes
bash scripts/smoke.sh
```

## Metrics are evidence, not design targets

A number that is gamed rather than earned is worse than no number, because it buys confidence
without paying for it. Every threshold here has a reason written next to it, and every exception is
in the [technical-debt register](technical-debt.md) with what it would take to close.
