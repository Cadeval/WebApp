# IFC geometry parallelism and public recorded demo

The strict schema/EXPRESS gate still runs before geometry extraction. The calculation adapter now bulk-converts included elements with IfcOpenShell's native multicore iterator and reusable conversion caches. Python entity reads and numeric aggregation remain on the calling thread. The iterator results are reduced immediately to volume, area, length and world-coordinate bounding boxes, keyed by STEP ID. Mesh buffers are not retained across iterator advancement. Original IFC order controls all report rows and aggregation, independent of worker completion order.

IFC_GEOMETRY_THREADS defaults to four workers per assessment and can be set through the same environment variable. The runtime bounds it to available CPUs and sixteen workers maximum. Use one to reduce CPU/memory pressure under concurrent requests. Omitted shapes or iterator failures retain serial conversion fallback; unreadable quantities remain unavailable and cannot produce a complete zero-impact report. Reports retain geometry-processing counters and a code hash for the new adapter.

[IfcOpenShell recommends the geometry iterator for bulk multicore conversion](https://docs.ifcopenshell.org/ifcopenshell/geometry_iterator.html). No Python thread pool shares live IFC entity objects, no process reloads the complete model per element, and schema validation has not been bypassed or weakened.

## Actual-model benchmark

The accompanying JSON records the first 200 eligible geometry-bearing elements in each supplied original A–D IFC, using the existing settings and installed IfcOpenShell. Individual create_shape, a one-thread iterator and a four-thread iterator were timed. All measured volumes, areas, lengths and bounds matched within 1e-7, and source SHA256 hashes stayed unchanged. Observed speedup of the geometry stage versus the previous individual loop: A 1.47×, B 1.74×, C 3.37×, D 3.39×. These sample geometry timings do not claim whole-assessment speedup: file opening, semantic processing and strict schema validation remain separate costs. The real houses still fail assessment validation.

## Public demo

The landing page `/` links to the prerecorded demo at `/demo`; the demo markup and controller load only on `/demo`, which is accessible without login or an enabled BIM plugin. The 72-second sequence displays the actual A–D house geometry in its opening/closing chapters and uses two clearly labelled controlled fixtures for reference/method/report/comparison chapters. House assessment errors are explicitly shown; fixture results are never attributed to houses. The public content contains static approved geometry and recorded fixture reports, not private upload IDs, session data, user libraries or live assessment endpoints. The four large geometry assets are fetched sequentially only after visitors choose Load and play demo; normal home loading does not fetch them.

The same outerHTML HTMX content boundary, page cleanup and history restore initialization apply. Play/pause/restart/replay remain the only exploration controls. Browser checks rendered actual House A and D geometry, advanced through controlled-example chapters, and found no console errors. Anonymous native route tests verify page/fragment consistency, GET-only access, no private-record leakage and unchanged data counts. Source files, user configurations and saved assessments were preserved.

Verification: 57 isolated Python tests passed, including parallel-versus-single-thread report equality in metre and millimetre IFCs, serial fallback, strict validation-before-geometry, worker limits and anonymous demo access. Playback timeline tests verify all chapters and A–D scene order. Development system check passed.
