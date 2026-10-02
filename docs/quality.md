# Quality gates

The deterministic gauntlet has these gates, in this order:

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
| 9 | Mutation tests | `uv run mutmut run` | **no (advisory)** |
| 10 | Smoke (CLI + Docker) | `bash scripts/smoke.sh` | yes |
| 11 | Diff review | a person and an independent agent | by convention |

`main` is protected. The gate jobs are required status checks. A branch must be up to date. The history stays linear. The repository refuses force pushes and deletion.

The CI **enforces the order**. It does not only describe it. Gates 1 to 8 run in one job. The smoke job and the mutation job declare `needs: quality`. Thus they cannot start until the earlier gates pass. Without this, the jobs run at the same time, and the "gauntlet" is a list and not a sequence.

## Purpose of each gate

### Property tests

Hypothesis tests the invariants that are easier to state than to list. These are the invariants:

- Canonical serialization round-trips and is idempotent.
- The selection is bounded, ordered, and a subset.
- No input changes an unknown license to an allowed license.
- The evidence is always a real vocabulary term.
- No other matched term subsumes a matched term.
- Arbitrary Unicode never raises an error.

### Acceptance scenarios

The file `tests/acceptance/features/pipeline.feature` contains the scenarios in Gherkin. `pytest-bdd` runs them. The steps call the **real** stage functions. The tests replace only the shard reader and the HTTP transport. Thus the scenarios are deterministic and reach no third-party site.

The main scenario is the whole flow: FinePDF row, relevance decision, PDF retrieval, image extraction, manifest. The other scenarios are failures:

- an unsafe URL
- HTML served as a PDF
- a malformed PDF
- a PDF with no images
- blank text
- a publication that is refused without a license

### Architecture checks

The check is an AST walk over `src/`. The domain must not import a network, filesystem, PDF, or Hub library. The domain must not import an adapter or the CLI. The package must stay acyclic. The analyser has its own regression tests, because an earlier version silently matched nothing.

### Complexity: one limit, two gates

The **maximum cyclomatic complexity is 6**. Both gates enforce it:

- **Ruff** (`mccabe.max-complexity = 6`) caps the raw complexity. It is fast and it runs in the editor. Thus it must catch a function with too many branches.
- **CRAP** caps the complexity *weighted by the quality of the tests*. At full coverage, CRAP is equal to the raw complexity. Thus the same limit of 6 applies. Below full coverage, the ceiling drops sharply.

In the past, the two limits disagreed. Ruff allowed 8 and CRAP rejected 7 at full coverage. Thus the limit of Ruff was unreachable. The slower gate always fired first, in CI and not locally.

The project kept the limit of 6. It did not increase it. All 203 functions in `src/` already meet it. An increase would relax a standard that the code already meets. It would only avoid the occasional forced split. Some functions are the specification of their own branching, for example `policy.decide` and `retrieval.evaluate`. They have exactly 6 and they are not exempt. When one of them needs a seventh branch, ask if the rule itself has grown. Do not increase the number.

`scripts/crap.py` reports the functions that are one branch from the limit. Thus you can see a cluster at the threshold before it blocks an unrelated change.

### CRAP

```
CRAP(f) = complexity(f)² × (1 − coverage(f))³ + complexity(f)
```

CRAP shows how dangerous a function is to change. It is high when the function is complicated and has poor coverage. It approaches the raw complexity when the coverage approaches 100%. **The threshold is 6.** At full coverage, a function can have a complexity of 6. At 80% coverage, the ceiling is about 3.

The gate treats a function with **no** recorded statements as *unmeasured*. It does not treat it as fully covered. An unmeasured function **with real branching** (complexity above 1) fails the gate. This difference separates a gate from a formality. An empty coverage report, or a report that was made before a file grew, gives each function 100%. Then the gate passes and measures nothing.

Note the qualifier `complexity > 1`. It is deliberate. A function with straight-line code and no coverage data still passes quietly. Each realistic stale or empty report also removes the functions with branching, so the gate fires. But the gate is a filter and not a total check.

This gate does real work. To bring the project under the limit, the team made the HTTP transport testable. Its client is now injectable. `httpx.MockTransport` tests the real streaming and redirect code offline. The team also split a dozen functions that had grown one branch at a time.

CRAP is a design constraint. It is **not a target**. To raise the coverage of a very complex function and get under the line is the exact action that the number is meant to discourage. Read the complexity column too.

### Mutation tests (advisory)

`mutmut` tests the pure domain. The decisions are in the domain. A surviving mutant there says something real. This is the current result:

| | |
| --- | --- |
| mutants | 1544 |
| killed | 1352 |
| survived | 181 |
| skipped | 11 |
| kill rate | **88%** |

This gate is **not** a merge gate. A kill rate starts a discussion. It is not a pass/fail line. A surviving mutant sometimes needs a new test. Sometimes the mutant is equivalent. A block on a percentage rewards assertions that copy the source and not the behavior.

The advisory status has a cost. `continue-on-error` makes the job **neutral** in the checks UI. Thus a crashed `mutmut` does not look different from a clean advisory run. The `|| true` that also hid the crash is removed. The job uploads the list of surviving mutants as an artifact. The evidence is available for anyone who looks. Nobody has to look.

The team classified the survivors and did not ignore them. Three classes were important. The team killed them:

- **Schema key names.** A mutant renamed `"dataset"` to `"DATASET"` in a manifest and survived. No test asserted the exact keys. These keys are the published contract.
- **Boundaries.** The mutants were `< 1` to `<= 1` and `>= MIN` to `> MIN`. The tests checked only the far side of each boundary. Thus a limit that *rejects a legitimate value* was invisible. One of these mutants rejects each connect timeout of one second or less.
- **Published values.** Mutants replaced a lookup key with `None`. For example, `image.get("sha256")` became `image.get(None)`. They survived in *both* published-row builders, for almost every column. The rows still carried every documented key, so the schema tests passed. But the columns were empty. No test asserted that a published row carries the values that the earlier stages produced. This is the only claim that the dataset exists to make. A related survivor inverted the document sort key. No test found it, because no test mixed present and absent `row_index`.

`tests/unit/test_mutation_survivors.py` covers all three classes. It names the mutant that each test kills. The team checked each test. It put its mutant back by hand and saw the test fail. This raised the kill rate from 72% to 88%.

Most of the remaining 181 survivors are **mutations of diagnostic strings**. Examples are an upper-cased message and a message replaced with `None`. If the team chases them, the tests become a transcription of the source. The tests assert the *content* of a message where it matters. Examples are a reason that a caller matches on and a field name that a user needs to act on.

### Smoke

`scripts/smoke.sh` runs the real CLI from end to end over the committed fixtures, offline. In CI it also runs the Docker image. The script asserts each stage on its **counts**. It does not assert only that "a manifest exists":

- `score` must find relevant rows. If it does not, the later stages are vacuous.
- `retrieve`, offline, must fail each attempt **with a recorded reason**. The script checks that the reason histogram sums to the attempt count.
- `extract` runs over the committed fixture PDFs. It must decode **2 images from 3 documents**. It must give one success with zero images and one recorded failure. It must write the artifacts.
- The script compares the outputs of `select`, `score`, and `extract` byte for byte across two runs.

An earlier version ran `extract` over an empty retrieval. Thus the stage never opened a PDF. It could not catch the pypdf `DependencyError` that this gate has caught. A gate that cannot fail is not a gate.

This gate has proved its value twice. Two bugs passed a fully green suite and reached the repository. Only the real path caught them:

- `httpx.Timeout` was constructed with two of its four required arguments. The fixture transport cannot reach this error.
- `pypdf.errors.DependencyError: jbig2dec binary is not available` inherits from nothing that is specific to PDF. It aborted a whole run. The hand-built fixture PDFs cannot reach this error.

A fixture suite cannot fail in ways that its fixtures cannot express.

## Run all gates

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

A number that a team games is worse than no number. It gives confidence without cost. Each threshold has a reason that is written next to it. Each exception is in the [technical-debt register](technical-debt.md) with the work that closes it.
