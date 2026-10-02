# Development MCP tools

Cadevil has a native Django-Bolt MCP endpoint at `http://127.0.0.1:8000/dev/mcp` when started with `make debug` (`config.settings.dev`). The debug launcher owns the Bolt subprocess and the external MCP subprocesses, outside the reload worker tree. It uses the already-installed `django-bolt[mcp]` dependency and `api.mount_mcp` rather than a second web server. The mount is stateless for compatibility with multiple workers.

Both `DEBUG=True` and `DEVELOPMENT_MCP_ENABLED=True` are required. Production settings use `DEBUG=False`, so no MCP route is registered, even if the development opt-in is accidentally set. MCP is not a production feature.

Only two explicit read-only tools are exposed:

- `project_info`: framework, demo model identifiers, package formats and signing contract.
- `demo_report`: precomputed public demo building statistics, GWP/LCA values, recovery cost analysis and provisional/complete status. Allowed identifiers are house-a through house-d and example-a/example-b. House estimates remain provisional; currency is EUR.

These tools do not access user uploads, authentication data, admin actions, plugin activation state, or live calculations. Existing REST handlers are not automatically exposed. Bolt's localhost Host/Origin protection is retained.

## Codex setup

Project `.codex/config.toml` enables:

- `context7`: locally downloaded, pinned `@upstash/context7-mcp` 4.1.1, with public library documentation lookup tools. Runtime is stored at `/Users/mia/Documents/ChatGPT/CadEval/mcp_tools/context7`. No API key is configured. The debug plugin launches its stdio subprocess and exposes its allowlisted tools at `http://127.0.0.1:8017/mcp/`.
- `gitolite_repository`: downloaded official `mcp-server-git` 2026.8.18 for the existing local checkout; only status, diffs, log, show and branch listing are enabled. Remote configuration and SSH credentials are unchanged. The debug plugin launches its stdio subprocess behind `http://127.0.0.1:8018/mcp/`. Write tools are filtered by the adapter itself, as well as by Codex configuration.
- `cadevil_development`: native localhost endpoint, exposing only the two tools above. The development server must be running.

The configuration is also installed in the current CadEval workspace so chats started there can discover the same tools. Existing global servers are preserved. Codex loads project MCP configuration for trusted projects; restart/reload the MCP connection or begin a new session to discover newly configured tools. Configuration does not inject tools into an already-running turn.

Gitolite is the primary remote. GitHub integration is planned for later and was not installed in this setup. The Git MCP server inspects the local checkout; fetching or pushing continues through the existing Git/SSH workflow. Existing shell, file access and browser automation remain available without additional servers.

Context7 downloads and dependencies belong to the development workspace, not the production application or its Python dependency lock. No private project files were sent during the public Django documentation lookup verification.

References: https://bolt.farhana.li/topics/mcp/ ; https://learn.chatgpt.com/docs/extend/mcp?surface=cli ; https://github.com/upstash/context7


## Library documentation coverage

Context7 library resolution was verified for the following primary-source collections:

| Library | Context7 library ID |
| --- | --- |
| Python | `/python/cpython` |
| Django | `/django/django` |
| Django-Bolt | `/dj-bolt/django-bolt` |
| Rust | `/rust-lang/reference` |
| PyO3 | `/pyo3/pyo3` |
| maturin | `/pyo3/maturin` |
| IfcOpenShell | `/ifcopenshell/ifcopenshell` |
| pandas | `/pandas-dev/pandas` |
| cryptography | `/pyca/cryptography` |

Context7 is an independent documentation retrieval server; these IDs identify source collections, not dedicated MCP servers operated by each library's maintainers. Select documentation matching the installed dependency version before applying advice. Unindexed dependencies can still be checked against their official documentation with the browser or local source.

The official Django forum discussion still describes a documentation MCP server as a proposal: https://forum.djangoproject.com/t/official-mcp-server-for-djangos-documentation/44188 . No additional per-library servers were installed without a verified benefit over the shared documentation lookup.

## Debug launch and plugin lifecycle

`make debug` invokes the `debugserver` management command with explicit development settings. A single supervisor starts Bolt in a child process and starts the enabled `cadevil.mcp.context7` and `cadevil.mcp.git` plugins. Each adapter initializes its upstream stdio server, serves Streamable HTTP on loopback, preserves tool schemas/results, and exposes only its read-only allowlist. Host and Origin protection reject outside origins.

Disable an MCP plugin in Plugin Manager to stop its adapter and upstream process; enable it to start again. Changes are polled once per second. Native `cadevil.mcp.native` uses the supervised Bolt child and rejects tool calls immediately when disabled. Native route registration changes require a restart; no native route is registered under production settings. Stopping the launcher shuts down its adapters, their SDK-owned stdio process groups, and Bolt. Failed adapters retry with bounded exponential backoff; failures are logged without taking down the web server. Adapter logs are in `data/debug-mcp/`.

Subprocess definitions are trusted Python plugin contributions to `debug_mcp_process`. They are registered only by debug-compatible hooks, never by browser archive uploads. `AppConfig.ready` performs discovery only and never launches processes. The isolated SDK runtime is the already-downloaded Git MCP environment; no SDK dependency was added to the production lock.

`make run` explicitly selects `config.settings.prod` and never invokes the supervisor. `debugserver` refuses production settings even with MCP opt-in. The adapter entry script also refuses to run without the debug launcher marker. Context7/Git Codex connections now use the localhost URLs rather than launching independent stdio copies; reload the Codex connections after starting `make debug`.

Set `CADEVIL_MCP_TOOL_ROOT` to relocate the downloaded tool directory. Development settings contain the adapter ports. The runtime currently uses MCP SDK 1.30.0 required by the pinned Git MCP package, and the adapter imports match that version.

## Environment compatibility

Python manifests and signed browser `plugin.json` files can declare `compatibility` as `debug`, `production`, or `both`. The field defaults to `both` for existing packages. Labels are visible in Plugin Manager and Plugin Store, and are derived from manifests rather than editable form fields.

All three MCP plugins are **Debug only**. The BIM workspace, Rust IFC editor, and Rust Snake plugins are **Debug and production**. Production-only plugins are supported through the same contract. Incompatible plugins cannot be enabled, their registration hooks/contributions are skipped, and protected plugin pages/assets reject access. Stored enabled preferences survive switching environments; effective availability also requires compatibility.
