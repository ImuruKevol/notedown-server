# Changelog

## 1.1.0 - 2026-08-19

### Fixed

- Made legacy batch sync conflict handling all-or-nothing so metadata, files, and
  attachments cannot be partially persisted before a later item reports a
  conflict.
- Rejected duplicate `relativePath` entries in plan and batch payloads before
  any mutation, preventing request-order-dependent writes and synthetic
  conflicts.
- Required exact metadata and per-file revision matches for content-changing
  writes, so a corrupted or global future revision cannot bypass optimistic
  locking. Exact same-content and delete retries remain idempotent.
- Replaced device-clock ordering for divergent file and attachment content with
  Git-backed server ancestry checks. A known server ancestor downloads cleanly;
  a local hash outside the server lineage becomes a conflict even when that
  device's clock is behind.
- Added a durable mutation checkpoint spanning `state.json`, `metadata.db`, the
  physical file tree, and the internal Git repository. Runtime failures roll
  back immediately, and interrupted mutations are recovered on the next start.
- Ensured rollback operations use the active in-memory state instead of
  reloading the store during an open transaction.

### Changed

- Added `skipped` and `skippedAttachments` batch response fields for items that
  passed preflight but were deliberately not written because another item
  conflicted. A metadata preview that was not applied is reported as
  `status: skipped` with `reason: batch_conflict`.
- Limited stored client telemetry to the 128 most recently seen clients and
  bounded client IDs, nested metadata, strings, keys, and list sizes to prevent
  unbounded `state.json` and manifest growth.
- Updated the OpenAPI document and operational documentation for the hardened
  synchronization contract.
- Added OCI image version, source, and Git revision labels so deployed registry
  images can be matched to a source release without relying on tag dates.

### Verification

- 56 API and storage regression tests, including multi-device conflicts,
  deletes, duplicate payload paths, runtime rollback, and restart recovery.
