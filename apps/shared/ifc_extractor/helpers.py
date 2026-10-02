# -*- coding: utf-8 -*-
import csv
import gc
import io
import math
import multiprocessing
import os
import pprint
import time
from collections import defaultdict

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
import pandas as pd
from pandas.core.frame import DataFrame

from apps.shared.models import (
    BuildingMetrics,
    MaterialProperties,
)
from apps.shared.assessment_logging import InMemoryLogHandler
from .material_assessment import AssessmentOptions, load_reference
from .ifc_assessment import assess_ifc

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


def create_plan_svg_bboxes(ifc_path, svg_size: int = 1000, margin: int = 20) -> str:
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

    # Pick the storey with Elevation=0, or fall back to the first storey
    ground_floor = select_ground_floor(storeys)

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
        except RuntimeError as e:
            pprint.pprint(e)
            continue

        if not shape or not shape.geometry:
            continue

        verts = shape.geometry.verts
        if not verts:
            continue

        pminx: float = float("inf")
        pmaxx: float = float("-inf")
        pminy: float = float("inf")
        pmaxy: float = float("-inf")

        for i in range(0, len(verts), 3):
            x = verts[i + 0]
            y = verts[i + 1]
            if x < pminx:
                pminx = x
            if x > pmaxx:
                pmaxx = x
            if y < pminy:
                pminy = y
            if y > pmaxy:
                pmaxy = y

        if pminx < pmaxx and pminy < pmaxy:
            product_bboxes[product] = (pminx, pminy, pmaxx, pmaxy)
            if pminx < all_xmin:
                all_xmin = pminx
            if pmaxx > all_xmax:
                all_xmax = pmaxx
            if pminy < all_ymin:
                all_ymin = pminy
            if pmaxy > all_ymax:
                all_ymax = pmaxy

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
        label_escaped = (
            label_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        )

        # Text
        text_elem = (
            f'<text x="{cx:.1f}" y="{cy:.1f}" '
            f'fill="#fff" font-size="12" font-weight="bold" '
            f'text-anchor="middle" alignment-baseline="middle">'
            f"{label_escaped}</text>"
        )
        svg_texts.append(text_elem)

    # Blueprint background: a dark navy/blue
    blueprint_bg = "#002b36"  # or #001f3f, #0f1420, etc.

    svg_header = (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{svg_size}" height="{svg_size}" '
        f'viewBox="0 0 {svg_size} {svg_size}" '
        f'style="background: {blueprint_bg};">\n'
        f"  <title>Blueprint Ground Floor Plan</title>\n"
    )
    svg_footer = "</svg>"

    svg_title = f"  <title>2D Down Projection - {filename}</title>\n"

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
        f"fill: var(--primary-bg); "
        f"font-size: 14px; "
        f"font-weight: bold; "
        f"text-anchor: middle; "
        f'alignment-baseline: middle;">'
        f"{filename} - 2D Plan"
        f"</text>"
    )

    # We can push the bounding boxes down by `top_bar_height + some margin` if we want
    # but for now, we’ll just overlay it. If you want to shift, you can do so in your margin logic.

    svg_footer = "\n</svg>"

    # Assemble all parts
    svg_body = [top_bar_rect, top_bar_text] + svg_rects
    svg_str = svg_header + svg_title + "\n".join(svg_body) + svg_footer

    return svg_str


def file_to_dict(filepath: str, delimiter: str = ";"):
    """
    Reads a CSV or XLSX file and returns a nested dictionary with two keys:
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
    - `sheet_name` specifies which sheet to read (default is the first sheet).
    """
    data = load_reference(filepath)
    header = list(dict.fromkeys(key for row in data.values() for key in row))
    return {"header": header, "data": data}


def dict_to_file_string(
    nested_dict, delimiter: str = ";", filename: str = "Config.xlsx"
) -> bytes | str | None:
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

    Alternatively:

    Into an Excel file (binary content) with columns:
      FirstColumnName, header1, header2, ...
      (outer_key1), (val1), (val2), ...
      (outer_key2), (val3), (val4), ...
      ...

    Returns a bytes object that can be used for file downloads.
    """
    headers = nested_dict["header"]
    data_dict = nested_dict["data"]
    first_column_name = "Key"  # or "ID", or whatever you'd like

    # StringIO lets us write CSV data to an in-memory buffer
    output = io.StringIO()

    if filename.endswith(".csv"):
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
    elif filename.endswith(".xlsx"):
        headers = nested_dict["header"]
        data_dict = nested_dict["data"]
        first_column_name = "Key"  # or "ID", or whatever you'd like

        # Create a list of rows for the DataFrame
        rows = []
        for outer_key, row_dict in data_dict.items():
            row = {first_column_name: outer_key}  # Start with the first column (Key/ID)
            row.update(row_dict)  # Add the rest of the columns
            rows.append(row)

        # Create a DataFrame from the rows
        df = pd.DataFrame(rows)

        # Ensure the column order matches: first column + headers
        df = df[[first_column_name] + headers]

        # Write the DataFrame to an Excel file in memory
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Sheet1")

        # Retrieve the binary content of the Excel file
        excel_bytes = output.getvalue()
        output.close()
        return excel_bytes


prices_unknown_ifc_name_set: set[str | str] = set()
passport_unknown_ifc_name_set: set[str | str] = set()
unknown_ifc_name_set: set[str | str] = set()

# Name of the German quantity carrying the (brutto) volume of a single
# material layer/component, as produced by the "Component Quantities" pset.
GERMAN_COMPONENT_VOLUME_KEY = "Schicht/Komponenten Volumen (brutto)"


def coerce_float(value) -> float:
    """
    Robustly coerce a raw property value (as found in IFC psets or user
    config dictionaries) into a finite float.

    - None/missing -> 0.0
    - bool -> 0.0 (not a valid numeric quantity in this domain)
    - int/float -> float(value), non-finite (NaN/Infinity) -> 0.0
    - str -> parsed, supporting comma decimals as well as dot/comma/space
      thousands separators; blank or unparsable strings -> 0.0
    - anything else -> 0.0
    """
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        result = float(value)
        return result if math.isfinite(result) else 0.0
    if not isinstance(value, str):
        return 0.0

    text = value.strip()
    if not text:
        return 0.0
    # Whitespace is sometimes used as a thousands separator (e.g. "1 234,56").
    text = "".join(text.split())

    has_comma = "," in text
    has_dot = "." in text
    if has_comma and has_dot:
        if text.rfind(",") > text.rfind("."):
            # Comma is the decimal separator, dot(s) are thousands separators.
            text = text.replace(".", "").replace(",", ".")
        else:
            # Dot is the decimal separator, comma(s) are thousands separators.
            text = text.replace(",", "")
    elif has_comma:
        # Only comma(s) present. A single comma is treated as a decimal
        # separator (matching common German number formatting); multiple
        # commas indicate thousands grouping without a decimal part.
        if text.count(",") == 1:
            text = text.replace(",", ".")
        else:
            text = text.replace(",", "")

    try:
        result = float(text)
    except (TypeError, ValueError):
        return 0.0
    return result if math.isfinite(result) else 0.0


def safe_ratio(numerator: float, denominator: float) -> float:
    """
    Pure helper returning ``numerator / denominator``, or ``0.0`` when the
    denominator is zero (avoids ZeroDivisionError on degenerate models).
    """
    if not denominator:
        return 0.0
    return numerator / denominator


EXTERNAL_PSET_NAMES = (
    "Pset_WallCommon",
    "Pset_WindowCommon",
    "Pset_DoorCommon",
    "Pset_CurtainWallCommon",
    "Pset_SlabCommon",
    "Pset_RoofCommon",
)


def _coerce_external_flag(value) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and math.isfinite(value):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "t", "y"}:
            return True
        if normalized in {"false", "0", "no", "f", "n"}:
            return False
    return None


def _declared_external(element) -> bool | None:
    for pset_name in EXTERNAL_PSET_NAMES:
        pset = ifcopenshell.util.element.get_pset(element, pset_name)
        if pset and "IsExternal" in pset:
            flag = _coerce_external_flag(pset["IsExternal"])
            if flag is not None:
                return flag
    return None


def is_external(element) -> bool:
    """
    Determine if an element is external based on Pset_*Common.IsExternal.

    Windows and doors often omit their own ``IsExternal`` property. In that
    case, inherit the classification from the wall/curtain wall they fill or
    are aggregated into. An explicit value on the element always wins.
    """
    declared = _declared_external(element)
    if declared is not None:
        return declared

    if element.is_a("IfcWindow") or element.is_a("IfcDoor"):
        for fills_relation in getattr(element, "FillsVoids", ()) or ():
            opening = fills_relation.RelatingOpeningElement
            for voids_relation in getattr(opening, "VoidsElements", ()) or ():
                if is_external(voids_relation.RelatingBuildingElement):
                    return True

        aggregate = ifcopenshell.util.element.get_aggregate(element)
        if aggregate is not None and (
            aggregate.is_a("IfcWall") or aggregate.is_a("IfcCurtainWall")
        ):
            return is_external(aggregate)

    return False


def get_qto_area(element, preferred_names: list[str]) -> float | None:
    """
    Retrieve a positive area from standard Qto sets.
    """
    qtos = ifcopenshell.util.element.get_psets(element, qtos_only=True)
    for name in preferred_names:
        for qto in qtos.values():
            if name in qto:
                val = coerce_float(qto[name])
                if val > 0:
                    return val
    return None


def get_gross_side_area(element, geometry=None) -> float:
    """
    Calculate gross side area for walls and curtain walls.
    Prefer Qto GrossSideArea. Fallback to geometry vertical-side-area.
    """
    area = get_qto_area(element, ["GrossSideArea", "Area", "NetSideArea"])
    if area is not None:
        return area
    if geometry is not None:
        try:
            area = coerce_float(ifcopenshell.util.shape.get_side_area(geometry))
            return area if area > 0 else 0.0
        except Exception:
            pass
    return 0.0


def get_opening_area(element, geometry=None) -> float:
    """
    Calculate opening area for windows and doors.
    Prefer Qto Area. Fallback to geometry vertical-side-area.
    """
    area = get_qto_area(element, ["Area", "GrossArea", "NetArea"])
    if area is not None:
        return area
    if geometry is not None:
        try:
            area = coerce_float(ifcopenshell.util.shape.get_side_area(geometry))
            return area if area > 0 else 0.0
        except Exception:
            pass
    return 0.0


def select_ground_floor(storeys):
    """
    Pure helper choosing which building storey should be treated as the
    ground floor. Prefers a storey whose Elevation is exactly 0, otherwise
    falls back to the first storey in the list. Returns None when no
    storeys are given.
    """
    if not storeys:
        return None
    for storey in storeys:
        if storey.get_info().get("Elevation") == 0:
            return storey
    return storeys[0]


def resolve_qto_volume(quantities: dict, geometry_volume: float | None = None) -> float:
    """
    Pure helper resolving an element's overall volume from its standard
    IFC quantity sets (as returned by
    ``ifcopenshell.util.element.get_psets(element, qtos_only=True)``).

    Prefers a finite, strictly positive ``GrossVolume`` over a finite,
    strictly positive ``NetVolume`` across all quantity sets. An invalid,
    missing, zero or negative quantity value never short-circuits the
    resolution; it is simply skipped in favour of the next candidate.

    When neither quantity yields a usable value, falls back to
    ``geometry_volume`` (typically the element's current computed
    geometry volume) when it is a finite, non-negative number, so a
    normal element with geometry but no usable Qto still gets a volume
    instead of silently becoming zero. Returns ``0.0`` when nothing
    usable is available at all.
    """
    quantities = quantities or {}
    for key in ("GrossVolume", "NetVolume"):
        for pset_values in quantities.values():
            value = (pset_values or {}).get(key)
            if value in (None, ""):
                continue
            coerced = coerce_float(value)
            if coerced > 0:
                return coerced

    if geometry_volume is not None:
        coerced_geometry = coerce_float(geometry_volume)
        if coerced_geometry >= 0:
            return coerced_geometry

    return 0.0


def resolve_component_volume(
    subdict: dict, fallback_volume: float | None = 0.0
) -> float | None:
    """
    Pure helper resolving the volume of a single material component/layer
    from its "Component Quantities" sub-dictionary.

    Prefers the German "Schicht/Komponenten Volumen (brutto)" quantity
    (either directly on the sub-dict, or nested under "properties" for
    complex quantities), falling back to ``fallback_volume`` (typically the
    element's standard Qto volume) when it is missing.
    """
    subdict = subdict or {}
    value = subdict.get(GERMAN_COMPONENT_VOLUME_KEY)
    if value in (None, ""):
        properties = subdict.get("properties") or {}
        value = properties.get(GERMAN_COMPONENT_VOLUME_KEY)
    if value in (None, ""):
        return fallback_volume
    return coerce_float(value)


def _material_component_name(material, fallback_name, index: int) -> str:
    """
    Pure helper deriving a display name for a single material component,
    preferring the material's own ``Name``, then a caller-supplied
    ``fallback_name`` (e.g. a layer/constituent/profile ``Name``), and
    finally a positional placeholder so a component is never dropped just
    because it is unnamed in the source IFC file.
    """
    name = getattr(material, "Name", None) if material is not None else None
    if not name:
        name = fallback_name
    if not name:
        name = f"Material {index + 1}"
    return name


def _accumulate_named_volumes(names, volumes) -> dict[str, float]:
    """
    Pure helper summing ``volumes`` into a dict keyed by ``names``,
    accumulating (rather than overwriting) entries that share the same
    name, e.g. two layers made of the same material.
    """
    result: dict[str, float] = {}
    for name, volume in zip(names, volumes):
        result[name] = result.get(name, 0.0) + volume
    return result


def resolve_material_component_volumes(
    material, total_volume: float
) -> dict[str, float]:
    """
    Pure helper allocating ``total_volume`` across the individual
    materials of an element's associated ``IfcMaterial`` (as returned by
    ``ifcopenshell.util.element.get_material(element, should_skip_usage=True)``),
    used as a fallback for elements that carry no German "Component
    Quantities" pset.

    - A single ``IfcMaterial`` receives the entire ``total_volume``.
    - ``IfcMaterialLayerSet`` allocates proportionally to each layer's
      ``LayerThickness`` when the layers carry usable (positive) thickness
      data, otherwise (and for all other set types) the volume is split
      evenly across the set's members.
    - Returns an empty dict when there is no associated material.
    """
    if material is None:
        return {}

    if material.is_a("IfcMaterialLayerSet"):
        layers = material.MaterialLayers or ()
        if not layers:
            return {}
        thicknesses = [
            coerce_float(getattr(layer, "LayerThickness", None)) for layer in layers
        ]
        total_thickness = sum(thicknesses)
        names = [
            _material_component_name(
                layer.Material, getattr(layer, "Name", None), index
            )
            for index, layer in enumerate(layers)
        ]
        if total_thickness > 0:
            volumes = [
                total_volume * (thickness / total_thickness)
                for thickness in thicknesses
            ]
        else:
            volumes = [total_volume / len(layers)] * len(layers)
        return _accumulate_named_volumes(names, volumes)

    if material.is_a("IfcMaterialProfileSet"):
        profiles = material.MaterialProfiles or ()
        if not profiles:
            return {}
        names = [
            _material_component_name(
                profile.Material, getattr(profile, "Name", None), index
            )
            for index, profile in enumerate(profiles)
        ]
        volumes = [total_volume / len(profiles)] * len(profiles)
        return _accumulate_named_volumes(names, volumes)

    if material.is_a("IfcMaterialConstituentSet"):
        constituents = material.MaterialConstituents or ()
        if not constituents:
            return {}
        names = [
            _material_component_name(
                constituent.Material, getattr(constituent, "Name", None), index
            )
            for index, constituent in enumerate(constituents)
        ]
        volumes = [total_volume / len(constituents)] * len(constituents)
        return _accumulate_named_volumes(names, volumes)

    if material.is_a("IfcMaterialList"):
        materials = material.Materials or ()
        if not materials:
            return {}
        names = [
            _material_component_name(single_material, None, index)
            for index, single_material in enumerate(materials)
        ]
        volumes = [total_volume / len(materials)] * len(materials)
        return _accumulate_named_volumes(names, volumes)

    # Plain single IfcMaterial (or an unexpected/unknown material type).
    name = _material_component_name(material, None, 0)
    return {name: total_volume}


def _distribute_volumes(
    component_data: list[tuple[str, float | None]], total_volume: float
) -> dict[str, float]:
    """
    Pure helper that preserves explicit non-None volumes and distributes
    any remaining volume (at least 0) evenly among components with None
    volume, accumulating duplicate names to conserve total volume.
    """
    explicit_names = []
    explicit_volumes = []
    missing_names = []

    for name, volume in component_data:
        if volume is None:
            missing_names.append(name)
        else:
            explicit_names.append(name)
            explicit_volumes.append(volume)

    if not missing_names:
        return _accumulate_named_volumes(explicit_names, explicit_volumes)

    sum_explicit = sum(explicit_volumes)
    remaining = max(total_volume - sum_explicit, 0.0)
    shared = remaining / len(missing_names)

    all_names = explicit_names + missing_names
    all_volumes = explicit_volumes + [shared] * len(missing_names)

    return _accumulate_named_volumes(all_names, all_volumes)


def resolve_element_components(element, qto_volume: float) -> dict[str, float]:
    """
    Resolves the named material components (and their volumes) of a
    single IFC element, preferring the German "Component Quantities"
    pset when present, and falling back to the element's associated
    ``IfcMaterial`` (single material, layer set, profile set,
    constituent set or material list) otherwise so a plain element that
    only carries a standard Qto volume still yields a material instead
    of being silently skipped.
    """
    pset_dict = ifcopenshell.util.element.get_pset(
        element=element, name="Component Quantities"
    )
    if pset_dict:
        data: list[tuple[str, float | None]] = []
        for ifc_name, subdict in pset_dict.items():
            # Filter out the "id" of the "Component Quantities" object as it contains an int,
            # and we are only interested in the quantities of the sub elements.
            if ifc_name != "id":
                data.append(
                    (
                        ifc_name,
                        resolve_component_volume(subdict=subdict, fallback_volume=None),
                    )
                )
        # An empty "Component Quantities" pset is still truthy (it always
        # carries an "id" key), so it must not be treated as authoritative
        # when it contains no actual components; fall through to the
        # material association below instead of suppressing it.
        if data:
            return _distribute_volumes(data, qto_volume)

    material = ifcopenshell.util.element.get_material(element, should_skip_usage=True)
    return resolve_material_component_volumes(
        material=material, total_volume=qto_volume
    )


def iterate_geometry(iterator):
    """
    Wraps IfcOpenShell's geometry iterator protocol, where
    ``iterator.initialize()`` already positions the iterator on the
    first item (retrievable via ``iterator.get()``), and
    ``iterator.next()`` advances to the following one, returning
    ``False`` once exhausted.

    A naive ``while iterator.next(): iterator.get()`` loop silently
    skips that first item; this generator yields every item, including
    it, by calling ``get()`` before checking whether ``next()`` can
    advance any further.
    """
    while True:
        yield iterator.get()
        if not iterator.next():
            break


def ifc_product_walk(
    user_id: str,
    user_config: dict,
    ifc_file_path: str,
    assessment_options: dict | None = None,
) -> tuple[defaultdict[str, MaterialProperties], BuildingMetrics]:
    """
    Calculate Space and Material Properties for a Building
    -> defaultdict[str, MaterialProperties],
       BuildingMetrics
    """
    # properties = gebäude_kenndaten
    # pprint(product.get_info_2(recursive=True))
    # VERBOSE = True

    report = assess_ifc(ifc_file_path, user_config, AssessmentOptions(**(assessment_options or {})))
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

    # settings.set(settings.USE_PYTHON_OPENCASCADE, False)

    logger.sync_emit(record=f">>>> {settings}", user_id=user_id)

    logger.sync_emit(record=">>>> Loading ifc file.", user_id=user_id)

    ifc_model: ifcopenshell.file.file = ifcopenshell.open(
        ifc_file_path, should_stream=False
    )

    metrics = BuildingMetrics()

    gross_facade_area = 0.0
    opening_area = 0.0

    logger.sync_emit(
        record=f">>>> Preparations done within {time.time() - start}s", user_id=user_id
    )
    logger.sync_emit(record=f">>>> Schema used: {ifc_model.schema}", user_id=user_id)
    logger.sync_emit(record=f">>>> Opened file: {ifc_file_path}", user_id=user_id)

    storeys = ifc_model.by_type("IfcBuildingStorey")
    metrics.stockwerke = len(storeys)

    # Pick the storey with Elevation=0, or fall back to the first storey.
    # Safely handles the case where there are no storeys at all.
    ground_floor = select_ground_floor(storeys)

    # TODO: Figure out if we need to enable recursive for some files
    if ground_floor is not None:
        ground_floor_elements = ifcopenshell.util.element.get_decomposition(
            element=ground_floor, is_recursive=True
        )
    else:
        ground_floor_elements = []

    # Dictionary to store elements by material
    iterator = ifcopenshell.geom.iterator(
        settings=settings,
        file_or_filename=ifc_model,
        num_threads=max(1, multiprocessing.cpu_count() - 1),
        # TODO: Chceck if this introduces errors
        # Currently lowers the calculation time by 50%
        # geometry_library="hybrid-cgal-simple-opencascade",
    )
    elements_by_material: defaultdict[str, MaterialProperties] = defaultdict(
        MaterialProperties
    )

    logger.sync_emit(record=">>> Now iterating over the elements.", user_id=user_id)

    if iterator.initialize():
        i = 0
        for element_shape in iterate_geometry(iterator):
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
                    pprint.pprint(
                        f">>>?? No Geometry: {element.get_info(recursive=True)}?"
                    )
                    pprint.pprint(e)
                continue
            # Facade extraction logic (Core Envelope)
            if is_external(element):
                if element.is_a("IfcWall") or element.is_a("IfcCurtainWall"):
                    gross_facade_area += get_gross_side_area(element, element_geometry)
                elif element.is_a("IfcWindow") or element.is_a("IfcDoor"):
                    opening_area += get_opening_area(element, element_geometry)

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
            i += 1

        metrics.fassadenflaeche = gross_facade_area
        metrics.fassaden_oeffnungsflaeche = opening_area
        metrics.fassaden_opake_flaeche = max(0.0, gross_facade_area - opening_area)
        metrics.fenster_wand_verhaeltnis = safe_ratio(opening_area, gross_facade_area)

        metrics.grundstuecksfläche = metrics.bebaute_fläche + metrics.unbebaute_fläche
        metrics.bgf_bf_ratio = safe_ratio(
            metrics.brutto_grundfläche, metrics.bebaute_fläche
        )
        metrics.bri_bgf_ratio = safe_ratio(
            metrics.brutto_rauminhalt, metrics.brutto_grundfläche
        )

        logger.sync_emit(record=f">>>?? {metrics.netto_raumfläche}?", user_id=user_id)
        logger.sync_emit(
            record=f">>>?? {metrics.konstruktions_grundfläche}?", user_id=user_id
        )
        logger.sync_emit(record=f">>>?? {metrics.brutto_grundfläche}?", user_id=user_id)
        logger.sync_emit(record=f">>>?? {metrics.bebaute_fläche}?", user_id=user_id)

        logger.sync_emit(
            record="Materials not yet in material passport file", user_id=user_id
        )
        logger.sync_emit(record=passport_unknown_ifc_name_set, user_id=user_id)

        logger.sync_emit(record="Materials not yet in prices file", user_id=user_id)
        logger.sync_emit(record=prices_unknown_ifc_name_set, user_id=user_id)

        logger.sync_emit(record="Materials Found!", user_id=user_id)
        logger.sync_emit(record=elements_by_material, user_id=user_id)

        logger.sync_emit(record="Properties:", user_id=user_id)
        logger.sync_emit(record=metrics, user_id=user_id)

    metrics.assessment_report = report
    for name, totals in report["materials"].items():
        material = MaterialProperties()
        for key in ("volume", "area", "length", "mass", "waste_mass", "recyclable_mass",
                    "global_brutto_price", "local_brutto_price", "local_netto_price"):
            setattr(material, key, totals.get(key))
        for indicator in ("ap", "gwp", "penrt"):
            for period in ("a1_a3", "a1_a3_b4"):
                setattr(material, f"{indicator}_ml_{period}", totals.get(f"{indicator}_{period}"))
        elements_by_material[name] = material
    logger.sync_emit(record={"assessment_complete": report["complete"], "issues": report["issues"],
                             "material_issues": [row for row in report["rows"] if row["issues"]]}, user_id=user_id)
    return elements_by_material, metrics


def float_or_zero(property_dict: dict, property_name: str) -> float:
    element_property = property_dict.get(property_name)
    return coerce_float(element_property)


def prepare_comparison_data(documents, selected_property) -> DataFrame:
    """
    Prepares the comparison data for a selected material property.
    """
    # Initialize a dictionary to hold comparison data
    comparison_data = {"Material Name": []}
    document_names = [f"Document {i + 1}" for i in range(len(documents))]

    # Prepare columns for each document in the comparison
    for doc_index, document in enumerate(documents):
        comparison_data[document_names[doc_index]] = []

    # Collect material names from all documents and ensure uniqueness
    all_materials = set()
    for document in documents:
        materials = document.material_properties.all()
        material_names = {material.name for material in materials}
        all_materials.update(material_names)

    # Iterate over all unique materials and build the comparison table
    for material_name in sorted(all_materials):
        comparison_data["Material Name"].append(material_name)
        for doc_index, document in enumerate(documents):
            # Get the material with the same name in the current document
            material: MaterialProperties = document.material_properties.filter(
                name=material_name
            ).first()

            # If the material exists, fetch the selected property; otherwise, set to 0
            if material:
                property_value = getattr(material, selected_property, None)
            else:
                property_value = None

            comparison_data[document_names[doc_index]].append(property_value)

    # Convert the comparison data to a Pandas DataFrame
    return pd.DataFrame(comparison_data)
