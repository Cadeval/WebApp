# Reference-name neighbours and coefficient coverage

New reports rank up to three reference names for unmatched material labels using character-trigram TF-IDF cosine similarity. The corpus is the selected reference configuration; ties are deterministic. Suggestions below 0.2 are omitted. This is a display threshold, not a calibrated confidence boundary. Case/diacritic similarity can suggest an exact-looking label but does not auto-confirm it.

The UI retains exact-match versus unknown status, reference coefficient coverage and missing fields, and links source IFC elements. Unknown material rows remain unknown and retain unavailable numerical totals. No external classification service is called and no AI model is installed.

GLiNER-style multilingual classification/extraction could add material-family and composition features to retrieval. Accuracy must be measured on labelled German IFC materials before enabling automated mappings. Physical equivalence and numerical coefficients still require an authoritative reference.

A future model-inference cache should use exact request keys first, scoped by reference/configuration hash, model version, schema and method options. A semantic nearest-neighbour cache may retrieve prior candidates, but uncertain matches or conflicting composition/grade/density must re-run classification or require review. Similarity scores are not calibrated probabilities. Coefficient completeness is checked against the current reference on every use.
