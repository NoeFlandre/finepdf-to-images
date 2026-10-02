# Technical debt

This page records the debt explicitly. It does not hide it. Each entry states what is missing. It gives the reason why it was acceptable to ship without it. It gives the trigger that starts the work to pay it.

## ~~TD-001~~ (retired)

Issue #5 added the full gauntlet: property tests, Gherkin acceptance scenarios, coverage, CRAP, mutation testing, and a smoke test of the real CLI. CI runs all of them. See [Quality gates](quality.md).

## TD-002: `ty` is pre-1.0

**State.** The type check uses `ty` from Astral. It is an alpha tool. Its configuration surface is unstable and its inference is incomplete.

**Reason.** The project uses this checker as its standard. It is fast, and it already catches real errors. A second type checker in CI gives no additional signal.

**Trigger.** Evaluate the pin again when `ty` reaches a stable release. Evaluate it also if a false positive forces a suppression that hides a real defect.

## TD-003: branch protection does not include administrators

**State.** `main` requires these checks to pass: `Deterministic quality gates`, `Docker smoke`, and `Docs build`. It requires branches to be up to date. It forbids force pushes and deletions. It requires a linear history. It does **not** set `enforce_admins`. Thus a repository administrator can merge past a red gate.

**Reason.** The repository has one maintainer. If the project locks the administrator out, it removes a real recovery path. In exchange it gets a theoretical guarantee.

**Trigger.** Enable `enforce_admins` if a second maintainer joins. Enable it also if anyone ever merges past a red gate in practice.

## TD-004: the pilot has no curated allow-list entries

**State.** `ALLOWED_LICENSES` has values. But nothing in the pipeline produces a `curated-allowlist` declaration. Thus each artifact in the current run resolves to `metadata-only`. The published dataset contains provenance and hashes and **no third-party bytes**.

**Reason.** To clear a document for redistribution, a person must establish its terms. For an arbitrary sample of the open web, this is work for each document. A guess is what [ADR-0005](adr/0005-conservative-publication-policy.md) refuses to do. A policy without entries is the honest state. It is not a wrong configuration.

**Trigger.** Add entries when there is a concrete set of documents with established terms. The entries must be reviewed records with a source attribution in this repository. Until then, the dataset card must state plainly that the dataset republishes no source bytes.

## ~~TD-005~~ (retired)

The retrieval stage uses the policy. Each record carries a `decide()` verdict. The publication stage also uses it. Its card comes from `policy_summary()`. See [Publishing](publishing.md).

<!-- The original entry, kept for the reasoning rather than as an open item. -->
### Why it was open

**State.** `domain.policy.decide()` is complete and tested, but nothing calls it. There is no retrieval or extraction stage yet to produce artifacts for it to judge. There is no publication stage to use its verdicts or to write `policy_summary()` into a dataset card.

**Reason.** The policy is a standalone pure function on purpose. The project delivered it before the stages that need it. Thus those stages use a rule that is already decided. They do not invent a rule. To connect it to a stage that does not exist, the project must write that stage here.

**Trigger.** Issues #3 and #4 pass `require_artifact_hash=True` when they judge retrieved bytes. Issue #2 writes the card from `policy_summary()`. It publishes only what `decide()` permits. Until all three are done, the statement "this project republishes no third-party bytes" is true because the project publishes no bytes at all. It is not true because the policy refused them.

## TD-006: DNS rebinding and host names that resolve to private addresses

**State.** `validate_url` refuses IP **literals** that name a loopback, private, link-local, reserved, multicast, or unspecified address. It does not catch a *host name* that resolves to one of those addresses. It also does not catch a host that resolves differently between the check and the request.

**Reason.** To resolve a name is I/O. The URL rules are in the pure domain so that tests do not need a network. To close this gap correctly, the code must resolve the name in the adapter. It must check the resolved address. It must pin the connection to that address. This is real work. A bounded pilot that fetches a few dozen public documents does not need it.

**Trigger.** Do this work before anyone points this pipeline at untrusted URLs from inside a network that has valuable targets. Do it also before anyone runs the pipeline as a service. Until then, the exposure is a developer machine that fetches public PDFs.

## TD-007: the bytes of extracted images are not portable across Pillow builds

**State.** Pillow re-encodes the embedded images to PNG when they are not already in a standard format. The PNG encoding calls deflate. The result depends on the implementation that the installed wheel uses. The macOS wheel of this project links **zlib-ng**. The Linux wheel in CI links **plain zlib**. They give different bytes for the same pixels.

Thus the same pipeline, on the same inputs, with the same pinned dependency versions, gives **different image `sha256` values, different content-addressed paths, and a different `images_digest`** on a different platform. All other stages are byte-identical: selection, scoring, retrieval manifests, and PDF artifacts. This stage is the exception.

**How the team found it.** Golden tests that pinned the encoded hashes passed locally and failed in CI. The test did its job.

**What the team did.** The golden tests assert the **decoded pixels**, which are portable. Each extract manifest records `encoder`: the pypdf version, the Pillow version, and the zlib build of Pillow. Thus a published run states what produced it.

**Reason why the team did not simply fix it.** Each option has a cost:

- Encoding at `compress_level=0` removes the variance. But it increases an image of 1241x1755 from about 200 KB to about 6.5 MB.
- To publish the original embedded stream bytes of the PDF is faithful and portable. But for images with raw samples the result is not a viewable file.
- To vendor an encoder is not proportionate for a pilot.

**Trigger.** Do this work before anyone uses image hashes to compare two runs from different machines. Do it also before anyone regenerates the published dataset on a different platform, because the artifact paths change. If this matters more than the file size, publish the original embedded streams and record the format of each image.

## TD-008: the code decodes inline images before it can count them

**State.** The code checks `max_images` against the images that a page *declares*. It does this before this project decodes any image. This bounds the image XObjects. It does not bound the **inline** images. These are the `BI`/`ID`/`EI` operators in a content stream. pypdf decodes each inline image in order to name it. It does this in the same call that lists the images of a page. A hand-built document of **1.4 KB** has 300 flate-compressed inline images of 600x600. It peaks at about **300 MB** of resident memory before the limit takes effect. This is a decompression bomb. The input size bound from the retrieval stage does not help.

**What the team did.** `max_pages` (default 300) bounds how many pages can do this. The code parses each page, with or without images. The exposure for each page remains.

**Reason why the team did not simply fix it.** To bound a single page, the code must not use the content-stream parser of pypdf. There are two options. The code can scan the raw stream for inline-image operators before it gives the page to pypdf. Or the project can replace the parser. Both options are not proportionate for a pilot that fetches a few dozen public documents under a cap of 25 MB.

**How the team found it.** An independent review measured it against the code that had just "fixed" the XObject case. The regression test from that time asserted only that *our* decode path did not run. It could not observe the decoding inside pypdf. It passed against the vulnerable code. The docstring of that test now says so.

**Trigger.** Do this work before anyone runs this stage over untrusted documents at scale or unattended. Do it also before anyone runs it where a spike of 300 MB for each document matters. A memory limit for each process is a cheaper mitigation than the replacement of the parser.

## An upgrade of `pyarrow` is a republication

The project pins `pyarrow` to an exact version. `adapters/parquet.py` pins each writer option that it can reach. But the `created_by` field of the Parquet footer carries the version string of pyarrow. The API cannot set it. Thus the published bytes are byte-stable for a given pyarrow version. They change when you upgrade.

Publication is idempotent by content hash. Thus an upgrade rewrites each published Parquet file. It makes a commit that changes no data. For a short time, "a second apply is a no-op" is false, for a reason that is not related to the dataset. Treat an upgrade as a deliberate republication. Upgrade it alone. Publish again. Say so.
