import html
import math
from functools import lru_cache
from uuid import UUID

import pandas as pd
import plotly.graph_objs as go
import plotly.io as pio
from django.db.models import QuerySet

from .helpers import float_or_zero, prepare_comparison_data
from plugins.bim_model_manager.django.models import CadevilDocument, MaterialProperties


class RecyclingSimulationError(Exception):
    """Base class for recycling simulation errors."""


class MaterialNotFoundError(RecyclingSimulationError):
    """Raised when a specified material is not found in the document."""


def _recycling_unavailable(message: str) -> str:
    return (
        '<div class="model-detail__empty" role="status">'
        f"Recycling simulation unavailable: {html.escape(message)}"
        "</div>"
    )


def _select_simulation_material(
    ifc_document: CadevilDocument, material_id: str | UUID | None
) -> MaterialProperties | None:
    """
    Resolve the material a per-material simulation should run for.

    With an explicit ``material_id`` it must belong to ``ifc_document``
    (``MaterialNotFoundError`` is raised otherwise). Without one, the
    material with the highest mass is picked (ties broken by id for
    determinism). Returns ``None`` only when the document has no materials
    at all and no explicit id was requested.
    """
    if material_id is not None:
        try:
            return ifc_document.material_properties.get(id=material_id)
        except (MaterialProperties.DoesNotExist, ValueError, TypeError):
            raise MaterialNotFoundError(
                f"Material with ID {material_id} not found in document."
            ) from None

    materials = list(ifc_document.material_properties.all())
    if not materials:
        return None

    def material_sort_key(material: MaterialProperties) -> tuple[int, float, str]:
        try:
            mass = float(material.mass)
        except (TypeError, ValueError):
            mass = 0.0
        if math.isfinite(mass) and mass > 0:
            return 0, -mass, str(material.id)
        return 1, 0.0, str(material.id)

    return min(materials, key=material_sort_key)


def _resolve_material_config(
    config_dict: dict, material_name: str
) -> tuple[dict | None, str | None]:
    """Look up the per-material config block.

    Returns ``(m_config, None)`` on success, or ``(None, error_html)`` - an
    already-rendered 'unavailable' snippet - when the configuration is
    missing or malformed for this material.
    """
    if not isinstance(config_dict, dict) or material_name not in config_dict:
        return None, _recycling_unavailable(
            f"Configuration missing for '{material_name}'."
        )
    m_config = config_dict[material_name]
    if not isinstance(m_config, dict):
        return None, _recycling_unavailable(
            f"Configuration is invalid for '{material_name}'."
        )
    return m_config, None


def _resolve_service_life(
    m_config: dict, material_name: str
) -> tuple[int | None, str | None]:
    """Validate and return the material's ``Nutzungsdauer`` (service life, in
    whole years), or ``(None, error_html)`` when it is missing/invalid."""
    nutzungsdauer = float_or_zero(m_config, "Nutzungsdauer")
    if (
        not math.isfinite(nutzungsdauer)
        or nutzungsdauer <= 0
        or not nutzungsdauer.is_integer()
    ):
        return None, _recycling_unavailable(
            f"Invalid service life (Nutzungsdauer) for '{material_name}'."
        )
    return int(nutzungsdauer), None


def _clamped_rate(
    m_config: dict, key: str, *, minimum: float = 0.0, maximum: float = 1.0
) -> float:
    """Read a percentage config value and clamp it to a sane fraction.

    A missing or non-numeric value is treated as 0 (a no-op) rather than
    rejecting the whole simulation, so new, optional config columns stay
    backward compatible with configs that don't define them yet.
    """
    rate = float_or_zero(m_config, key) / 100
    if not math.isfinite(rate):
        return 0.0
    return max(minimum, min(maximum, rate))



@lru_cache(maxsize=1000)
def plot_mass(
    ifc_document: CadevilDocument,
) -> str:
    """
    Create a bar chart of multiple attributes for different materials using Plotly.

    Parameters:
    -----------
    ifc_document : CadevilDocument
        An instance of the calculated ifc properties
    Returns:
    --------
    plotly.graph_objs._figure.Figure
        A Plotly figure object ready to be displayed or saved
    """
    # Prepare data for plotting
    materials = ifc_document.material_properties.all()
    # Create the figure
    fig = go.Figure()
    fig.add_trace(
        go.Pie(
            labels=[html.escape(material.name) for material in materials],
            values=[material.mass for material in materials],
            hole=0.3,
            textinfo="label",
            rotation=45,
            hovertemplate="<b>%{label}</b><br>"
            + "mass: %{value:,.1f} kg<br>"
            + "percentage: %{percent}<br>"
            + "<extra></extra>",
        ),
    )

    # Customize the layout
    # fig.update_layout(
    #     title="Mass of Materials",
    #     margin=dict(t=0, b=0, l=0, r=0),
    #     xaxis_title="Materials",
    #     yaxis_title="Attribute Values",
    #     xaxis_tickangle=-45,
    #     height=600,
    #     width=800,
    #     barmode="group",  # This creates grouped bars
    #     template="plotly_white",
    #     legend_title_text="Attributes",
    # )
    fig.update_layout(
        title="Mass by Material",
        xaxis_title="Materials",
        yaxis_title="Attribute Values",
        legend_title_text="Attributes",
        showlegend=False,
        paper_bgcolor="hsla(210, 100%, 50%, 0.0)",
        autosize=True,  # Let Plotly size the chart based on the container
        # margin=dict(t=40, b=40, l=40, r=40),
        legend=dict(x=0.8, y=0.5),  # positions the legend
    )

    # For local debugging
    # fig.show()

    html_plot: str = pio.to_html(
        fig,
        auto_play=True,
        full_html=False,
        include_plotlyjs=False,
        include_mathjax=False,
        div_id="mass_pie",
        config={"responsive": True},
    )

    return html_plot


@lru_cache(maxsize=1000)
def plot_material_waste_grades(
    ifc_document: CadevilDocument,
) -> str:
    """
    Create a bar chart of multiple attributes for different materials using Plotly.

    Parameters:
    -----------
    ifc_document : CadevilDocument
        A CadevilDocument with material names as keys and MaterialAccumulator instances as values
    title : str, optional
        Title of the plot

    Returns:
    --------
    plotly.graph_objs._figure.Figure
        A Plotly figure object ready to be displayed or saved
    """

    # Prepare data for plotting
    materials = ifc_document.material_properties.all()

    # Create the figure with grouped bars
    fig = go.Figure()

    fig.add_trace(
        go.Pie(
            labels=[html.escape(material.name) for material in materials],
            values=[material.waste_mass for material in materials],
            hole=0.3,
            rotation=45,
            textinfo="label",
            hovertemplate="<b>%{label}</b><br>"
            + "mass: %{value:,.1f} kg<br>"
            + "percentage: %{percent}<br>"
            + "<extra></extra>",
        ),
    )
    # traces.append(trace)
    # pprint(traces)

    # Customize the layout
    fig.update_layout(
        title="Mass of Material Waste",
        xaxis_title="Materials",
        yaxis_title="Attribute Values",
        legend_title_text="Attributes",
        showlegend=False,
        paper_bgcolor="hsla(210, 100%, 50%, 0.0)",
        autosize=True,  # Let Plotly size the chart based on the container
        # margin=dict(t=40, b=40, l=0, r=0),
        legend=dict(x=0.8, y=0.5),  # positions the legend
    )

    # For local debugging
    # fig.show()

    html_plot: str = pio.to_html(
        fig,
        auto_play=True,
        full_html=False,
        include_plotlyjs=False,
        include_mathjax=False,
        div_id="material_plot",
    )

    return html_plot


@lru_cache(maxsize=1000)
def plot_material_costs(
    ifc_document: CadevilDocument,
) -> str:
    """
    Create a grouped bar chart comparing the global gross, local gross, and
    local net price of every material in the document using Plotly.

    Parameters:
    -----------
    ifc_document : CadevilDocument
        An instance of the calculated ifc properties

    Returns:
    --------
    plotly.graph_objs._figure.Figure
        A Plotly figure object ready to be displayed or saved
    """
    materials = list(ifc_document.material_properties.all())
    material_names = [html.escape(material.name) for material in materials]

    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            name="Global gross price",
            x=material_names,
            y=[material.global_brutto_price for material in materials],
            hovertemplate="<b>%{x}</b><br>Global gross price: %{y:,.2f}<extra></extra>",
        ),
    )
    fig.add_trace(
        go.Bar(
            name="Local gross price",
            x=material_names,
            y=[material.local_brutto_price for material in materials],
            hovertemplate="<b>%{x}</b><br>Local gross price: %{y:,.2f}<extra></extra>",
        ),
    )
    fig.add_trace(
        go.Bar(
            name="Local net price",
            x=material_names,
            y=[material.local_netto_price for material in materials],
            hovertemplate="<b>%{x}</b><br>Local net price: %{y:,.2f}<extra></extra>",
        ),
    )

    total_local_gross_cost = sum(
        material.local_brutto_price or 0.0 for material in materials
    )

    fig.update_layout(
        title=f"Cost by Material (total local gross: {total_local_gross_cost:,.2f})",
        xaxis_title="Materials",
        yaxis_title="Cost",
        legend_title_text="Price type",
        barmode="group",
        paper_bgcolor="hsla(210, 100%, 50%, 0.0)",
        autosize=True,
    )

    html_plot: str = pio.to_html(
        fig,
        auto_play=True,
        full_html=False,
        include_plotlyjs=False,
        include_mathjax=False,
        div_id="material_cost_plot",
        config={"responsive": True},
    )

    return html_plot


@lru_cache(maxsize=1000)
def create_onorm_1800_visualization(
    ifc_document: CadevilDocument,
) -> str:
    """
    Create a comprehensive visualization of ÖNORM 1800 building metrics.

    Parameters:
    -----------
    ifc_document : CadevilDocument
        A CadevilDocument with material names as keys and MaterialAccumulator instances as values

    Returns:
    --------
    plotly.graph_objs._figure.Figure
        A Plotly figure object visualizing the building metrics
    """
    # Prepare data for plotting
    building_metrics = ifc_document.building_metrics.get()

    metrics = {
        "GF": {
            "value": building_metrics.grundstuecksfläche,
            "unit": building_metrics.grundstuecksfläche_unit,
            "desc": "Gross Floor Area",
        },
        "BF": {
            "value": building_metrics.bebaute_fläche,
            "unit": building_metrics.bebaute_fläche_unit,
            "desc": "Building Footprint",
        },
        "UF": {
            "value": building_metrics.unbebaute_fläche,
            "unit": building_metrics.unbebaute_fläche_unit,
            "desc": "Usable Floor Area",
        },
        "BRI": {
            "value": building_metrics.brutto_rauminhalt,
            "unit": building_metrics.brutto_rauminhalt_unit,
            "desc": "Gross Building Volume",
        },
        "BGF": {
            "value": building_metrics.brutto_grundfläche,
            "unit": building_metrics.brutto_grundfläche_unit,
            "desc": "Heated Gross Floor Area",
        },
        "KGF": {
            "value": building_metrics.konstruktions_grundfläche,
            "unit": building_metrics.konstruktions_grundfläche_unit,
            "desc": "Cooled Gross Floor Area",
        },
        "NRF": {
            "value": building_metrics.netto_raumfläche,
            "unit": building_metrics.netto_raumfläche_unit,
            "desc": "Net Room Floor Area",
        },
    }

    # Create figure with secondary y-axis
    # fig = make_subplots(
    #     rows=2,
    #     cols=1,
    #     specs=[[{"type": "bar"}], [{"type": "pie"}]],  # Explicitly specify plot types
    #     subplot_titles=("Building Metrics Overview", "Floor Area Distribution"),
    #     vertical_spacing=0.3,
    #     row_heights=[0.7, 0.3],
    # )
    fig = go.Figure()

    # First subplot - Bar chart with metrics
    metric_names = list(metrics.keys())
    metric_values = [metrics[m]["value"] for m in metric_names]

    # Add bar chart
    fig.add_trace(
        go.Bar(
            name="Building Metrics",
            x=metric_names,
            y=metric_values,
            text=[
                f"{v:,.1f} {metrics[m]['unit']}"
                for m, v in zip(metric_names, metric_values)
            ],
            textposition="outside",
            hovertemplate="<b>%{x}</b><br>"
            + "Value: %{y:,.1f}<br>"
            + "<extra></extra>",
        ),
    )

    # Second subplot - Pie chart for area distribution
    # area_metrics = {k: v for k, v in metrics.items() if v["unit"] == "m²"}

    # fig.add_trace(
    #     go.Pie(
    #         labels=[f"{k} ({metrics[k]['desc']})" for k in metrics.keys()],
    #         values=[metrics[k]["value"] for k in metrics.keys()],
    #         hole=0.3,
    #         textinfo="label+percent",
    #         hovertemplate="<b>%{label}</b><br>"
    #                       + "Area: %{value:,.1f} m²<br>"
    #                       + "Percentage: %{percent}<br>"
    #                       + "<extra></extra>",
    #     ),
    #     row=2,
    #     col=1,
    # )

    # Update layout
    fig.update_layout(
        title={
            "text": "ÖNORM 1800 Building Metrics",
            "y": 0.95,
            "x": 0.5,
            "xanchor": "center",
            "yanchor": "top",
        },
        showlegend=False,
        paper_bgcolor="hsla(210, 100%, 50%, 0.0)",
    )

    # Update axes
    fig.update_xaxes(title_text="Metrics")
    fig.update_yaxes(title_text="Value")

    html_plot: str = pio.to_html(
        fig,
        auto_play=True,
        full_html=False,
        include_plotlyjs=False,
        include_mathjax=False,
        div_id="1800_plot",
    )

    return html_plot


@lru_cache(maxsize=1000)
def material_property_table(
    ifc_document_list: QuerySet[CadevilDocument],
) -> str:
    """
    Plots a comparison chart with a dropdown selector for material properties.
    """
    # List of material properties
    material_properties = [
        "global_brutto_price",
        "local_brutto_price",
        "local_netto_price",
        "volume",
        "area",
        "length",
        "mass",
        "penrt_ml_a1_a3",
        "gwp_ml_a1_a3",
        "ap_ml_a1_a3",
        "recyclable_mass",
        "waste_mass",
    ]

    # Precompute data frames for all properties
    property_data_frames = {}
    for property_name in material_properties:
        df = prepare_comparison_data(ifc_document_list, property_name)

        # Debug: Log the data retrieved for this property
        print(f"Data for {property_name}:")
        print(df)

        # Check if the data is valid
        if df.isnull().values.any():
            print(f"Warning: Missing data for property {property_name}")

        property_data_frames[property_name] = df

    # Use the first property for the initial table
    initial_property = material_properties[0]
    initial_df = property_data_frames[initial_property]

    # Initialize the table with the first property
    fig = go.Figure(
        data=[
            go.Table(
                header=dict(
                    values=list(initial_df.columns),
                    fill_color="lightgrey",
                    align="center",
                ),
                cells=dict(
                    values=[
                        [
                            html.escape(str(v))
                            if col == "Material Name" and isinstance(v, str)
                            else v
                            for v in initial_df[col].tolist()
                        ]
                        for col in initial_df.columns
                    ],
                    fill_color="white",
                    align="center",
                ),
            )
        ]
    )

    # Add dropdown buttons for each property
    dropdown_buttons = []
    for property_name, temp_df in property_data_frames.items():
        dropdown_buttons.append(
            dict(
                args=[
                    [  # Replace figure's data
                        go.Table(
                            header=dict(
                                values=list(temp_df.columns),
                                fill_color="lightgrey",
                                align="center",
                            ),
                            cells=dict(
                                values=[
                                    [
                                        html.escape(str(v))
                                        if col == "Material Name" and isinstance(v, str)
                                        else v
                                        for v in temp_df[col].tolist()
                                    ]
                                    for col in temp_df.columns
                                ],
                                fill_color="white",
                                align="center",
                            ),
                        )
                    ]
                ],
                label=property_name,
                method="update",  # Replace the whole figure's data
            )
        )

        # Debug: Log dropdown button configuration
        print(f"Dropdown Button for {property_name}:")
        print(
            {
                "header.values": list(temp_df.columns),
                "cells.values": [temp_df[col].tolist() for col in temp_df.columns],
            }
        )

    # Add the dropdown menu to the layout
    fig.update_layout(
        updatemenus=[
            dict(
                buttons=dropdown_buttons,
                direction="down",
                showactive=True,
                x=0.5,
                xanchor="center",
                y=1.1,
                yanchor="top",
            )
        ],
        title="Comparison of Material Properties Across Documents",
    )

    # fig.show()
    # Show the plot
    html_plot: str = pio.to_html(
        fig,
        auto_play=True,
        full_html=False,
        include_plotlyjs=False,
        include_mathjax=False,
        div_id="property_table",
    )

    return html_plot


def simulate_material_decay_plotly(
    ifc_document: CadevilDocument,
    config_dict: dict,
    years: int = 50,
    material_id: str | UUID | None = None,
) -> str:
    """
    Simulates material decay/recycling over time and returns a Plotly chart as HTML.
    """
    if not isinstance(years, int) or years <= 0:
        raise ValueError("Simulation duration 'years' must be a positive integer.")

    material_data = _select_simulation_material(ifc_document, material_id)
    if material_data is None:
        return _recycling_unavailable("No materials found in document.")

    material_name = material_data.name
    escaped_material_name = html.escape(material_name)
    material_uuid = str(material_data.id)

    m_config, error = _resolve_material_config(config_dict, material_name)
    if error is not None:
        return error

    service_life, error = _resolve_service_life(m_config, material_name)
    if error is not None:
        return error

    abfallreduktion = _clamped_rate(m_config, "Abfallreduktion")
    neu_abfallreduktion = _clamped_rate(m_config, "Neu Abfallreduktion")
    recycling = _clamped_rate(m_config, "Recycling")
    neu_recycling = _clamped_rate(m_config, "Neu Recycling")

    results = {"Year": [0], "Material Left (%)": [100.0]}
    material_left = 100.0

    for year in range(1, years + 1):
        if year % service_life == 0:
            if year == service_life:
                material_left = (
                    material_left * (1 - neu_abfallreduktion)
                    + material_left * neu_recycling
                )
            else:
                material_left = (
                    material_left * (1 - abfallreduktion) + material_left * recycling
                )

            material_left = (
                max(0.0, min(100.0, material_left))
                if math.isfinite(material_left)
                else 0.0
            )

        results["Year"].append(year)
        results["Material Left (%)"].append(material_left)

    results_df = pd.DataFrame(results)

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=results_df["Year"],
            y=results_df["Material Left (%)"],
            mode="lines+markers",
            name=f"{escaped_material_name} decay",
        )
    )

    div_id = f"decay_simulation_{material_uuid.replace('-', '_')}"

    fig.update_layout(
        title=f"Material Recycling Simulation: {escaped_material_name}",
        xaxis_title="Time (Years)",
        yaxis_title="Material Remaining (%)",
        template="plotly_white",
        autosize=True,
    )

    html_plot: str = pio.to_html(
        fig,
        auto_play=False,
        full_html=False,
        include_plotlyjs=False,
        include_mathjax=False,
        div_id=div_id,
        config={"displaylogo": False, "responsive": True},
    )

    return html_plot


def simulate_material_cost_projection_plotly(
    ifc_document: CadevilDocument,
    config_dict: dict,
    years: int = 50,
    material_id: str | UUID | None = None,
) -> str:
    """
    Projects a material's rising life-cycle cost over time and returns a
    Plotly chart as HTML.

    The material is assumed to be replaced every ``Nutzungsdauer`` (service
    life) years - the same replacement schedule used by
    ``simulate_material_decay_plotly`` - each time at its local gross price
    (``MaterialProperties.local_brutto_price``, already the total price for
    the material's installed quantity) escalated by the material's
    configured annual "Preissteigerung" (price increase) rate. That column
    is optional: configs uploaded before this feature existed simply have no
    escalation applied (0%), so they keep working unchanged. The chart plots
    the *cumulative* amount spent on the material so far, which only ever
    grows, giving a clear "rising costs over time" picture of its life-cycle
    cost.
    """
    if not isinstance(years, int) or years <= 0:
        raise ValueError("Simulation duration 'years' must be a positive integer.")

    material_data = _select_simulation_material(ifc_document, material_id)
    if material_data is None:
        return _recycling_unavailable("No materials found in document.")

    material_name = material_data.name
    escaped_material_name = html.escape(material_name)
    material_uuid = str(material_data.id)

    m_config, error = _resolve_material_config(config_dict, material_name)
    if error is not None:
        return error

    service_life, error = _resolve_service_life(m_config, material_name)
    if error is not None:
        return error

    # Optional: absent for configs that predate this feature, and simply
    # means "no price escalation" rather than blocking the projection the
    # way a missing/invalid service life does.
    escalation_rate = _clamped_rate(m_config, "Preissteigerung")

    try:
        base_cost = float(material_data.local_brutto_price)
    except (TypeError, ValueError):
        base_cost = 0.0
    if not math.isfinite(base_cost) or base_cost < 0:
        base_cost = 0.0

    results = {"Year": [0], "Cumulative Cost": [base_cost]}
    cumulative_cost = base_cost

    for year in range(1, years + 1):
        if year % service_life == 0:
            try:
                replacement_cost = base_cost * (1 + escalation_rate) ** year
            except OverflowError:
                replacement_cost = math.inf
            if math.isfinite(replacement_cost):
                cumulative_cost += replacement_cost

        results["Year"].append(year)
        results["Cumulative Cost"].append(cumulative_cost)

    results_df = pd.DataFrame(results)

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=results_df["Year"],
            y=results_df["Cumulative Cost"],
            mode="lines+markers",
            line_shape="hv",
            name=f"{escaped_material_name} cost",
        )
    )

    div_id = f"cost_projection_{material_uuid.replace('-', '_')}"

    fig.update_layout(
        title=f"Rising Cost Over Time: {escaped_material_name}",
        xaxis_title="Time (Years)",
        yaxis_title="Cumulative Cost",
        template="plotly_white",
        autosize=True,
    )

    html_plot: str = pio.to_html(
        fig,
        auto_play=False,
        full_html=False,
        include_plotlyjs=False,
        include_mathjax=False,
        div_id=div_id,
        config={"displaylogo": False, "responsive": True},
    )

    return html_plot
