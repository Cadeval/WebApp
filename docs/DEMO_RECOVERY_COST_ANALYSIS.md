# Recovery grade cost analysis

The demo groups initial global gross material costs in EUR by each material row's recovery grade (1–5). It does not multiply the total purchase cost by the mass-weighted building grade. Costs with no valid integer grade are kept in an ungraded category. This is a descriptive cost distribution, not a prediction of salvage revenue or lifecycle cost savings.

Each category shows its known cost subtotal, share of the overall known cost, and number of rows with unavailable prices. Missing prices are excluded from subtotals and never filled with zero; explicit zero prices are retained. A category's complete cost remains unavailable if any of its rows lacks a price. With no positive known cost, percentage shares are unavailable.

The breakdown is generated from the actual material-assessment rows using the same reference workbook and scenario as the existing demo recording. Every model's category subtotals reconcile to its existing initial global gross cost known subtotal within €0.01. A–D retain their provisional validation status. Both the visible analysis and downloadable model report switch with the selected geometry. Opening the demo performs no new assessment or database writes.

Recovery grades alone do not define resale prices, deconstruction charges, recycling processing costs or disposal fees. Those would require separate explicit monetary assumptions.

## Regular material passport reports

The same analysis is calculated by `apps/shared/ifc_extractor/recovery_costs.py` for new assessments and saved with the report. Regular authenticated passport pages display the six categories, complete category costs, known subtotals, percentage shares and missing-price counts. JSON downloads include the analysis; `?download=recovery_csv` exports the category table. All downloads retain the existing report owner check.

Historical reports with saved material rows are enriched on read in memory without modifying stored assessments or recalculating IFC geometry. Reports without material rows request recalculation. Normal strict schema validation is unchanged.

Validation: 29 assessment and report tests passed, including missing prices, explicit zero, invalid/ungraded grades, reconciliation, persistence, HTMX rendering, historic-report enrichment and cross-user download denial.
