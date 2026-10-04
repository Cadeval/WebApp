# Plugin resources

BIM Workspace and the IFC editor own their templates and public browser assets. The plugin manager provides a resource registry without Django imports, then connects those declarations to Django through a separate adapter. This keeps resource ownership independent of the web framework and makes another adapter possible without changing the declaration format.

## Resource ownership

The bundled source packages use this layout:

```text
apps/plugins/bim_model_manager/
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

Generic page shells, authentication, plugin administration and shared browser helpers remain in the host's `resources/` and `apps/shared/templates/` directories. Existing relative template names and `/static/...` URLs remain the same. For example, `bim/models.html` is still a template name and `/static/js/3d_view.js` remains the viewer URL; their files now live with BIM Workspace.

Each source package declares one resource owner. BIM Workspace's `resources.json` is:

```json
{
  "version": 1,
  "plugin_id": "cadevil.bim.model_manager",
  "package": "apps.plugins.bim_model_manager"
}
```

The editor uses `cadevil.example.editor` and `apps.plugins.example_plugin`. The descriptor version describes the resource format; the plugin's manifest carries its release version.

`apps/plugin_manager/resource_registry.py` defines `ResourceBundle` and `ResourceRegistry`. A bundle contains the owner ID, Python package name and resource root. `from_builtins()` reads only resource declarations in explicitly configured bundled source packages. It validates the metadata, rejects duplicate owners/packages and escaping or symlinked resource paths, and requires the `resources/__init__.py` namespace. The registry reads metadata without importing Django, plugin factories, model modules or hooks. It does not scan uploaded packages or runtime storage.

## Django registry hook

`apps/plugin_manager/django_resources.py` is the framework adapter. The final development and production settings append its `resource_app_configs(BASE_DIR, PLUGIN_BUILTINS)` result to `INSTALLED_APPS`. Production first filters out development MCP plugins. Container settings inherit this production registration; the adapter is not installed twice.

Django then populates its app registry through normal startup. Each resource-only `AppConfig` uses the child name `<plugin package>.resources`, a distinct label derived from that package, and a `path` pointing to the owning plugin's root. The adapter imports the trusted child namespace; Python may initialize its parent package at this point. This import happens in the Django adapter, after metadata discovery, rather than in the framework-independent registry.

The child namespace does not hold domain models. The adapter overrides `import_models()` so a future resource `models.py` cannot register models accidentally, and it disables migrations for its own app label with `MIGRATION_MODULES[label] = None`. Historical plugin-root models and migration files remain on disk without being registered as new installed applications. Persistent domain models and their migrations continue to belong to the host's existing applications.

Django's app-directory template loader finds `templates/` using `AppConfig.path`. Its default form renderer likewise finds the moved model-choice widget templates through installed app directories. Neither requires plugin template paths in `TEMPLATES["DIRS"]` or a custom form renderer.

Django's app static finder uses that same path. In `ready()`, the adapter also adds each plugin's `static/` root to `STATICFILES_DIRS`, because Bolt's native server reads those directories directly in development and production. The image retains the original files in their owning plugin directories rather than duplicating the large public geometry assets.

## Workflow and package boundaries

Resource registration happens at application startup, independently of each user's enabled workflows. A registered template or static file does not grant access to a plugin route. Existing route permission, ownership and personal workflow checks still control private building data and plugin pages. The public `/demo` can therefore use bundled house geometry and its templates without requiring a visitor to enable BIM Workspace.

Signed archives uploaded through the plugin catalog remain browser worker packages. They do not become Django applications, add server-side Python modules, or contribute directories to the resource registry. A new trusted source plugin with web resources must be explicitly configured in `PLUGIN_BUILTINS`, provide its descriptor and empty child namespace, and use resource names that do not conflict with another plugin or the host. Its runtime files must also be reviewed in the [Docker input allowlist](DOCKER.md).

The `apps/plugins/resources.py` helper exposes the declared static roots to framework-independent tooling. `npm test` temporarily merges those roots so existing browser imports and public fixture paths can be tested from their unchanged URLs. This test mirror is temporary and is not a production resource directory.
