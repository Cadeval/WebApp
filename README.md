# Cadevil

Cadevil is an open-source building assessment platform for architects and engineers, developed from a master's thesis project. It connects IFC geometry with material quantities, environmental impacts, material passports and recovery costs in EUR. Users choose signed workflow plugins for their own workspace.

The landing page `/` explains the platform and links to the public `/demo`, which presents recorded results and provisional estimates for the A–D house geometry. Django sessions and Django-Bolt serve full pages, HTMX fragments, background job status and the admin log WebSocket.

## Local setup

Use Python 3.13 or newer, [uv](https://docs.astral.sh/uv/), Node.js 22 or newer, and Rust/Cargo for native plugin tests. Run commands from the repository root:

```sh
make install
make migrate
make superuser
make debug
```

Open http://127.0.0.1:8000/. SQLite is the default; persistent data is in `data/`. `make debug` supervises the web server and enabled development MCP plugins. The MCP runtimes are installed separately from application dependencies; see [Development MCP tools](docs/DEVELOPMENT_MCP.md). The lockfile currently uses Django 5.2 and Django-Bolt 0.11.1.

Settings read environment variables directly. [.env.example](.env.example) documents them; copying it to `.env` does not load it automatically. `make run` selects production settings and requires a strong `SECRET_KEY`, explicit hosts and trusted HTTPS origins. Development MCP processes and endpoints are disabled in production. SQLite and the local cache remain the development defaults; locked Psycopg and Redis clients support production service backends. [Docker Compose](docs/COMPOSE.md) runs the frontend on external `swagnet`, with the application, PostgreSQL and Redis on a separate private network. Redis caches sessions while PostgreSQL retains their durable records. Uploads have no public media mount: keep `MEDIA_URL="/"` and do not configure a reverse-proxy alias exposing `MEDIA_ROOT`; use the owner-checked download endpoints. Gitolite is the primary remote; the GitHub workflow is prepared for future use.

## Production container

For a production container, see [Docker packaging](docs/DOCKER.md). The image
uses a reviewed file allowlist and runtime secrets, with persistent data stored
separately. Development MCP tools are excluded from the image.

## Building workflows

Sign in and add **BIM Workspace** to your workflow on `/plugins/manage/`. Its tabs group Models, Map, References, Reference editor, Material passport and Comparison. Upload IFC or CityJSON data, start assessments, inspect geometry and select grouped validation warnings to focus their IFC elements. Validation warnings remain nonfatal; material/LCA estimates identify their assumptions. CityJSON import uses ifccityjson; IFC export uses the separate documented converter.

The map provides Austria/Vienna location lookup, utility pricing and planning context with provider dates and limitations. Live provider failures remain visible. Building thumbnails, job progress and result exports reuse the same protected workflow and owner checks.

Independent OpenStudio/EnergyPlus helpers remain available under `apps/plugins/bim_model_manager/ifc_extractor`. They use documented residential assumptions and require a separately installed OpenStudio CLI and weather data. The current browser workflow does not provide an EPW upload or energy simulation action.

## Plugins and signing

`/plugins/manage/` combines the site catalog and each user's enabled workflows. Only selected, approved, compatible plugins appear in personal navigation. Administrators review packages and control site availability; development MCP tools are admin debug services rather than personal workflows. Disabling a tool preserves the user's selection and data.

Each catalog entry links to its SBOM view and JSON download. The view identifies
the inventory's origin and coverage, including signed publisher evidence or a
file-only inventory when dependency evidence is unavailable.

Generate and register an Ed25519 signing key in **User settings → Security**. Private keys are created in the browser. Download the signing CLI, sign your package locally, and upload a ZIP, TAR, tar.gz or tar.xz containing `plugin.json`, its declared assets and signature. Standalone JS/WASM uploads are unsupported. Approved JavaScript runs in a restricted module worker; WASM uses no host imports. Trusted installed Python plugins use the `cadevil.plugins` entry-point contract. See [external repository design](docs/external-plugin-repositories.md) for the proposed admin-managed remote catalog.

The bundled IFC editor changes local IFC STEP properties and downloads a new file; upload that file again to generate geometry. The bundled Snake plugin is also a dedicated Rust worker. After editing Rust source, rebuild the checked-in artifacts:

```sh
rustup target add wasm32-unknown-unknown
make rebuild
```

The IFC/BIM workspace keeps its templates in `apps/plugins/bim_model_manager/templates/` and its browser assets and demo recordings in `apps/plugins/bim_model_manager/static/`. The IFC editor owns the corresponding `templates/` and `static/` directories under `apps/plugins/example_plugin/`.

Each declares its resource ownership in `resources.json`. A plugin-manager hook registers the configured Django application: BIM Workspace owns its models and migrations, while the IFC editor uses a resource-only application. Django discovers their templates, form widgets and static files automatically. The adapter also registers the static directories with Bolt's native server. Resource registration is independent of personal workflow activation; the existing route and ownership checks determine access. See [plugin resource registration](docs/PLUGIN_RESOURCES.md) for the declaration format, Django hook and loader behavior.

## Verification

```sh
make test
```

This runs native Bolt/Django integration tests, Node browser/worker tests, and both dependency-free Rust crates. Test discovery is limited to `apps` and `tests`; historical sources in ignored `reference/` are not the running application. `npm ci --ignore-scripts` uses the pinned Three.js test dependency. `npm test` temporarily merges the registered static roots, preserving the browser's relative imports and fixture paths. IFC editor WebAssembly builds write to `apps/plugins/example_plugin/static/wasm/`; Snake builds write to `resources/static/wasm/`.

See [the code audit](docs/CODE_AUDIT.md) for deletion evidence, security fixes, scan scope and remaining limitations. The local audit MCP provides repeatable read-only Semgrep and Ruff checks during `make debug`.

## Security, accessibility and operations

The [security policy](SECURITY.md) is also available on `/security`; public
disclosure metadata is served as UTF-8 text at `/.well-known/security.txt`.
Configure the deployment contact, HTTPS canonical/policy URLs and expiry using
the documented `SECURITY_TXT_*` environment variables. Review the expiry before
4 January 2027 rather than automatically renewing stale contact details.

Download the release CycloneDX inventory at `/security/sbom.json`, or regenerate
it with `make sbom`; [SBOM documentation](docs/SBOM.md) distinguishes application
components, development tooling and scan coverage. The `/accessibility` page
documents supported keyboard navigation and remaining limitations.

[Logging documentation](docs/LOGGING.md) explains request correlation IDs,
redaction, output settings and the permission-gated admin log popover. Use the
response's `X-Request-ID` to relate a reported failure to its server events.

## Structure

- `manage.py`, `config/`: settings and native route composition.
- `apps/shared/`: shared identity models and fresh authentication migrations, plus generic page, access and logging services.
- `apps/mycelium/`: landing page, demo, sessions and user/admin settings.
- `apps/plugin_manager/`: discovery, user selections, signed packages, resource registration and debug supervisors.
- `apps/plugins/`: plugin implementations, trusted MCP manifests and the Rust browser plugins.
- `apps/plugins/bim_model_manager/`: IFC/BIM routes, assessment/geometry/CityJSON/location services, `ifc_extractor/`, domain models and upload helpers under `django/`, plus `templates/`, `static/` and its resource declaration.
- `apps/plugins/example_plugin/`: IFC editor Rust source, `templates/`, `static/`, and its resource declaration.
- `apps/plugins/development_mcp/`: debug-only native tools and UI/UX/code-audit MCP bridges.
- `resources/`: shared application templates and static assets, including the page shell and landing page.
- `tests/`: Bolt transport helpers and integration settings.

BIM Workspace owns the `bim_model_manager` Django app label, its `bim_model_manager_*` tables and its initial migrations under `apps/plugins/bim_model_manager/django/migrations/`. Shared authentication remains separate; the old BIM re-exports, callable wrappers and migration histories are removed. Version 0.15.0 requires an empty application database, rather than an in-place upgrade from 0.14. Back up existing state, initialize the new schema and recreate login access before importing the models or packages you want to use. Runtime uploads, databases and generated caches are excluded from source cleanup. The project uses the [MIT license](LICENSE).
