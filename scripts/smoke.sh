#!/usr/bin/env bash
# Exercise the real CLI end to end against the committed fixtures.
#
# This is the gate that catches what the unit and integration suites structurally cannot: argument
# wiring, exit codes, and the console script actually being installed. Two real bugs reached the
# repository past a green suite and were caught on the real path -- an httpx.Timeout constructed
# with the wrong arguments, and a pypdf DependencyError that aborted an entire run.
#
# Every stage is asserted on its counts, not merely on "a manifest exists". An earlier version of
# this script ran `extract` over an empty retrieval, so the stage never opened a PDF -- it could
# not have caught the very bug this gate is credited with catching.
#
# Offline by design: no network, no Hugging Face token, no third-party site.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${1:-$(mktemp -d)}"
RUN="${SMOKE_RUN:-uv run}"
HELPERS="$ROOT/scripts/smoke_assertions.py"

cd "$ROOT"
echo "smoke output: $OUT"

step() { printf '\n=== %s ===\n' "$1"; }

step "help and version"
$RUN finepdf-to-images --help > /dev/null
$RUN finepdf-to-images version

step "usage errors exit 2"
set +e
$RUN finepdf-to-images > /dev/null 2>&1; [ $? -eq 2 ] || { echo "no-command must exit 2"; exit 1; }
$RUN finepdf-to-images select --source-dir tests/fixtures/shards --config bogus --out "$OUT/x" \
  > /dev/null 2>&1; [ $? -eq 2 ] || { echo "bad config must exit 2"; exit 1; }
set -e

step "select"
$RUN finepdf-to-images select --source-dir tests/fixtures/shards --limit 20 --out "$OUT/select"

step "score"
$RUN finepdf-to-images score --records "$OUT/select/records.jsonl" --out "$OUT/score"
RELEVANT="$($RUN python "$HELPERS" relevant-count "$OUT/score/scored.jsonl")"
echo "  $RELEVANT relevant rows"

step "retrieve (offline: every fixture url is unreachable, so every row fails with a reason)"
$RUN finepdf-to-images retrieve \
  --scored "$OUT/score/scored.jsonl" \
  --select-manifest "$OUT/select/manifest.json" \
  --relevant-only --connect-timeout 0.05 --read-timeout 0.05 --retries 0 \
  --out "$OUT/retrieve"
$RUN python "$HELPERS" check-retrieve "$OUT/retrieve/manifest.json" "$RELEVANT"

# The committed fixture PDFs stand in for documents the offline run could not fetch, so `extract`
# really opens a PDF and decodes an image.
step "extract (over the committed fixture PDFs, so images are really decoded)"
$RUN python "$HELPERS" stage-pdfs "$OUT/staged"
$RUN finepdf-to-images extract \
  --retrieved "$OUT/staged/retrieved.jsonl" --pdf-root "$OUT/staged" --out "$OUT/extract"
$RUN python "$HELPERS" check-extract "$OUT/extract"

step "determinism"
$RUN finepdf-to-images select --source-dir tests/fixtures/shards --limit 20 --out "$OUT/select2" \
  > /dev/null
cmp "$OUT/select/manifest.json" "$OUT/select2/manifest.json"
$RUN finepdf-to-images score --records "$OUT/select2/records.jsonl" --out "$OUT/score2" > /dev/null
cmp "$OUT/score/manifest.json" "$OUT/score2/manifest.json"
$RUN finepdf-to-images extract \
  --retrieved "$OUT/staged/retrieved.jsonl" --pdf-root "$OUT/staged" --out "$OUT/extract2" \
  > /dev/null
cmp "$OUT/extract/images.jsonl" "$OUT/extract2/images.jsonl"
echo "select, score and extract outputs are byte-identical across runs"

step "every stage wrote a manifest"
for stage in select score retrieve extract; do
  test -s "$OUT/$stage/manifest.json" || { echo "$stage wrote no manifest"; exit 1; }
  echo "  $stage/manifest.json ok"
done

printf '\nsmoke passed\n'
