# Cadevil

Cadevil assesses IFC buildings, presents material passports and recovery costs in EUR, and supports signed browser workflow plugins. Django sessions and Django-Bolt serve the full pages, HTMX fragments, background job status and admin log WebSocket. The public `/demo` presents recorded results and provisional estimates for the A–D house geometry.

## Local setup

Use Python 3.13 or newer, [uv](https://docs.astral.sh/uv/), Node.js 22 or newer, and Rust/Cargo for native plugin tests. Run commands from the repository root:

```sh
make install
make migrate
make superuser
make debug
```

Open http://127.0.0.1:8000/. SQLite is the default; persistent data is in `data/`. `make debug` supervises the web server and enabled development MCP plugins. The MCP runtimes are installed separately from application dependencies; see [Development MCP tools](docs/DEVELOPMENT_MCP.md). The lockfile currently uses Django 5.2 and Django-Bolt 0.11.1.

Settings read environment variables directly. [.env.example](.env.example) documents them; copying it to `.env` does not load it automatically. `make run` selects production settings and requires a strong `SECRET_KEY`, explicit hosts and trusted HTTPS origins. Development MCP processes and endpoints are disabled in production. The locked install uses SQLite and the local cache. PostgreSQL requires a separately installed Psycopg driver, and Redis requires the `redis` Python package; provision these before selecting those backends. Use a shared cache when deploying multiple workers. Uploads have no public media mount: keep `MEDIA_URL="/"` and do not configure a reverse-proxy alias exposing `MEDIA_ROOT`; use the owner-checked download endpoints. Gitolite is the primary remote; the GitHub workflow is prepared for future use.

## Building workflows

Sign in and add **BIM Workspace** to your workflow on `/plugins/manage/`. Its tabs group Models, Map, References, Reference editor, Material passport and Comparison. Upload IFC or CityJSON data, start assessments, inspect geometry and select grouped validation warnings to focus their IFC elements. Validation warnings remain nonfatal; material/LCA estimates identify their assumptions. CityJSON import uses ifccityjson; IFC export uses the separate documented converter.

The map provides Austria/Vienna location lookup, utility pricing and planning context with provider dates and limitations. Live provider failures remain visible. Building thumbnails, job progress and result exports reuse the same protected workflow and owner checks.

Independent OpenStudio/EnergyPlus helpers remain available under `apps/shared/ifc_extractor`. They use documented residential assumptions and require a separately installed OpenStudio CLI and weather data. The current browser workflow does not provide an EPW upload or energy simulation action.

## Plugins and signing

`/plugins/manage/` combines the site catalog and each user's enabled workflows. Only selected, approved, compatible plugins appear in personal navigation. Administrators review packages and control site availability; development MCP tools are admin debug services rather than personal workflows. Disabling a tool preserves the user's selection and data.

Generate and register an Ed25519 signing key in **User settings → Security**. Private keys are created in the browser. Download the signing CLI, sign your package locally, and upload a ZIP, TAR, tar.gz or tar.xz containing `plugin.json`, its declared assets and signature. Standalone JS/WASM uploads are unsupported. Approved JavaScript runs in a restricted module worker; WASM uses no host imports. Trusted installed Python plugins use the `cadevil.plugins` entry-point contract. See [external repository design](docs/external-plugin-repositories.md) for the proposed admin-managed remote catalog.

The bundled IFC editor changes local IFC STEP properties and downloads a new file; upload that file again to generate geometry. The bundled Snake plugin is also a dedicated Rust worker. After editing Rust source, rebuild the checked-in artifacts:

```sh
rustup target add wasm32-unknown-unknown
make rebuild
```

## Verification

```sh
make test
```

This runs native Bolt/Django integration tests, Node browser/worker tests, and both dependency-free Rust crates. Test discovery is limited to `apps` and `tests`; historical sources in ignored `reference/` are not the running application. `npm ci --ignore-scripts` uses the pinned Three.js test dependency. WebAssembly builds write to `resources/static/wasm/`.

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
- `apps/shared/`: persistent models, migrations, assessments, exchanges and browser services.
- `apps/mycelium/`: landing page, demo, sessions and user/admin settings.
- `apps/plugin_manager/`: discovery, user selections, signed packages and debug supervisors.
- `apps/plugins/`: BIM routes, trusted MCP manifests and the Rust browser plugins.
- `resources/`: active Django templates and static browser assets.
- `tests/`: Bolt transport helpers and integration settings.

Database migrations and historical model callables are retained even when their original views have been retired. Runtime uploads, databases and generated caches are excluded from source cleanup. The project uses the [MIT license](LICENSE).
