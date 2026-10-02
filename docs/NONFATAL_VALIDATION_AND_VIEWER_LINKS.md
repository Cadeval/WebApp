# Nonfatal IFC validation and linked diagnostics

Schema/EXPRESS validation now records warnings and allows numerical assessment to continue. The JSON stores checked/valid status, all raw diagnostics, original occurrence counts, affected product references and grouped diagnostics. A report with schema warnings is provisional (`complete=false`); `calculation_complete` retains coefficient completeness independently. Unreadable IFC files or unusable references remain fatal because no meaningful assessment can run.

Repeated messages are grouped by category, attribute/rule and normalized explanation, excluding instance-specific EXPRESS evaluation output. Every occurrence is counted; unique associated product identities are retained. Technical explanations stay expandable and HTML-escaped. Schema warnings on representations or other dependent records trace inverse ownership to products, without traversing beyond products into their neighbours. Errors without associated geometry explain that limitation.

Authenticated reports use the owner-protected model viewer route with an encoded `element` query parameter. The viewer receives the selection in server-rendered data attributes, resolves GlobalId through ancestor metadata, highlights the mesh and fits the camera to its product bounds after GLB loading. Missing geometry produces a visible selection status. It does not grant cross-user access or modify the source IFC. Historical row-based reports gain grouped calculation/material diagnostics in memory; older reports need recalculation to obtain previously unsaved schema details.

The existing public demo remains a fixed recording. New normal calculations use the nonfatal policy.

## Actual-model validation

All four original A–D files completed numerical analysis without a fatal schema gate; SHA-256 identities were unchanged. A: 22,674 schema occurrences / 13 combined diagnostic groups, B: 28,535 / 19, C: 16,089 / 18, D: 21,373 / 20. Group counts include calculation/material/quantity messages alongside schema diagnostics. Reports remain provisional.
