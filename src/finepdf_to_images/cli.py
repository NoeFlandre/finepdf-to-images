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
from finepdf_to_images.adapters.images import PypdfImageExtractor
from finepdf_to_images.adapters.retrieval import HttpxTransport
from finepdf_to_images.adapters.source import (
    HuggingFaceShardReader,
    LocalShardReader,
    ShardReader,
)
from finepdf_to_images.adapters.storage import read_bytes, read_jsonl
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
from finepdf_to_images.pipeline import run_extract, run_retrieve, run_score, run_select

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
        transport=HttpxTransport(),
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


#: Subcommand dispatch. ``argparse`` guarantees the key exists before we look it up, so there is
#: no unreachable fallback branch to carry.
COMMANDS: dict[str, Callable[[argparse.Namespace], int]] = {
    "version": _cmd_version,
    "select": _cmd_select,
    "score": _cmd_score,
    "retrieve": _cmd_retrieve,
    "extract": _cmd_extract,
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
    except SourceConfigurationError as error:
        # A bad or unsafe input reference is a usage error, not a crash, and must never be
        # answered by widening the read.
        print(f"{PROG}: {error}", file=sys.stderr)
        raise SystemExit(EXIT_USAGE) from error
    except (OSError, ValueError) as error:
        # ValueError covers pyarrow's ArrowInvalid, so a corrupt or truncated shard fails with a
        # diagnostic instead of a traceback. SourceConfigurationError is handled above.
        print(f"{PROG}: {error}", file=sys.stderr)
        raise SystemExit(EXIT_FAILURE) from error
    raise SystemExit(code)
