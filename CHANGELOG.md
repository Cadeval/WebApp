# Changelog

## 0.6.0 — Unreleased

- Add a private building map with searchable source models, shared-position markers, owner-set locations and links into the 3D viewer and saved assessments.
- Import CityJSON through IfcCityJSON 0.8.5 with explicit LoD selection, retained originals, conversion provenance and geometry-derived map anchors.
- Export detailed IFC components as schema-validated CityJSON 1.1, using parallel native tessellation and source-keyed private caches.
- Keep map navigation within the HTMX content container and use locally bundled Leaflet in the site's grey/white scheme.
- Bump BIM Workspace to 1.3.0; both development and production remain compatible.

## 0.5.0 — Unreleased

### Added

- IFC material textures and a selected-part inspector for declared physical properties, saved assessment impacts, Euro costs and recovery grades.
- An owned, source-fingerprinted material index with cached element details loaded on demand.
- Display controls for material textures, room envelopes and opening-cut volumes.

### Changed

- Fit directional shadows and camera clipping to each model, move the grid below its floor, and preserve authored transparency and IFC colors.
- Place the inspector beside the canvas and dispose model resources on HTMX navigation.
- Preserve authored colors in the demo and fit each preview's camera and ground grid to its own bounds.
- Bump the BIM Workspace plugin to 1.2.0.

## 0.4.0 — Unreleased

### Added

- User settings for profile, theme, password, signing keys and access/capacity.
- Permission-aware user and group administration, with delegated grant limits and protected administrators.
- Debug-only UI/UX Suite MCP plugin for bounded local frontend audits and guidance.

### Changed

- Unified the plugin catalog and personal collection into Plugins with a single navigation entry.
- Upgraded to HTMX 4.0.0; migrated inheritance, lifecycle cleanup and full/partial history responses.
- Improved landing-page guidance, accessible form feedback and readable neutral themes.

### Breaking changes

- Legacy Store and signing-key pages redirect to Plugins and Security settings respectively.
- Shell integrations use HTMX 4 colon-delimited events and explicit attribute inheritance.

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
