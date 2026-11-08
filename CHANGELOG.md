# Changelog

## 0.2.0rc1 — release candidate (unreleased)

Tested on NetBox 4.6.9, PostgreSQL 16.15, Python 3.12: 47 tests, all passing.

### Fixed (defects confirmed in 0.1.0)

- **HTTP 500 on model validation errors.** `form.add_error(None, ValidationError(dict))` raised
  `ValueError` for fields the form does not have (`prefix`, `address`), e.g. a duplicate gateway IP.
  Errors are now shown as messages and nothing is written.
- **Webhooks for rolled-back objects.** Events queued by provisional saves were flushed after a
  rollback (verified on 0.1.0: 3 webhooks delivered over HTTP for objects that do not exist).
  `clear_events` is now sent on every rollback path.
- **Lock did not exclude NetBox's allocator.** The wizard now takes PostgreSQL advisory lock
  `ADVISORY_LOCK_KEYS['available-prefixes']`, the same lock as
  `POST /api/ipam/prefixes/<id>/available-prefixes/`, plus an overlap check before commit and after
  commit. Residual risk documented in the README.
- **Container gate was UI-only.** Parent status and `mark_utilized` are checked server-side at
  review and again under lock at confirm.
- **README claimed fully-utilised parents have no free space.** They do in NetBox; they are now
  refused explicitly.
- **Global parents ignored VRF children.** Explicit `global_parent_policy`; VRF parents never look at
  other VRFs, so overlapping space across isolated VRFs stays allowed.
- **Duplicate gateway IPs** in VRFs without `enforce_unique` are refused.
- **Gateways on P2P sizes.** No gateway for /30, /31, /126, /127 (configurable).

### Changed

- Two-step flow: preview/select, then a review page with a **signed plan**; the confirm endpoint
  accepts nothing else. Mass selections (default 16+) require typing the count.
- Prefix length is a dropdown with total/free counts for the parent.
- **Select all free**, **Clear**, optional quick-select; prefixes containing existing IPs or ranges
  are flagged, skipped by *Select all*, and need an acknowledgement.
- Free-space panel: free blocks, children per status, duplicate-prefix warning.
- Reserved prefixes can be split; optional conversion of the parent to Container.
- Defaults: `max_subnets` 64 → 256, `max_preview` 256 → 512, `default_status` active → reserved.
- URLs: `/plugins/subnet-wizard/<parent-pk>/` and `/confirm/`. The old single-page POST no longer
  writes anything.

## 0.1.0 — 2026-10-23

Initial release.
