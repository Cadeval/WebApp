# Thesis material passport implementation

The thesis is the calculation specification. The supplied MP indicators and
modifications workbook supplies reference values, including recovery percentages,
semicolon-separated combination lists, service lives, and prices per m², m³, m and kg.
The workbook is imported without changing it. Blank coefficients remain unavailable.

Calculation code is migrated from `reference/_src/ifc_extractor` into
`plugins/bim_model_manager/ifc_extractor`. The original reference snapshot is retained. The
existing BIM plugin contributes a Material Passport menu item, and its enabled
state gates the calculation, report and comparison routes at runtime. Existing Rust/Bolt API
work is preserved. The assessment route uses normal Django request handling,
authentication and CSRF checks alongside the existing API; computational functions
are independent of that transport so the API can call the same implementation.

## Calculations

Every element/material occurrence is retained with its IFC identifier. Volume ×
density gives mass. Mass × GWP/AP/PENRT gives initial A1–A3 quantities. At full
replacement events, the same mass and production impacts are added to the selected observation-period
inventory and A1–A3 plus B4 totals. The report separates initial quantities, B4
contributions, cumulative material consumption, waste and recoverable masses.
It does not invent transport, operational or end-of-life emission coefficients.

Recovery coefficients are percentages, as in the authoritative workbook. Base
coefficients apply unless a configured companion material is present in the same
element. Any listed companion triggers the adjusted `NEU` coefficients. The lists
are separated by semicolons so material names containing commas remain intact.
The general label Beton matches concrete-family names; exact names and explicit
wildcard patterns are also supported. Co-presence is the thesis's rule input; it
does not independently prove physical bonding or separability.

Unit prices use their declared quantity basis. Standard IFC areas and lengths are
preferred over geometry fallbacks. SI conversion applies to exported quantity
sets. Shared element areas/lengths remain estimates for layer-specific costing and
are recorded as such. Windows and doors are excluded from material calculations.

## Policies not specified by the thesis

The calculation page requires the user to choose whether replacement exactly at
the selected observation endpoint is included, and whether descriptive material grades are averaged equally
or weighted by installed mass. It also requires an LCA averaging denominator:
initial installed mass (for both periods), or the count of distinct assessed
material names for arithmetic means of material totals. These choices have different
units and neither is silently attributed to the thesis. The configuration and every report retain the choices.
The lower-level library has the documented default of excluding the endpoint; an
unspecified building-grade policy produces an unavailable grade, not a guessed one.
Unspecified LCA averaging similarly produces unavailable averages.
The optional OI3 replacement policy is separate from the thesis default and needs
the required fossil/biogenic coefficients. It is never selected by the web form.
The simplified passport is not advertised as certified OI3, EI10 or ÖNORM compliance.

## Validation and reporting

IFC schema/EXPRESS validation runs before analysis and records nonfatal warnings. Calculations continue, and reports with validation issues are explicitly provisional. Unreadable IFC files and invalid reference data still produce actionable errors. Duplicate reference keys are rejected.
Unmatched materials and missing numeric coefficients remain visible in the element
inventory. A total with any unavailable contribution is unavailable; a separate
known subtotal is retained in the JSON report and never presented as a full total.
An element omitted for missing quantities invalidates full building totals, retaining
known subtotals. Material-bearing proxies are assessed; opening voids are excluded.
Each report stores IFC/reference/configuration hashes, the configuration snapshot,
separate core/adapter/quantity-resolution code hashes,
exclusions, assumptions and quantity diagnostics.

The BIM plugin owns `BuildingMetrics` and nullable material results under its
`bim_model_manager` Django app label. The initial migrations live in the plugin's
`django/migrations/` namespace. Application 0.15.0 starts a fresh schema; initialize
an empty Cadevil database rather than applying it over an existing 0.14 database.
Keep an independent backup, recreate login access and import the inputs needed
for new assessments. Calculations produce new reports with coverage diagnostics.

## Verification

Tests exercise handwritten expected outcomes, combination adjustments confined to
their element, negative GWP, blank versus zero, unmatched records, all cost bases,
replacement boundaries, window/door exclusion, and actual IFCs in metres and
millimetres. Web tests cover authentication, plugin gating, upload/import/calculation,
saved results, report download, input ownership and rejection of invalid input.
No four-house performance result is claimed merely from passing these tests.

## Full thesis audit and presentation

The original thesis remains authoritative across its introduction, methodology,
case-study boundary and discussion. `THESIS_REQUIREMENTS.md` maps requirements to
code and validation. The broader reporting phase retained the earlier calculations
and recorded its local report migration, database backup and Rust/Bolt work.
The verification counts below describe those historical phases; current persistence
uses the plugin-owned schema introduced in 0.15.0.

Reports now include element summaries, geometric/semantic inventory, storey identity
and elevation, CSV export, all three cost totals, initial and observation-period
impact averages and signed SVG charts with numerical tables. Small chart values
use significant digits rather than rounding nonzero impacts to zero.

`/plugins/bim/material-passport/compare/` is authenticated, plugin-gated and owner-scoped.
It compares mass, waste, recycling, GWP/AP/PENRT, costs and descriptive grades.
Different reference configurations, boundaries or selected policies produce explicit
compatibility diagnostics; incomplete and historical reports are also flagged.
No composite architectural ranking is calculated.

Missing storey containment is reported. World-coordinate positive-volume bounding-box
intersections identify potential overlaps for manual review, excluding face contact.
This is a broad-phase candidate check, capped at 200 with truncation recorded, not
solid-intersection validation or verification of correct semantic storey assignment.
The IfcOpenShell owning shape remains alive while geometry buffers are read.
Area/length fallback measurements retain element-local axes under placement rotation;
only overlap bounds use world-coordinate vertices.

Final validation: 32 core/web tests pass; development Django system check passes.
The authoritative 115-record workbook was exercised with an IFC fixture yielding
8,250 kg installed mass and 729.5 global gross material cost. Sixteen workbook
records lack most coefficients and seventeen lack service life. Missing entries
remain unavailable. These are fixture checks, not A–D research results.

## Configurable period and selected case audit

The form and CLI accept a positive integer observation span, defaulting to 50
years for older form requests. Endpoint labels and report columns use the selected
span. The CLI accepts `--years`; the saved options preserve the period. A regression
checks 100-year replacement results, persistence and labels and rejects a zero span.
The complete core and web suite passed 33 tests.

The selected newer A–D exports were assessed with the June 2026 workbook, 50 years,
endpoint included, mass-weighted grades and installed-mass LCA normalization.
They all failed schema/EXPRESS validation before numerical assessment: A 22,674
diagnostics; B 28,535; C V3 16,122; D 21,373. These counts refer to validator
messages, not unique affected building elements. No complete A–D result is claimed.
Original models and reference data were preserved. The revised thesis records
these outcomes separately from passing software fixtures and preliminary name
matching. Corrected case exports and independent quantity checks remain necessary
for a complete building comparison.
