# -*- coding: utf-8 -*-
import csv
import gc
import multiprocessing
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pprint import pprint
from collections.abc import Iterator

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

from model_manager.ifc_extractor.data_models import (
    material_passport_element,
    gebäude_kenndaten,
    material_name_translation_dict,
    material_accumulator,
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

# except ImportError:
#     pprint("""Cannot import ifcopenshell. This is necessary to run the program.
#            Please install it via pip or conda and retry.""")


def read_config(config_file: str) -> dict[str, str | int]:
    config_dict: dict[str, str | int] = {}
    with open(config_file, newline="") as csvfile:
        reader = csv.reader(csvfile, delimiter=";", quotechar="'")
        for row in reader:
            config_dict[row[0]] = row[1]
    return config_dict


unimp_name_set: set[str | str] = set()
unimp_name_set2: set[str | str] = set()


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


def ifc_product_walk(
    ifc_file_path: str,
) -> tuple[
    defaultdict[str, material_accumulator], dict[str, list[float | str] | float | str]
]:
    """
    -> list[entity_instance]
    """
    properties = gebäude_kenndaten
    # pprint(product.get_info_2(recursive=True))
    # VERBOSE = True

    gc.unfreeze()
    _ = gc.collect()

    start = time.time()

    pprint(">>> Starting up.", width=140, sort_dicts=True, compact=True)
    settings = ifcopenshell.geom.settings()

    # Already set to True by default
    settings.set(settings.WELD_VERTICES, True)
    settings.set(settings.GENERATE_UVS, True)

    settings.set(settings.ELEMENT_HIERARCHY, True)
    settings.set(settings.KEEP_BOUNDING_BOXES, True)

    settings.set(settings.USE_MATERIAL_NAMES, True)
    settings.set(settings.PRECISION, 1e-016)

    pprint(">>> Loading ifc file.", width=140, sort_dicts=True, compact=True)
    ifc_model: ifcopenshell.file = ifcopenshell.open(ifc_file_path, should_stream=False)

    # Dictionary to store elements by material
    iterator = ifcopenshell.geom.iterator(
        settings, ifc_model, multiprocessing.cpu_count()
    )
    gc.freeze()
    elements_by_material: defaultdict[str, material_accumulator] = defaultdict(
        material_accumulator
    )

    storeys = ifc_model.by_type("IfcBuildingStorey")
    properties["Stockwerke"] = len(storeys)
    eg_found = False
    for storey in storeys:
        storey_name = storey.get_info()["Name"]
        if storey_name == "EG":
            eg_found = True
            ground_floor = storey

    if not eg_found:
        raise ValueError("Storeys do not contain one with the name EG")
    else:
        del eg_found
        del storeys

    # TODO: Figure out if we need to enable recursive for some files
    ground_floor_elements = ifcopenshell.util.element.get_decomposition(
        element=ground_floor, is_recursive=True
    )
    material_prices_dict = read_config("../schema/prices.csv")
    passport_config_dict = read_passport_config(
        "../schema/material_passport_house_a_b.csv"
    )

    pprint(
        ">>> Now iterating over the elements.", width=140, sort_dicts=True, compact=True
    )
    accumulator_dict = {}

    if iterator.initialize():
        i = 0
        while iterator.next():
            element_shape = iterator.get()
            element_geometry = element_shape.geometry
            element: ifcopenshell.entity_instance = ifc_model.by_guid(
                element_shape.guid
            )

            vol = ifcopenshell.util.shape.get_volume(geometry=element_geometry)
            area = ifcopenshell.util.shape.get_side_area(
                geometry=element_geometry, axis="Z"
            )

            properties["BRI"][0] += vol
            properties["BGF"][0] += area

            if element in ground_floor_elements:
                properties["BF"][0] += area

            if element.is_a("IfcSpace"):
                properties["NRF"][0] += area

            else:
                properties["KGF"][0] += area

            # Ignore all spaces and similar, they do not have good materials
            if not (
                element.is_a("IfcSpace")
                or element.is_a("IfcDoor")
                or element.is_a("IfcWindow")
            ):
                populate_material_set(
                    element=element,
                    volume=vol,
                    passport_config_dict=passport_config_dict,
                    elements_by_material=elements_by_material,
                )
                # pprint(elements_by_material)

            i += 1
        # WARN: Since we don't do deductions yet, assume NUF == NRF and BF == BGF
        # properties["NUF"][0] = properties["NRF"][0]
        # properties["BRI/NUF"] = properties["BRI"][0] / properties["NUF"][0]

        properties["GF"][0] = properties["BF"][0] + properties["UF"][0]
        properties["BGF/BF"][0] = properties["BGF"][0] / properties["BF"][0]
        properties["BRI/BGF"][0] = properties["BRI"][0] / properties["BGF"][0]

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
    pprint(object=unimp_name_set, width=140, sort_dicts=True, compact=True)
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
    pprint(object=unimp_name_set2, width=140, sort_dicts=True, compact=True)
    pprint(
        "--------------------------------------------------------------------------------------",
        width=140,
        sort_dicts=True,
        compact=True,
    )

    pprint("Properties:", width=140, sort_dicts=True, compact=True)
    pprint(object=properties, width=140, sort_dicts=True, compact=True)
    pprint(
        "--------------------------------------------------------------------------------------",
        width=140,
        sort_dicts=True,
        compact=True,
    )

    pprint(f"Schema used: {ifc_model.schema}", width=140, sort_dicts=True, compact=True)
    pprint(f"Opened file: {ifc_file_path}", width=140, sort_dicts=True, compact=True)
    pprint(
        "--------------------------------------------------------------------------------------",
        width=140,
        sort_dicts=True,
        compact=True,
    )
    pprint(
        f"Done within {time.time() - start}s", width=140, sort_dicts=True, compact=True
    )
    for key in elements_by_material.keys():
        pprint(elements_by_material[key].volume)
    return elements_by_material, properties


def populate_material_set(
    element: ifcopenshell.entity_instance,
    volume: float,
    passport_config_dict: dict[
        str,
        tuple[
            int | float | str,
            int | float | str,
            int | float | str,
            int | float | str,
            int | float | str,
            int | float | str,
        ],
    ],
    elements_by_material: defaultdict[str, material_accumulator],
):

    for ifc_name in material_name_iterator(element=element):

        if ifc_name and (ifc_name in material_name_translation_dict):
            tname = material_name_translation_dict[ifc_name]
            element_passport = material_passport_element()

            element_passport.volume = volume
            if tname and (tname in passport_config_dict.keys()):
                element_config = passport_config_dict[tname]

                element_passport.density = float(element_config[0])

                element_passport.gwp = float(element_config[3])
                element_passport.ap = float(element_config[4])
                element_passport.penrt = float(element_config[5])

                element_passport.waste_grade = float(element_config[1])
                element_passport.recyclable_grade = float(element_config[2])

                element_passport.mass = (
                    element_passport.density * element_passport.volume
                )
                element_passport.waste_mass = (
                    element_passport.mass * element_passport.waste_grade
                )
                element_passport.recyclable_mass = (
                    element_passport.mass * element_passport.recyclable_grade
                )

                element_passport.ap_ml = element_passport.mass * element_passport.ap
                element_passport.gwp_ml = element_passport.mass * element_passport.gwp
                element_passport.penrt_ml = (
                    element_passport.mass * element_passport.penrt
                )

                elements_by_material[tname].volume += element_passport.volume
                elements_by_material[tname].mass += element_passport.mass

                elements_by_material[tname].waste_mass += element_passport.waste_mass
                elements_by_material[
                    tname
                ].recyclable_mass += element_passport.recyclable_mass

                elements_by_material[tname].ap_ml += element_passport.ap_ml
                elements_by_material[tname].gwp_ml += element_passport.gwp_ml
                elements_by_material[tname].penrt_ml += element_passport.penrt_ml

            # assert elements_by_material[tname].waste_grade == elements_by_material[tname].recyclable_grade            elements_by_material[tname].density = float(element_config[0])

            else:
                unimp_name_set.add(ifc_name)
                # pprint(f"Not in passport_config_dict: {ifc_name}")
        else:
            unimp_name_set2.add(ifc_name)
            # pprint(f"Not in material_name_translation_dict: {ifc_name}")


def material_name_iterator(element: ifcopenshell.entity_instance) -> Iterator[str]:

    if element.HasAssociations:
        for association in element.HasAssociations:
            if association.is_a("IfcRelAssociatesMaterial"):
                material_select = association.RelatingMaterial

                # Single material
                if material_select.is_a("IfcMaterial"):
                    yield material_select.Name

                # Material layer set
                elif material_select.is_a("IfcMaterialLayerSet"):
                    for layer in material_select.MaterialLayers:
                        if layer.Material:
                            yield layer.Material.Name

                # Material list
                elif material_select.is_a("IfcMaterialList"):
                    for mat in material_select.Materials:
                        yield mat.Name

                # Material layer set usage
                elif material_select.is_a("IfcMaterialLayerSetUsage"):
                    layer_set = material_select.ForLayerSet
                    for layer in layer_set.MaterialLayers:
                        if layer.Material:
                            yield layer.Material.Name

                # Material profile set
                elif material_select.is_a("IfcMaterialProfileSet"):
                    for profile in material_select.MaterialProfiles:
                        if profile.Material:
                            yield profile.Material.Name

                # Material profile set usage
                elif material_select.is_a("IfcMaterialProfileSetUsage"):
                    profile_set = material_select.ForProfileSet
                    for profile in profile_set.MaterialProfiles:
                        if profile.Material:
                            yield profile.Material.Name

                # Constituent set
                elif material_select.is_a("IfcMaterialConstituentSet"):
                    for constituent in material_select.MaterialConstituents:
                        if constituent.Material:
                            yield constituent.Material.Name
                else:
                    pprint(
                        f"Fall through: {element.get_info()}",
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
                        yield material_name
                else:
                    pprint(
                        f"No necessary association: {element.get_info()}",
                        width=140,
                        sort_dicts=True,
                        compact=True,
                    )
    else:
        pass
        # pprint(f"No associations at all: {element.get_info()}", width=140, sort_dicts=True, compact=True)
