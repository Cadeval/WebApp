# Fresh IFC calculations and readable element names

On 2 October 2026 the four current `28V_new` A–D exports were assessed against `/Users/mia/Library/Mobile Documents/com~apple~CloudDocs/Cadevil/Tables June 2026/MP indicators and modfications.xlsx` (115 material records). Workbook SHA-256: `4bbefa2e1a6d0694258baaaf44140b4275286bcca9795b6086e98d656ac87d0e`.

Each run uses the normal warning-mode schema/EXPRESS checks, native geometry threads and parallel validation future, 50 years with inclusive endpoint, recovery weighting by installed mass and LCA averaging by initially installed mass. No validation is bypassed. All four reports are provisional/incomplete; the numbers below are known subtotals, not complete house totals. Original IFC/workbook hashes were checked after every run. House C is the current `_28V_new` file, not the historical V3 export.

| House | Seconds | Schema warnings | Known installed mass, kg | Known GWP A1–A3, kg CO₂e | Known GWP A1–A3 + B4, kg CO₂e | Known initial global gross cost, € |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| A | 144.24 | 22,674 | 7,032,465.468 | 547,586.422 | 81,647.150 | 1,736,623.314 |
| B | 190.80 | 28,535 | 2,913,570.945 | -146,761.826 | -677,481.534 | 731,761.824 |
| C | 133.03 | 16,089 | 6,909,564.242 | 1,305,656.621 | 1,773,519.835 | 3,295,466.952 |
| D | 152.19 | 21,373 | 7,156,609.006 | 1,268,273.673 | 1,602,241.482 | 1,887,638.267 |

Fresh full JSON reports, name-led component CSVs and the machine-readable summary are in `/Users/mia/Documents/ChatGPT/CadEval/name_rerun_stage/results/`. All building values match the preceding actual-house recording. Fresh public aggregate snapshots, both controlled fixture reports, and name metadata on all six GLBs replace the demo assets. Prior public calculation JSONs remain in `name_rerun_stage/previous-demo/`. Ordinary user records, uploads and selected configurations were not modified.

`element_identity.py` resolves readable labels from IFC Name, LongName or a useful class/Tag fallback. UUID/GUID-shaped Tags remain technical data. Repeated names use storey/class context and a short STEP discriminator, for example `Decke-002 · UG01 (#51505)`. Missing STEP identities use an instance ordinal, keeping GUIDs out of the primary label. Stable GUID/STEP values remain in JSON, CSV technical columns and viewer selection links. Existing report names on rows survive even when no inventory exists. Saved reports are enriched only in memory.

The same identity mapping is used by element summaries, inventory/component rows, material classification links, schema/calculation/quantity/material diagnostics, overlap links and viewer metadata. CSVs lead with readable names and neutralize spreadsheet formula prefixes, including after whitespace. Django escapes IFC names; viewer text uses textContent. The viewer cache prefix is `identity-v2` so old GLB metadata cannot be reused as fresh output.

The browser verification also exposed an intrinsic canvas sizing feedback loop in the demo. Its canvas now fills a bounded scene using absolute positioning; renderer sizing no longer enlarges the parent. The public house summary overlay is bounded and scrollable.

Verification includes real A–D row/warning/overlap/viewer label consistency, stable identifier retention, unchanged source hashes and exact building-value comparison. Focused regression suites cover serial/parallel equality, duplicate/missing/whitespace/LongName/UUID-Tag cases, historical report preservation, escaped HTML, report owner checks, public demo access, model-specific values, playback controls and generated glTF name fallback. JavaScript metric tests use the existing staged Three.js modules and a controls stub; browser checks use the real rendering stack.
