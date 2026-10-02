# Original thesis requirement audit

Authority: `Master_thesis_june_2026.docx`, especially §§01.1–01.2, 03.3–03.4, 04.1–04.2 and 05.1–05.4. The completed 04.3 does not replace these requirements. Paragraph numbers below refer to the original document extraction.

| Requirement | Thesis evidence | Existing implementation | Audit action and validation |
|---|---|---|---|
| Preserve source models and separate external reference data | §04.2.1 [481], §04.2.5 [544] | Read-only IFC assessment; external XLSX/CSV | Retain; verify source bytes unchanged in end-to-end assessment |
| IFC4 input with validation before processing | §03.3.1.3 [410], §04.2.4 [529] | IfcOpenShell schema/EXPRESS gate | Retain real IFC tests and invalid-input persistence rejection |
| Latest manually compiled material/pricing dataset | §04.2.5 [535–541] and user-selected workbook | Workbook importer and alias mapping | Audit actual latest workbook fields; use for calculation validation without editing it |
| Material masses and GWP/AP/PEI from volume and density | §03.4.3.1 [449] | Core calculation | Retain handwritten outcomes, missing-data and negative-GWP tests; PENRT is the supplied workbook energy indicator |
| Element-local combination adjustment and year 0/50 material results | §03.4.3.2 [454–455] | Correct row calculations, incomplete display | Add element summaries and visible/exportable initial and observation-period mass, waste, recycling and all impacts |
| Building and material aggregates; averaged LCA indicators | §03.4.3.3 [459] | Totals and grade policy; no LCA averages | Implemented required user choice of installed-mass normalization or arithmetic mean of distinct material totals. Exact denominator, units and policy are saved; neither is attributed to an unstated normative method |
| Material cost estimation | §04.2.5 [535], §05.1 [594] | Three unit-price bases calculated; limited display | Display all building cost totals and include cost in graphical comparison; do not invent discounted lifecycle costing |
| Structured geometric/semantic inventory | §05.1 [594] | Material rows retain GUID/class but no storey inventory | Record names, classification, storey relationships, SI quantities and quantity provenance; export CSV |
| Identify potential model issues | §01.1 [173], §05.1 [594], §05.3 [602] | Schema gate and quantity warnings | Add storey-assignment diagnostics and explicitly provisional geometry-overlap candidates, without correcting source files or claiming full model certification |
| No misleading full totals when extraction omits elements | §05.3 [602,608,611] | Omitted unquantified elements can leave numeric full totals | Invalidate full totals, retain known subtotals and diagnostic provenance; regression test |
| Compare alternative models using consistent boundaries | §01.2 [222–225], §04.1 [468], §05.3 [608] | Legacy comparison route depends on obsolete imports; working passport route has no comparison | Add plugin-gated, owner-scoped comparison and boundary/reference mismatch diagnostics; refuse definitive comparison when boundaries differ |
| Graphical reports for material quantities, impacts, recycling and cost | §01.1 [167,177], §05.1 [594] | Legacy chart helpers exist; not integrated with working passport reports | Add accessible signed charts to reports and multi-model comparison; preserve unavailable values and negative GWP |
| Traceable configuration, source, system boundary and assumptions | §05.3 [606,611] | Core hash and config snapshot | Expand code provenance to IFC adapter/quantity resolution and expose method/coverage in reports and exports |
| Four source houses provide case-study context | §04.2.1 [475–481] | No research results established by existing tests | Validate implementation with fixtures and actual workbook; do not fabricate A–D assessment or research conclusions |
| ArchiPHYSIK energy output | §03.3.1 [416–417], §05.4 [620] | Legacy optional energy modules | Explicitly excluded from primary proof of concept; do not replace with a new energy engine |
| Regulatory certification and complete LCA | §03.4.2 [443–445], §05.4 | Simplified coefficient-based passport | No thesis-defined complete transport/operation/end-of-life coefficients or certified algorithm; retain explicit limits |
| Architectural quality ranking | §05.1 [594] | No universal ranking | Compare environmental/economic indicators separately; avoid inventing a composite architectural score |
| Normative area ratios and building-physics calculations | General physical-parameter goals; no case-study formulas defining GF/BGF/BRI | Legacy helper has suspect formulas | Preserve legacy refactor; provide measured inventory without asserting normative spatial metrics not defined by the thesis |

This map distinguishes established implementation requirements from research context and excluded scope.

## Final code and validation evidence

| Requirement group | Code in application repository | Evidence |
|---|---|---|
| Bottom-up calculations, element/material/building totals, averages, missing contributions | `apps/shared/ifc_extractor/material_assessment.py` | Original numerical suite plus `test_thesis_alignment.py`: handwritten year 0/50 values, explicit denominator cases, omitted-element invalidation |
| IFC validation, SI quantities, storeys, proxy coverage, provisional overlaps, source identity | `apps/shared/ifc_extractor/ifc_assessment.py`, `quantity_resolution.py` | Metre/mm fixtures, rotated geometry-only fallback, valid spatial containment, world-coordinate overlap candidates, material-bearing proxies, source bytes unchanged |
| Authentication, imports, persistence, owner-scoped CSV/JSON and multi-model comparison | `apps/shared/assessment_web.py`, plugin `api.py` / `passport_views.py` and `apps/shared/bolt_pages.py` | Existing six web tests plus four alignment web tests: matching/mismatching boundaries, foreign selection, runtime plugin gate, averaging required and retained |
| Graphical outputs, signed values, missing data, compatibility diagnostics | `apps/shared/assessment_presentation.py`, four shared passport templates | Signed-chart test distinguishes negative, zero and unavailable; browser inspection of report and comparison; accessible tables accompany charts |
| Authoritative workbook | User-selected `MP indicators and modfications.xlsx` | 115 records loaded. Actual-workbook IFC fixture: 8,250 kg initial mass and 729.5 global gross material cost. 16 records lack most coefficients; 17 lack service life. No source edits |
| Integrated application | Existing plugin-manager registration and Django route alongside Bolt | Development system check passed; **32 tests passed** in isolated database through project `uv`, including window/door subclass exclusions |
| Existing local migration and user state | Previously applied report migration; theme pending | No new model field or database migration needed for this phase. Existing backup and transaction-only persistence verification retained; Rust/API refactor and reference tree preserved |

The GUI averages default to no selection and require a deliberate choice. Lower-level calls may leave the policy unspecified, producing unavailable averages. Geometric overlap diagnostics are capped broad-phase bounding-box candidates; they are not solid-intersection validation. Storey metadata and missing containment are exposed, but semantic correctness of an assigned storey still requires model review. No normative GF/BGF/BRI formulas, energy certification, complete lifecycle coefficients or composite architectural score were invented.

The earlier scoped material-passport implementation, 19-test evidence and local migration are retained; this phase adds the established requirements that were missing from that narrower delivery. The reference snapshot remains authoritative for legacy extraction, while the original thesis governs scope and method. Earlier generic claims of completion were not treated as evidence of full implementation coverage.


## Fresh verification — 1 October 2026

Original thesis and revised-document hashes, workbook inputs, independent
arithmetic and the native workbook workflow are recorded in
`THESIS_FRESH_VALIDATION.json`. The fresh audit confirmed the bottom-up
material/element/building calculations, element-local combinations, signed
impacts, missing-data handling, pricing units, observation policies, normalized
indicators, report provenance and native page integration. The selected
inclusive year-50 boundary and installed-mass policies remain explicit choices,
not claims of unspecified normative rules.

The missing-volume false-completeness case was corrected and verified with
schema validation enabled. The active 3D viewer was restored through native
Bolt routes, checked over HTTP for all four exports, and inspected in Safari.
Source files were preserved; geometry viewing does not bypass assessment gates.
Final relevant suite: 48 Python tests, 7 viewer JavaScript tests, and a passing
isolated real Bolt HTTP workflow. See `BIM_WORKSPACE.md` for viewer details.
The earlier test counts above are historical validation, not the latest suite.

This proves the tested implementation behavior, not full A–D research results.
The supplied house assessment files fail strict schema validation, the workbook
contains missing values, overlaps remain provisional, and storey correctness
requires model review. Energy certification, complete lifecycle assessment and
normative spatial ratios remain outside the thesis-defined primary algorithm.
