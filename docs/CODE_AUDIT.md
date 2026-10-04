# Code audit and cleanup — 0.11.0

Audit date: 4 October 2026. Baseline: release `0.10.0`, commit
`3ad35a69247cf2cd857f4cdddb762918c2006b34`.

This review added an offline development audit MCP, removed 2,162 unused tracked
files, and corrected security and calculation/editor defects. It covered the
configured application, Python and Rust implementations, browser code,
templates/assets, plugin loaders, build/test configuration, dependency lock and
migration history. Deletion decisions came from route, import, template and
plugin reachability checks followed by regression and live-server verification;
a static warning alone was not a deletion criterion.

## Corrected findings

| Area | Before | Result and evidence |
| --- | --- | --- |
| Private media | Bolt's automatic `MEDIA_ROOT` mount bypassed the application's owner checks. An existing synthetic upload returned HTTP 200 anonymously on the real server, although the in-process client returned 404. | `MEDIA_URL="/"` disables Bolt's automatic mount. The same real-server request now returns 404; owner-checked application download handlers remain. |
| IFC editor identity | JavaScript passed a STEP ID to a Rust export accepting an array index. Editing `#1` could silently change `#2`; noncontiguous IDs could fail. | The worker passes the resolved entity index. Actual-WASM regressions cover `#1/#2`, `#10/#20`, inspection, edit and export. |
| IFC editor errors | The worker interpreted a numeric Rust error code as the length of an old output buffer. | A bounded mapping produces readable errors; failed edits and loads preserve the previous document. |
| WASM allocation | A caller could request a very large input allocation before parser limits applied. | Reservation rejects inputs above 32 MiB before allocation, returns the failure sentinel and preserves existing state. Native and actual-WASM tests verify this. |
| Reference errors | Uploaded material/configuration names could appear in an HTML error response. | The route returns explicit plain text, with an HTML-bearing duplicate-key regression. |
| CSV export | Formula detection missed `=`, `+`, `-` or `@` after leading whitespace. | Detection neutralizes these formula cells while retaining numeric negative values. Tests cover headers, materials and cells. |
| Form validation | `request.POST or None` made an empty POST unbound and hid required-field errors. | Handlers bind forms by request method; tests verify errors and absence of calculation or persistence on invalid input. |
| Quantities and units | Failure of an unused length calculation could discard valid volume/area; three cost charts labelled prices as an unspecified currency. | The unused computation is removed, valid quantities survive, and cost charts use EUR. |
| Browser lifecycle | HTMX replacement could leave workers, renders, fetches, listeners or Blob URLs attached to departed pages. | Viewer, demo, map, thumbnail and worker teardown is exercised, including BFCache restoration and overlapping file loads. |
| Runtime and build | Stale dependencies, broken Rust output paths and obsolete test commands obscured the supported execution path. | Locked Python/Node installs, current test targets and correct WASM output paths are in place. PyJWT is pinned to a minimum of 2.15.1; production validates the secret key and disables permissive CORS. |

The media setting follows the mount guard in [Bolt 0.11.1's native server](https://github.com/dj-bolt/django-bolt/blob/v0.11.1/src/server.rs).
An empty Django `MEDIA_URL` is unsuitable because Django adds the script prefix;
the absolute slash remains stable. Deployment proxies must likewise keep
`MEDIA_ROOT` private. PyJWT's update is documented in the [upstream releases](https://github.com/jpadilla/pyjwt/releases).

## Removed code and preserved contracts

The explicit deletion manifest contains 2,112 frontend files, 41 legacy backend
files and nine obsolete routing/scaffold/test files. The frontend removals are
mainly 2,060 unreferenced individual SVG icons, unused fonts/images, unreachable
Celery-era pages and templates shadowed by the active loader. Backend removals
include the unmounted Django/DRF/Celery/Channels BIM stack, an unregistered copied
Snake plugin, obsolete tests importing absent applications, and unused custom
template libraries. Active tests replace the retired editor transport coverage.

Unused imports, inactive forms, duplicate helpers, commented legacy code and a
pass-only chart placeholder were also removed. Channels, Daphne and
django-htmx dependencies were unnecessary for the active native Bolt routes and
websockets. The pytest runtime dependency is deliberately retained:
IfcOpenShell 0.8.5 imports `_pytest.assertion` during EXPRESS validation, and real
assessment tests exposed that requirement.

All 29 migration files and serialized upload/validator callables are preserved.
The cleanup retains model/schema history, signal registration, compatible route
aliases, independent numerical/OpenStudio utilities, required vendor assets and
licenses, both WASM applications, source IFCs, demo recordings, reference
archives and thesis documents. It introduces no database migration. Per-file
hashes and deletion reasons are recorded in the audit evidence; Git history and
the installation backup support recovery.

## Audit MCP

`cadevil.mcp.code_audit` version `1.0.0` runs as a supervised debug-only subprocess
at `http://127.0.0.1:8020/mcp/`. It is absent from production and personal workflow
selection. Its only tools are `code_audit_info` and `code_audit_local`; the latter
accepts an analyzer choice and bounded pagination. It cannot accept a path, URL,
command, custom rule or automatic fix.

The adapter uses official Semgrep `1.179.0` and Ruff `0.16.10` in an isolated
runtime, with 552 local rule files pinned to [Semgrep rules commit
`a84ff9c`](https://github.com/semgrep/semgrep-rules/tree/a84ff9cc2453ca91d581380de4b8b3f272f6f4be).
The rule license, archive hash and exact tool dependency lock are retained.
It snapshots fixed source roots without following symlinks, excludes private
data, databases and generated/vendor files, and binds subsequent pages to a
source-content hash. Coverage, analyzer errors and unscanned files are reported.

Analyzer subprocesses receive a restricted environment and a macOS sandbox that
denies all network access. A socket attempt inside the same profile failed with
`PermissionError`; source remained unchanged after MCP scans. Other operating
systems fail closed until equivalent isolation is implemented. Each analyzer
has a 45-second timeout and bounded output; source snapshots are limited to
2,000 files and 32 MiB. Loopback Host/Origin checks and the existing debug
supervisor own the HTTP bridge and subprocess lifecycle.

This is a project adapter around the official CLI. Upstream `semgrep mcp` was
tested but its startup requested OAuth metadata, preventing an entirely offline
startup. Full setup and reconnect instructions are in
[DEVELOPMENT_MCP.md](DEVELOPMENT_MCP.md).

## Verification and residual scope

The complete `make test` run completed 574 Python tests with one optional test
skipped, 182 passing Node tests and 36 passing Rust tests. It includes 14 focused
audit-MCP tests and actual-WASM editor integration. All 45 retained templates
compile; 89 referenced local dependencies resolve. Ruff reports no unused or
undefined Python imports/variables under its `F` rules.

The final complete MCP snapshot inventoried 308 files; Semgrep reported scanning
302 and Ruff scanned 183 Python files. It produced 956 review candidates without
analyzer errors. Most were existing formatting findings
(489 `E702`, 206 `E701`, 33 `E402`), test assertions/password fixtures or patterns
requiring context, such as property access and fixed subprocess calls. Generated
test-cache files are excluded from audit scope. These findings are not a count
of confirmed vulnerabilities. Broad
formatting changes were deferred to keep this cleanup reviewable.

An independent manual review examined authentication/CSRF, ownership and workflow
isolation, user/group delegation, signed archives, private assets, browser sinks,
admin log websocket authorization, fixed provider networking/XML, caches,
parallel/native execution and both Rust ABIs. It confirmed the media and editor
findings above and found no additional concrete defect in those boundaries.
The separate dependency audit queried public package/version metadata for 47
locked runtime dependencies and reported no known vulnerabilities after the
PyJWT update; no source or model content was sent to an audit service.

Browser verification used an isolated synthetic database with the supplied
A–D house geometry. House B selection updated provisional GWP and EUR cost;
House A rendered 10,989 surfaces with ground and vegetation. Login, owner models,
the six-tab BIM workspace and HTMX navigation to the map worked, with no captured
console errors. The houses' incomplete assessment status remains visible;
rendering is not evidence that their material validation has become complete.

Community static rules do not provide proprietary cross-file analysis or prove
security. CSS/build inventory does not imply language-specific rule coverage.
This review does not establish safety for every hostile native-library input or
an external deployment/proxy. The locked default installation uses SQLite and
local caching; PostgreSQL or Redis deployments require separately installed
drivers and deployment checks. Undocumented external consumers of removed internal
files cannot be ruled out. Browser signing-key download Blob URLs remain until
replacement or `pagehide` so users do not lose an unretrieved encrypted key;
earlier HTMX-departure cleanup remains a bounded lifecycle improvement.
