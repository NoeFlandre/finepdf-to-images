"""Compute the CRAP score for every function in the package.

    CRAP(f) = complexity(f)^2 * (1 - coverage(f))^3 + complexity(f)

CRAP asks one question: *how dangerous is this function to change?* It is high when a function is
both complicated and poorly covered, and it collapses toward the raw complexity as coverage
approaches 100%. A simple function needs no tests to be safe; a tangled one needs a lot.

Usage::

    uv run pytest --cov --cov-report=xml
    uv run python scripts/crap.py

The threshold is deliberately strict (6). At full coverage a function may be as complex as 6; at
80% coverage the ceiling is about 3. That is a design constraint, not a target to be gamed: raising
coverage on a monster to get under the line is exactly the move the number exists to discourage,
and a reviewer should read the complexity column too.
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass

DEFAULT_THRESHOLD = 6.0
COVERAGE_XML = pathlib.Path("coverage.xml")
SOURCE_ROOT = pathlib.Path("src/finepdf_to_images")


@dataclass(frozen=True, slots=True)
class Score:
    module: str
    name: str
    line: int
    complexity: int
    covered: int
    total: int

    @property
    def coverage(self) -> float:
        """Fraction of this function's statements that the test suite executed."""
        return 1.0 if self.total == 0 else self.covered / self.total

    @property
    def crap(self) -> float:
        uncovered = 1.0 - self.coverage
        return self.complexity**2 * uncovered**3 + self.complexity


def line_hits(xml_path: pathlib.Path) -> dict[str, dict[int, int]]:
    """``{source file: {line number: hit count}}`` from a coverage XML report."""
    if not xml_path.is_file():
        raise SystemExit(f"{xml_path} not found. Run: uv run pytest --cov --cov-report=xml")
    tree = ElementTree.parse(xml_path)
    hits: dict[str, dict[int, int]] = {}
    for class_element in tree.iter("class"):
        filename = class_element.get("filename", "")
        per_file = hits.setdefault(filename, {})
        for line in class_element.iter("line"):
            number = int(line.get("number", "0"))
            per_file[number] = int(line.get("hits", "0"))
    return hits


def functions(path: pathlib.Path) -> list[tuple[str, int, int, int]]:
    """``(name, start line, end line, complexity)`` for every function in ``path``."""
    from radon.complexity import cc_visit

    source = path.read_text(encoding="utf-8")
    return [
        (block.fullname, block.lineno, block.endline, block.complexity)
        # Functions and methods only. radon also reports a block per class whose complexity is the
        # sum of its methods, which would count the same branches twice and flag a class for the
        # sins of its parts.
        for block in cc_visit(source)
        if block.letter in {"F", "M"}
    ]


def scores(root: pathlib.Path, hits: dict[str, dict[int, int]]) -> list[Score]:
    results: list[Score] = []
    for path in sorted(root.rglob("*.py")):
        key = _matching_key(path, hits)
        per_file = hits.get(key, {})
        for name, start, end, complexity in functions(path):
            executable = {line: count for line, count in per_file.items() if start <= line <= end}
            results.append(
                Score(
                    module=str(path),
                    name=name,
                    line=start,
                    complexity=complexity,
                    covered=sum(1 for count in executable.values() if count > 0),
                    total=len(executable),
                )
            )
    return results


def _matching_key(path: pathlib.Path, hits: dict[str, dict[int, int]]) -> str:
    """Coverage records paths relative to its own source root; match on the tail."""
    wanted = path.as_posix()
    for key in hits:
        if wanted.endswith(key) or key.endswith(wanted):
            return key
    return wanted


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument("--coverage-xml", type=pathlib.Path, default=COVERAGE_XML)
    parser.add_argument("--source", type=pathlib.Path, default=SOURCE_ROOT)
    parser.add_argument(
        "--top", type=int, default=10, help="how many of the worst functions to print"
    )
    args = parser.parse_args(argv)

    results = scores(args.source, line_hits(args.coverage_xml))
    if not results:
        raise SystemExit(f"no functions found under {args.source}")

    worst = sorted(results, key=lambda score: (-score.crap, score.module, score.line))
    over = [score for score in worst if score.crap > args.threshold]

    print(f"{'CRAP':>6}  {'CC':>3}  {'COV':>6}  FUNCTION")
    for score in worst[: args.top]:
        marker = "!" if score.crap > args.threshold else " "
        print(
            f"{score.crap:>6.2f}{marker} {score.complexity:>3}  "
            f"{score.coverage:>6.1%}  {score.module}:{score.line} {score.name}"
        )

    total = len(results)
    print(f"\n{total} functions, threshold {args.threshold}")
    if over:
        print(f"{len(over)} over threshold:", file=sys.stderr)
        for score in over:
            print(
                f"  {score.crap:.2f}  {score.module}:{score.line} {score.name} "
                f"(complexity {score.complexity}, coverage {score.coverage:.1%})",
                file=sys.stderr,
            )
        return 1
    print("all functions within the CRAP threshold")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
