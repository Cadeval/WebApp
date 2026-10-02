# Development MCP tools

Cadevil has a native Django-Bolt MCP endpoint at `http://127.0.0.1:8000/dev/mcp` when started with `make debug` (`config.settings.dev`). It uses the already-installed `django-bolt[mcp]` dependency and `api.mount_mcp` rather than a second web server. The mount is stateless for compatibility with multiple workers.

Both `DEBUG=True` and `DEVELOPMENT_MCP_ENABLED=True` are required. Production settings use `DEBUG=False`, so no MCP route is registered, even if the development opt-in is accidentally set. MCP is not a production feature.

Only two explicit read-only tools are exposed:

- `project_info`: framework, demo model identifiers, package formats and signing contract.
- `demo_report`: precomputed public demo building statistics, GWP/LCA values, recovery cost analysis and provisional/complete status. Allowed identifiers are house-a through house-d and example-a/example-b. House estimates remain provisional; currency is EUR.

These tools do not access user uploads, authentication data, admin actions, plugin activation state, or live calculations. Existing REST handlers are not automatically exposed. Bolt's localhost Host/Origin protection is retained.

## Codex setup

Project `.codex/config.toml` enables:

- `context7`: locally downloaded, pinned `@upstash/context7-mcp` 4.1.1, with public library documentation lookup tools. Runtime is stored at `/Users/mia/Documents/ChatGPT/CadEval/mcp_tools/context7`. No API key is configured.
- `gitolite_repository`: downloaded official `mcp-server-git` 2026.8.18 for the existing local checkout; only status, diffs, log, show and branch listing are enabled. Remote configuration and SSH credentials are unchanged.
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
