# -*- coding: utf-8 -*-
import csv
import gc
import time
import multiprocessing

from pprint import pprint
from collections import defaultdict
from collections.abc import Iterator

from ifcopenshell.geom import iterator

from model_manager.models import (
    BuildingMetrics,
    CadevilDocument,
    FileUpload,
    MaterialProperties,
)

try:
    import ifcopenshell
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

    from ifcopenshell.entity_instance import entity_instance

except ImportError:
    pprint(
        """Cannot import ifcopenshell. This is necessary to run the program.
           Please install it via pip or conda and retry."""
    )
    exit(1)

DEBUG = True
DEBUG_VERBOSE = False

from model_manager.ifc_extractor.data_models import (
    material_name_translation_dict,
)

"""
    "IfcWall",
    "IfcColumn",
    "IfcBeam",
    "IfcSlab",
    "IfcCurtainWall",
    "IfcWindow",
    "IfcDoor",
"""


def csv_to_dict(filepath, delimiter=';'):
    """
    Reads a CSV file and returns a dictionary where:
      - The key of the dictionary is the value from the first column.
      - The value is another dictionary mapping the remaining columns to their values.
    """
    result = {}

    with open(filepath, mode='r', encoding='utf-8') as f:
        reader = csv.reader(f, delimiter=delimiter)

        # Read the header row
        header = next(reader)

        # The first column in the header is the outer key
        # The remaining columns will be included in the inner dict
        for row in reader:
            outer_key = row[0]
            # Map each remaining column header to its corresponding cell
            inner_dict = {
                header[i]: row[i]
                for i in range(1, len(header))
            }
            result[outer_key] = inner_dict
    return result

def read_config(config_file: str) -> dict[str, str | int]:
    config_dict: dict[str, str | int] = {}
    with open(config_file, newline="") as csvfile:
        reader = csv.reader(csvfile, delimiter=";", quotechar="'")
        for row in reader:
            config_dict[row[0]] = row[1]
    return config_dict


prices_unknown_ifc_name_set: set[str | str] = set()
passport_unknown_ifc_name_set: set[str | str] = set()
unknown_ifc_name_set: set[str | str] = set()


# 0: key Material; <- name referenced in code
# 1:     baubook Wahl; <- name of material on baubook website
# 2: 0   Dichte    kg/m³;
# 3:     Verwertungspotential ; <- Not in use
# 4: 1   'Abfallreduktion oder -erhöhung) %'; <- waste grade in %
# 5: 2   Anteil Recycling %; <- $4 + $5 = 100
# 6: 3   GWP-total Globales Erwärmungspotenzial - total            kg CO2 Äq./kg;
# 7: 4   AP Versauerungspotenzial von Boden und Wasser        kg SO2 Äq./kg;
# 8: 5   PENRT Nicht erneuerbare Primärenergie - total           MJ/kg
def read_passport_config(
    passport_file: str,
) -> dict[
    str,
    tuple[
        int | float | str,
        int | float | str,
        int | float | str,
        int | float | str,
        int | float | str,
        int | float | str,
    ],
]:
    passport_dict: dict[
        str,
        tuple[
            int | float | str,
            int | float | str,
            int | float | str,
            int | float | str,
            int | float | str,
            int | float | str,
        ],
    ] = {}
    with open(passport_file, newline="") as csvfile:
        reader = csv.reader(csvfile, delimiter=";", quotechar="'")
        for row in reader:
            passport_dict[row[0]] = (row[2], row[4], row[5], row[6], row[7], row[8])
    return passport_dict


# 0:  key Material;
# 1:  0   Materialart laut BKI;
# 2:  1   Kostengruppe;
# 3:  2   Brutto;
# 4:  3   Netto;
# 5:  4   Brutto (Wien);
# 6:  5   Netto (Wien);
# 7:  6   Einheit;
def read_material_prices_config(
    passport_file: str,
) -> dict[
    str,
    tuple[
        int | float | str,
        int | float | str,
        int | float | str,
        int | float | str,
        int | float | str,
        int | float | str,
        int | float | str,
        int | float | str,
    ],
]:
    prices_dict: dict[
        str,
        tuple[
            int | float | str,
            int | float | str,
            int | float | str,
            int | float | str,
            int | float | str,
            int | float | str,
            int | float | str,
            int | float | str,
        ],
    ] = {}
    with open(passport_file, newline="") as csvfile:
        reader = csv.reader(csvfile, delimiter=";", quotechar="'")
        for row in reader:
            prices_dict[row[0]] = (
                row[1],
                row[2],
                row[3],
                row[4],
                row[5],
                row[6],
                row[7],
                row[8],
            )
    return prices_dict


async def extract_building_properties(
    ifc_file_path: str,
) -> BuildingMetrics:
    """
    Calculate Space for a Building
    -> BuildingMetrics
    """
    # pprint(product.get_info_2(recursive=True))
    # VERBOSE = True

    gc.unfreeze()
    _ = gc.collect()

    start = time.time()

    pprint(
        ">>>> Starting up Building Metrics Calculation.",
        width=140,
        sort_dicts=True,
        compact=True,
    )
    settings = ifcopenshell.geom.settings()

    # Already set to True by default
    settings.set(settings.WELD_VERTICES, True)
    settings.set(settings.GENERATE_UVS, True)

    settings.set(settings.REORIENT_SHELLS, True)

    settings.set(settings.ELEMENT_HIERARCHY, True)
    settings.set(settings.KEEP_BOUNDING_BOXES, True)
    settings.set(settings.USE_WORLD_COORDS, True)

    settings.set(settings.USE_MATERIAL_NAMES, True)
    settings.set(settings.PRECISION, 1e-016)

    pprint(settings)
    pprint(">>>> Loading ifc file.", width=140, sort_dicts=True, compact=True)
    ifc_model: ifcopenshell.file = ifcopenshell.open(ifc_file_path, should_stream=False)

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

    pprint(
        "--------------------------------------------------------------------------------------",
        width=140,
        sort_dicts=True,
        compact=True,
    )

    pprint(
        f">>>> Calculating Volumes",
        width=140,
        sort_dicts=True,
        compact=True,
    )

    pprint(
        "--------------------------------------------------------------------------------------",
        width=140,
        sort_dicts=True,
        compact=True,
    )
    pprint(
        f">>>> Schema used: {ifc_model.schema}",
        width=140,
        sort_dicts=True,
        compact=True,
    )
    pprint(f"Opened file: {ifc_file_path}", width=140, sort_dicts=True, compact=True)

    # Dictionary to store elements by material
    iterator = ifcopenshell.geom.iterator(
        settings=settings,
        file_or_filename=ifc_model,
        num_threads=multiprocessing.cpu_count(),
        geometry_library="hybrid-cgal-opencascade",
    )

    gc.freeze()

    storeys = ifc_model.by_type("IfcBuildingStorey")
    metrics.stockwerke = len(storeys)
    eg_found = False

    pprint(
        "--------------------------------------------------------------------------------------",
        width=140,
        sort_dicts=True,
        compact=True,
    )

    pprint(
        f">>>> Preparations done within {time.time() - start}s",
        width=140,
        sort_dicts=True,
        compact=True,
    )

    for storey in storeys:
        if DEBUG_VERBOSE:
            pprint(storey.get_info())
        storey_name = storey.get_info()["Name"]
        if storey.get_info()["Elevation"] == 0:
            eg_found = True
            ground_floor = storey
        if storey.get_info()["Name"] == "UG01":
            first_storey = storey

        # pprint(ifcopenshell.util.element.get_psets(element=storey))
        # decompose_area(settings, storey)

    if not eg_found:
        pprint(storeys)
        for storey in storeys:
            pprint(storey.get_info())
        raise ValueError("Storeys do not contain one with the name EG")
    else:
        del eg_found
        del storeys

    # TODO: Figure out if we need to enable recursive for some files
    ground_floor_elements = ifcopenshell.util.element.get_decomposition(
        element=ground_floor, is_recursive=True
    )
    # first_floor_elements = ifcopenshell.util.element.get_decomposition(
    #     element=first_storey, is_recursive=True
    # )

    pprint(
        "--------------------------------------------------------------------------------------",
        width=140,
        sort_dicts=True,
        compact=True,
    )

    pprint(
        f">>>> Located floors within {time.time() - start}s",
        width=140,
        sort_dicts=True,
        compact=True,
    )

    pprint(
        "--------------------------------------------------------------------------------------",
        width=140,
        sort_dicts=True,
        compact=True,
    )

    pprint(
        ">>> Now iterating over the elements.", width=140, sort_dicts=True, compact=True
    )

    if iterator.initialize():
        i = 0
        while iterator.next():
            try:
                element_shape = iterator.get()
                element_geometry = element_shape.geometry
                element: ifcopenshell.entity_instance = ifc_model.by_guid(
                    element_shape.guid
                )

                volume: float = ifcopenshell.util.shape.get_volume(
                    geometry=element_geometry
                )
                area: float = ifcopenshell.util.shape.get_side_area(
                    geometry=element_geometry, axis="Z"
                )
                # area: float = ifcopenshell.util.shape.get_footprint_area(
                #     geometry=element_geometry,
                #     axis="Z",
                # )
                length: float = ifcopenshell.util.shape.get_max_xy(
                    geometry=element_geometry
                )
            except Exception as e:
                if DEBUG_VERBOSE:
                    pprint(f">>>?? No Geometry: {element.get_info(recursive=True)}?")
                    pprint(e)
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

        pprint(f">>>?? {metrics.netto_raumfläche}?")
        pprint(f">>>?? {metrics.konstruktions_grundfläche}?")
        pprint(f">>>?? {metrics.brutto_grundfläche}?")
        pprint(f">>>?? {metrics.bebaute_fläche}?")
        metrics.grundstuecksfläche = metrics.bebaute_fläche + metrics.unbebaute_fläche
        metrics.bgf_bf_ratio = metrics.brutto_grundfläche / metrics.bebaute_fläche
        metrics.bri_bgf_ratio = metrics.brutto_rauminhalt / metrics.brutto_grundfläche

    pprint(
        "--------------------------------------------------------------------------------------",
        width=140,
        sort_dicts=True,
        compact=True,
    )

    pprint("Properties:", width=140, sort_dicts=True, compact=True)
    pprint(object=metrics, width=140, sort_dicts=True, compact=True)
    pprint(
        "--------------------------------------------------------------------------------------",
        width=140,
        sort_dicts=True,
        compact=True,
    )

    pprint(
        f">>>> Done within {time.time() - start}s",
        width=140,
        sort_dicts=True,
        compact=True,
    )
    pprint(
        "--------------------------------------------------------------------------------------",
        width=140,
        sort_dicts=True,
        compact=True,
    )
    return metrics


def ifc_product_walk(
    ifc_document: CadevilDocument,
    ifc_file_path: str,
) -> defaultdict[str, MaterialProperties]:
    """
    -> defaultdict[str, MaterialProperties]
    """
    # properties = gebäude_kenndaten
    # pprint(product.get_info_2(recursive=True))
    # VERBOSE = True

    gc.unfreeze()
    _ = gc.collect()

    start = time.time()

    pprint(
        ">>>> Starting up Material Properties Calculation.",
        width=140,
        sort_dicts=True,
        compact=True,
    )
    settings = ifcopenshell.geom.settings()

    # Already set to True by default
    settings.set(settings.WELD_VERTICES, True)
    settings.set(settings.GENERATE_UVS, True)

    settings.set(settings.ELEMENT_HIERARCHY, True)
    settings.set(settings.KEEP_BOUNDING_BOXES, True)

    settings.set(settings.USE_MATERIAL_NAMES, True)
    settings.set(settings.PRECISION, 1e-016)

    pprint(">>>> Loading ifc file.", width=140, sort_dicts=True, compact=True)
    ifc_model: ifcopenshell.file = ifcopenshell.open(ifc_file_path, should_stream=False)

    gc.freeze()

    # Dictionary to store elements by material
    iterator = ifcopenshell.geom.iterator(
        settings=settings,
        file_or_filename=ifc_model,
        num_threads=multiprocessing.cpu_count() * 2,
        # TODO: Chceck if this introduces errors
        # Currently lowers the calculation time by 50%
        geometry_library="hybrid-cgal-opencascade",
    )
    elements_by_material: defaultdict[str, MaterialProperties] = defaultdict(
        MaterialProperties
    )
    passport_config_dict = read_passport_config(
        "../schema/material_passport_house_a_b.csv"
    )
    material_prices_dict = read_material_prices_config("../schema/prices.csv")

    pprint(
        ">>> Now iterating over the elements.", width=140, sort_dicts=True, compact=True
    )
    accumulator_dict = {}

    if iterator.initialize():
        i = 0
        while iterator.next():
            try:
                element_shape = iterator.get()
                element_geometry = element_shape.geometry
                element: ifcopenshell.entity_instance = ifc_model.by_guid(
                    element_shape.guid
                )

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
                    pprint(f">>>?? No Geometry: {element.get_info(recursive=True)}?")
                    pprint(e)
                continue
            # if element.is_a("IfcWall"):
            #     pprint(
            #         f"BEGIN--{element.get_info_2()['Name']}----------------------------------------------------------------------------",
            #         width=140,
            #         sort_dicts=True,
            #         compact=True,
            #     )
            #     pprint(ifcopenshell.util.element.get_psets(element=element))
            #     pprint(
            #         "--------------------------------------------------------------------------------------",
            #         width=140,
            #         sort_dicts=True,
            #         compact=True,
            #     )

            # Ignore all spaces and similar, they do not have good materials
            if not (
                element.is_a("IfcSpace")
                or element.is_a("IfcDoor")
                or element.is_a("IfcWindow")
                or element.is_a("IfcOpeningElement")
                or element.is_a("IfcBuildingElementProxy")
            ):
                # FIXME: Current logic inflates metrics for layered materials
                #        Invert the order of logic for calculating things like
                #        length, area, volume,...
                # pprint(
                #     "--------------------------------------------------------------------------------------",
                #     width=140,
                #     sort_dicts=True,
                #     compact=True,
                # )
                # pprint(ifcopenshell.util.element.get_psets(element=element))
                pprint(ifcopenshell.util.element.get_psets(element=element), sort_dicts=True)
                # for ifc_name, length, volume, area in material_metric_iterator(element=element):
                #     pass
                continue

                for is_mono_elment, subelement, ifc_name in material_name_iterator(
                    element=element
                ):
                    # if is_mono_elment:
                    #     pprint(
                    #         f">>?? {element.get_info()['Name']}>> {volume} :: {area}"
                    #     )
                    #     pprint(ifcopenshell.util.element.get_psets(element=element))

                    if DEBUG_VERBOSE:
                        pprint(f">>>?ELEMENTTYPE: {is_mono_elment} {type(subelement)}?")

                    # Conditionally overwrite shape metrics if we have a multi material element
                    if False and not is_mono_elment:
                        ifc_shape = ifcopenshell.geom.create_shape(
                            settings=settings, inst=subelement
                        )
                        if ifc_shape:
                            element_geometry = ifc_shape.geometry
                            volume: float = ifcopenshell.util.shape.get_volume(
                                geometry=element_geometry
                            )
                            area: float = ifcopenshell.util.shape.get_side_area(
                                geometry=element_geometry, axis="Z"
                            )
                            length: float = ifcopenshell.util.shape.get_max_xy(
                                geometry=element_geometry
                            )
                        else:
                            pprint(
                                f">>>? WARN: Element has no shape? {type(subelement)}"
                            )

                    # TODO:
                    #       - check if we already filter out all unneded volumes
                    #       - check if we can move this inside the if clause
                    # length, area, volume = extract_metrics(element=subelement)

                    if ifc_name and (ifc_name in material_name_translation_dict):
                        tname = material_name_translation_dict[ifc_name]

                        if tname in passport_config_dict:
                            element_config = passport_config_dict[tname]
                        else:
                            passport_unknown_ifc_name_set.add(ifc_name)
                            # pprint(f"Not in passport_config_dict: {ifc_name}")
                            continue  # TODO: Handle this more gracefully

                        if tname in material_prices_dict:
                            element_prices = material_prices_dict[tname]
                        else:
                            prices_unknown_ifc_name_set.add(ifc_name)
                            continue  # TODO: Handle this more gracefully

                        density = float(element_config[0])

                        gwp = float(element_config[3])
                        ap = float(element_config[4])
                        penrt = float(element_config[5])

                        # if material_type !+ "single":
                        #     waste_grade = float(element_config[1])
                        # else:
                        #     waste_grade = float(element_config[1])
                        #     if surrounding_material_names[0]:
                        #
                        #     if surrounding_material_names[1]:

                        waste_grade = float(element_config[1])
                        recyclable_grade = float(element_config[2])

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
                        if element_prices[7] == "Fläche":
                            multiplier = area

                        elif element_prices[7] == "Masse":
                            multiplier = mass

                        elif element_prices[7] == "Länge":
                            multiplier = length

                        elif element_prices[7] == "Volumen":
                            multiplier = volume
                            # pprint(f"$?: {volume * float(element_prices[5])}")
                        else:
                            pprint(
                                f">>! ERROR: multiplier type {element_prices[7]} is unknown!"
                            )
                            multiplier: float = 1

                        elements_by_material[
                            tname
                        ].global_brutto_price += multiplier * float(element_prices[3])
                        elements_by_material[
                            tname
                        ].local_brutto_price += multiplier * float(element_prices[4])
                        elements_by_material[
                            tname
                        ].local_netto_price += multiplier * float(element_prices[5])

                        elements_by_material[tname].volume += volume
                        elements_by_material[tname].mass += mass
                        elements_by_material[tname].area += area
                        elements_by_material[tname].length += length

                        elements_by_material[tname].waste_mass += waste_mass
                        elements_by_material[tname].recyclable_mass += recyclable_mass

                        elements_by_material[tname].ap_ml += ap_ml
                        elements_by_material[tname].gwp_ml += gwp_ml
                        elements_by_material[tname].penrt_ml += penrt_ml

                        # For debugging the dict layout
                        # pprint(elements_by_material[tname].toJSON())

                        # assert elements_by_material[tname].waste_grade == elements_by_material[tname].recyclable_grade
                        # elements_by_material[tname].density = float(element_config[0])

                    else:
                        unknown_ifc_name_set.add(ifc_name)
                        # pprint(f"Not in material_name_translation_dict: {ifc_name}")

            i += 1

    pprint(
        "--------------------------------------------------------------------------------------",
        width=140,
        sort_dicts=True,
        compact=True,
    )

    pprint(
        object="Materials not yet in material passport file",
        width=140,
        sort_dicts=True,
        compact=True,
    )
    pprint(
        object=passport_unknown_ifc_name_set, width=140, sort_dicts=True, compact=True
    )
    pprint(
        "--------------------------------------------------------------------------------------",
        width=140,
        sort_dicts=True,
        compact=True,
    )

    pprint(
        object="Materials not yet in prices file",
        width=140,
        sort_dicts=True,
        compact=True,
    )
    pprint(object=prices_unknown_ifc_name_set, width=140, sort_dicts=True, compact=True)
    pprint(
        "--------------------------------------------------------------------------------------",
        width=140,
        sort_dicts=True,
        compact=True,
    )
    pprint(
        object="Names not yet in Materialname translation file",
        width=140,
        sort_dicts=True,
        compact=True,
    )
    pprint(object=unknown_ifc_name_set, width=140, sort_dicts=True, compact=True)
    pprint(
        "--------------------------------------------------------------------------------------",
        width=140,
        sort_dicts=True,
        compact=True,
    )
    pprint(
        object="Materials Found!",
        width=140,
        sort_dicts=True,
        compact=True,
    )
    pprint(
        object=elements_by_material,
        width=140,
        sort_dicts=True,
        compact=True,
    )
    pprint(
        "--------------------------------------------------------------------------------------",
        width=140,
        sort_dicts=True,
        compact=True,
    )

    pprint(
        f">>>> Schema used: {ifc_model.schema}",
        width=140,
        sort_dicts=True,
        compact=True,
    )
    pprint(f"Opened file: {ifc_file_path}", width=140, sort_dicts=True, compact=True)
    pprint(
        "--------------------------------------------------------------------------------------",
        width=140,
        sort_dicts=True,
        compact=True,
    )
    pprint(
        f">>>> Done within {time.time() - start}s",
        width=140,
        sort_dicts=True,
        compact=True,
    )
    pprint(
        "--------------------------------------------------------------------------------------",
        width=140,
        sort_dicts=True,
        compact=True,
    )
    return elements_by_material


# def extract_metrics(element: entity_instance) -> tuple[float, float, float]:

def material_metric_iterator(element: ifcopenshell.entity_instance) -> Iterator[tuple[str,float,float,float]]:
    if ifcopenshell.util.element.get_psets(element=element):
        pass


def material_name_iterator(
    element: ifcopenshell.entity_instance,
) -> Iterator[tuple[bool, entity_instance, str]]:
    if element.HasAssociations:
        for association in element.HasAssociations:
            if association.is_a("IfcRelAssociatesMaterial"):
                material_select = association.RelatingMaterial

                # Material layer set
                if material_select.is_a("IfcMaterialLayerSet"):
                    pprint(f">>>? {material_select.MaterialLayers}")
                    for layer in material_select.MaterialLayers:
                        if layer.Material:
                            yield False, layer, layer.Material.Name

                elif material_select.is_a("IfcMaterialList"):
                    pprint(f">>>? {material_select.Materials}")
                    for mat in material_select.Materials:
                        yield False, mat, mat.Name

                # Material layer set usage
                elif material_select.is_a("IfcMaterialLayerSetUsage"):
                    layer_set = material_select.ForLayerSet
                    pprint(f">>>? {layer_set.MaterialLayers}")
                    for layer in layer_set.MaterialLayers:
                        if layer.Material:
                            yield False, layer, layer.Material.Name
                        else:
                            if DEBUG:
                                pprint(
                                    f">>>! WARNING: No material name on {layer.get_info()}"
                                )

                # Material profile set
                elif material_select.is_a("IfcMaterialProfileSet"):
                    pprint(f">>>? {material_select.MaterialProfiles}")
                    for profile in material_select.MaterialProfiles:
                        if profile.Material:
                            yield False, profile, profile.Material.Name

                # Material profile set usage
                elif material_select.is_a("IfcMaterialProfileSetUsage"):
                    profile_set = material_select.ForProfileSet
                    pprint(f">>>? {profile_set.MaterialProfiles}")
                    for profile in profile_set.MaterialProfiles:
                        if profile.Material:
                            yield False, profile, profile.Material.Name

                # Constituent set
                # Can use GetFaction if implemented
                elif material_select.is_a("IfcMaterialConstituentSet"):
                    # pprint(f">>>< {material_select.get_info()}")
                    pprint(f">>>? {material_select.MaterialConstituents}")
                    for constituent in material_select.MaterialConstituents:
                        if constituent.Material:
                            yield False, constituent, constituent.Material.Name
                        else:
                            if DEBUG:
                                pprint(
                                    f">>>! WARNING: No material name on {layer.get_info()}"
                                )
                # Single material
                elif material_select.is_a("IfcMaterial"):
                    yield True, material_select, material_select.Name

                else:
                    pprint(
                        object=f">>>! Fall through: {element.get_info()}",
                        width=140,
                        sort_dicts=True,
                        compact=True,
                    )
            else:
                material = ifcopenshell.util.element.get_material(element=element)
                # TODO: Deconstruct multi material objects.
                if material:
                    material_name: str = (
                        ifcopenshell.util.element.get_material(element=element)
                        .get_info()
                        .get("Name")
                    )
                    if material_name:
                        yield True, element, material_name
                else:
                    if DEBUG:
                        pprint(
                            f">>>! No necessary association: {element.get_info()}",
                            width=140,
                            sort_dicts=True,
                            compact=True,
                        )
    else:
        if DEBUG:
            pprint(
                f">>>! No associations at all: {element.get_info()}",
                width=140,
                sort_dicts=True,
                compact=True,
            )


def decompose_area(settings, storey):

    total_area = 0
    elements = ifcopenshell.util.element.get_decomposition(
        element=storey, is_recursive=False
    )
    for element in elements:
        if element.is_a("IfcSlab"):
            element_shape = ifcopenshell.geom.create_shape(
                settings=settings, inst=element
            )
            element_geometry = element_shape.geometry
            # total_area += ifcopenshell.util.shape.get_side_area(
            #     geometry=element_geometry, axis="Z"
            # )
            height_min = ifcopenshell.util.shape.get_bottom_elevation(
                geometry=element_geometry
            )
            height_max = ifcopenshell.util.shape.get_top_elevation(
                geometry=element_geometry
            )
            area = ifcopenshell.util.shape.get_footprint_area(
                geometry=element_geometry,
                axis="Z",
            )
            pprint(
                f">>?? {element.get_info()['Name']}>> {height_min} :: {height_max} :: {area}"
            )
            total_area += area

    pprint(f"{storey.get_info()["Name"]}:{total_area}")
