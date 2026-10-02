# ADR-0013: Publish artifact bytes for allow-listed sources

Status: accepted (2026-09-17)

## Context

Until now, the dataset published metadata and hashes only. Each retrieved artifact resolved to `metadata-only`. `build_plan` had an interlock that refused to publish bytes. This was the correct default while no code implemented upload. But the images that give the project its name were never visible. Nobody could use the dataset without fetching 16 URLs again. These URLs can rot.

ADR-0002 set the rule for this case: a document in FinePDFs is not a permission to republish it. FinePDFs has the ODC-BY license. This license covers the collection: the extracted text, the metadata, and the compilation. It does not cover the copyright of the PDFs that the rows point to. Thus `EvidenceSource` admits exactly one basis to publish bytes: `CURATED_ALLOWLIST`. This is "a human decision recorded in this repository".

The team scanned the 16 retrieved PDFs for a license that a machine can check. It found **none**. There was no Creative Commons URL, no `dc:rights`, and no public-domain dedication. Two documents assert copyright. The absence of a statement is not a permission. Thus a detector clears nothing. It can never clear anything. The documents that are free to republish are free by **statute**. No scanner can read 17 U.S.C. 105 from a page that does not mention it.

## Decision

1. **Use a curated allow-list that is keyed by exact host** (`domain/allowlist.py`). Each entry records the identifier. It also records the specific instrument that puts the work outside copyright or grants reuse. A test refuses an entry whose basis cites no instrument. "It looked official" is not a basis.
2. **Add three entries. A person approved each entry on purpose:**
   - `ntp.niehs.nih.gov`: a work of the US federal government. No copyright exists under 17 U.S.C. 105.
   - `archive.opengazettes.org.za`: a South African provincial gazette. Section 12(8)(a) of the Copyright Act 98 of 1978 denies copyright to official texts of a legislative, administrative, or legal nature.
   - `eeas.europa.eu`: an EU institutional document. Commission Decision 2011/833/EU authorizes reuse on equivalent attribution terms.
3. **Both ends of a retrieval must be on the allow-list, and on the same entry.** A request that starts at an approved host and ends at another host has left the permission behind. Without this rule, any open redirect on an approved host puts arbitrary bytes into the dataset. The two ends are the requested URL and the final URL after redirects. `RetrievalRecord` carries them. It does not record the intermediate hops. Thus "each host in the chain" overstates the check.
4. **Match by exact host. Never match by suffix or substring.** `nih.gov` does not clear `evil.nih.gov.attacker.test`. `ntp.niehs.nih.gov` in a path or query clears nothing.
5. **Replace the interlock with checks that are stricter than the caller.** An artifact ships only if all three conditions are true:
   - Its path is the content-addressed path for the bytes that the caller passed.
   - Its digest belongs to a row that the policy cleared.
   - The total stays under `MAX_ARTIFACT_BYTES` (64 MB).

   "Stricter than the caller" must be true for *both* payloads. An early version joined the PDFs to the `disposition` of the policy. But it took the image column at face value. Thus for images the check was only as strong as its caller. Now the image clearance also joins to `cleared_row_ids`.
6. **Check the claim of the card and the payload against each other**, in both directions. A manifest that says the dataset republishes source bytes, with no artifact in the plan, is a published falsehood. The reverse is also a falsehood.
7. **Declare the type of the image column in the card front matter.** Then the Hub renders a picture. The Hub infers an undeclared column as a string. Then the viewer shows a path.

## Consequences

- The images from allow-listed sources render in the dataset viewer. Issue #19 asked for this.
- Most rows do not change. They have metadata and hashes, `image: null`, and `pdf: null`. The dataset says what it declined to publish. It does not hide it.
- **The allow-list is the main standing risk of the project.** It contains three human judgements about foreign statutes. The team made them by a reading of the law and not by the acquisition of a license. One of them, the EU Decision, does not extend to third-party material that a document can quote. `TAKEDOWN_CONTACT` exists for this case. The project honors requests without proof of ownership.
- To add a source is a deliberate edit to a reviewed file. It is not a configuration change. It requires you to run `retrieve` again. The code decides the disposition when it fetches the bytes and stores it in the record. Thus the allow-list is not retroactive over an existing run.

## Amendment (2026-09-18)

The decision stands. One of its checks stopped running. The team restored it.

`_check_artifacts` enforced the 64 MB cap. Only `build_plan` could reach it. The minimal Parquet (ADR-0015, ADR-0016) replaced the layout of six files. Then `build_plan` had no callers. The removal of dead code that followed took the cap check with it. This document and `docs/publishing.md` continued to describe a ceiling that nothing measured.

`check_artifact_byte_cap` restores the cap. The publish stage calls it next to `check_text_byte_cap`. Thus one point enforces both caps. It measures the image bytes that are embedded in the rows that the run publishes. It does not measure everything that the run extracted. The text cap needed the same correction. It refused a valid run because of text that the run never published.

The other checks in point 5 are not affected. The content-addressed paths and the clearance by row id are both on the path that the code still uses.
