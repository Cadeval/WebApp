# yourapp/templatetags/plotly_charts.py
from __future__ import annotations

from django.utils.safestring import SafeString
from django import template
from django.utils.safestring import mark_safe

import html
import plotly.graph_objects as go

register = template.Library()


def _first_bm(doc):
    """
    Return the first BuildingMetrics for ``doc``, or None if there are none.

    Uses ``list(doc.building_metrics.all())`` instead of
    ``doc.building_metrics.first()`` because ``.first()`` forces an
    ``order_by("pk")`` clone that bypasses ``prefetch_related`` caching and
    triggers an extra query per document.
    """
    try:
        metrics = list(doc.building_metrics.all())
        return metrics[0] if metrics else None
    except Exception:
        return None


def _sum_mats(doc, field: str) -> float:
    total = 0.0
    try:
        for m in doc.material_properties.all():
            val = getattr(m, field, 0) or 0
            total += float(val)
    except Exception:
        pass
    return total


@register.simple_tag
def plot_bgf_bar(docs) -> SafeString:
    """
    Bar chart: BGF per model.
    """
    labels, bgf_vals, bgf_units = [], [], []
    for d in docs:
        bm = _first_bm(d)
        if bm:
            labels.append(html.escape(d.description or "(untitled)"))
            bgf_vals.append(float(bm.brutto_grundfläche or 0))
            bgf_units.append(bm.brutto_grundfläche_unit or "m²")

    unit = bgf_units[0] if bgf_units else "m²"

    fig = go.Figure()
    fig.add_bar(x=labels, y=bgf_vals, name=f"BGF ({unit})")
    fig.update_layout(
        title="BGF Vergleich",
        xaxis_title="Modell",
        yaxis_title=unit,
        margin=dict(l=30, r=20, t=50, b=40),
    )
    plot_html = fig.to_html(full_html=False, include_plotlyjs=False)
    return mark_safe(plot_html)


@register.simple_tag
def plot_bgf_bri_scatter(docs) -> SafeString:
    """
    Scatter: BGF vs BRI, bubble size by NRF.
    """
    x_bgf, y_bri, size_nrf, labels = [], [], [], []
    x_unit = y_unit = ""
    for d in docs:
        bm = _first_bm(d)
        if not bm:
            continue
        labels.append(html.escape(d.description or "(untitled)"))
        x_bgf.append(float(bm.brutto_grundfläche or 0))
        y_bri.append(float(bm.brutto_rauminhalt or 0))
        size_nrf.append(float(bm.netto_raumfläche or 0))
        x_unit = bm.brutto_grundfläche_unit or "m²"
        y_unit = bm.brutto_rauminhalt_unit or "m³"

    # Normalize bubble size
    sizes = []
    if size_nrf:
        max_nrf = max(size_nrf) or 1.0
        sizes = [max(12.0, (v / max_nrf) * 40.0) for v in size_nrf]

    fig = go.Figure()
    fig.add_scatter(
        x=x_bgf,
        y=y_bri,
        mode="markers+text",
        text=labels,
        textposition="top center",
        marker=dict(size=sizes or 18),
        name="Modelle",
    )
    fig.update_layout(
        title="BGF vs. BRI (Bubble size = NRF)",
        xaxis_title=f"BGF ({x_unit})",
        yaxis_title=f"BRI ({y_unit})",
        margin=dict(l=30, r=20, t=50, b=40),
    )
    plot_html = fig.to_html(full_html=False, include_plotlyjs=False)
    return mark_safe(plot_html)


@register.simple_tag
def plot_material_mass_stacked(docs) -> SafeString:
    """
    Stacked bar: total material mass per model (sum of MaterialProperties.mass).
    """
    # Build a dict of {material_name: [mass per doc ...]}
    material_names = set()
    for d in docs:
        for m in getattr(d, "material_properties", []).all():
            if m.name:
                material_names.add(html.escape(m.name))
    material_names = sorted(material_names)

    doc_labels = [html.escape(d.description or "(untitled)") for d in docs]
    # Collect per material series
    series = {name: [] for name in material_names}
    for d in docs:
        mats = getattr(d, "material_properties", []).all()
        by_name = {(html.escape(m.name) if m.name else m.name): float(getattr(m, "mass", 0) or 0) for m in mats}
        for name in material_names:
            series[name].append(by_name.get(name, 0.0))

    fig = go.Figure()
    for name in material_names:
        fig.add_bar(name=name, x=doc_labels, y=series[name])

    fig.update_layout(
        barmode="stack",
        title="Material Mass by Model (kg)",
        xaxis_title="Model",
        yaxis_title="kg",
        margin=dict(l=30, r=20, t=50, b=50),
        legend=dict(orientation="h", yanchor="bottom", y=-0.2),
    )
    plot_html = fig.to_html(full_html=False, include_plotlyjs=False)
    return mark_safe(plot_html)


@register.simple_tag
def plot_gwp_grouped(docs) -> SafeString:
    """
    Bar chart: total GWP A1-A3 (sum of gwp_ml_a1_a3) per model.

    Extraction only ever populates A1-A3 material data (no lifecycle/B4
    figures), so this intentionally shows A1-A3 only.
    """
    doc_labels = [html.escape(d.description or "(untitled)") for d in docs]
    a1a3 = [_sum_mats(d, "gwp_ml_a1_a3") for d in docs]

    fig = go.Figure()
    fig.add_bar(name="GWP A1–A3", x=doc_labels, y=a1a3)
    fig.update_layout(
        title="GWP A1–A3 by Model (kg CO₂e)",
        xaxis_title="Model",
        yaxis_title="kg CO₂e",
        margin=dict(l=30, r=20, t=50, b=40),
    )
    plot_html = fig.to_html(full_html=False, include_plotlyjs=False)
    return mark_safe(plot_html)


@register.simple_tag
def plot_circularity_shares(docs) -> SafeString:
    """
    Grouped bar chart: Recyclable and Waste mass shares (%) per model.
    """
    doc_labels = []
    recyclable_shares = []
    waste_shares = []

    for d in docs:
        # Use already-computed comparison_stats if available (populated by view)
        stats = getattr(d, "comparison_stats", None)
        rec = was = None
        if stats:
            rec = stats.get("recyclable_mass_share")
            was = stats.get("waste_mass_share")

        if rec is not None and was is not None:
            doc_labels.append(html.escape(d.description or "(untitled)"))
            recyclable_shares.append(rec)
            waste_shares.append(was)
        else:
            # Fallback to prefetched materials (e.g. in tests)
            total_mass = _sum_mats(d, "mass")
            if total_mass > 0:
                doc_labels.append(html.escape(d.description or "(untitled)"))
                rec_mass = _sum_mats(d, "recyclable_mass")
                was_mass = _sum_mats(d, "waste_mass")
                # Calculate percentages
                recyclable_shares.append((rec_mass / total_mass) * 100.0)
                waste_shares.append((was_mass / total_mass) * 100.0)

    fig = go.Figure()
    if not doc_labels:
        # Render a clear in-chart unavailable state for empty data
        fig.update_layout(
            title="Circularity Shares (No Data Available)",
            margin=dict(l=30, r=20, t=50, b=40),
        )
        plot_html = fig.to_html(full_html=False, include_plotlyjs=False)
        return mark_safe(plot_html)

    fig.add_bar(name="Recyclable Mass Share", x=doc_labels, y=recyclable_shares)
    fig.add_bar(name="Waste Mass Share", x=doc_labels, y=waste_shares)

    fig.update_layout(
        barmode="group",
        title="Circularity Shares",
        xaxis_title="Model",
        yaxis_title="Percentage (%)",
        margin=dict(l=30, r=20, t=50, b=40),
    )
    plot_html = fig.to_html(full_html=False, include_plotlyjs=False)
    return mark_safe(plot_html)
