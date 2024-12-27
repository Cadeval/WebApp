from pprint import pprint

import plotly.graph_objs as go
import plotly.io as pio
from asgiref.sync import sync_to_async
from plotly.subplots import make_subplots

from model_manager.models import CadevilDocument


# def debug_show_plot(plt):


def single_building_metrics_pie(metrics_dict):
    pass


async def plot_mass(
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
    materials = await sync_to_async(
        lambda: list(ifc_document.material_properties.all())
    )()
    # Create the figure
    fig = go.Figure()
    fig.add_trace(
        go.Pie(
            labels=[material.name for material in materials],
            values=[material.mass for material in materials],
            hole=0.3,
            textinfo="label",
            rotation=190,
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
        title="Area by Material",
        xaxis_title="Materials",
        yaxis_title="Attribute Values",
        legend_title_text="Attributes",
        showlegend=False,
        margin=dict(t=40, b=40, l=0, r=0),
        legend=dict(x=0.8, y=0.5),  # positions the legend
    )

    # For local debugging
    # fig.show()

    html_plot: str = pio.to_html(fig, auto_play=True, div_id="mass_pie")

    return html_plot


async def plot_material_waste_grades(
        ifc_document: CadevilDocument,
        title="Material Attributes",
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
    materials = await sync_to_async(
        lambda: list(ifc_document.material_properties.all())
    )()

    # Create the figure with grouped bars
    fig = go.Figure()

    fig.add_trace(
        go.Pie(
            labels=[material.name for material in materials],
            values=[material.waste_mass for material in materials],
            hole=0.3,
            textinfo="label",
            hovertemplate="<b>%{label}</b><br>"
                          + "area: %{value:,.1f} m²<br>"
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
        margin=dict(t=40, b=40, l=0, r=0),
        legend=dict(x=0.8, y=0.5),  # positions the legend

    )

    # For local debugging
    # fig.show()

    html_plot: str = pio.to_html(fig, auto_play=True, div_id="material_plot")

    return html_plot


async def create_onorm_1800_visualization(
        ifc_document: CadevilDocument,
):
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
    building_metrics = await ifc_document.building_metrics.aget()

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
    fig = make_subplots(
        rows=2,
        cols=1,
        specs=[[{"type": "bar"}], [{"type": "pie"}]],  # Explicitly specify plot types
        subplot_titles=("Building Metrics Overview", "Floor Area Distribution"),
        vertical_spacing=0.3,
        row_heights=[0.7, 0.3],
    )

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
        row=1,
        col=1,
    )

    # Second subplot - Pie chart for area distribution
    # area_metrics = {k: v for k, v in metrics.items() if v["unit"] == "m²"}

    fig.add_trace(
        go.Pie(
            labels=[f"{k} ({metrics[k]['desc']})" for k in metrics.keys()],
            values=[metrics[k]["value"] for k in metrics.keys()],
            hole=0.3,
            textinfo="label+percent",
            hovertemplate="<b>%{label}</b><br>"
                          + "Area: %{value:,.1f} m²<br>"
                          + "Percentage: %{percent}<br>"
                          + "<extra></extra>",
        ),
        row=2,
        col=1,
    )

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
    )

    # Update axes
    fig.update_xaxes(title_text="Metrics", row=1, col=1)
    fig.update_yaxes(title_text="Value", row=1, col=1)

    html_plot: str = pio.to_html(fig, auto_play=True, div_id="1800_plot")

    return html_plot

# Example usage
# if __name__ == "__main__":

# Option 3: Save the plot as a static image
# pio.write_image(fig, file="../material_waste_grades.png")
