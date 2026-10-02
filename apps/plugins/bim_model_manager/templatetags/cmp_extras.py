import os
# yourapp/templatetags/cmp_extras.py
from django import template
from math import isfinite

register = template.Library()

TOL = 1e-9  # float comparison tolerance


def _num(x) -> float | None:
    try:
        v = float(x)
        return v if isfinite(v) else None
    except Exception:
        return None


def _bm(doc):
    """
    Return the first BuildingMetrics for ``doc``, or None if there are none.

    Uses ``list(doc.building_metrics.all())`` instead of
    ``doc.building_metrics.first()`` because ``.first()`` forces an
    ``order_by("pk")`` clone that bypasses ``prefetch_related`` caching and
    triggers an extra query per document.
    """
    if not hasattr(doc, "building_metrics"):
        return None
    metrics = list(doc.building_metrics.all())
    return metrics[0] if metrics else None


@register.simple_tag
def bm_best_value(docs, field, mode: str="max"):
    """
    Return the best numeric value for BuildingMetrics.field across docs.
    mode: "max" or "min"
    """
    values = []
    for doc in docs:
        bm = _bm(doc)
        val = _num(getattr(bm, field, None)) if bm else None
        if val is not None:
            values.append(val)
    if not values:
        return ""
    return max(values) if mode == "max" else min(values)


@register.filter
def bm_value(doc, field):
    """Get a numeric (or raw) value from BuildingMetrics.field for one doc."""
    bm = _bm(doc)
    return getattr(bm, field, "") if bm else ""


@register.filter
def bm_unit(doc, field):
    """Get the '<field>_unit' value from BuildingMetrics for one doc."""
    bm = _bm(doc)
    return getattr(bm, f"{field}_unit", "") if bm else ""


@register.filter
def ratio_percent(value) -> float | None:
    """Convert a finite ratio to a percentage while preserving missing data."""
    numeric = _num(value)
    return numeric * 100.0 if numeric is not None else None


@register.filter
def is_best_value(value, best) -> bool:
    """
    True if value equals best within tolerance (for floats).

    Missing/unavailable values (``None`` or ``""``) are never considered
    "best", even when compared against another missing value, so a
    document without data is never highlighted.
    """
    if value is None or value == "" or best is None or best == "":
        return False
    try:
        v = float(value)
        b = float(best)
        if not (isfinite(v) and isfinite(b)):
            return False
        return abs(v - b) <= max(TOL, abs(b) * 1e-9)
    except Exception:
        return False


# ---- Material totals helpers ----

def _materials(doc):
    return getattr(doc, "material_properties", None).all() if hasattr(doc, "material_properties") else []


def _has_materials(doc) -> bool:
    """True if ``doc`` has at least one MaterialProperties record."""
    return bool(list(_materials(doc)))


def _sum_field(qs, field) -> float:
    total = 0.0
    for m in qs:
        v = _num(getattr(m, field, 0))
        if v is not None:
            total += v
    return total


@register.simple_tag
def mats_total(doc, field) -> float:
    """Sum a numeric field across doc.material_properties."""
    return _sum_field(_materials(doc), field)


@register.simple_tag
def mats_best_total(docs, field, mode: str="min"):
    """
    Best total across all docs for the material field.
    Defaults to 'min' because impacts are typically better lower.

    Documents with no MaterialProperties records at all are excluded from
    the comparison so a missing/zero-material document can never be
    highlighted as "best" simply for having nothing to report.
    """
    vals = []
    for doc in docs:
        if not _has_materials(doc):
            continue
        vals.append(_sum_field(_materials(doc), field))
    if not vals:
        return ""
    return min(vals) if mode == "min" else max(vals)
@register.filter
def basename(value):
    return os.path.basename(str(value)) if value else ""
