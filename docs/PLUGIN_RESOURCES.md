# Plugin resources and application loading

BIM Workspace and the IFC editor own their templates and public browser assets. The plugin manager discovers resource declarations through a registry without Django imports, then connects them to Django through a separate adapter. BIM Workspace also owns its persistence as a real Django application. Resource ownership stays independent of the web framework, so another framework can implement its own adapter without changing the declaration format.

## Resource ownership

The bundled source packages use this layout:

```text
apps/plugins/bim_model_manager/
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
    static/css/...
    static/js/...
    static/bim-demo/...
apps/plugins/example_plugin/
    resources.json
    resources/__init__.py
    templates/example_plugin/ifc_editor.jinja2
    static/css/ifc_editor.css
    static/js/ifc_editor_controller.js
    static/js/plugins/example_plugin_worker.js
    static/wasm/example_plugin.wasm
```

Generic page shells, authentication, plugin administration and shared browser helpers remain in the host's `resources/` and `apps/shared/templates/` directories. Existing relative template names and `/static/...` URLs remain the same. For example, `bim/models.html` is still a template name and `/static/js/3d_view.js` remains the viewer URL; their files live with BIM Workspace.

Each source package declares one resource owner. BIM Workspace's `resources.json` is:

```json
{
  "version": 1,
  "plugin_id": "cadevil.bim.model_manager",
  "package": "apps.plugins.bim_model_manager"
}
```

The editor uses `cadevil.example.editor` and `apps.plugins.example_plugin`. The descriptor version describes the resource format; the plugin's manifest carries its release version.

`apps/plugin_manager/resource_registry.py` defines `ResourceBundle` and `ResourceRegistry`. A bundle contains the owner ID, Python package name and resource root. `from_builtins()` reads only declarations in explicitly configured bundled source packages. It validates metadata, rejects duplicate owners/packages and escaping or symlinked resource paths, and requires the `resources/__init__.py` namespace. The registry reads metadata without importing Django, plugin factories, model modules or hooks. Uploaded packages and runtime storage are outside this discovery process.

## Django registry hook

`apps/plugin_manager/django_resources.py` is the framework adapter. The final development and production settings append its application configurations to `INSTALLED_APPS`. Production first filters out development MCP plugins. Container settings inherit this production registration; applications are registered once through Django's normal startup.

A trusted plugin's `resources/__init__.py` can declare `DJANGO_APP_CONFIG` as the dotted path to its Django application configuration. BIM Workspace points to `apps.plugins.bim_model_manager.django.apps.BIMConfig`. This configuration uses:

- Application name: `apps.plugins.bim_model_manager.django`.
- App label: `bim_model_manager`.
- Application path: the BIM plugin root, for its `templates/` and `static/` directories.
- Migration namespace: `apps.plugins.bim_model_manager.django.migrations`.

The plugin manager's `PluginAppConfig` provides the common resource-loading bridge. Django loads BIM's domain models and migrations through its real application configuration. Plugins such as the IFC editor that have only browser resources receive a resource-only configuration, using the child name `<plugin package>.resources` and a distinct resource label. These resource-only configurations disable model imports and migrations for their own labels.

The Django adapter imports trusted resource namespaces and their configured application classes. Python may initialize parent packages during this step. These imports happen in the framework adapter after declaration discovery, rather than in the framework-independent registry.

Django's app-directory template loader finds `templates/` using each configuration's application path. Its default form renderer likewise finds the model-choice widget templates through installed app directories. Plugin template paths do not need to be added to `TEMPLATES["DIRS"]`, and a custom form renderer is unnecessary.

Django's app static finder uses the same path. The common adapter also adds each plugin's `static/` root to `STATICFILES_DIRS` in `ready()`, because Bolt's native server reads those directories directly in development and production. The image retains files in their owning plugin directories rather than duplicating the large public geometry assets.

## Plugin-owned persistence

Active BIM domain models and upload/validation helpers live under `apps/plugins/bim_model_manager/django/`. All nine BIM models use the `bim_model_manager` app label and default `bim_model_manager_<modelname>` table names. Their content types, permissions and migration history belong to that plugin. Shared authentication remains the host's `shared.CadevilUser`; it has no plugin upload relation or BIM model re-exports.

Version 0.15.0 of the application and 2.0.0 of BIM Workspace start fresh authentication and BIM migration histories. The shared BIM compatibility wrappers and historical shared/plugin-root migrations are removed. This is a breaking persistence change: initialize an empty Cadevil database for this release. Back up an existing deployment separately, recreate login access, and import the models and packages needed in the new database. An existing 0.14 database cannot be upgraded by running the new migrations over it.

## Python implementation ownership

The plugin owns its calculation implementation under `ifc_extractor/`, its assessment/presentation, geometry/viewer, CityJSON and location services, and the corresponding tests. Adjacent `cityjson_schemas/` and `location_data/` fixtures stay with their services. Generic request adapters, page rendering, authorization, identity and application logging remain host services.

Application imports use the plugin's module paths directly, for example `apps.plugins.bim_model_manager.ifc_extractor.assessment_futures`. The development MCP native implementation and its UI/UX/code-audit bridges belong to `apps/plugins/development_mcp/` and remain debug-only.

## Workflow and package boundaries

Application and resource registration happen at startup, independently of each user's enabled workflows. A registered template or static file does not grant access to a plugin route. Route permission, ownership and personal workflow checks still control private building data and plugin pages. The public `/demo` can use bundled house geometry and templates without requiring a visitor to enable BIM Workspace.

Signed archives uploaded through the plugin catalog remain browser worker packages. They do not become Django applications, add server-side Python modules, or contribute directories to the resource registry. A trusted source plugin with web resources must be explicitly configured in `PLUGIN_BUILTINS`, provide its descriptor and resource namespace, and use resource names that do not conflict with another plugin or the host. Plugins with domain models additionally declare their real Django application configuration. Runtime files must also be reviewed in the [Docker input allowlist](DOCKER.md).

The `apps/plugins/resources.py` helper exposes declared static roots to framework-independent tooling. `npm test` temporarily merges those roots so browser imports and public fixture paths can be tested from their unchanged URLs. The test mirror is temporary and is not a production resource directory.
