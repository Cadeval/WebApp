# Changelog

## 0.3.0 — Unreleased

### Changed

- Plugin Manager is the shared catalog and Plugin Store is each user's chosen collection, adjacent in navigation.
- Workflow navigation, editor contributions, pages and signed worker assets require an explicit user selection.
- Publishing moved to Manager; uploaded workers have an independent workflow page.
- Documented administrator-managed external repositories, publisher trust and reviewed release imports.

### Breaking changes

- Store enable/disable actions now affect the current user's selection. Staff site-wide activation remains at
  `/plugins/{plugin_id}/enable/` and `/plugins/{plugin_id}/disable/`.
- Existing users must choose their workflow tools in Manager. Global availability alone no longer grants workflow access.

## 0.2.0 — Unreleased

### Added

- IFC material passports aligned with the thesis, material reference neighbors, and recovery-grade cost analysis in EUR.
- Parallel IfcOpenShell geometry and asynchronous assessment futures.
- Public demo with actual house previews, precomputed provisional values and step controls.
- Unified Bolt/HTMX pages, linked validation diagnostics and administrator WebSocket log streaming.
- Signed archive plugin store, browser-generated Ed25519 keys and local signing CLI.
- Environment compatibility labels and supervised debug-only MCP plugins in `make debug`.

### Changed

- Application modules now live under `apps`, with shared models and migrations.
- Pages share a muted grey/white palette and vendored CSS normalization.
- Validation errors are nonfatal, grouped by count and linked to IFC elements in the viewer.
- Production launch explicitly selects production settings and excludes MCP.

### Breaking changes

- Standalone JavaScript/WASM uploads, their metadata fields and the old artifact route were removed. Upload a signed ZIP/TAR archive with metadata in `plugin.json` instead.

This pre-1.0 minor version records the changed upload contract. Plugin API compatibility remains 1.0; independent and third-party package versions are unchanged.
