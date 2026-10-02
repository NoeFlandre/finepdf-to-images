# Extracting images

```bash
uv run finepdf-to-images extract \
  --retrieved out/retrieve/retrieved.jsonl \
  --pdf-root out/retrieve \
  --out out/extract
```

## Scope

This stage extracts the images that are **embedded** in a PDF. It does **not** do these tasks:

- render pages to images
- run OCR
- infer the layout or classify what a picture shows

A scanned document can have one big image on each page. The stage makes one image for each page. This is correct. It is not the same as "the figures in this document". Both limits are out of scope for this first proof of concept. If a fixture does not show that they are necessary, to add them is to guess.

## What each image record contains

Each record contains these fields: `document_row_id`, `document_row_index`, `pdf_sha256`, `page_index`, `image_index`, `sha256`, `mime`, `width`, `height`, `byte_size`, `path`, `duplicate_of`.

The page index and the image index are **positions and not identities**. The same picture can occur on several pages. Each occurrence gets its own record. All records point to one shared artifact.

The dimensions come from the **decoded image**. They do not come from the `/Width` and `/Height` entries of the PDF. These entries are what the document claims. A manifest must record what the artifact is.

## Input safety

The stored path must be **exactly** the content-addressed path that the digest gives. The bytes that the stage reads must hash to that digest. The first check is necessary for this reason. A `retrieved.jsonl` that a person edited by hand can name `../secret.pdf`. It can also name an absolute path. `pathlib` resolves an absolute path and discards the root. Then the stage reads arbitrary files and publishes the images in them. The second check finds a corrupted or swapped artifact. The stage does not index it under a false identity.

The stage counts `max_images` from the page resource dictionaries. It does this before this project decodes anything. In the past, the stage counted while it collected. A whole page of XObjects was already decoded when the limit took effect. A document of 395 KB declared 300 images at 600x600. It peaked at **283 MB** before it refused. Now it peaks at 13 MB.

**This bound is not complete.** pypdf decodes the *inline* images of a page (`BI`/`ID`/`EI`) in order to list them. Thus a small document with 300 inline images still peaks at several hundred MB. `max_pages` (default 300) limits how many pages can do this. The exposure for each page remains. See [TD-008](technical-debt.md). The project records this risk. It does not claim to solve it.

## Identity and layout

The stage stores each image at `images/<aa>/<bb>/<sha256>.<ext>`. The path is sharded, so no directory grows without bound. The stage deduplicates by content across the **whole run**. Thus the same logo on forty pages is one artifact with forty references.

The supported media types are `image/png`, `image/jpeg`, and `image/tiff`. The stage checks the bytes against the magic bytes of the type that the extractor claimed. The label of a library is a claim. The bytes are the evidence. This is the same reasoning as the PDF header check in [retrieval](retrieval.md).

## Ordering

The order is: document, then page, then position on the page. All three come from the PDF. Thus the order is the own order of the document and not an arbitrary order. Two runs over the same input give the same manifest bytes.

The order of the input records decides which occurrence of a repeated image is the original. The artifact is content-addressed. Thus only the `duplicate_of` pointer moves. It references `<pdf_sha256>#<page>.<index>` and not a row id. A row id can be empty, or it can repeat across documents.

## Failures

A **PDF with no embedded images is a success with zero images**. It is not a failure. Most PDFs on the open web contain no images.

A document that the stage cannot read fails with a bounded diagnostic. It contributes **nothing**. Partial output from a document that we could not read is worse than no output. One failure never stops the other documents.

These are real reasons that occurred on the pinned shard:

- `DependencyError: jbig2dec binary is not available`. This is an optional external decoder. The pilot has no reason to install it.
- `document declares more than 200 images; refusing to unpack`. This is a bound and not a preference.

## Results on real data

The table shows the results for the 16 PDFs that the stage retrieved from the pinned shard:

| | |
| --- | --- |
| documents | 16 |
| with images | 10 |
| failed | 2 |
| image references | 71 |
| unique artifacts | 62 |
| output size | 2.7 MB |
| types | 52 JPEG, 19 PNG |
| sizes | 2x50 to 1241x1755 |

## Fixtures

The directory `tests/fixtures/pdfs/` holds seven PDFs. A person wrote them **by hand, byte by byte**, in `tests/fixtures/build_pdf_fixtures.py`. They are golden fixtures. If a PDF library changes its output between versions, the expected hashes change. Then the test asserts the behavior of the library and not our behavior. Each fixture is a few hundred bytes. You can read it in a text editor.

The cases are: two images on a page, the same image twice, no images, a rotated page, two pages, malformed bytes, and a truncated file.

```bash
uv run python tests/fixtures/build_pdf_fixtures.py
```

### What the project pins and what it cannot pin

The fixtures store raw `FlateDecode` samples. **pypdf and Pillow re-encode them to PNG.** Thus the bytes of the published artifact, its `sha256`, its content-addressed path, and the manifest digest all depend on these two libraries. `pyproject.toml` pins both libraries to **exact** versions.

This is not enough for portability. The PNG encoding calls deflate. The result depends on the implementation that the installed wheel links to. The macOS wheel of this project uses **zlib-ng**. The Linux wheel in CI uses **plain zlib**. They give different bytes for the same pixels. The project tried to pin the encoded hashes, and it failed in CI. For this reason the project wrote [TD-007](technical-debt.md).

Thus the golden tests assert the **decoded pixels**, which are portable. They also assert the dimensions, the media type, the count, and the order. Each extract manifest records an `encoder` block. It contains the pypdf version, the Pillow version, and the zlib build of Pillow. Thus a published run states what produced it.

**Note:** All other stages of this pipeline give the same bytes on all machines. This stage does not.

## The stage discards images below 32px

PDFs embed table borders and underlines as real images. On the pilot, 44% of the published rows were images with a side under 32px. 178 of them were 1 to 3px tall. These are page rules and not pictures.

The stage keeps an extracted image only when **both** sides are at least 32px. It applies this rule at extraction. Thus the index and its counts describe real images. See [ADR-0017](adr/0017-discard-images-below-32px.md) for the measured distribution behind the number.
