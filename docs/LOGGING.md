# Application logging

Application diagnostics go to stderr as one JSON object per record. The existing
admin WebSocket keeps its `logs` / `entries` protocol and SQLite columns; its
messages are readable text with the same safe context. Staff still need the
explicit `shared.view_application_logs` permission.

`LOG_LEVEL` controls the root logger (default `INFO`). `ADMIN_LOG_ENABLED=false`
disables the SQLite sink without disabling console diagnostics. `ADMIN_LOG_PATH`
changes its location (default `data/live-logs.sqlite3`). Protect that directory as
operational data and collect stderr with the deployment's log service. The admin
store keeps at most 2,000 rows; reconnects read batches of at most 200. It is a
diagnostic feed, not an immutable audit trail. Rotating or retaining collected
stderr is the operator's responsibility.

Every application API calls `configure_api_logging`, which places an async native logging
wrapper before Bolt's Django middleware adapter. It
accepts a canonical UUID `X-Request-ID`, or generates one, and keeps it in context
through async and synchronous form/calculation code. It logs the HTTP method,
status and elapsed milliseconds. Browser pages supply a constant module/handler
label; native handlers without a label appear as `<native>`. It never reads a
body, query string, cookie, authorization header, username, network address or
uploaded filename for logging. `X-Request-ID` is returned for ordinary responses
and explicit Bolt `HTTPException` responses. Uncaught native exceptions retain
Bolt's error response behavior; their request ID appears in the request failure
record, but may be absent from the response.

Assessment lifecycle events record duration, completion and summary counts.
Calculation diagnostics and model/material details remain in assessment reports.
Session login/logout, password changes, signing key registration/revocation, account administration and signed plugin
availability/upload/discovery changes emit events without credentials or account
identifiers. These operational events are insufficient to reconstruct who changed
a particular record; add a separate, access-controlled audit model if that is
required by a deployment.

Both formatters omit unknown `extra` fields and redact common credential patterns,
URL credentials/queries, upload paths and control characters. Messages are bounded
to 8,000 characters. Exception logs keep the exception class and up to 20 stack
locations, while omitting exception values, source lines and locals. The SQLite
reader also applies redaction to old rows. Pattern redaction cannot reliably
identify arbitrary personal data already present in historical free-form logs;
remove legacy log stores according to the deployment's retention policy if needed.
New logging calls must use fixed messages and the small context allowlist in
`apps/shared/logging_utils.py`. Do not log request objects, payloads, configuration,
IFC entities, signing material or exception messages.
Django request/security diagnostics are rendered with fixed labels because their
original messages can contain raw paths and origins. SQL logging is disabled even
at `LOG_LEVEL=DEBUG`, since SQL parameter values can contain account or model data.

Bolt 0.11.1 enables its native access logger when `django.server` accepts `WARNING`.
The application config gives that logger a null handler and `CRITICAL` level,
disabling the native raw-path feed in favor of middleware diagnostics. This gate
is verified against [Bolt's pinned server implementation](https://github.com/dj-bolt/django-bolt/blob/v0.11.1/src/server.rs).
The wrapper orders Bolt's middleware list during API construction; maintain its
native transport/mount/concurrent progress tests when upgrading Bolt. Placing a
call-style logging middleware in Django's `MIDDLEWARE` would activate Bolt's sync
compatibility bridge and serialize async handlers, so the wrapper stays native.
Requests rejected by Rust before Python dispatch, unmatched URLs, static assets,
WebSockets, Django admin ASGI mounts and external MCP subprocesses are outside this HTTP middleware. Native
stderr startup/configuration warnings, subprocess output and a reverse proxy's
logs do not pass through the Python redaction formatters. Configure their access
logging/retention independently. The explicit `LOGGING` configuration replaces
Django's default console/mail handlers; send production alerts from the collected
structured logs.

Run the focused native transport, redaction, concurrency and existing log stream
checks with:

```sh
python manage.py test apps.shared.test_logging apps.shared.test_live_logs --settings=tests.passport_test_settings
```
