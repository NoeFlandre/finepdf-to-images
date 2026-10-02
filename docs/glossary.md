# Glossary

This page defines the technical names of the project. Each name has one meaning. The documentation uses each name in the same way.

| Term | Meaning |
| --- | --- |
| ADR | Architecture decision record. A short document that gives one decision and the reason for it. |
| Adapter | A thin module that does side effects (network, filesystem, PDF, Hub). The domain does not import it. |
| Allow-list | The list of identifiers that a person approved for byte publication. The pilot has no entries. |
| Artifact | A file that the pipeline makes or retrieves, for example a PDF or an image. |
| Bounded | Limited by a fixed number. A bounded sample has a fixed maximum number of rows. |
| Composition root | The module that connects the adapters to the domain (`cli`, `pipeline`). |
| Content addressing | Naming a file by the SHA-256 hash of its bytes. |
| Dataset card | The README of the published dataset. Its front matter declares the configs. |
| Deterministic | Giving the same bytes from the same pinned inputs. |
| Domain | The pure part of the code. It makes decisions and has no side effects. |
| Dry run | A run that makes no upload. The command `publish` is a dry run unless you add `--apply`. |
| Extract | To take the embedded images out of a PDF. |
| FinePDFs | The source dataset `HuggingFaceFW/finepdfs`. |
| Fixture | A small committed test input. |
| Gate | A check that must pass. A blocking gate stops a merge. |
| Pinned | Fixed to one exact version. The pipeline reads one pinned shard. |
| Policy | The rules in `domain/policy.decide()` that decide what the pipeline publishes. |
| Provenance | The record of the origin of a row: its FinePDFs row, URL, and hashes. |
| Retrieve | To download a source PDF from its URL. |
| Row | One record of a dataset. |
| Score | The number that gives the agriculture relevance of a row. |
| Selection | The step that keeps a bounded sample of rows from the pinned shard. |
| Shard | One file of the FinePDFs dataset. |
| Split | A named part of the published dataset, for example `relevant` or `retrieved`. |
| Takedown | A request to remove published content. |
