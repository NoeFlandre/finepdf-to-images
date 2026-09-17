"""Scriptable command line entry point with stable exit codes.

Exit codes are part of the public contract and are asserted by the smoke test:

=====  ==========================================================
Code   Meaning
=====  ==========================================================
0      the requested command completed
1      the command ran but the requested work failed
2      usage error (unknown command, bad or missing arguments)
=====  ==========================================================
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from finepdf_to_images import __version__
from finepdf_to_images.adapters.hub import HubError, HuggingFaceHub
from finepdf_to_images.adapters.images import PypdfImageExtractor
from finepdf_to_images.adapters.retrieval import HttpxTransport
from finepdf_to_images.adapters.source import (
    HuggingFaceShardReader,
    LocalShardReader,
    ShardReader,
)
from finepdf_to_images.adapters.storage import read_bytes, read_jsonl
from finepdf_to_images.domain.publication import DEFAULT_REPO, PublicationError
from finepdf_to_images.domain.retrieval import RetrievalLimits
from finepdf_to_images.domain.source import (
    DEFAULT_CONFIG,
    DEFAULT_LIMIT,
    DEFAULT_REVISION,
    DEFAULT_SEED,
    DEFAULT_SHARD,
    DEFAULT_SPLIT,
    SamplingSpec,
    SourceConfigurationError,
    SourceRef,
)
from finepdf_to_images.pipeline import run_extract, run_publish, run_retrieve, run_score, run_select

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2

PROG = "finepdf-to-images"


def _cmd_version(_args: argparse.Namespace) -> int:
    print(__version__)
    return EXIT_OK


def _reader_for(args: argparse.Namespace) -> ShardReader:
    """Pick the input adapter. ``--source-dir`` keeps a run entirely offline."""
    if args.source_dir is not None:
        return LocalShardReader(root=pathlib.Path(args.source_dir))
    return HuggingFaceShardReader()


def _cmd_select(args: argparse.Namespace) -> int:
    ref = SourceRef(revision=args.revision, config=args.config, split=args.split, shard=args.shard)
    spec = SamplingSpec(limit=args.limit, seed=args.seed, strategy=args.strategy)
    result = run_select(
        reader=_reader_for(args), ref=ref, spec=spec, out_dir=pathlib.Path(args.out)
    )
    print(f"source     {ref.dataset}@{ref.revision}")
    print(f"shard      {ref.path}")
    print(f"sampling   limit={spec.limit} strategy={spec.strategy} seed={spec.seed}")
    print(f"fetched    {result.rows_fetched} rows in {result.row_groups_read} row group(s)")
    print(f"selected   {result.selected}")
    print(f"manifest   {result.manifest_path}")
    print(f"records    {result.records_path}")
    print(f"digest     {result.manifest['records_digest']}")
    return EXIT_OK


def _cmd_score(args: argparse.Namespace) -> int:
    records = read_jsonl(pathlib.Path(args.records))
    result = run_score(records=records, out_dir=pathlib.Path(args.out))
    print(f"scored     {result.scored}")
    print(f"relevant   {result.relevant}")
    print(f"manifest   {result.manifest_path}")
    print(f"scored at  {result.scored_path}")
    print(f"digest     {result.manifest['scored_digest']}")
    return EXIT_OK


#: Substituted by the CLI tests so the retrieve command can be exercised without a network. The
#: command is the composition root for the riskiest stage, and leaving it untested because it is
#: "just wiring" is how wiring bugs reach production.
TRANSPORT_FACTORY: Callable[[], Any] = HttpxTransport


def _cmd_retrieve(args: argparse.Namespace) -> int:
    scored = read_jsonl(pathlib.Path(args.scored))
    rows = [row for row in scored if not args.relevant_only or _is_relevant(row)]
    source = _source_from(pathlib.Path(args.select_manifest))
    limits = RetrievalLimits(
        connect_timeout=args.connect_timeout,
        read_timeout=args.read_timeout,
        max_bytes=args.max_bytes,
        max_redirects=args.max_redirects,
        retries=args.retries,
    )
    result = run_retrieve(
        transport=TRANSPORT_FACTORY(),
        rows=rows,
        source=source,
        out_dir=pathlib.Path(args.out),
        limits=limits,
    )
    print(f"attempted  {result.attempted}")
    print(f"retrieved  {result.retrieved}")
    print(f"unique     {result.unique}")
    print(f"failed     {result.failed}")
    for reason, count in result.manifest["failures"].items():
        print(f"  {reason:<20} {count}")
    print(f"manifest   {result.manifest_path}")
    return EXIT_OK


#: Blocks and inner keys a stage manifest must carry for the later stages to use it. Checked on
#: read so a truncated file names itself rather than surfacing as a KeyError deep in card
#: rendering.
_REQUIRED_MANIFEST_FIELDS: dict[str, dict[str, tuple[str, ...]]] = {
    "select": {
        "source": ("dataset", "revision", "config", "split", "shard", "path"),
        "sampling": ("limit", "seed", "strategy"),
    }
}


def _stage_manifest(path: pathlib.Path, stage: str) -> Mapping[str, Any]:
    """Read a stage manifest, refusing one from a different stage."""
    manifest = json.loads(read_bytes(path))
    if not isinstance(manifest, dict) or manifest.get("stage") != stage:
        raise ValueError(f"{path} is not a {stage} manifest")
    _require_blocks(manifest, path, stage)
    return manifest


def _require_blocks(manifest: Mapping[str, Any], path: pathlib.Path, stage: str) -> None:
    """Check the blocks and inner keys the later stages index directly."""
    for block, fields in _REQUIRED_MANIFEST_FIELDS.get(stage, {}).items():
        if not isinstance(manifest.get(block), dict):
            raise ValueError(f"{path} has no usable {block!r} block")
        absent = [field for field in fields if field not in manifest[block]]
        if absent:
            raise ValueError(f"{path}: {block!r} is missing {', '.join(absent)}")


def _source_from(path: pathlib.Path) -> Mapping[str, Any]:
    """Read the select manifest's source block, refusing anything that is not one.

    Pointing --select-manifest at the *score* manifest is an easy mistake, and it used to produce
    a bare KeyError traceback rather than a diagnostic.
    """
    manifest = json.loads(read_bytes(path))
    if not isinstance(manifest, dict) or manifest.get("stage") != "select":
        raise ValueError(f"{path} is not a select manifest (stage is not 'select')")
    source = manifest.get("source")
    if not isinstance(source, dict):
        raise ValueError(f"{path} has no usable 'source' block")
    return source


def _is_relevant(row: Mapping[str, Any]) -> bool:
    relevance = row.get("relevance")
    return bool(isinstance(relevance, dict) and relevance.get("relevant"))


def _cmd_extract(args: argparse.Namespace) -> int:
    # pypdf logs a warning per malformed image XObject. Real documents produce dozens, which
    # buries the actual result; the per-document reason is recorded in the manifest either way.
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    records = read_jsonl(pathlib.Path(args.retrieved))
    result = run_extract(
        extractor=PypdfImageExtractor(),
        records=records,
        pdf_root=pathlib.Path(args.pdf_root),
        out_dir=pathlib.Path(args.out),
    )
    counts = result.manifest["counts"]
    print(f"documents  {counts['documents']}")
    print(f"  with images   {counts['documents_with_images']}")
    print(f"  failed        {counts['documents_failed']}")
    print(f"images     {counts['images']}")
    print(f"unique     {counts['unique_images']}")
    print(f"manifest   {result.manifest_path}")
    return EXIT_OK


#: Substituted by the CLI tests so publication can be exercised against a fake Hub. The real one
#: is never constructed in a test, so no test can reach the network or a credential.
HUB_FACTORY: Callable[[], Any] = HuggingFaceHub


def _cmd_publish(args: argparse.Namespace) -> int:
    result = run_publish(
        hub=HUB_FACTORY(),
        repo=args.repo,
        select_manifest=_stage_manifest(pathlib.Path(args.select_manifest), "select"),
        scored=read_jsonl(pathlib.Path(args.scored)),
        retrieved=read_jsonl(pathlib.Path(args.retrieved)),
        documents=read_jsonl(pathlib.Path(args.documents)),
        images=read_jsonl(pathlib.Path(args.images)),
        extract_manifest=_stage_manifest(pathlib.Path(args.extract_manifest), "extract")
        if args.extract_manifest
        else None,
        apply=args.apply,
        out_dir=pathlib.Path(args.out) if args.out else None,
        pdf_root=pathlib.Path(args.pdf_root) if args.pdf_root else None,
        image_root=pathlib.Path(args.image_root) if args.image_root else None,
    )
    return _report_publication(result, applied_requested=args.apply)


def _report_removals(removed: Sequence[str], limit: int = 10) -> None:
    """What the publication would delete.

    A publication removes what it does not contain, so the operator sees the removals before
    --apply rather than discovering them afterwards. Truncated because the first cleanup of the
    old layout removes ~200 files and a reviewable dry run is not a wall of paths.
    """
    if not removed:
        return
    print(f"remove     {len(removed)} published file(s) the plan no longer contains")
    for path in removed[:limit]:
        print(f"  {path}")
    if len(removed) > limit:
        print(f"  ... and {len(removed) - limit} more")


def _report_publication(result: Any, *, applied_requested: bool) -> int:
    counts = result.plan.manifest["counts"]
    print(f"repo       {result.plan.repo}")
    print(f"documents  {counts['documents']} ({counts['relevant']} relevant)")
    print(f"retrieved  {counts['retrieved']}")
    print(f"images     {counts['images']} ({counts['unique_images']} unique)")
    print("files")
    for file in result.plan.files:
        print(f"  {file.path:<24} {file.size:>8} bytes  {file.sha256[:16]}...")
    print(f"digest     {result.plan.digest}")
    _report_removals(result.removed)

    if not applied_requested:
        print("\nDRY RUN - nothing was uploaded. Re-run with --apply to publish.")
        print("already published and identical" if result.noop else "would publish the files above")
        return EXIT_OK
    if result.noop:
        print(f"\nno-op: every file is already published, unchanged, at {result.revision}")
        return EXIT_OK
    if not result.ok:
        print(
            f"\nverification FAILED; missing or mismatched: {list(result.missing)}; "
            f"still present after deletion: {list(result.remaining)}",
            file=sys.stderr,
        )
        return EXIT_FAILURE
    print(f"\npublished at revision {result.revision}")
    print(f"verified   {len(result.verified)} file(s) present with the expected bytes")
    if result.removed:
        print(f"removed    {len(result.removed)} stale file(s) in the same commit")
    print(f"           https://huggingface.co/datasets/{result.plan.repo}")
    return EXIT_OK


#: Subcommand dispatch. ``argparse`` guarantees the key exists before we look it up, so there is
#: no unreachable fallback branch to carry.
COMMANDS: dict[str, Callable[[argparse.Namespace], int]] = {
    "version": _cmd_version,
    "select": _cmd_select,
    "score": _cmd_score,
    "retrieve": _cmd_retrieve,
    "extract": _cmd_extract,
    "publish": _cmd_publish,
}


def _add_select_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "select",
        help="select a bounded sample from one pinned FinePDFs shard",
        description=(
            "Read at most --limit rows from exactly one pinned FinePDFs shard and write a "
            "deterministic manifest. Only the row groups the limit requires are fetched; the "
            "full dataset is never enumerated or downloaded."
        ),
    )
    parser.add_argument("--revision", default=DEFAULT_REVISION, help="immutable dataset commit sha")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="language config, e.g. eng_Latn")
    parser.add_argument("--split", default=DEFAULT_SPLIT, help="split name, e.g. train")
    parser.add_argument("--shard", default=DEFAULT_SHARD, help="shard file, e.g. 000_00000.parquet")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="maximum rows to keep")
    parser.add_argument("--seed", default=DEFAULT_SEED, help="seed for the hash sampling strategy")
    parser.add_argument(
        "--strategy",
        default="head",
        choices=["head", "hash"],
        help="head keeps the first rows; hash keeps a seeded stable sample of the read window",
    )
    parser.add_argument(
        "--source-dir",
        default=None,
        help="read the shard from a local directory instead of the Hub (offline runs and tests)",
    )
    parser.add_argument("--out", required=True, help="output directory for the manifest")


def _add_score_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "score",
        help="score selected records for agriculture relevance",
        description=(
            "Score the records written by `select` against a small, documented English "
            "agriculture vocabulary. Every positive result carries the exact terms that produced "
            "it, so the decision can be audited rather than trusted."
        ),
    )
    parser.add_argument("--records", required=True, help="records.jsonl written by `select`")
    parser.add_argument("--out", required=True, help="output directory for the scoring manifest")


def _add_retrieve_parser(subparsers: argparse._SubParsersAction) -> None:
    defaults = RetrievalLimits()
    parser = subparsers.add_parser(
        "retrieve",
        help="retrieve the source PDFs for scored rows, under strict bounds",
        description=(
            "Fetch the source PDF for each selected row. Only http and https URLs are requested, "
            "and only after validation; responses are bounded by timeout and size, validated by "
            "PDF magic bytes rather than by content type, and deduplicated by SHA-256. Every "
            "failure is recorded with its reason."
        ),
    )
    parser.add_argument("--scored", required=True, help="scored.jsonl written by `score`")
    parser.add_argument(
        "--select-manifest", required=True, help="manifest.json written by `select`, for provenance"
    )
    parser.add_argument("--out", required=True, help="output directory")
    parser.add_argument(
        "--relevant-only",
        action="store_true",
        help="retrieve only rows the scorer marked relevant (the usual pilot behaviour)",
    )
    parser.add_argument("--connect-timeout", type=float, default=defaults.connect_timeout)
    parser.add_argument("--read-timeout", type=float, default=defaults.read_timeout)
    parser.add_argument("--max-bytes", type=int, default=defaults.max_bytes)
    parser.add_argument("--max-redirects", type=int, default=defaults.max_redirects)
    parser.add_argument("--retries", type=int, default=defaults.retries)


def _add_extract_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "extract",
        help="extract and index the images embedded in retrieved PDFs",
        description=(
            "Extract every image embedded in each retrieved PDF, deduplicated by SHA-256 and "
            "indexed with document id, PDF hash, page and image index, media type, dimensions "
            "and byte size. A PDF with no embedded images is a zero-image success, not a "
            "failure. Page rendering and OCR are out of scope."
        ),
    )
    parser.add_argument("--retrieved", required=True, help="retrieved.jsonl written by `retrieve`")
    parser.add_argument(
        "--pdf-root", required=True, help="directory the retrieve stage wrote its pdfs/ tree into"
    )
    parser.add_argument("--out", required=True, help="output directory")


def _add_publish_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "publish",
        help="publish the bounded pilot result to a Hugging Face dataset",
        description=(
            "Assemble the published rows, manifest and dataset card from the pilot output and "
            "upload them in one commit. Without --apply this is a dry run: it reads the Hub to "
            "see whether the result is already there, reports the exact files and counts, and "
            "changes nothing. Publishing the same pilot output twice "
            "is a no-op. The card is generated from the policy and vocabulary in code, so it "
            "cannot drift from the rules it describes."
        ),
    )
    parser.add_argument("--select-manifest", required=True, help="manifest.json from `select`")
    parser.add_argument("--scored", required=True, help="scored.jsonl from `score`")
    parser.add_argument("--retrieved", required=True, help="retrieved.jsonl from `retrieve`")
    parser.add_argument("--documents", required=True, help="documents.jsonl from `extract`")
    parser.add_argument("--images", required=True, help="images.jsonl from `extract`")
    parser.add_argument("--extract-manifest", default=None, help="manifest.json from `extract`")
    parser.add_argument("--repo", default=DEFAULT_REPO, help="destination dataset repository")
    parser.add_argument("--out", default=None, help="also write the planned files here for review")
    parser.add_argument(
        "--pdf-root",
        default=None,
        help=(
            "directory holding the retrieved PDFs (the `retrieve` output). Required whenever "
            "the policy cleared any row for byte publication: the run fails rather than "
            "publishing a card that claims bytes it does not carry."
        ),
    )
    parser.add_argument(
        "--image-root",
        default=None,
        help=(
            "directory holding the extracted images (the `extract` output). Required whenever "
            "any published image row points at bytes, for the same reason as --pdf-root."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="actually upload. Without this nothing on the Hub is written.",
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser. Kept separate so documentation can render ``--help``."""
    parser = argparse.ArgumentParser(
        prog=PROG,
        description=(
            "Bounded proof-of-concept pipeline: select a tiny pinned FinePDFs shard, score "
            "agriculture relevance, retrieve the selected PDFs, extract their images, and "
            "publish the result."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")
    subparsers.add_parser("version", help="print the package version and exit")
    _add_select_parser(subparsers)
    _add_score_parser(subparsers)
    _add_retrieve_parser(subparsers)
    _add_extract_parser(subparsers)
    _add_publish_parser(subparsers)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    """Run the CLI. Always exits via :class:`SystemExit`."""
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.command is None:
        parser.print_help()
        raise SystemExit(EXIT_USAGE)

    try:
        code = COMMANDS[args.command](args)
    except (SourceConfigurationError, PublicationError) as error:
        # A bad or unsafe input reference is a usage error, not a crash, and must never be
        # answered by widening the read.
        print(f"{PROG}: {error}", file=sys.stderr)
        raise SystemExit(EXIT_USAGE) from error
    except HubError as error:
        # A dead network or a wrong repo name is a failure to publish, not a crash.
        print(f"{PROG}: {error}", file=sys.stderr)
        raise SystemExit(EXIT_FAILURE) from error
    except (OSError, ValueError, KeyError) as error:
        # KeyError covers a truncated stage manifest: the card and the commit message index the
        # source and sampling blocks directly, and a missing key should say which file is wrong.
        # ValueError covers pyarrow's ArrowInvalid, so a corrupt or truncated shard fails with a
        # diagnostic instead of a traceback. SourceConfigurationError is handled above.
        print(f"{PROG}: {error}", file=sys.stderr)
        raise SystemExit(EXIT_FAILURE) from error
    raise SystemExit(code)
