# Quality gates

The deterministic gauntlet, in order:

```
baseline -> Ruff -> ty -> tests -> property tests -> acceptance tests
         -> architecture checks -> CRAP -> mutation tests -> smoke test -> diff review
```

Gates available at bootstrap:

| Gate | Command |
| --- | --- |
| Baseline | `uv sync --locked` |
| Lint | `uv run ruff check .` |
| Format | `uv run ruff format --check .` |
| Types | `uv run ty check` |
| Tests | `uv run pytest -m "not architecture"` |
| Architecture | `uv run pytest -m architecture` |
| Smoke | `docker build -t finepdf-to-images . && docker run --rm finepdf-to-images --help` |

Property tests, acceptance scenarios, coverage/CRAP and mutation testing are added with the
pipeline issues they measure (#5). CI runs the deterministic subset on every pull request. Making a
failing gate actually block a merge additionally requires branch protection with these checks
marked required on `main`; that is a repository setting, not something this repository can assert
about itself. Required CI never reaches the network, never downloads
FinePDFs, and never needs a secret.

Metrics are evidence, not design targets. The CRAP threshold is documented in `scripts/` when it
lands; a number that is gamed rather than earned is worse than no number.
