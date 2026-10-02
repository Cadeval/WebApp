# Plugin manager and runtime

Staff users can open **Plugin Manager** from the shared main menu at `/plugins/manage/`. The manager supports full-page navigation and HTMX updates. Upload, enable, disable and discovery-refresh actions require an active staff session, POST and CSRF protection. Action requests preserve the manager URL, and successful toggles and discovery refreshes update the shared navigation without a full page reload. Contributions marked `full_page` explicitly opt out of inherited HTMX boosting.

The plugin manager's native Bolt API is discovered once. The restored bundled browser pages are `/plugins/ifc-editor/` and `/plugins/rust-snake/`; their routes require login and an enabled, error-free installed-package record. BIM pages also reject disabled or errored plugin records. Existing enable settings are preserved when discovery runs.

## Discovery and registration

Installed packages contribute through the `cadevil.plugins` entry-point group. Configured bundled manifests are loaded from `PLUGIN_BUILTINS`. Discovery isolates manifest and registration-hook failures, rejects duplicate IDs and validates metadata before persistence. Persisted priorities must fit a signed 32-bit integer. Navigation and editor contributions are validated even when submitted through the generic registration API, including their types, priorities, local URLs and runtime bounds.

A registration hook can contribute only under its own plugin ID. A failed hook rolls back its contributions and manifest. Registry operations are locked so readers cannot observe a half-rebuilt registry. Missing packages are marked unavailable and disabled. Installed-package contributions cannot acquire activation through a same-ID uploaded record, including after another worker refreshes discovery.

Discovery refresh updates shared package metadata. Other server processes refresh their local contribution registry when that metadata changes; enabled/error state is read from the database. **Changing installed Python code requires a server restart.** Discovery refresh does not reload already imported Python modules or dynamically rebuild Bolt route definitions.

Installed Python plugins execute with server privileges and remain trusted code. Disabling a plugin removes its active contributions and gated page access; it does not sandbox Python imports or undo their side effects.

## Uploaded browser plugins

Staff can upload UTF-8 JavaScript worker modules (`.js` or `.mjs`) or WebAssembly version 1 modules (`.wasm`). Empty, oversized, unsupported and visibly malformed inputs are rejected. Uploads start disabled. Failed database insertion removes the newly saved artifact, and concurrent duplicate IDs return a conflict. The admin form cannot enable records that have discovery errors.

Executable uploads use `PrivatePluginStorage`, outside `MEDIA_ROOT`; `PLUGIN_ARTIFACT_ROOT` can select its directory. A configuration placing that directory inside public media is rejected. Artifact reads go through `/plugins/{plugin_id}/artifact/`, which requires login and an enabled, error-free uploaded record with a supported artifact type. Responses use the appropriate content type, `nosniff`, same-origin resource policy, a restrictive CSP and private/no-store caching. Missing files return 404.

The current configuration editor renders enabled worker panels through `plugin_manager/editor_panels.html`, including when no material configuration is selected. Browser uploads run in workers and must be reviewed and trusted; workers are not a complete security sandbox.

The generic JavaScript-worker protocol is:

1. Host sends `{type: "initialize", wasmUrl: ""}`; worker replies `{type: "ready"}`.
2. Host sends `{type: "run", value: number}`; worker replies `{type: "result", value: finiteNumber}` or `{type: "error", message: string}`.

Uploaded WebAssembly must expose `calculate(number) -> number`; the legacy `double(number)` export is also supported. The host never invokes an arbitrary first exported function. WebAssembly loading rejects external origins and redirects, checks size before instantiation and validates host messages and finite results. The default upload and generic WASM size limit is 2 MiB.

## Worker lifecycle

Initialization and calculations have bounded watchdogs. Failed workers terminate and release timers; stale messages from replaced workers are ignored. Runs are accepted only after readiness and cannot overlap. Reload replaces the worker, and disposal removes its button listeners. A malformed panel cannot prevent other panels from mounting. HTMX cleanup disposes only affected panels. Browser back/forward-cache restoration remounts disposed panels. Browser timer calls use the correct global receiver.

## Verification, 2 October 2026

- **72 Python tests pass** against the installed project: plugin-manager discovery, uploads, access/CSRF/lifecycle checks, BIM pages, material-passport web integration and live logs. Tests use isolated databases and temporary artifact directories.
- **25 JavaScript tests pass** for the generic runtime, WebAssembly protocol and Rust Snake runtime, including startup timeout, stale events, scoped cleanup and back/forward-cache restoration.
- A disposable **four-process Bolt server** passed HTTP checks for JavaScript/WASM uploads, disabled and anonymous artifact access, private-media exclusion, disabled bundled pages, CSRF/origin rejection, navigation refresh and regular/HTMX discovery refresh.
- Browser checks showed both uploaded JavaScript and WASM examples calculate **21 → 42** and retain correct behavior after worker reload, without console errors. Restored IFC Editor and Snake pages loaded; the IFC Rust worker reached ready state. The separate IFC file-editing workflow is not part of this verification claim.
- All 26 installed code/template payload files matched the tested versions. `makemigrations plugin_manager --check --dry-run` reported no changes.
- `plugin_manager.0003_private_plugin_artifacts` was applied to the backed-up local development database. All three existing plugins retained their source and enabled state. There were no existing uploaded records or legacy public plugin-artifact directory to relocate locally.

The Python run still emits the existing Django startup database-query warning and an IFCOpenShell finalizer warning; neither fails the tests. File and database backups, the install manifest, migration result and browser screenshot are retained under `/Users/mia/Documents/ChatGPT/CadEval/plugin_hardening_stage/`.

For another deployment with existing uploads, relocate its recorded artifact files from public media into the private storage directory, retaining their relative names, and remove the public copies before serving them under the new storage configuration.
