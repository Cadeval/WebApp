# -*- coding: utf-8 -*-
import gc
import multiprocessing
import os
import time
import pprint
from collections import defaultdict

from ifcopenshell.geom import iterator

from model_manager.models import (
    BuildingMetrics,
    MaterialProperties,
)
from webapp.logger import InMemoryLogHandler

# try:
import ifcopenshell.api
import ifcopenshell.express
import ifcopenshell.express.rules
import ifcopenshell.file
import ifcopenshell.geom
import ifcopenshell.ifcopenshell_wrapper
import ifcopenshell.util
import ifcopenshell.util.classification
import ifcopenshell.util.cost
import ifcopenshell.util.element
import ifcopenshell.util.geolocation
import ifcopenshell.util.placement
import ifcopenshell.util.representation
import ifcopenshell.util.schema
import ifcopenshell.util.selector
import ifcopenshell.util.shape
import ifcopenshell.util.unit

# except ImportError:
#     pprint(
#         """Cannot import ifcopenshell. This is necessary to run the program.
#            Please install it via pip or conda and retry."""
#     )
#     exit(1)


DEBUG = True
DEBUG_VERBOSE = False

"""
    "IfcWall",
    "IfcColumn",
    "IfcBeam",
    "IfcSlab",
    "IfcCurtainWall",
    "IfcWindow",
    "IfcDoor",
"""

def create_plan_svg_bboxes(ifc_path, svg_size=1000, margin=20):
    """
    Creates a 'blueprint-style' SVG top-down bounding box plan of the ground floor
    from the given IFC file, color-coding elements by type and styling it like a blueprint.

    :param ifc_path: path to the IFC file
    :param svg_size: width/height (pixels) of the SVG canvas
    :param margin: padding around the drawing in the SVG
    :return: string containing the entire SVG markup
    """
    filename = os.path.basename(ifc_path)  # just the file name
    ifc_file = ifcopenshell.open(ifc_path)
    storeys = ifc_file.by_type("IfcBuildingStorey")

    if not storeys:
        return "No storeys found in IFC."

    # Pick the first storey as default, or specifically the one with Elevation=0
    ground_floor = storeys[0]
    for st in storeys:
        if st.get_info().get("Elevation", None) == 0:
            ground_floor = st
            break

    # Decomposition to get all elements on this storey
    ground_floor_elements = ifcopenshell.util.element.get_decomposition(
        element=ground_floor, is_recursive=True
    )

    # Geometry settings (triangulation, no OpenCascade)
    settings = ifcopenshell.geom.settings()
    settings.set(settings.USE_PYTHON_OPENCASCADE, False)

    product_bboxes = {}
    all_xmin = float("inf")
    all_ymin = float("inf")
    all_xmax = float("-inf")
    all_ymax = float("-inf")

    # Gather bounding boxes
    for product in ground_floor_elements:
        if not hasattr(product, "Representation"):
            continue

        try:
            shape = ifcopenshell.geom.create_shape(settings, product)
        except:
            continue

        if not shape or not shape.geometry:
            continue

        verts = shape.geometry.verts
        if not verts:
            continue

        pminx = float("inf")
        pmaxx = float("-inf")
        pminy = float("inf")
        pmaxy = float("-inf")

        for i in range(0, len(verts), 3):
            x = verts[i + 0]
            y = verts[i + 1]
            if x < pminx: pminx = x
            if x > pmaxx: pmaxx = x
            if y < pminy: pminy = y
            if y > pmaxy: pmaxy = y

        if pminx < pmaxx and pminy < pmaxy:
            product_bboxes[product] = (pminx, pminy, pmaxx, pmaxy)
            if pminx < all_xmin: all_xmin = pminx
            if pmaxx > all_xmax: all_xmax = pmaxx
            if pminy < all_ymin: all_ymin = pminy
            if pmaxy > all_ymax: all_ymax = pmaxy

    if not product_bboxes:
        return "No geometric bounding boxes found for this storey."

    width = all_xmax - all_xmin
    height = all_ymax - all_ymin
    if width <= 0 or height <= 0:
        return "Degenerate bounding box (all geometry in a single line or point)."

    # Scale to fit
    scale_x = (svg_size - 2 * margin) / width
    scale_y = (svg_size - 2 * margin) / height
    scale_factor = min(scale_x, scale_y)

    def world_to_svg(px, py):
        sx = (px - all_xmin) * scale_factor + margin
        sy = (all_ymax - py) * scale_factor + margin
        return sx, sy

    # -----------
    # Color logic
    # -----------
    # You can expand this dictionary with more IFC classes or customize the colors:
    colors_by_type = {
        "IfcWall": "#66ccff",  # Light cyan
        "IfcDoor": "#ffcc00",  # Yellowish
        "IfcWindow": "#ff66ff",  # Pinkish
        "IfcSlab": "#99ff99",  # Light green
        "IfcBeam": "#ff9966",  # Orange
        "IfcColumn": "#ff6699",  # Pink
        "IfcStair": "#ccccff",  # Light violet
        "IfcFlowTerminal": "#ffd966",  # Golden
        # fallback color if not in dict:
    }
    default_color = "#ffffff"  # white lines for unknown types

    # -----------
    # Build SVG
    # -----------
    svg_rects = []
    svg_texts = []

    for product, (pminx, pminy, pmaxx, pmaxy) in product_bboxes.items():
        sx_min, sy_max = world_to_svg(pminx, pminy)
        sx_max, sy_min = world_to_svg(pmaxx, pmaxy)
        rect_width = abs(sx_max - sx_min)
        rect_height = abs(sy_max - sy_min)

        top_left_x = min(sx_min, sx_max)
        top_left_y = min(sy_min, sy_max)

        # Determine color by product type
        ifctype = product.is_a()
        stroke_color = colors_by_type.get(ifctype, default_color)

        # Slight fill with opacity for differentiation
        # (a faint version of the stroke color)
        fill_color = stroke_color
        fill_opacity = 0.1  # faint fill so we can see overlapping

        # Product label
        label_text = (
            f"{ifctype} : "
            f"{getattr(product, 'Name', '') or getattr(product, 'Tag', '') or product.GlobalId}"
        )

        # Rect element
        rect_elem = (
            f'<rect x="{top_left_x:.1f}" y="{top_left_y:.1f}" '
            f'width="{rect_width:.1f}" height="{rect_height:.1f}" '
            f'fill="{fill_color}" fill-opacity="{fill_opacity}" '
            f'stroke="{stroke_color}" stroke-width="2" '
            f'vector-effect="non-scaling-stroke" />'
        )
        svg_rects.append(rect_elem)

        # Center of bounding box for text
        cx = (sx_min + sx_max) / 2
        cy = (sy_min + sy_max) / 2

        # Escape special characters
        label_escaped = label_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        # Text
        text_elem = (
            f'<text x="{cx:.1f}" y="{cy:.1f}" '
            f'fill="#fff" font-size="12" font-weight="bold" '
            f'text-anchor="middle" alignment-baseline="middle">'
            f'{label_escaped}</text>'
        )
        svg_texts.append(text_elem)

    # Blueprint background: a dark navy/blue
    blueprint_bg = "#002b36"  # or #001f3f, #0f1420, etc.

    svg_header = (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{svg_size}" height="{svg_size}" '
        f'viewBox="0 0 {svg_size} {svg_size}" '
        f'style="background: {blueprint_bg};">\n'
        f'  <title>Blueprint Ground Floor Plan</title>\n'
    )
    svg_footer = "</svg>"

    svg_title = f'  <title>2D Down Projection - {filename}</title>\n'

    # A stylized text bar at the top (like Archicad’s tab):
    # We'll place it near the top, full width, with some offset:
    top_bar_height = 30
    top_bar_rect = (
        f'<rect x="0" y="0" '
        f'width="{svg_size}" height="{top_bar_height}" '
        f'style="fill: var(--secondary-color);" />'
    )
    top_bar_text = (
        f'<text x="{svg_size / 2:.1f}" y="{top_bar_height / 2:.1f}" '
        f'style="'
        f'fill: var(--primary-bg); '
        f'font-size: 14px; '
        f'font-weight: bold; '
        f'text-anchor: middle; '
        f'alignment-baseline: middle;">'
        f'{filename} - 2D Plan'
        f'</text>'
    )

    # We can push the bounding boxes down by `top_bar_height + some margin` if we want
    # but for now, we’ll just overlay it. If you want to shift, you can do so in your margin logic.

    svg_footer = "\n</svg>"

    # Assemble all parts
    svg_body = [top_bar_rect, top_bar_text] + svg_rects
    svg_str = (
            svg_header
            + svg_title
            + "\n".join(svg_body)
            + svg_footer
    )

    return svg_str


def csv_to_dict(filepath, delimiter=';'):
    """
    Reads a CSV file and returns a nested dictionary with two keys:
      {
        "header": [... list of column headers ...],
        "data": {
            outer_key1: {header1: val1, header2: val2, ...},
            outer_key2: {...},
            ...
        },
      }

    - "header" is a list of column headers (excluding the first column).
    - "data" is a dictionary keyed by the first column in each row.
    """
    nested_result = {
        "header": [],
        "data": {}
    }

    with open(filepath, mode='r', encoding='utf-8') as f:
        reader = csv.reader(f, delimiter=delimiter)

        # Read the header row
        header_row = next(reader)
        # Store the header row (excluding the first column)
        nested_result["header"] = header_row[1:]

        # Read the data
        for row in reader:
            outer_key = row[0]
            inner_dict = {
                header_row[i]: row[i]
                for i in range(1, len(header_row))
            }
            nested_result["data"][outer_key] = inner_dict

    return nested_result


import csv
import io


def dict_to_csv_string(nested_dict, delimiter=';'):
    """
    Inverse of csv_to_dict: takes a nested dictionary of this form:
      {
        "header": [... column headers ...],
        "data": {
            outer_key1: {header1: val1, header2: val2, ...},
            outer_key2: {header1: val3, header2: val4, ...},
            ...
        }
      }

    And returns a CSV string with columns:
      FirstColumnName, header1, header2, ...
      (outer_key1), (val1), (val2), ...
      (outer_key2), (val3), (val4), ...
      ...
    """
    headers = nested_dict["header"]
    data_dict = nested_dict["data"]
    first_column_name = "Key"  # or "ID", or whatever you'd like

    # StringIO lets us write CSV data to an in-memory buffer
    output = io.StringIO()
    writer = csv.writer(output, delimiter=delimiter)

    # Write the header row: e.g. ["Key", "header1", "header2", ...]
    writer.writerow([first_column_name] + headers)

    # Write each row from the data dictionary
    for outer_key, row_dict in data_dict.items():
        row = [outer_key]  # start the row with the outer key
        # Append each column value in the same order as 'headers'
        for col in headers:
            row.append(row_dict.get(col, ""))  # fallback to "" if missing
        writer.writerow(row)

    # Retrieve the entire CSV string from the StringIO buffer
    csv_string = output.getvalue()
    output.close()
    return csv_string


prices_unknown_ifc_name_set: set[str | str] = set()
passport_unknown_ifc_name_set: set[str | str] = set()
unknown_ifc_name_set: set[str | str] = set()


def ifc_product_walk(
        user_id: str,
        user_config: dict,
        ifc_file_path: str,
) -> tuple[defaultdict[str, MaterialProperties], BuildingMetrics]:
    """
    Calculate Space and Material Properties for a Building
    -> defaultdict[str, MaterialProperties],
       BuildingMetrics
    """
    # properties = gebäude_kenndaten
    # pprint(product.get_info_2(recursive=True))
    # VERBOSE = True

    gc.unfreeze()
    _ = gc.collect()
    logger = InMemoryLogHandler()
    start = time.time()

    settings = ifcopenshell.geom.settings()

    # Already set to True by default
    settings.set(settings.WELD_VERTICES, True)
    settings.set(settings.GENERATE_UVS, True)

    settings.set(settings.ELEMENT_HIERARCHY, True)
    settings.set(settings.KEEP_BOUNDING_BOXES, True)

    settings.set(settings.USE_MATERIAL_NAMES, True)
    settings.set(settings.PRECISION, 1e-016)

    settings.set(settings.USE_PYTHON_OPENCASCADE, False)

    logger.sync_emit(record=f">>>> {settings}", user_id=user_id)

    logger.sync_emit(record=">>>> Loading ifc file.", user_id=user_id)

    ifc_model: ifcopenshell.file = ifcopenshell.open(ifc_file_path, should_stream=False)

    gc.freeze()

    metrics = BuildingMetrics()

    grundstuecksfläche = 0.0
    bebaute_fläche = 0.0
    unbebaute_fläche = 0.0
    brutto_rauminhalt = 0.0
    brutto_grundfläche = 0.0
    konstruktions_grundfläche = 0.0
    netto_raumfläche = 0.0
    fassadenflaeche = 0.0
    stockwerke = 0.0

    logger.sync_emit(record=f">>>> Preparations done within {time.time() - start}s", user_id=user_id)
    logger.sync_emit(record=f">>>> Schema used: {ifc_model.schema}", user_id=user_id)
    logger.sync_emit(record=f">>>> Opened file: {ifc_file_path}", user_id=user_id)

    storeys = ifc_model.by_type("IfcBuildingStorey")
    metrics.stockwerke = len(storeys)
    eg_found = False

    for storey in storeys:
        # if DEBUG_VERBOSE:
        # pprint(ifcopenshell.util.element.get_psets(storey))
        storey_name = storey.get_info()["Name"]
        if storey.get_info()["Elevation"] == 0:
            eg_found = True
            ground_floor = storey
        if storey.get_info()["Name"] == "UG01":
            first_storey = storey

        # pprint(ifcopenshell.util.element.get_psets(element=storey))
        # decompose_area(settings, storey)

    # if not eg_found:
    #     pprint(storeys)
    #     for storey in storeys:
    #         pprint(storey.get_info())
    #     raise ValueError("Storeys do not contain one with the name EG")
    # else:
    #     del eg_found
    #     del storeys

    # TODO: Figure out if we need to enable recursive for some files
    ground_floor_elements = ifcopenshell.util.element.get_decomposition(
        element=ground_floor, is_recursive=True
    )
    # first_floor_elements = ifcopenshell.util.element.get_decomposition(
    #     element=first_storey, is_recursive=True
    # )

    # Dictionary to store elements by material
    iterator = ifcopenshell.geom.iterator(
        settings=settings,
        file_or_filename=ifc_model,
        num_threads=multiprocessing.cpu_count() * 2,
        # TODO: Chceck if this introduces errors
        # Currently lowers the calculation time by 50%
        geometry_library="hybrid-cgal-simple-opencascade",
    )
    elements_by_material: defaultdict[str, MaterialProperties] = defaultdict(
        MaterialProperties
    )

    logger.sync_emit(record=">>> Now iterating over the elements.", user_id=user_id)

    if iterator.initialize():
        i = 0
        while iterator.next():
            element_shape = iterator.get()
            element_geometry = element_shape.geometry
            element: ifcopenshell.entity_instance = ifc_model.by_guid(
                element_shape.guid
            )
            try:
                # Volume is taken from the pset instead
                volume: float = ifcopenshell.util.shape.get_volume(
                    geometry=element_geometry
                )
                # area: float = ifcopenshell.util.shape.get_side_area(
                #     geometry=element_geometry, axis="Z"
                # )
                area: float = ifcopenshell.util.shape.get_footprint_area(
                    geometry=element_geometry,
                    axis="Z",
                )
                length: float = ifcopenshell.util.shape.get_max_xy(
                    geometry=element_geometry
                )

            except Exception as e:
                if DEBUG_VERBOSE:
                    pprint.pprint(f">>>?? No Geometry: {element.get_info(recursive=True)}?")
                    pprint.pprint(e)
                continue
            metrics.brutto_rauminhalt += volume

            # if "Morph" in element.get_info()["Name"]:
            #     pprint(f"{element.get_info()["Name"]}:{area}")
            #     pprint(ifcopenshell.util.element.get_container(element))

            if not element.is_a("IfcSlab"):
                metrics.brutto_grundfläche += area

            if element in ground_floor_elements and element.is_a("IfcSlab"):
                # pprint(element.get_info())
                # pprint(area)
                metrics.bebaute_fläche += area
                # pprint(metrics.bebaute_fläche)

            if element.is_a("IfcSpace"):
                metrics.netto_raumfläche += area

            elif not element.is_a("IfcSlab") and not element.is_a("IfcSpace"):
                metrics.konstruktions_grundfläche += area
                # pprint(f">>>?? No Geometry: {element.get_info_2(recursive=True)}?")

            i += 1
            # Ignore all spaces and similar, they do not have good materials
            if not (
                    element.is_a("IfcSpace")
                    or element.is_a("IfcOpeningElement")
                    or element.is_a("IfcBuildingElementProxy")
            ):

                pset_dict = ifcopenshell.util.element.get_psets(element=element)
                if not pset_dict.get("Component Quantities"):
                    pprint.pprint(pset_dict, sort_dicts=True)
                    continue
                else:
                    keys = pset_dict.get("Component Quantities")

                for ifc_name in keys:

                    # Filter out the "id" of the "Component Quantities" object as it contains an int,
                    # and we are only interested in the quantities of the sub elements.
                    if ifc_name != "id":
                        subdict = pset_dict.get("Component Quantities").get(ifc_name)
                        # pprint({ifc_name:subdict}, sort_dicts=True)
                        # volume: float = subdict.get("Schicht/Komponenten Volumen (brutto)") or subdict["properties"].get("Schicht/Komponenten Volumen (brutto)")

                        if user_config.get(ifc_name):
                            element_properties = user_config[ifc_name]
                        else:
                            passport_unknown_ifc_name_set.add(ifc_name)
                            # pprint.pprint(f"Not in passport_config_dict: {ifc_name}")
                            continue  # TODO: Handle this more gracefully
                        density = float_or_zero(property_dict=element_properties, property="Dichte")

                        gwp = float_or_zero(property_dict=element_properties, property="GWP")
                        ap = float_or_zero(property_dict=element_properties, property="AP")
                        penrt = float_or_zero(property_dict=element_properties, property="PENRT")

                        # if material_type !+ "single":
                        #     waste_grade = float(element_config[1])
                        # else:
                        #     waste_grade = float(element_config[1])
                        #     if surrounding_material_names[0]:
                        #
                        #     if surrounding_material_names[1]:

                        waste_grade = float_or_zero(property_dict=element_properties, property="NEU Abfallreduktion")
                        recyclable_grade = float_or_zero(property_dict=element_properties, property="NEU Recycling")

                        mass = density * volume
                        waste_mass = mass * waste_grade
                        recyclable_mass = mass * recyclable_grade

                        ap_ml = mass * ap
                        gwp_ml = mass * gwp
                        penrt_ml = mass * penrt

                        # Calculate based on unit
                        # see the comments above the definition
                        # for information on the indices
                        # pprint(f"$?: {element_prices}")
                        # Base case is
                        # if element_prices[7] == "Fläche":
                        #     multiplier = area
                        #
                        # elif element_prices[7] == "Masse":
                        #     multiplier = mass
                        #
                        # elif element_prices[7] == "Länge":
                        #     multiplier = length
                        #
                        # elif element_prices[7] == "Volumen":
                        #     multiplier = volume
                        #     # pprint(f"$?: {volume * float(element_prices[5])}")
                        # else:
                        #     pprint.pprint(
                        #         f">>! ERROR: multiplier type {element_prices[7]} is unknown!"
                        #     )
                        #     multiplier: float = 1
                        #
                        # elements_by_material[
                        #     ifc_name
                        # ].global_brutto_price += multiplier * float(element_prices[3])
                        # elements_by_material[
                        #     ifc_name
                        # ].local_brutto_price += multiplier * float(element_prices[4])
                        # elements_by_material[
                        #     ifc_name
                        # ].local_netto_price += multiplier * float(element_prices[5])

                        elements_by_material[ifc_name].volume += volume
                        elements_by_material[ifc_name].mass += mass
                        elements_by_material[ifc_name].area += area
                        elements_by_material[ifc_name].length += length

                        elements_by_material[ifc_name].waste_mass += waste_mass
                        elements_by_material[ifc_name].recyclable_mass += recyclable_mass

                        elements_by_material[ifc_name].ap_ml += ap_ml
                        elements_by_material[ifc_name].gwp_ml += gwp_ml
                        elements_by_material[ifc_name].penrt_ml += penrt_ml

                        # For debugging the dict layout
                        # pprint.pprint(str(elements_by_material[ifc_name]))

                        # assert elements_by_material[ifc_name].waste_grade == elements_by_material[ifc_name].recyclable_grade
                        # elements_by_material[ifc_name].density = float(element_config[0])

            i += 1
        logger.sync_emit(record=f">>>?? {metrics.netto_raumfläche}?", user_id=user_id)
        logger.sync_emit(record=f">>>?? {metrics.konstruktions_grundfläche}?", user_id=user_id)
        logger.sync_emit(record=f">>>?? {metrics.brutto_grundfläche}?", user_id=user_id)
        logger.sync_emit(record=f">>>?? {metrics.bebaute_fläche}?", user_id=user_id)

        metrics.grundstuecksfläche = metrics.bebaute_fläche + metrics.unbebaute_fläche
        metrics.bgf_bf_ratio = metrics.brutto_grundfläche / metrics.bebaute_fläche
        metrics.bri_bgf_ratio = metrics.brutto_rauminhalt / metrics.brutto_grundfläche

    logger.sync_emit(record="Materials not yet in material passport file", user_id=user_id)
    logger.sync_emit(record=passport_unknown_ifc_name_set, user_id=user_id)

    logger.sync_emit(record="Materials not yet in prices file", user_id=user_id)
    logger.sync_emit(record=prices_unknown_ifc_name_set, user_id=user_id)

    logger.sync_emit(record="Materials Found!", user_id=user_id)
    logger.sync_emit(record=elements_by_material, user_id=user_id)

    logger.sync_emit(record="Properties:", user_id=user_id)
    logger.sync_emit(record=metrics, user_id=user_id)

    return elements_by_material, metrics


def float_or_zero(property_dict: dict, property: str) -> float:
    element_property = property_dict.get(property)
    # None, 0 and "" are cast to False
    if not element_property:
        return 0.0
    else:
        return float(element_property.replace(",", "."))
