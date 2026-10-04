# Audit software bill of materials

The repository contains two reproducible [CycloneDX 1.6 JSON](https://github.com/CycloneDX/specification/blob/1.6/schema/bom-1.6.schema.json) inventories:

- [`sbom/cadevil.cdx.json`](../sbom/cadevil.cdx.json): locked Python application dependencies, browser packages, vendored assets, and the two bundled Rust/WASM plugins.
- [`sbom/cadevil-development.cdx.json`](../sbom/cadevil-development.cdx.json): Python development groups and their dependency closure, plus separately captured local Git MCP, Context7, UI/UX Suite, Semgrep/Ruff, dependency-audit, and SBOM tool environments.

These are dependency inventories for auditing. They do not assert that packages are vulnerability-free, prove a build is reproducible, or attest that CDN responses match local archives. There is no vulnerability scan or hosted source upload in generation.

## Regenerate and verify

From the repository root, with uv and Python 3.14 available:

```sh
make sbom-setup
make sbom
make sbom-check
```

Setup downloads only the tools listed in [`sbom/tools-requirements.lock`](../sbom/tools-requirements.lock), with exact versions and required artifact hashes, into `sbom/.venv`. It does not change the application environment, `pyproject.toml`, or `uv.lock`. The tool environment is ignored by the existing `.venv/` Git ignore rule. Set `SBOM_TOOL_DIR=/path/to/sbom-tools` on each command to use another isolated tool directory.

Generation is offline. The pinned [uv 0.12.22 lock exporter](https://docs.astral.sh/uv/concepts/projects/export/) supplies Python components, archive URLs/hashes, and dependencies. `scripts/generate_sbom.py` normalizes the export and merges npm locks, Cargo locks and declared local/CDN assets. [CycloneDX Python Library 11.12.0](https://github.com/CycloneDX/cyclonedx-python-lib/tree/v11.12.0), with jsonschema 4.26.0, validates both documents against its bundled strict 1.6 schemas without retrieving remote references.

`make sbom-check` performs two independent regenerations and compares their bytes with the tracked files. It also verifies Python lock closure including requested extras and development groups, npm dependency resolution, tool snapshot versions against their pinned locks, asset version declarations, unique component references, explicit dependency entries, and absence of dangling graph edges. Changed inputs make the check fail until the BOMs are regenerated. The application name/version comes from `pyproject.toml`; do not hand-edit a generated BOM.

The generator removes uv's random serial number and generation timestamp. Sorted output and stable package URLs make unchanged inputs produce the same bytes. Metadata records hashes of the input locks, generator, public dependency snapshots, licenses and bundled assets. It never includes environment directory paths, hostnames, user/model data, secrets or database contents. Local paths in the BOM are repository-relative public asset/evidence names.

## Scope and evidence

| Source | What is inventoried | Evidence and boundaries |
| --- | --- | --- |
| `uv.lock` | All application dependencies selected by `uv export --no-dev`; the second BOM also includes all dependency groups | Exact package versions, package URLs, dependency graph, and every locked sdist/wheel URL and hash. This is the union of platform variants, rather than a record of one deployment's installed wheels. Conditional dependencies remain in component properties. `pytest` is a runtime dependency because the application invokes IfcOpenShell EXPRESS validation. |
| `package-lock.json` | Three.js 0.184.0 | Three is declared as a Node test devDependency and is also used by the browser's CDN import map, so it belongs in the runtime inventory. The npm archive SHA-512 is retained; it does not verify CDN response bytes. |
| `resources/static/js/htmx.js` | htmx.org 4.0.0 | Version declaration, tracked bytes, adjacent `htmx.version.txt` provenance and `htmx.LICENSE` (0BSD). |
| `resources/static/js/plotly-2.35.3.min.js` | Plotly.js 2.35.3 | Version and MIT declaration in the file header, plus SHA-256 of the tracked bundle. The separate locked Python Plotly 7.0.0 package is also recorded. Internal bundled JavaScript packages are not independently enumerated. |
| `resources/static/vendor/leaflet/` | Leaflet 1.9.4, CSS and images | Version, retained BSD-2-Clause license and provenance, and SHA-256 of each tracked file. |
| `resources/static/js/svg-pan-zoom.min.js` | svg-pan-zoom 3.6.2 | Header version and local SHA-256. No local license text is retained; license remains explicitly unverified, rather than assigning an inferred SPDX identifier. |
| `resources/static/css/vendor/normalize.css` | normalize.css 8.0.1 | Header version, retained MIT license and both files' SHA-256. |
| `resources/templates/base.jinja2` | Three.js and Font Awesome CDN declarations | Exact declared versions/URLs are checked during generation. Three's addons share its package. Font Awesome 4.7.0 CSS and webfonts have the upstream [MIT and OFL-1.1 licenses](https://fontawesome.com/v4/license/); its documentation is excluded. CDN bytes are not downloaded or hashed. |
| Bundled plugins' `Cargo.toml` and `Cargo.lock` | IFC example crate 0.1.1 and Snake crate 2.0.0 | Each lock currently has only its first-party root crate; no registry dependencies. Source is covered by repository MIT `LICENSE`. Tracked WASM files have separate SHA-256 file components. Source-to-binary equivalence, Rust standard library contents, compiler and linker are not attested. |
| `sbom/inputs/` | Reviewed snapshots of optional development tooling | npm locks retain package archive integrity and dependency graphs. Python tool snapshots record exact installed versions, declared `Requires-Dist` relations and distribution metadata hashes for the macOS arm64 CPython 3.14 environments. These are distinct from the application's universal Python lock. Optional/conditional relations are recorded conservatively when the target is present; inactive extras can appear in that graph. |
| Semgrep rules provenance | Locally retained rule archive | Exact official repository commit, source archive URL/SHA-256 and declared Semgrep Rules License. Rule data is included only in the development inventory. |

Package licenses in `sbom/inputs/python-license-evidence.json` were captured from public distribution `METADATA` with matching names and versions. `License-Expression` is kept as an expression; legacy license names/classifiers remain names. The metadata hash and field used are recorded. Missing declarations or unmatched versions are marked as unverified. A package-level declaration does not independently identify all licenses of native libraries embedded in a wheel.

Both BOMs declare incomplete composition. They exclude operating-system packages, system Python/Node/Rust runtimes, system native libraries, nested libraries embedded in wheels/bundles, external services, administrator-uploaded plugins, uploaded buildings/models, and deployment-specific configuration. Before auditing a deployed image, generate an additional inventory of that image and of any separately installed plugins.

## Refresh optional tool evidence

Ordinary regeneration uses the tracked, reviewed snapshots. After changing the isolated local MCP tools, refresh them explicitly:

```sh
make sbom-capture-tools CADEVIL_MCP_TOOL_ROOT=/path/to/mcp_tools
make sbom-check
```

The selected directory must contain `context7/package-lock.json`, `ui-ux-suite/package-lock.json`, `python/mcp-server-git`, `code-audit/.venv`, and `dependency-audit/.venv`, plus the code/dependency-audit locks and code-audit provenance. The SBOM tool snapshot comes from the isolated environment executing the generator. The application environment defaults to `.venv`; the script also accepts `--application-env` for a different location. Capture reads only dependency locks and installed distribution metadata. It copies no application code, settings, executable binaries or model data into the evidence snapshots.

Review the input and BOM diffs before committing refreshed evidence. Keep `tools-requirements.in` and its hash-locked output together when upgrading the generator/validator. Generator and validator version constants are intentional compatibility checks and must match the reviewed tool upgrade.

## Plugin Manager inventories

Each visible, environment-compatible plugin has a **View SBOM** link in `/plugins/manage/`. The detail page uses the existing HTMX content boundary and offers a private JSON download at `/plugins/<plugin_id>/sbom.json`. Anonymous visitors are redirected to login. Access follows the same catalog visibility as Plugins: regular users can inspect available tools and their saved workflow selections; administrators can inspect pending packages. Debug-only inventories are unavailable in production.

The two bundled Rust tools project their Cargo dependency closure and compiled WASM component from the audited application BOM. The viewer verifies the deployed WASM hash before displaying it. BIM Workspace shows the shared application runtime with its scope stated explicitly, rather than claiming those dependencies belong exclusively to BIM. Development MCP plugins show only their captured tool-environment closure. An installed Python plugin without inventory evidence shows an unavailable state; no dependencies are inferred from its name.

A browser package may include a fixed root **`sbom.cdx.json`** containing CycloneDX 1.6 JSON. Add it before running the local signing CLI; the same Ed25519 signature binds its SHA-256 and every other package file. The upload reader bounds the SBOM to 256 KiB, 1,000 components, 1,000 dependency records, 5,000 graph edges, 12 nested component levels and bounded display text. It rejects duplicate JSON fields/component references and dangling graph edges. This is a reader for the supported component/dependency subset, not a full CycloneDX schema validator or an independent dependency audit. It never resolves URLs, external schemas or executable content.

The view rechecks the stored archive SHA-256, the registered signing key and signature, and the member SHA-256. Uploaded SBOM descriptions and licenses remain publisher declarations. With no publisher SBOM, it shows a clearly marked **signed file inventory only**, with an incomplete-composition marker and no inferred third-party dependencies or licenses. Unverifiable/revoked archives have no inventory download. Responses are private and not cached; component text is escaped, and download filenames are fixed. The host does not add server storage paths, account IDs, key material or registry-owner details. Publisher fields are retained as supplied, and downloads preserve the exact signed SBOM bytes. Publishers are responsible for the information they include.
