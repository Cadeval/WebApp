# Loading and task feedback

Calculations, model/reference uploads, CityJSON import and export, plugin package checks and map-based local data lookups show a named task in the shared page shell. The indicator lists concurrent tasks separately and shows elapsed time. It remains visible until every active task finishes. The rest of the page remains available, including the administrator's log popover.

Material-passport calculation and comparison, IFC/reference uploads and CityJSON conversion use explicit HTMX POST requests with `#content-container` outer replacement. File forms retain multipart encoding, CSRF fields and a native browser form fallback. Redirected results update the page URL. Pending forms reject repeat submissions without disabling successful fields or removing a submit button's name/value from the request. Validation and request failures release the pending state so the user can retry.

The model viewer and public demo distinguish server preparation, geometry download, processing and first rendering. Preparation and processing have no reliable work total and remain indeterminate. A download percentage is shown only when an unencoded response supplies a usable Content-Length and the received byte count agrees; otherwise the display shows bytes received. Reaching 100% downloaded does not imply that the model has finished parsing or rendering. Selecting a model element also reports loading its material properties.

Ready source IFC, CityJSON, CSV and JSON downloads remain native browser transfers. Their notice explains the browser handoff without pretending to observe download completion. CityJSON preparation has its own tracked task before its download link becomes available.

## Shared lifecycle

`resources/static/js/task_activity.js` owns a singleton activity controller outside swapped content. `window.CadevilActivity.start({label, target, source})` returns a task token; `update(token, {label})` changes its stage and `finish(token)` completes it. Completion is idempotent. `data-task-label` supplies workflow-specific labels for forms or request links. Native download links use `data-download-notice`.

HTMX 4 requests are tracked by their `event.detail.ctx` identity. Cleanup listens to `htmx:finally:request` on the document and original source, covering completion before or after an outerHTML replacement; the vendored library dispatches on the document when its source has detached. Abort signals clean up the affected request independently. An unrelated settle or script error cannot hide another task's indicator. Navigating away clears activity timers, and returning from browser history resets native-form pending state.

Busy targets use reference-counted `aria-busy`, preserving any prior value. A polite live status announces task changes while the elapsed clock stays outside the live region. Reduced-motion preferences stop spinner animation. Feedback uses the site's shared light/dark neutral palette.

## Verification

Activity tests cover overlapping requests, detached-source completion, aborts, server/network failures, repeat submits, unchanged field serialization, native fallback/history restoration, source-only download notices, manual stages and duplicate module imports. Rendered-form checks cover multipart encoding, CSRF-compatible forms, explicit content boundaries and local lookup targets. Geometry-loading tests separately cover byte progress, preparation/processing transitions, cancellation and resource cleanup.
