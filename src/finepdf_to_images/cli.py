"""Scriptable command line entry point with stable exit codes.

Exit codes are part of the public contract and are asserted by the smoke test:

=====  ==========================================================
Code   Meaning
=====  ==========================================================
0      the requested command completed
2      usage error (unknown command, bad or missing arguments)
1      the command ran but the requested work failed
=====  ==========================================================
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from finepdf_to_images import __version__

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2

PROG = "finepdf-to-images"


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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI. Always raises :class:`SystemExit`; the return type keeps ``ty`` happy."""
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.command is None:
        parser.print_help()
        raise SystemExit(EXIT_USAGE)
    if args.command == "version":
        print(__version__)
        raise SystemExit(EXIT_OK)

    parser.error(f"unknown command: {args.command}")  # argparse exits with EXIT_USAGE
    raise SystemExit(EXIT_USAGE)  # pragma: no cover - unreachable, kept for type narrowing
