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

`make debug` invokes the `debugserver` management command with explicit development settings. A single supervisor starts Bolt in a child process and starts the enabled Context7, Git, UI/UX and code-audit subprocess plugins. Each adapter initializes its upstream stdio server, serves Streamable HTTP on loopback, preserves tool schemas/results, and exposes only its read-only allowlist. Host and Origin protection reject outside origins.

Disable an MCP plugin in Plugin Manager to stop its adapter and upstream process; enable it to start again. Changes are polled once per second. Native `cadevil.mcp.native` uses the supervised Bolt child and rejects tool calls immediately when disabled. Native route registration changes require a restart; no native route is registered under production settings. Stopping the launcher shuts down its adapters, their SDK-owned stdio process groups, and Bolt. Failed adapters retry with bounded exponential backoff; failures are logged without taking down the web server. Adapter logs are in `data/debug-mcp/`.

Subprocess definitions are trusted Python plugin contributions to `debug_mcp_process`. They are registered only by debug-compatible hooks, never by browser archive uploads. `AppConfig.ready` performs discovery only and never launches processes. The isolated SDK runtime is the already-downloaded Git MCP environment; no SDK dependency was added to the production lock.

`make run` explicitly selects `config.settings.prod` and never invokes the supervisor. `debugserver` refuses production settings even with MCP opt-in. The adapter entry script also refuses to run without the debug launcher marker. Context7/Git Codex connections now use the localhost URLs rather than launching independent stdio copies; reload the Codex connections after starting `make debug`.

Set `CADEVIL_MCP_TOOL_ROOT` to relocate the downloaded tool directory. Development settings contain the adapter ports. The runtime currently uses MCP SDK 1.30.0 required by the pinned Git MCP package, and the adapter imports match that version.

## Environment compatibility

Python manifests and signed browser `plugin.json` files can declare `compatibility` as `debug`, `production`, or `both`. The field defaults to `both` for existing packages. Labels are visible in Plugin Manager and Plugin Store, and are derived from manifests rather than editable form fields.

All five developer MCP plugins are **Debug only**. They appear in the administrator catalog and cannot be selected as personal browser workflows. The BIM workspace, Rust IFC editor, and Rust Snake plugins are **Debug and production**. Production-only plugins are supported through the same contract. Incompatible plugins cannot be enabled, their registration hooks/contributions are skipped, and protected plugin pages/assets reject access. Stored enabled preferences survive switching environments; effective availability also requires compatibility.

## Local UI/UX audits

`cadevil.mcp.ui_ux` uses [UI/UX Suite](https://github.com/Aboudjem/ui-ux-suite), pinned to `0.6.1`. It is a community MIT project. The supervisor starts a bounded wrapper on `http://127.0.0.1:8019/mcp/` during `make debug`. It exposes only `uiux_audit_local` and `uiux_guidance`; source paths, browser/deep modes, URLs and output locations are not accepted. The frontend snapshot excludes symlinks, server code, uploads and credentials. Static findings require verification against rendered pages.

Install the locked development runtime (one package, no browser peers or install scripts):

```sh
npm ci --prefix /Users/mia/Documents/ChatGPT/CadEval/mcp_tools/ui-ux-suite --ignore-scripts --omit=optional --legacy-peer-deps
```

The project Codex config includes the two read tools. Existing Codex sessions may need a reconnect to discover newly configured servers. The plugin remains excluded from user workflows and cannot start with `DEBUG=False`.

## Offline code audits

`cadevil.mcp.code_audit` is a bundled read-only MCP adapter, version `1.0.0`, supervised at `http://127.0.0.1:8020/mcp/`. It exposes `code_audit_info` and `code_audit_local`. The audit accepts only an analyzer choice (`all`, `semgrep`, `ruff`) and bounded pagination (`offset`, `limit`). Later pages require `snapshot_hash` from the first page's `coverage.sha256`; a source change rejects the page and asks the caller to restart, preventing mixed snapshots. It accepts no source path, URL, command, custom rule or fix option. It never modifies the checkout. Disabling the plugin stops it; new discovery enables it through the usual existing plugin policy and later administrator choices are preserved. It is excluded from production and personal workflow selection.

The isolated runtime is `/Users/mia/Documents/ChatGPT/CadEval/mcp_tools/code-audit/.venv`. It contains official [Semgrep](https://pypi.org/project/semgrep/) `1.179.0` and [Ruff](https://pypi.org/project/ruff/) `0.16.10`; exact transitive versions are recorded in the adjacent `requirements.lock`. No audit dependency is added to the production application lock. An installation can be recreated with `uv pip sync --python /Users/mia/Documents/ChatGPT/CadEval/mcp_tools/code-audit/.venv/bin/python /Users/mia/Documents/ChatGPT/CadEval/mcp_tools/code-audit/requirements.lock` after creating the environment. Package downloads occur only during setup.

Python, JavaScript, TypeScript and Rust community rules are local copies from the [official Semgrep rules repository](https://github.com/semgrep/semgrep-rules), pinned to commit `a84ff9cc2453ca91d581380de4b8b3f272f6f4be`. The downloaded [source archive](https://codeload.github.com/semgrep/semgrep-rules/tar.gz/a84ff9cc2453ca91d581380de4b8b3f272f6f4be) has SHA-256 `b227c2d234ffd9c84c4dbd6619a5897a7192637c141baeace3bfc37b0715a887`. Its 552 YAML rule files and upstream license are retained under `upstream-rules/`; download provenance is in `provenance.json`. Rule updates require an explicit reviewed runtime update, not an audit call. The rules retain their upstream [Semgrep Rules License](https://github.com/semgrep/semgrep-rules/blob/a84ff9cc2453ca91d581380de4b8b3f272f6f4be/LICENSE).

The current official [`semgrep mcp`](https://github.com/semgrep/semgrep/tree/develop/cli/src/semgrep/mcp) was tested first. Its stdio startup requests OAuth metadata from `semgrep.dev`, and its default scan requires auto configuration unless an explicit custom rule is supplied. This prevents an entirely offline upstream MCP startup. Cadevil therefore exposes its own small MCP adapter around the official CLI; it does not install the archived `semgrep-mcp` package or use a hosted audit service.

Every call creates a private temporary snapshot of authored source under the root `shared`, `mycelium`, `plugin_manager`, `plugins` and `config` packages, frontend JavaScript/demo/templates/styles, `tests` and `.github/workflows`. A fixed top-level allowlist inventories `manage.py`, `pyproject.toml`, `uv.lock`, `Makefile`, package manifests/locks, Dockerfile and Compose metadata when present. It excludes reference archives, uploads/data, databases, other hidden files including `.env`, symlinks, virtual environments, vendor libraries, build outputs and binary assets. Opening every path component without following links prevents directory-link swaps from escaping the fixed roots. Source coverage, skipped files, a snapshot SHA-256, analyzer errors and scanned/unscanned-file lists accompany the results. Inventorying CSS or build metadata does not mean a language-specific rule applies: the output explicitly states each analyzer's language scope and coverage gaps. Pagination reuses the previous result only when source content and the analyzer choice match.

Semgrep runs with explicit local rules, metrics/version checks/tracing disabled, two workers, a 256 MiB per-worker limit and five-second per-rule timeout. Ruff runs without project configuration, cache or fixes and checks Python errors, unused imports/variables, common bugs, async issues and security review candidates. Each analyzer is capped at 45 seconds and 16 MiB of output; snapshots are capped at 2,000 files, 1 MiB per file and 32 MiB overall. Errors and skipped source mark the result incomplete; a failed scan is never presented as a clean audit.

Analyzer subprocesses additionally run through a fixed macOS `sandbox-exec` profile denying **all network access**. They receive no inherited project tokens, proxies or Semgrep user configuration. Audit calls therefore have no egress and transmit no source. The current isolated runtime requires this reviewed macOS sandbox; hosts without it fail closed until an equivalent OS isolation implementation is added. Codex itself uses another sandbox, so testing this nested profile from Codex can require a scoped local execution approval even though the analyzers deny network access.

These static findings require review against imports, routes, templates, tests and runtime behavior before removing code. Community Semgrep rules lack the proprietary cross-file engine, and this adapter does not consult a vulnerability feed or execute project code. Existing Codex chats may need an MCP reconnect/reload to discover `cadevil_code_audit`; the standard MCP SDK can verify the local endpoint immediately.
