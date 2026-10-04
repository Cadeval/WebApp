# Cadevil

Cadevil is an open-source building assessment platform for architects and engineers, developed from a master's thesis project. It connects IFC geometry with material quantities, environmental impacts, material passports and recovery costs in EUR. Users choose signed workflow plugins for their own workspace.

The unauthenticated landing page `/` starts with a link to the public `/demo`, followed by an expanded overview of the platform, Mycelium and its installed tools. Plugins own their overview templates and register them through the plugin manager. These public descriptions come from installed, production-compatible source plugins; catalog approval and personal workflow selection still determine which tools a user can open. Signed-in users see their personal workflow instead of the public overview. The demo presents recorded results and provisional estimates for the A–D house geometry. Django sessions and Django-Bolt serve full pages, HTMX fragments, background job status and the admin log WebSocket.

Release 0.16.0 ships BIM Workspace 2.0.3, IFC Editor 2.0.4 and Snake 2.0.1. Mycelium ships with the application and has no separate distribution version.

## Local setup

Use Python 3.13 or newer, [uv](https://docs.astral.sh/uv/), Node.js 22 or newer, and Rust installed through rustup. The wheel build pins Rust 1.98.1 and its `wasm32-unknown-unknown` target. Prepare that toolchain, then run commands from the repository root:

```sh
rustup toolchain install 1.98.1 --profile minimal
rustup target add --toolchain 1.98.1 wasm32-unknown-unknown
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

## Build the application package

```sh
make build
# Equivalent package frontend:
uv build
```

[uv builds](https://docs.astral.sh/uv/concepts/projects/build/) the wheel and source distribution through the pinned Hatchling backend. A [custom Hatch build hook](https://hatch.pypa.io/latest/plugins/build-hook/custom/) compiles the two bundled Rust workers when building the wheel, using the pinned [rustup toolchain](https://rust-lang.github.io/rustup/overrides.html) and [WASM target](https://rust-lang.github.io/rustup/cross-compilation.html). The source distribution carries the reviewed runtime and build inputs without compiling Rust. Builds use temporary outputs and explicit file manifests; they do not replace the checked-in browser binaries. The wheel's derived inventory records the hashes of its newly built WASM files. See [Docker packaging](docs/DOCKER.md) for how the image consumes the reviewed wheel payload.

Checks run automatically within that lifecycle: the hook validates source inputs, Docker exclusions and compiler copies, SBOM evidence and plugin isolation before building, then validates the completed archive and verifies that source inputs stayed unchanged. Failed artifacts are removed. Docker obtains its validated runtime payload directly from the build hook.

`make install` also activates the [Git checkout hook](docs/CHECKOUT_HOOKS.md). Subsequent branch and file checkouts run the same source validator using the existing Python environment. Existing custom hooks are preserved; the documentation explains how to chain the checker when needed.

## Building workflows

Sign in and add **BIM Workspace** to your workflow on `/plugins/manage/`. Its tabs group Models, Map, References, Reference editor, Material passport and Comparison. Upload IFC or CityJSON data, start assessments, inspect geometry and select grouped validation warnings to focus their IFC elements. Validation warnings remain nonfatal; material/LCA estimates identify their assumptions. CityJSON import uses ifccityjson; IFC export uses the separate documented converter.

The map provides Austria/Vienna location lookup, utility pricing and planning context with provider dates and limitations. Live provider failures remain visible. Building thumbnails, job progress and result exports reuse the same protected workflow and owner checks.

Independent OpenStudio/EnergyPlus helpers remain available under `plugins/bim_model_manager/ifc_extractor`. They use documented residential assumptions and require a separately installed OpenStudio CLI and weather data. The current browser workflow does not provide an EPW upload or energy simulation action.

## Mycelium plugin framework

Mycelium connects Cadevil's workflow tools through plugin manifests, registration hooks and a shared catalog. Administrators control site availability; users choose which approved, compatible plugins appear in their personal workspace. It currently ships with Cadevil, with its implementation in `plugin_manager/` and its host pages in `mycelium/`.

Trusted Python plugins are installed on the server and discovered through configured bundled manifests or `cadevil.plugins` entry points. They can own Django models, migrations, templates and assets, including registered descriptions for the guest landing page. Uploaded packages contain signed JavaScript/WASM browser workers; users sign their archives locally before submitting them for administrator review. Browser archives do not contribute server-rendered landing templates.

Resource declarations use a framework-independent registry. A separate Django adapter connects installed plugins to Django's application loading mechanism. Broader framework independence remains a future goal: the current catalog and workflow implementation still use Django. See [plugin resource registration](docs/PLUGIN_RESOURCES.md) for the registration contract and adapter.

Development MCP plugins run through `make debug` as administrator development tools and are excluded from production. Plugin manifests declare whether they support development, production or both environments. The plugin API currently uses version 1.0; Mycelium does not have a separate release version.

## Plugins and signing

`/plugins/manage/` combines the site catalog and each user's enabled workflows. Only selected, approved, compatible plugins appear in personal navigation. Administrators review packages and control site availability; development MCP tools are admin debug services rather than personal workflows. Disabling a tool preserves the user's selection and data.

Each catalog entry links to its SBOM view and JSON download. The view identifies
the inventory's origin and coverage, including signed publisher evidence or a
file-only inventory when dependency evidence is unavailable.

Generate and register an Ed25519 signing key in **User settings → Security**. The browser encrypts the private key with your passphrase and downloads it; only the public key reaches the server. The server issues its X.509 code-signing certificate. Download the signing CLI, sign your package locally, and upload a ZIP, TAR, tar.gz or tar.xz containing `plugin.json`, its declared assets and signature. The CLI asks for the passphrase locally. Keep the encrypted key file and passphrase: the server cannot recover them. Standalone JS/WASM uploads are unsupported.

Teams have member and manager roles, scoped management permissions and public signing keys available from **User settings → Teams**. Members can publish packages with a team key's valid certificate; the encrypted private key stays with its designated signer. Removing a member or archiving a team revokes affected keys and disables their packages. Security settings support certificate downloads, renewal and key revocation. Renewed certificate JSON can be passed to the CLI with `--certificate` while retaining the existing encrypted private key.

Approved JavaScript runs in a restricted module worker; WASM uses no host imports. Before starting a worker, the browser verifies its signed bytes, certificate chain, publisher scope and fresh signed revocation evidence. Bundled browser workers use the server's code-signing certificate too. Operators initialize the private CA explicitly and set `PLUGIN_CA_PUBLIC_URL` to the deployment's HTTPS origin before issuing certificates; see [deployment and CA setup](docs/COMPOSE.md). Trusted installed Python plugins use the `cadevil.plugins` entry-point contract. See [external repository design](docs/external-plugin-repositories.md) for the proposed admin-managed remote catalog.

The bundled IFC editor changes local IFC STEP properties and downloads a new file; upload that file again to generate geometry. The bundled Snake plugin is also a dedicated Rust worker. After editing Rust source, build a fresh wheel containing the compiled workers:

```sh
make rebuild
```

`make rebuild` runs `uv build --wheel`; it leaves the checked-in browser artifacts unchanged.

The IFC/BIM workspace keeps its templates in `plugins/bim_model_manager/templates/` and its browser assets and demo recordings in `plugins/bim_model_manager/static/`. The IFC editor owns the corresponding `templates/` and `static/` directories under `plugins/example_plugin/`.

Each declares its resource ownership in `resources.json`. A plugin-manager hook registers the configured Django application: BIM Workspace owns its models and migrations, while the IFC editor uses a resource-only application. Django discovers their templates, form widgets and static files automatically. The adapter also registers the static directories with Bolt's native server. Resource registration is independent of personal workflow activation; the existing route and ownership checks determine access. See [plugin resource registration](docs/PLUGIN_RESOURCES.md) for the declaration format, Django hook and loader behavior.

## Verification

```sh
make test
```

This runs native Bolt/Django integration tests, Node browser/worker tests, and both dependency-free Rust crates. All project tests live under `tests/`: Python suites use the corresponding package subdirectories, browser suites use `tests/browser/`, and private Rust unit suites use `tests/rust/`. Django discovers `tests` only; historical sources in ignored `reference/` are not the running application. `npm ci --ignore-scripts` uses the pinned Three.js test dependency. `npm test` temporarily merges the registered static roots and stages the browser tests beside those assets, preserving their relative imports and fixture paths. Package builds use isolated WASM outputs instead of overwriting the checked-in browser artifacts.

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
- `shared/`: shared identity models and fresh authentication migrations, plus generic page, access and logging services.
- `mycelium/`: landing page, demo, sessions and user/admin settings.
- `plugin_manager/`: discovery, user selections, signed packages, resource registration and debug supervisors.
- `plugins/`: plugin implementations, trusted MCP manifests and the Rust browser plugins.
- `plugins/bim_model_manager/`: IFC/BIM routes, assessment/geometry/CityJSON/location services, `ifc_extractor/`, domain models and upload helpers under `django/`, plus `templates/`, `static/` and its resource declaration.
- `plugins/example_plugin/`: IFC editor Rust source, `templates/`, `static/`, and its resource declaration.
- `plugins/development_mcp/`: debug-only native tools and UI/UX, code-audit and Docker inspection MCP bridges.
- `resources/`: shared application templates and static assets, including the page shell and landing page.
- `tests/`: Python suites and integration helpers, browser tests, and Rust unit test modules.

BIM Workspace owns the `bim_model_manager` Django app label, its `bim_model_manager_*` tables and its initial migrations under `plugins/bim_model_manager/django/migrations/`. Shared authentication remains separate; the old BIM re-exports, callable wrappers and migration histories are removed. Version 0.15.0 was the breaking persistence boundary and requires an empty application database when upgrading from 0.14. Back up existing state, initialize that schema and recreate login access before importing the models or packages you want to use. Version 0.16.0 moves Python packages out of `apps/` while preserving Django app labels, table names and the existing 0.15 migration history; an existing 0.15 database and its files are retained. Runtime uploads, databases and generated caches are excluded from source cleanup. The project uses the [MIT license](LICENSE).
