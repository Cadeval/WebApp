# CityJSON exchange and building map

BIM Workspace 1.3.0 adds a private building map at `/plugins/bim/map/`, available from the enabled workflow navigation and Model Manager. Search the building list, select a marker, then open its 3D model, assessments or location editor. Buildings at the same position share a marker with a building picker. Buildings without usable georeferencing stay in the list.

Only the current user's uploads and reports are included. The workflow must be globally enabled and selected by that user. Source downloads, export downloads and location changes enforce the same ownership gate. Page links and multipart conversion forms use HTMX 4 outer replacement of `content-container`, with native form fallback. File downloads retain ordinary browser transfers. Import, export preparation and local data lookups show named loading indicators; see [Loading and task feedback](LOADING_AND_TASK_FEEDBACK.md).

## Locations

- IFC site latitude/longitude is labelled an approximate site origin. It is not a surveyed building footprint or centroid.
- A projected placement needs one unambiguous map conversion, an explicit EPSG CRS, declared units and a precise offline PROJ transformation. Unsupported or invalid coordinates remain unavailable with an explanation.
- Imported CityJSON buildings use representative points of the selected geometry and its declared transform/CRS. These anchors are matched through exact CityObject identifiers to IFC GUIDs; repeated display names remain distinct.
- Owners may set WGS84 decimal coordinates for a particular uploaded building. This changes the marker, not the IFC geometry. Resetting the override restores the source-derived position.
- Upload UUID plus building GUID identifies a map row. The supplied A–D models repeat the same building GUID; three also repeat their site position.

The page parses at most 25 uploads per page with two workers and caches source-derived metadata by SHA256 outside public media. Overrides stay in the database and never enter shared geometry caches.

Leaflet 1.9.4 is bundled locally with its BSD-2-Clause licence. The default street tiles are OpenStreetMap, with permanent attribution, normal browser caching and an origin-only cross-site referrer policy. Tiles are requested only for the visible map. Turning off the street map leaves markers usable; tile failures show a fallback message. `BUILDING_MAP_TILE_URL` can select a self-hosted OSM-compatible tile service. Follow the [OSMF tile policy](https://operations.osmfoundation.org/policies/tiles/) when using their service.

## CityJSON import

Model Manager links to `/plugins/bim/cityjson/import/`. Upload CityJSON, optionally choose a specific LoD and source name attribute, then open the resulting IFC directly in the existing viewer. A blank LoD selects the highest level with supported geometry types. The original JSON, source/output hashes, selected level, original transform and conversion notes are retained privately in `ModelConversion`. Deleting an unused converted model removes its retained original after the database transaction commits.

The integration uses official `ifccityjson==0.8.5` with its compatible `cjio==0.9.0` dependency. The wrapper corrects upstream transform handling and IFC EXPRESS validity defects without changing the uploaded source. Unsupported selected geometry, instances, inner solid shells, extensions or classes produce actionable form errors before files are persisted. Other LoDs are pruned. Input/output, nesting and geometry limits plus a worker timeout bound native work. Appearance and construction material assignments are not transferred; assessments still need appropriate source properties and reference coefficients.

Missing precise map transformations are nonfatal: the IFC remains usable and the map lists the building without an inferred geographic location.

## CityJSON export

The viewer and Model Manager link to each owned model's export page. POST prepares the file in an isolated worker; GET only shows preparation status or downloads an already completed file. Native IfcOpenShell tessellation uses up to four threads. Cache keys include the exporter version and source SHA256; source stability is checked before publication.

IfcCityJSON supports CityJSON→IFC only, and the official IfcConvert 0.8.5 release rejects `.cityjson` output. Cadevil therefore implements a separate component exporter against the [CityJSON 1.1.3 specification](https://www.cityjson.org/specs/1.1.3/), validated offline with the bundled official CC0 schemas. It preserves building/part hierarchy, detailed component meshes, IFC GUIDs/classes and available material names. Detailed surfaces use LoD 3 and millimetre integer coordinates. This is not an envelope extraction or a watertight-solid guarantee. Any triangles collapsed at that precision, unexported represented elements and local-coordinate limitations are recorded in `cadevil` provenance in the output.

A supported explicit projected EPSG map conversion transforms exported geometry. A site reference alone, or an owner-set marker, does not relocate local IFC geometry. Textures, property sets, LCA/cost reports and procedural CAD semantics remain in the original source and assessment workflow.

## Validation

Native import tests cover LoDs, semantic faces, source transforms, nested/null attributes, repeated names, exact GUID mapping, IFC EXPRESS rules, atomic failures and bounded errors. Export tests cover metre/millimetre equivalence, WCS/rotated projected transforms, hierarchy, duplicate IDs, missing geometry, schema parity and immutable-source caching. Native Bolt tests exercise ownership, CSRF, disabled workflows, HTMX fragments, retained-source cleanup, location edits and prepared downloads. Frontend tests cover grouping, search, safe links, offline tiles and HTMX cleanup.

All four supplied `28V_new` house IFCs were read for map positions and exported with source hashes unchanged. Exports passed official schema, hierarchy/vertex references and geometry bounds within 0.5 mm of the viewer geometry, excluding opening helpers. Every represented non-opening element remained represented. Actual house sources contain site references and no surveyed map conversion, so their CityJSON geometry stays local. A fabricated two-building CityJSON fixture was converted and rendered in the browser to verify the complete import journey.

The map-to-model journey was informed by [xao-design's GitHub examples](https://github.com/xao-design); its frontend was written specifically for the existing Django/Bolt/HTMX application.
