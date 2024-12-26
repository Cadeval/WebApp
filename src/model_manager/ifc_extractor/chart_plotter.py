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
    fig = go.Figure(data=materials)

    fig.add_trace(
        go.Pie(
            labels=[material.name for material in materials],
            values=[material.area for material in materials],
            hole=0.3,
            textinfo="label",
            rotation=190,
            margin=dict(t=0, b=0, l=0, r=0),
            hovertemplate="<b>%{label}</b><br>"
                          + "area: %{value:,.1f} m²<br>"
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

    # For local debugging
    # fig.show()

    html_plot: str = pio.to_html(fig, auto_play=True, div_id="material_plot")

    return html_plot


# async def plot_material_waste_grades(
#     ifc_document: CadevilDocument,
#     attributes_to_plot=None,
#     title="Material Attributes",
# ) -> str:
#     """
#     Create a bar chart of multiple attributes for different materials using Plotly.
#
#     Parameters:
#     -----------
#     material_data : defaultdict
#         A defaultdict with material names as keys and MaterialAccumulator instances as values
#     attributes_to_plot : list, optional
#         List of attribute names to plot. If None, plots all numeric attributes.
#     title : str, optional
#         Title of the plot
#
#     Returns:
#     --------
#     plotly.graph_objs._figure.Figure
#         A Plotly figure object ready to be displayed or saved
#     """
#     # If no attributes specified, use all numeric attributes
#     if attributes_to_plot is None:
#         # Get all numeric attributes from the MaterialAccumulator dataclass
#         attributes_to_plot = [
#             f.name
#             for f in material_accumulator.__dataclass_fields__.values()
#             if isinstance(f.default, (int, float))
#         ]
#
#     # Prepare data for plotting
#     materials = await sync_to_async(
#         lambda: list(ifc_document.material_properties.all())
#     )()
#     pprint(f">>?MATERIALS: {materials}")
#
#     # Create a list of traces, one for each attribute
#     traces = []
#     # for attr in attributes_to_plot:
#     #     # Extract values for the current attribute
#     #     values = [getattr(material, attr, 0) for material in materials]
#     #
#     #     # Create a bar trace for this attribute
#     #     trace = go.Bar(
#     #         name=attr,
#     #         x=materials,
#     #         y=values,
#     #         text=[f"{v:.2f}" for v in values],
#     #         textposition="auto",
#     #     )
#     #     traces.append(trace)
#     #     pprint(traces)
#
#     # Create a bar trace for this attribute
#     # trace = go.Bar(
#     #     name="Volume",
#     #     x=names,
#     #     y=values,
#     #     text=[f"{v:.2f}" for v in values],
#     #     textposition="auto",
#     # )
#     # traces.append(trace)
#
#     # Create the figure with grouped bars
#     fig = go.Figure(data=traces)
#
#     fig.add_trace(
#         go.Pie(
#             labels=[material.name for material in materials],
#             values=[material.mass for material in materials],
#             hole=0.3,
#             textinfo="label",
#             hovertemplate="<b>%{label}</b><br>"
#             + "area: %{value:,.1f} m²<br>"
#             + "percentage: %{percent}<br>"
#             + "<extra></extra>",
#         ),
#     )
#     fig.update_layout(margin=dict(t=0, b=0, l=0, r=0))
#     # traces.append(trace)
#     # pprint(traces)
#
#     # Customize the layout
#     # fig.update_layout(
#     #     title=title,
#     #     xaxis_title="Materials",
#     #     yaxis_title="Attribute Values",
#     #     xaxis_tickangle=-45,
#     #     height=600,
#     #     width=800,
#     #     barmode="group",  # This creates grouped bars
#     #     template="plotly_white",
#     #     legend_title_text="Attributes",
#     # )
#
#     # For local debugging
#     # fig.show()
#
#     html_plot: str = pio.to_html(fig, auto_play=True, div_id="material_plot")
#
#     return html_plot


def create_onorm_1800_visualization(data_dict):
    """
    Create a comprehensive visualization of ÖNORM 1800 building metrics.

    Parameters:
    -----------
    data_dict : dict
        Dictionary containing ÖNORM 1800 metrics

    Returns:
    --------
    plotly.graph_objs._figure.Figure
        A Plotly figure object visualizing the building metrics
    """
    # Prepare data for plotting
    metrics = {
        "GF": {
            "value": float(data_dict.get("GF2554.4 m2", "0").split()[0]),
            "unit": "m²",
            "desc": "Gross Floor Area",
        },
        "BF": {
            "value": float(data_dict.get("BF2554.4 m2", "0").split()[0]),
            "unit": "m²",
            "desc": "Building Footprint",
        },
        "UF": {
            "value": float(data_dict.get("UF0.0 m2", "0").split()[0]),
            "unit": "m²",
            "desc": "Usable Floor Area",
        },
        "BRI": {
            "value": float(data_dict.get("BRI25935.18 m3", "0").split()[0]),
            "unit": "m³",
            "desc": "Gross Building Volume",
        },
        "BGF": {
            "value": float(data_dict.get("BGF54391.77 m2", "0").split()[0]),
            "unit": "m²",
            "desc": "Heated Gross Floor Area",
        },
        "KGF": {
            "value": float(data_dict.get("KGF47754.07 m2", "0").split()[0]),
            "unit": "m²",
            "desc": "Cooled Gross Floor Area",
        },
        "NRF": {
            "value": float(data_dict.get("NRF6637.7 m2", "0").split()[0]),
            "unit": "m²",
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
    area_metrics = {k: v for k, v in metrics.items() if v["unit"] == "m²"}

    fig.add_trace(
        go.Pie(
            labels=[f"{k} ({metrics[k]['desc']})" for k in area_metrics.keys()],
            values=[metrics[k]["value"] for k in area_metrics.keys()],
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
        height=1000,
        width=1000,
        showlegend=False,
        template="plotly_white",
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
