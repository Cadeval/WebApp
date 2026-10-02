# BIM workspace

The enabled BIM plugin contributes five full-page links: model manager,
reference configurations, configuration editor, material passport and comparison.
All 17 GET/POST registrations now live in
`apps/plugins/bim_model_manager/api.py`. `config/api.py` composes these native
Bolt APIs; `runbolt` discovers that project API alongside the installed apps.
There is no BIM Django/ASGI mount or Django URL dispatch. The removed plugin
`urls.py` and `passport_urls.py` have no remaining runtime references.

Endpoint paths and both reverse namespaces (`bim` and `material_passport`) are
unchanged. `config/urls.py` keeps Bolt's reverse-only bridge for Django template
links/redirects and the existing Django admin URLconf. The reverse bridge is
not a page dispatcher. Serve the application with Bolt; ordinary Django
`runserver` does not dispatch the native page routes. The existing `make debug`
target starts `uv run python manage.py runbolt --dev` with four processes.

The page handlers retain shared forms, models, storage and assessment code.
`apps/shared/bolt_pages.py` adapts Bolt requests to standard Django form parsing,
including multipart uploads, repeated checkbox values and upload limits.
The adapter explicitly runs Django's CSRF token/cookie/origin/referer check
after multipart parsing; the early Bolt middleware check is deferred rather
than bypassing CSRF protection. Templates issue CSRF cookies through the outer
Django middleware. Synchronous transactions, templates and file operations run
via `sync_to_async`. Streamed downloads preserve headers and close their files.
UUIDs are validated before owner-scoped lookup; missing and foreign objects
return 404. Unregistered HTTP methods are rejected by Bolt (typically 404).

Upload an IFC in the model manager, then follow Calculate passport. Upload or
select a CSV/XLSX reference in the reference library. Saving edits creates and
selects a new reference upload; the original file is preserved. Calculation
preselects the active reference and chosen model. Saved reports appear in the
model manager and comparison form. Models used by reports cannot be deleted
through this page. Stale or incomplete edits do not replace the active version.
Every page retains session login and the current PluginRecord gate; ownership
is enforced for selection, listing, downloads, comparisons and deletion.

Safari revealed that the async homepage rendered ORM-dependent plugin context
processors on its event loop, silently hiding navigation. The homepage render
in `apps/mycelium/api.py` now runs in a thread; a native route regression test
checks that enabled BIM links appear and disabled links disappear.

Validation completed:

- 45 calculation and native Bolt page tests pass with an isolated database.
- A temporary real `runbolt` server passes login, all five pages, multipart
  uploads, model/reference selection, editing, two assessments, report rendering,
  JSON/CSV/IFC downloads, comparison, CSRF rejection, foreign ownership,
  disabled plugin checks and anonymous redirects over HTTP.
- Safari verified home menu navigation, model manager/report links, reference
  selection, saving an edited version, model/reference preselection, calculation
  with the edited coefficients and submitting a two-report comparison.
- Django development checks and scoped diff whitespace checks pass.

The existing plugin discovery startup database warning remains. No schema
change is required. Historical plugin views/templates, reference snapshots,
Rust work and user data are preserved. No commit or deployment was performed.

Reproduce the isolated checks:

```sh
uv run --inexact python -m django test apps.shared.test_bim_pages \
  apps.shared.ifc_extractor.test_material_assessment \
  apps.shared.test_material_passport_web \
  apps.shared.ifc_extractor.test_thesis_alignment \
  apps.shared.test_thesis_alignment_web \
  --settings=tests.passport_test_settings
uv run --inexact python tests/bolt_runtime_smoke.py
uv run --inexact python -m django check --settings=config.settings.dev
```

The runtime smoke script uses a temporary SQLite database, uploads and server
port, then stops its server and removes temporary data. The optional `--safari`
mode keeps that test server available until its stop flag is created or a
20-minute timeout expires.

Implementation references: the installed Bolt 0.11.1 source, its
[Django middleware documentation](https://bolt.farhana.li/topics/middleware/)
and [response documentation](https://bolt.farhana.li/topics/responses/).


## Shared CSS and real model validation

Official Normalize.css 8.0.1 is vendored at
`resources/static/css/vendor/normalize.css`, with its MIT license alongside it.
The base and index templates load it before application styles; standalone
passport pages also load it. No CDN request is needed.
`resources/static/css/bim.css` provides the shared BIM presentation, including
light/dark colors, visible focus states, forms, model cards, scrollable tables,
sticky configuration headings and bounded validation diagnostics. Safari visual
review covers the model manager, actual MP reference editor, passport report
and comparison form.

Run the native HTTP workflow against every A–D `*28V_new.ifc` file with:

```sh
uv run --inexact python tests/bolt_runtime_smoke.py --real-models
```

This reads the originals under `/Users/mia/Desktop/projects/cadevil-data/IFC/`
and uses `MP_indicators_and_modfications_short.csv` from the adjacent schema
folder. It writes individual upload/assessment outcomes to
`docs/BIM_REAL_MODEL_VALIDATION.json`. Invalid models retain the existing strict
validation behavior and do not produce saved reports.

The isolated server uses a temporary 256 MiB Bolt request limit for these large
files. The application currently retains Bolt's default 1 MiB transport limit,
which rejects their uploads before the model form can enforce its 500 MiB
limit. Automatic approval review rejected a persistent global increase because
it would broaden large-request exposure across all endpoints; production
settings were not changed. A production upload-limit change needs separate
approval or a reviewed endpoint-specific approach.


## Fresh thesis and 3D viewer verification — 1 October 2026

The model manager now links each owned upload to a native Bolt viewer page
and geometry endpoint. IFC tessellation emits a cached GLB without editing the
source. Cache identity includes the source hash and export version; page and
geometry requests retain login, ownership and plugin gating. Invalid or empty
geometry returns 422. Visual inspection is independent of the strict assessment
schema gate and does not certify the model as suitable for calculation.

Safari rendered House A and verified element selection, IFC class/GlobalId,
dimensions in metres, corrected indexed triangle counts, clearing selection,
grid control and orbit. All four supplied viewer exports returned HTTP 200,
nonempty GLBs and IFC identities with unchanged source hashes; see
`BIM_VIEWER_VALIDATION.json` for exact filenames, hashes and geometry counts.
The browser's reported part count and exported GLB mesh count measure different
objects and should not be treated as interchangeable.

A fresh actual-workbook upload and saved-report workflow imported all 115 records
and produced 8,250 kg and 729.5 gross material cost for a controlled IFC. The
50-year inclusive boundary, mass-weighted recycling and installed-mass LCA
normalization were saved explicitly. See `THESIS_FRESH_VALIDATION.json` for
source hashes and independent arithmetic.

The audit fixed a false-completeness edge case: unreadable geometry with no
positive exported material volume now invalidates full totals. Its regression
uses valid IFC and simulates a geometry-read failure, retaining schema checking.
Viewer fixes also retain IFC names/identities and count triangles from indices.
Final checks: 48 relevant Python tests and 7 viewer JavaScript tests passed;
the isolated real Bolt HTTP smoke passed including geometry, page workflows,
CSRF, ownership and plugin disabling. Temporary runtime data was removed.

Limits remain: missing workbook coefficients/service lives, schema-invalid
house assessment inputs, provisional bounding-box overlap diagnostics and
manually reviewed storey semantics. Rendering all four exports does not prove
complete A–D assessment results, a complete LCA or energy certification.
