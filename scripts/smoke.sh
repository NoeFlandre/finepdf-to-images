#!/usr/bin/env bash
# Exercise the real CLI end to end against the committed fixtures.
#
# This is the gate that catches what the unit and integration suites structurally cannot: argument
# wiring, exit codes, and the console script actually being installed. Two real bugs reached the
# repository past a green suite and were caught on the real path -- an httpx.Timeout constructed
# with the wrong arguments, and a pypdf DependencyError that aborted an entire run.
#
# Offline by design: no network, no Hugging Face token, no third-party site.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${1:-$(mktemp -d)}"
RUN="${SMOKE_RUN:-uv run}"

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

step "retrieve (offline: every fixture url is unreachable, so every row fails with a reason)"
$RUN finepdf-to-images retrieve \
  --scored "$OUT/score/scored.jsonl" \
  --select-manifest "$OUT/select/manifest.json" \
  --relevant-only --connect-timeout 0.05 --read-timeout 0.05 --retries 0 \
  --out "$OUT/retrieve"

step "extract (nothing was retrieved, so this is an empty run)"
$RUN finepdf-to-images extract \
  --retrieved "$OUT/retrieve/retrieved.jsonl" --pdf-root "$OUT/retrieve" --out "$OUT/extract"

step "determinism"
$RUN finepdf-to-images select --source-dir tests/fixtures/shards --limit 20 --out "$OUT/select2" \
  > /dev/null
cmp "$OUT/select/manifest.json" "$OUT/select2/manifest.json"
echo "select manifest is byte-identical across runs"

step "every stage wrote a manifest"
for stage in select score retrieve extract; do
  test -s "$OUT/$stage/manifest.json" || { echo "$stage wrote no manifest"; exit 1; }
  echo "  $stage/manifest.json ok"
done

printf '\nsmoke passed\n'
