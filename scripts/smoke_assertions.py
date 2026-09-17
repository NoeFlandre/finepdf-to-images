"""Assertions and setup for ``scripts/smoke.sh``.

A separate file rather than heredocs inside the shell script: the assertions are the substance of
the smoke gate, and they should be readable, lintable and type-checked like anything else.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys

FIXTURE_PDFS = pathlib.Path("tests/fixtures/pdfs")

#: One of each interesting shape, so the stage is exercised rather than merely started.
STAGED_PDFS = ("two-images.pdf", "no-images.pdf", "malformed.pdf")


def relevant_count(scored: pathlib.Path) -> int:
    rows = [json.loads(line) for line in scored.read_text(encoding="utf-8").splitlines() if line]
    count = sum(1 for row in rows if row["relevance"]["relevant"])
    if count == 0:
        raise SystemExit("score found nothing relevant; every later stage would be vacuous")
    return count


def check_retrieve(manifest: pathlib.Path, expected: int) -> None:
    """Offline, every attempt must fail -- and must fail with a recorded reason."""
    parsed = json.loads(manifest.read_text(encoding="utf-8"))
    counts, failures = parsed.get("counts"), parsed.get("failures")
    if not isinstance(counts, dict) or not isinstance(failures, dict):
        raise SystemExit(f"{manifest} is not a retrieve manifest: {sorted(parsed)}")
    if counts["attempted"] != expected:
        raise SystemExit(f"expected {expected} attempts, got {counts}")
    if counts["failed"] != expected or counts["retrieved"] != 0:
        raise SystemExit(f"offline, every row must fail: {counts}")
    if sum(failures.values()) != expected:
        raise SystemExit(f"every failure must carry a reason: {failures}")
    print(f"  all {expected} attempts failed, each with a recorded reason: {failures}")


def stage_pdfs(root: pathlib.Path) -> None:
    """Lay out a retrieve-stage output from the committed fixtures."""
    records = []
    for index, name in enumerate(STAGED_PDFS):
        data = (FIXTURE_PDFS / name).read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        path = f"pdfs/{digest[:2]}/{digest[2:4]}/{digest}.pdf"
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        records.append(
            {
                "row_index": index,
                "row_id": f"<urn:uuid:{index:012d}>",
                "url": f"https://fixtures.invalid/{name}",
                "ok": True,
                "sha256": digest,
                "path": path,
            }
        )
    lines = [json.dumps(r, sort_keys=True, separators=(",", ":")) for r in records]
    (root / "retrieved.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def check_extract(out: pathlib.Path) -> None:
    """The stage must really decode images, not just start and write an empty manifest."""
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    counts = manifest.get("counts")
    if not isinstance(counts, dict):
        raise SystemExit(f"{out / 'manifest.json'} has no counts block: {manifest.keys()}")
    expectations = {
        "documents": len(STAGED_PDFS),
        "images": 2,
        "documents_with_images": 1,
        "documents_failed": 1,
    }
    for key, expected in expectations.items():
        # `.get` rather than `[]`: pointed at the wrong manifest this should say so, not raise a
        # bare KeyError traceback out of a shell gate.
        if counts.get(key) != expected:
            raise SystemExit(f"expected {key}={expected}, got {counts}")
    written = [path for path in (out / "images").rglob("*") if path.is_file()]
    if not written:
        raise SystemExit("no image artifact was written")
    print(
        f"  {counts['documents']} documents, {counts['images']} images decoded and written, "
        "1 zero-image success, 1 recorded failure"
    )


def main(argv: list[str]) -> int:
    command, *rest = argv
    if command == "relevant-count":
        print(relevant_count(pathlib.Path(rest[0])))
    elif command == "check-retrieve":
        check_retrieve(pathlib.Path(rest[0]), int(rest[1]))
    elif command == "stage-pdfs":
        stage_pdfs(pathlib.Path(rest[0]))
    elif command == "check-extract":
        check_extract(pathlib.Path(rest[0]))
    else:
        raise SystemExit(f"unknown command {command!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
