# Demo controls and house estimates

At `/demo`, previous/next buttons select adjacent chapters and pause playback. Buttons stop at the first/last chapter. Next house pauses playback and manually cycles A–D; Play, Restart, or chapter navigation restores the recorded scene selection.

House A–D values were pre-calculated from the original 28V_new IFC files with the June 2026 reference workbook, 50-year inclusive endpoint, installed-mass LCA denominator, mass recovery weighting and four geometry workers. Source and reference SHA-256 hashes were verified. Original IFCs, user records, and normal assessment validation are unchanged.

These are explicitly provisional demonstrations produced offline with `validate_schema=False`. All four originals fail strict schema/EXPRESS validation. Missing calculation inputs also prevent complete totals: UI retains unavailable totals and labels available contributions as known subtotals. Downloadable aggregate JSON retains completeness, issues/warnings counts, metric coverage, source/reference hashes and options; it excludes private user identifiers, IFC inventory and element-level details. B has a negative known GWP subtotal from reference coefficients; it is retained without implying a complete or validated result.

No assessments or writes run during playback. Existing geometry is reused. Eight playback tests cover stepping, endpoint behavior, house wraparound and timeline semantics.
