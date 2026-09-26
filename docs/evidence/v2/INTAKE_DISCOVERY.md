# V2 Actual Intake and First-Flow Discovery

**Status:** local snapshot capture/import validation accepted; producer completion provenance, production app/worker preparation, and live-flow qualification remain pending.
**Date:** 2026-09-26
**Selected first flow:** DATEV-hosted Workday, owner-selected.
**Authorization boundary:** this evidence records file inspection and local queue import only. It does not authorize browser navigation, form mutation, upload, or submission.

## Source and privacy

The source was an existing JobHunter `application_queue.jsonl` snapshot. Its absolute path and referenced document filenames remain on the local machine and are intentionally omitted here. The captured UTF-8 file was 2,239 bytes with SHA-256 `91597ba35a745ce5462ff58555150e94dd7a15c54631839cdc5e4facd29e1023`. The QueueImportService capture and a separate importer-validation read agreed on the same source fingerprint; the service also verified that the file size and modification time stayed stable while capturing it. This establishes capture consistency only. The inspected snapshot did not include a completion/run marker or a queue-to-document generation manifest, so producer completion and whether the referenced PDFs came from the same generation remain unverified. No queue, candidate facts, document text/private contents, application identifiers, or private URLs were copied into the repository.

The snapshot had one nonblank JSONL object. Its field names matched the importer contract, including application/source identity, employer/title/URL, platform, evaluation/status, artifact references, and metadata. The row's producer verdict was `consider` while its queue status was `ready_to_apply`; the current importer contract accepted it as `ready_to_apply` on Workday. Both referenced PDF files existed; the prior intake inspection parsed their PDF structure/metadata successfully with `pdfinfo`. No candidate or document text/private contents were recorded.

The selected row identifies the DATEV Workday posting chosen by the owner. This records the target flow selection only; it does not claim that the live application page was inspected.

## Isolated named-service validation

The named `QueueImportService` was run twice with the explicit source path and a disposable temporary SQLite database. The run produced two durable `success` records. Each reported one input line and one import. The final temporary database contained one persisted `ready_to_apply` job, and both persisted run fingerprints matched the source SHA-256 above. The JobHunter source remained unchanged. The engine was disposed before temporary-store cleanup.

This was a direct local service call: it did not launch the production app/API/worker, browser, pipeline, or submission path. It validates actual-input capture, parser/import behavior, durable run recording, and single-row upsert behavior for this snapshot. It does not establish production startup configuration or preparation readiness.

## Remaining acceptance

- **Accepted locally:** the existing queue snapshot was captured consistently, its one row was accepted by the current importer, the owner-selected DATEV Workday flow was recorded, the referenced PDFs existed and parsed, and two named-service imports persisted safely in a disposable store.
- **Pending:** producer completion/run provenance and a manifest tying the queue snapshot to its referenced PDFs. A stable content hash does not establish a complete producer generation.
- **Pending:** production UAA app/API/worker intake and preparation path, persisted attempt/review evidence, and the no-final-request preparation acceptance.
- **Unverified:** live DATEV navigation, mutation, and upload behavior. No live browser action was authorized or performed.
- **Not authorized here:** final submission. The controlled-submission gates remain separate and unchanged.
