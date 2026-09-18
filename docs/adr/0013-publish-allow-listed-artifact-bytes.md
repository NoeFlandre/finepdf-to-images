# ADR-0013 — Publish artifact bytes for allow-listed sources

Status: accepted (2026-09-17)

## Context

Until now the dataset published metadata and hashes only. Every retrieved artifact resolved to
`metadata-only`, and `build_plan` carried an interlock that refused outright to publish bytes —
the correct default while no code implemented upload, but it also meant the images the project is
named after were never visible, and the dataset could not be used without re-fetching 16 URLs that
may rot.

ADR-0002 set the rule that governs this: a document being in FinePDFs is not permission to
republish it. FinePDFs is ODC-BY, which covers the collection — the extracted text, the metadata,
the compilation — and says nothing about copyright in the PDFs the rows point at. `EvidenceSource`
accordingly admits exactly one basis for publishing bytes: `CURATED_ALLOWLIST`, "a human decision
recorded in this repository".

Scanning the 16 retrieved PDFs for a machine-checkable licence found **none**. No Creative Commons
URL, no `dc:rights`, no public-domain dedication; two assert copyright. Absence of a statement is
not permission, so a detector would have cleared nothing — and could never clear anything, because
the documents that *are* free to republish are free by **statute**, and no scanner can read
17 U.S.C. 105 off a page that does not mention it.

## Decision

1. **A curated allow list, keyed by exact host** (`domain/allowlist.py`). Each entry records the
   identifier and the specific instrument that puts the work outside copyright or grants reuse. A
   test refuses an entry whose basis cites no instrument, because "it looked official" is not a
   basis.
2. **Three entries, each approved deliberately:**
   - `ntp.niehs.nih.gov` — work of the US federal government; no copyright subsists under
     17 U.S.C. 105.
   - `archive.opengazettes.org.za` — South African provincial gazette; s. 12(8)(a) of the
     Copyright Act 98 of 1978 denies copyright to official texts of a legislative, administrative
     or legal nature.
   - `eeas.europa.eu` — EU institutional document; reuse authorised under Commission Decision
     2011/833/EU on equivalent attribution terms.
3. **Both ends of a retrieval must be allow listed, and to the same entry.** A request that
   starts at an approved host and ends elsewhere has left the permission behind. Without this, any
   open redirect on an approved host launders arbitrary bytes into the dataset. Precisely: the
   requested URL and the final URL after redirects, which is what `RetrievalRecord` carries.
   Intermediate hops are not recorded, so "every host in the chain" would overstate it.
4. **Matching is by exact host, never by suffix or substring.** `nih.gov` does not clear
   `evil.nih.gov.attacker.test`, and `ntp.niehs.nih.gov` appearing in a path or query clears
   nothing.
5. **Replace the interlock with checks that are stricter than the caller.** An artifact ships only
   if its path is the content-addressed path for the bytes actually passed, its digest belongs to a
   row the policy cleared, and the total stays under `MAX_ARTIFACT_BYTES` (64 MB). "Stricter than
   the caller" has to hold for *both* payloads: an early version joined PDFs to the policy's
   `disposition` but took the image column at face value, so for images the check was exactly as
   strong as its caller. Image clearance now joins to `cleared_row_ids` as well.
6. **The card's claim and the payload are checked against each other**, in both directions. A
   manifest saying source bytes are republished with no artifact in the plan is a published
   falsehood, and so is the reverse.
7. **Declare the image column's type in the card front matter** so the Hub renders a picture. An
   undeclared column is inferred as a string and the viewer shows a path.

## Consequences

- Images from allow-listed sources render in the dataset viewer. This is what issue #19 asked for.
- The overwhelming majority of rows are unchanged: metadata and hashes, `image: null`, `pdf: null`.
  The dataset says what it declined to publish rather than hiding it.
- **The allow list is the project's main standing risk.** It is three human judgements about
  foreign statutes, made by reading the law rather than by obtaining a licence, and one of them
  (the EU Decision) does not extend to third-party material a document may quote. `TAKEDOWN_CONTACT`
  exists for exactly this, and requests are honoured without requiring proof of ownership.
- Adding a source is a deliberate edit to a reviewed file, not a configuration change, and it
  requires re-running `retrieve`: the disposition is decided when the bytes are fetched and stored
  in the record, so the allow list is not retroactive over an existing run.

## Amendment (2026-09-18)

The decision stands; one of its checks stopped running and has been restored.

The 64 MB cap was enforced in `_check_artifacts`, which was reachable only from `build_plan`. Once
the minimal parquet (ADR-0015, ADR-0016) replaced the six-file layout, `build_plan` had no callers,
and the dead-code removal that followed took the cap check with it. This document and
`docs/publishing.md` went on describing a ceiling that nothing measured.

`check_artifact_byte_cap` restores it, and the publish stage calls it beside `check_text_byte_cap`
so both caps are enforced at one point. It measures the image bytes embedded in the rows being
published rather than everything the run extracted — the same correction the text cap needed after
it refused a valid run over text that was never published.

The other checks in point 5 are unaffected: content-addressed paths and clearance by row id both
sit on the path that is still in use.
