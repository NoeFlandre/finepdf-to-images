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
import pathlib
import sys
from collections.abc import Callable, Sequence

from finepdf_to_images import __version__
from finepdf_to_images.adapters.source import (
    HuggingFaceShardReader,
    LocalShardReader,
    ShardReader,
)
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
from finepdf_to_images.pipeline import run_select

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


#: Subcommand dispatch. ``argparse`` guarantees the key exists before we look it up, so there is
#: no unreachable fallback branch to carry.
COMMANDS: dict[str, Callable[[argparse.Namespace], int]] = {
    "version": _cmd_version,
    "select": _cmd_select,
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
