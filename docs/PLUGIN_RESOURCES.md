# Plugin resources and application loading

BIM Workspace and the IFC editor own their templates and public browser assets. The plugin manager discovers resource declarations through a registry without Django imports, then connects them to Django through a separate adapter. BIM Workspace also owns its persistence as a real Django application. Resource ownership stays independent of the web framework, so another framework can implement its own adapter without changing the declaration format.

## Resource ownership

The bundled source packages live at the repository root and use this layout:

```text
plugins/bim_model_manager/
    ifc_extractor/...
    assessment_web.py
    assessment_presentation.py
    ifc_viewer.py
    cityjson_import.py
    cityjson_export.py
    location_lookup.py
    django/apps.py
    django/models.py
    django/uploads.py
    django/migrations/...
    resources.json
    resources/__init__.py
    templates/bim/...
    templates/shared/...
    templates/bim_model_manager/overview.html
    static/css/...
    static/js/...
    static/bim-demo/...
plugins/example_plugin/
    resources.json
    resources/__init__.py
    templates/example_plugin/ifc_editor.jinja2
    static/css/ifc_editor.css
    static/js/ifc_editor_controller.js
    static/js/plugins/example_plugin_worker.js
    static/wasm/example_plugin.wasm
```

Generic page shells, authentication, plugin administration and shared browser helpers remain in the host's `resources/` and `shared/templates/` directories. Existing relative template names and `/static/...` URLs remain the same. For example, `bim/models.html` is still a template name and `/static/js/3d_view.js` remains the viewer URL; their files live with BIM Workspace.

Each source package declares one resource owner. BIM Workspace's `resources.json` is:

```json
{
  "version": 1,
  "plugin_id": "cadevil.bim.model_manager",
  "package": "plugins.bim_model_manager",
  "overview": {
    "template": "bim_model_manager/overview.html",
    "compatibility": "both"
  }
}
```

The editor uses `cadevil.example.editor` and `plugins.example_plugin`. The descriptor version describes the resource format; the plugin's manifest carries its release version.

`plugin_manager/resource_registry.py` defines `ResourceBundle`, `OverviewTemplate` and `ResourceRegistry`. A bundle contains the owner ID, Python package name, resource root and optional overview declaration. `from_builtins()` reads only declarations in explicitly configured bundled source packages. It validates metadata, rejects duplicate owners/packages and escaping or symlinked resource paths, and requires the `resources/__init__.py` namespace. The registry reads metadata without importing Django, plugin factories, model modules or hooks. Uploaded packages and runtime storage are outside this discovery process.

The optional `overview` declares an owned HTML/Jinja template and its environment compatibility (`debug`, `production` or `both`, default `both`). Its namespace follows the source package below `plugins`: BIM Workspace uses `bim_model_manager/`, the editor uses `example_plugin/`, and Snake uses `rust_example_plugin/`. Template paths must stay inside that namespace and the owner's `templates/` directory. Validation rejects links, traversal, absolute paths and oversized files.

## Django registry hook

`plugin_manager/django_resources.py` is the framework adapter. The final development and production settings append its application configurations to `INSTALLED_APPS`. Production first filters out development MCP plugins. Container settings inherit this production registration; applications are registered once through Django's normal startup.

A trusted plugin's `resources/__init__.py` can declare `DJANGO_APP_CONFIG` as the dotted path to its Django application configuration. BIM Workspace points to `plugins.bim_model_manager.django.apps.BIMConfig`. This configuration uses:

- Application name: `plugins.bim_model_manager.django`.
- App label: `bim_model_manager`.
- Application path: the BIM plugin root, for its `templates/` and `static/` directories.
- Migration namespace: `plugins.bim_model_manager.django.migrations`.

The plugin manager's `PluginAppConfig` provides the common resource-loading bridge. Django loads BIM's domain models and migrations through its real application configuration. Plugins such as the IFC editor that have only browser resources receive a resource-only configuration, using the child name `<plugin package>.resources` and a distinct resource label. These resource-only configurations disable model imports and migrations for their own labels.

The Django adapter imports trusted resource namespaces and their configured application classes. Python may initialize parent packages during this step. These imports happen in the framework adapter after declaration discovery, rather than in the framework-independent registry.

Django's app-directory template loader finds `templates/` using each configuration's application path. Its default form renderer likewise finds the model-choice widget templates through installed app directories. Plugin template paths do not need to be added to `TEMPLATES["DIRS"]`, and a custom form renderer is unnecessary.

Django's app static finder uses the same path. The common adapter also adds each plugin's `static/` root to `STATICFILES_DIRS` in `ready()`, because Bolt's native server reads those directories directly in development and production. The image retains files in their owning plugin directories rather than duplicating the large public geometry assets.

## Registered page overviews

The adapter's `register_landing_overview(AppConfig, declaration)` hook registers the owner's optional overview during normal application startup. The host's Mycelium and Plugin Manager configurations use the same hook for their own templates. Each application can register one overview. Django's normal template loader resolves it, and the adapter checks that the resulting template origin is the exact declared owner file; another application's same-named template cannot silently replace it.

The guest landing view obtains `public_overview_templates()`, ordered by application name, and includes the production-compatible descriptions. Its sequence explains Cadevil and Mycelium, choosing trusted tools, BIM Workspace, the IFC Editor and the browser example. A single demo action appears at the top of the page body and uses the usual HTMX outer replacement of `#content-container`. Signed-in landing requests do not collect these templates; they show the user's workspace actions. Full and HTMX responses follow the same rule.

Public descriptions are independent of catalog activation and personal selection. They describe installed source packages without granting route access or starting a workflow. The private plugin details view resolves the same owned declaration through `get_overview_for_plugin`; development-only descriptions remain unavailable with `DEBUG=False`. Uploaded browser archives cannot register server templates.

## Plugin-owned persistence

Active BIM domain models and upload/validation helpers live under `plugins/bim_model_manager/django/`. All nine BIM models use the `bim_model_manager` app label and default `bim_model_manager_<modelname>` table names. Their content types, permissions and migration history belong to that plugin. Shared authentication remains the host's `shared.CadevilUser`; it has no plugin upload relation or BIM model re-exports.

Version 0.15.0 of the application and 2.0.0 of BIM Workspace introduced fresh authentication and BIM migration histories. The shared BIM compatibility wrappers and historical shared/plugin-root migrations were removed. That was the breaking persistence boundary: an existing 0.14 database cannot be upgraded by running those initial migrations over it. Back up that deployment separately, initialize an empty Cadevil database, recreate login access, and import the models and packages needed in the new schema.

Application 0.16.0 and BIM Workspace 2.0.3 move the Python packages out of the old `apps/` parent. The Django application names and migration module paths now use the root packages shown above. The `shared` and `bim_model_manager` app labels, table names and existing initial migration identities are unchanged. This package move retains an existing 0.15 database and application files; it does not repeat the 0.15.0 schema reset.

## Python implementation ownership

The plugin owns its calculation implementation under `ifc_extractor/`, its assessment/presentation, geometry/viewer, CityJSON and location services. Its Python tests live under `tests/plugins/bim_model_manager/`; browser tests use `tests/browser/`, and Rust unit tests use `tests/rust/`. Adjacent `cityjson_schemas/` and `location_data/` runtime resources stay with their services. Generic request adapters, page rendering, authorization, identity and application logging remain host services.

Application imports use the plugin's module paths directly, for example `plugins.bim_model_manager.ifc_extractor.assessment_futures`. The development MCP native implementation and its UI/UX/code-audit bridges belong to `plugins/development_mcp/` and remain debug-only.

## Workflow and package boundaries

Application and resource registration happen at startup, independently of each user's enabled workflows. A registered template or static file does not grant access to a plugin route. Route permission, ownership and personal workflow checks still control private building data and plugin pages. The public `/demo` can use bundled house geometry and templates without requiring a visitor to enable BIM Workspace.

Signed archives uploaded through the plugin catalog remain browser worker packages. They do not become Django applications, add server-side Python modules, or contribute directories to the resource registry. A trusted source plugin with web resources must be explicitly configured in `PLUGIN_BUILTINS`, provide its descriptor and resource namespace, and use resource names that do not conflict with another plugin or the host. Plugins with domain models additionally declare their real Django application configuration. Runtime files must also be reviewed in the [Docker input allowlist](DOCKER.md).

The `plugins/resources.py` helper exposes declared static roots to framework-independent tooling. `npm test` temporarily merges those roots and stages the suites from `tests/browser/` alongside them, so relative browser imports and public fixture paths continue to work. Tests are absent from the plugin's shipped static directories. The test mirror is temporary and is not a production resource directory.
