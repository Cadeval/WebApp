from functools import lru_cache
from pprint import pprint

import pandas as pd
import plotly.graph_objs as go
import plotly.io as pio
from asgiref.sync import sync_to_async
from django.db.models import QuerySet
from plotly.subplots import make_subplots

from ifc_extractor.helpers import prepare_comparison_data, float_or_zero
from model_manager.models import CadevilDocument


# def debug_show_plot(plt):


def single_building_metrics_pie(metrics_dict):
    pass


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
            labels=[material.name for material in materials],
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

    html_plot: str = pio.to_html(fig, auto_play=True, full_html=False, include_plotlyjs=False, include_mathjax=False,
                                 div_id="mass_pie", config={"responsive": True})

    return html_plot


@lru_cache(maxsize=1000)
def plot_material_waste_grades(
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
    materials = ifc_document.material_properties.all()

    # Create the figure with grouped bars
    fig = go.Figure()

    fig.add_trace(
        go.Pie(
            labels=[material.name for material in materials],
            values=[material.waste_mass for material in materials],
            hole=0.3,
            rotation=45,
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
        paper_bgcolor="hsla(210, 100%, 50%, 0.0)",
        autosize=True,  # Let Plotly size the chart based on the container
        # margin=dict(t=40, b=40, l=0, r=0),
        legend=dict(x=0.8, y=0.5),  # positions the legend

    )

    # For local debugging
    # fig.show()

    html_plot: str = pio.to_html(fig, auto_play=True, full_html=False, include_plotlyjs=False, include_mathjax=False,
                                 div_id="material_plot")

    return html_plot


@lru_cache(maxsize=1000)
def create_onorm_1800_visualization(
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

    html_plot: str = pio.to_html(fig, auto_play=True, full_html=False, include_plotlyjs=False, include_mathjax=False,
                                 div_id="1800_plot")

    return html_plot


@lru_cache(maxsize=1000)
def material_property_table(
        ifc_document_list: QuerySet[CadevilDocument],
):
    """
    Plots a comparison chart with a dropdown selector for material properties.
    """
    # List of material properties
    material_properties = [
        "global_brutto_price", "local_brutto_price", "local_netto_price",
        "volume", "area", "length", "mass", "penrt_ml", "gwp_ml", "ap_ml",
        "recyclable_mass", "waste_mass"
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
                    values=[initial_df[col].tolist() for col in initial_df.columns],
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
                                values=[temp_df[col].tolist() for col in temp_df.columns],
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
        print({
            "header.values": list(temp_df.columns),
            "cells.values": [temp_df[col].tolist() for col in temp_df.columns],
        })

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

    fig.show()
    # Show the plot
    html_plot: str = pio.to_html(fig, auto_play=True, full_html=False, include_plotlyjs=False, include_mathjax=False,
                                 div_id="property_table")

    return html_plot


def simulate_material_decay_plotly(ifc_document: CadevilDocument, config_dict: dict, years=100):
    material_data = ifc_document.material_properties.all()[7]
    material_name = material_data.name
    nutzungsdauer = float_or_zero(config_dict[material_name], "Nutzungsdauer")
    abfallreduktion = float_or_zero(config_dict[material_name], "Abfallreduktion") / 100  # Convert to fraction
    recycling = float_or_zero(config_dict[material_name], "Recycling") / 100  # Convert to fraction

    # Initialize simulation
    results = {"Year": [], "Material Left (%)": []}
    material_left = 100.0  # Start with 100% of the material

    for year in range(1, years + 1):
        if year % nutzungsdauer == 0:  # Apply reduction/recycling every Nutzungsdauer
            material_left = (
                    material_left * (1 - abfallreduktion) + material_left * recycling
            )
        results["Year"].append(year)
        results["Material Left (%)"].append(material_left)

    # Convert results to a DataFrame
    results_df = pd.DataFrame(results)

    # Create a Plotly figure
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=results_df["Year"],
            y=results_df["Material Left (%)"],
            mode="lines+markers",
            name=material_name
        )
    )
    fig.update_layout(
        title=f"Material Decay Simulation for {material_name}",
        xaxis_title="Year",
        yaxis_title="Material Left (%)",
        template="plotly_white"
    )

    # Show the plot
    fig.show()

    html_plot: str = pio.to_html(fig, auto_play=True, full_html=False, include_plotlyjs=False, include_mathjax=False,
                                 div_id="property_table")

    return html_plot
