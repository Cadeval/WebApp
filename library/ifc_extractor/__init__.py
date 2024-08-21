# -*- coding: utf-8 -*-

# from numba import jit
# import multiprocessing
# from OCC.Extend.DataExchange import read_step_file
# from OCC.Display.WebGl import threejs_renderer
# from OCC.Core.BRep import BRep_Builder, BRep_Tool
# from OCC.Core.TopoDS import TopoDS_Shape, topods_Vertex, topods_Compound
# from scipy.spatial import Delaunay
# from OCC.Display.WebGl import x3dom_renderer
# from OCC.Core import BRep
# from OCC.Core import BRepTools
# from OCC.Core.BRepGProp import brepgprop_VolumeProperties
# from OCC.Core.GProp import GProp_GProps

# from OCC.Core import TopAbs
# from OCC.Core import TopoDS
# from OCC.Core import TopExp
import csv
import time
from pprint import pprint

# from collections import defaultdict
# from ifcopenshell.ifcopenshell_wrapper import set_feature
# from mathutils import Vector
# import numpy as np
# import multiprocessing
import ifcopenshell
import ifcopenshell.api
import ifcopenshell.express
import ifcopenshell.express.rules
import ifcopenshell.file
import ifcopenshell.geom

# import ifcopenshell.geom.occ_utils
import ifcopenshell.util
import ifcopenshell.util.element
import ifcopenshell.util.schema
import ifcopenshell.util.selector
import ifcopenshell.util.shape
import ifcopenshell.util.unit

# import ifcopenshell.api.owner

# import ifcopenshell.api.owner.settings
# import ifcopenshell.api.material
# import ifcopenshell.api.geometry
import ifcopenshell.validate

# from IfcOpenHouse.ios_utils import (
#     IfcOpenShellPythonAPI, placement_matrix, clipping, ColourRGB, TerrainBuildMethod,
#     build_native_bspline_terrain, build_tesselated_occ_terrain, ios_entity_overwrite_hook
# )


def read_config(config_file: str) -> dict:
    config_dict: dict = {}
    with open(config_file, newline="") as csvfile:
        reader = csv.reader(csvfile, delimiter=";", quotechar="'")

        for row in reader:
            config_dict[row[0]] = row[1]

    return config_dict


class IfcExtractor(object):
    def __init__(self, ifc_file_path: str):
        # Load the configuration for material prices from a CSV file
        self.config = read_config("data/schema/prices.csv")

        # Set the path to your IFC file and weather file TODO dynamically override this based on the user config
        self.weather_file_path = "data/schema/AUT_Vienna.Schwechat.110360_IWEC.epw"
        self.ifc_file_path = ifc_file_path

        # Open the IFC model from the given file path
        self.ifc_model = ifcopenshell.open(self.ifc_file_path)

        # Retrieve the application information from the IFC model
        self.application = self.ifc_model.by_type("IfcApplication")

        # Initialize an empty dictionary to store material costs
        self.material_dict = dict()
        self.material_set = set()

        # Initialize the geometry settings for shape creation
        self.s = ifcopenshell.geom.settings()

        # Disable the use of Python OpenCascade for geometry processing
        # This implies USE_WORLD_COORDS USE_BREP_DATA and DISABLE_TRIANGULATION. The serialized TopoDS_Shape of USE_BREP_DATA is deserialized by Python OpenCASCADE.
        # self.s.set(self.s.USE_PYTHON_OPENCASCADE, False)

        # Disable the use of material names in the geometry processing
        self.s.set(self.s.USE_MATERIAL_NAMES, True)

        # Enable welding vertices to generate normals, which makes the process slower
        self.s.set(self.s.WELD_VERTICES, True)

        # Use world coordinates to ignore transformations in the geometry
        self.s.set(self.s.USE_WORLD_COORDS, True)

        # Potentially faster
        # self.s.set(self.s.BOOLEAN_ATTEMPT_2D, True)

        # Keep bounding boxes for the geometries
        # self.s.set(self.s.KEEP_BOUNDING_BOXES, True)
        # 'APPLY_DEFAULT_MATERIALS',
        #  'APPLY_LAYERSETS',
        #  'BOOLEAN_ATTEMPT_2D',
        #  'BUILDING_LOCAL_PLACEMENT',
        #  'CONVERT_BACK_UNITS',
        #  'DEBUG_BOOLEAN',
        #  'DEFAULT_PRECISION',
        #  'DISABLE_BOOLEAN_RESULT',
        #  'DISABLE_OPENING_SUBTRACTIONS',
        #  'DISABLE_TRIANGULATION',
        #  'EDGE_ARROWS',
        #  'ELEMENT_HIERARCHY',
        #  'EXCLUDE_SOLIDS_AND_SURFACES',
        #  'GENERATE_UVS',
        #  'INCLUDE_CURVES',
        #  'LAYERSET_FIRST',
        #  'NO_NORMALS',
        #  'NO_WIRE_INTERSECTION_CHECK',
        #  'NO_WIRE_INTERSECTION_TOLERANCE',
        #  'NUM_SETTINGS',
        #  'SEW_SHELLS',
        #  'SITE_LOCAL_PLACEMENT',
        #  'STRICT_TOLERANCE',
        #  'USE_BREP_DATA',
        #  'USE_ELEMENT_GUIDS',
        #  'USE_ELEMENT_HIERARCHY',
        #  'USE_ELEMENT_NAMES',
        #  'USE_ELEMENT_STEPIDS',
        #  'USE_ELEMENT_TYPES',
        #  'USE_MATERIAL_NAMES',
        #  'USE_WORLD_COORDS',
        #  'USE_Y_UP',
        #  'VALIDATE_QUANTITIES',
        #  'WELD_VERTICES',
        #  'WRITE_GLTF_ECEF',

        # Generate UV mappings for the geometries
        self.s.set(self.s.GENERATE_UVS, True)

        # Apply default materials, which is required for glTF serialization
        self.s.set(self.s.APPLY_DEFAULT_MATERIALS, True)

        # Print all available settings (this line is commented out)
        # pprint(dir(self.s))

        # Initialize the properties dictionary to store various metrics

        #     "NF": "12,82",
        #     "BF/BGF": "0,69",
        self.properties = {
            "TSA": 0,  # Total something FIXME
            "BF": 0,  # Total Area of the plot Brutto Fläche
            "BGF": 0,  # BruttGo-Grundfläche (ÖNORM B 1800)
            "BGF/BF": 0,  # Ratio of BF to BGF
            "NGF": 0,  # Netto-Grundfläche (ÖNORM B 1800)
            "NF": 0,  # Nutzfläche (ÖNORM B 1800)
            "KGF": 0,  # Konstruktions-Grundfläche (ÖNORM B 1800)
            "BRI": 0,  # Brutto-Rauminhalt (ÖNORM B 1800)
            "EIKON": 0,  # EIKON value (example placeholder)
            "Energy Rating": "Unknown",  # Energy rating of the building
            "Floors": 0,  # Number of floors
            "Facade Area": 0,
        }
        # List of materials and costs
        self.walls = []
        self.ceiling = []
        self.roof = []
        self.facade = []

        # Probably metric or imperial
        self.units = self.get_ifc_units()
        # pprint(self.units)

    # Retrieve the units used in the IFC file
    def get_ifc_units(self):
        units_assignment = self.ifc_model.by_type("IfcUnitAssignment")[0]
        units = {}
        for unit in units_assignment.Units:
            try:
                unit_type = unit.UnitType
                if unit.is_a("IfcSIUnit"):
                    unit_name = unit.Name
                    units[unit_type] = unit_name
                elif unit.is_a("IfcDerivedUnit"):
                    unit_name = unit.UserDefinedType or "Derived Unit"
                    units[unit_type] = unit_name
            except AttributeError as e:
                pprint(e)
        return units

    def get_plot_area(self, site):
        # Find the IfcSite

        # Try to get the area from properties
        pset = ifcopenshell.util.element.get_pset(site, "Pset_SiteCommon")
        if pset and "TotalArea" in pset:
            # pprint("Using IfcSite TotalArea Pset")
            return pset["TotalArea"]

        # If not in properties, try to calculate from geometry
        elif site.Representation:
            # pprint("Using IfcSite Representation")
            settings = ifcopenshell.geom.settings()
            shape = ifcopenshell.geom.create_shape(settings, site)
            face = ifcopenshell.util.shape.get_largest_face(shape.geometry)
            return ifcopenshell.util.shape.get_area(face)

        else:
            # If no geometry, look for related IfcSpace entities
            # pprint("Using IfcSpace entities")
            spaces = ifcopenshell.util.element.get_decomposition(site)
            total_area = sum(
                self.get_space_area(space) for space in spaces if space.is_a("IfcSpace")
            )

            return total_area

    def get_space_area(self, space):
        pset = ifcopenshell.util.element.get_pset(space, "Pset_SpaceCommon")
        if pset and "GrossFloorArea" in pset:
            return pset["GrossFloorArea"]
        elif space.Representation:
            settings = ifcopenshell.geom.settings()
            shape = ifcopenshell.geom.create_shape(settings, space)
            return ifcopenshell.util.shape.get_footprint_area(shape.geometry)
        return 0

    def calculate_norm1800(self, product) -> None:
        if product.is_a("IfcSpace"):
            # pprint(f"Area properties for {product.id()}")
            # Calculate Netto-Grundfläche (NGF)
            product_shape = ifcopenshell.geom.create_shape(self.s, inst=product)
            product_geom = product_shape.geometry
            net_area = ifcopenshell.util.shape.get_footprint_area(product_geom)

            # Adjust for specific ÖNORM rules

            self.properties["NGF"] += net_area

            # Calculate Brutto-Grundfläche (BGF)
            gross_area = ifcopenshell.util.shape.get_area(product_geom)
            self.properties["BGF"] += gross_area

            # Calculate Konstruktions-Grundfläche (KGF)
            self.properties["KGF"] += gross_area - net_area

            # Calculate Brutto-Rauminhalt (BRI) assuming z axis is pointing upwards
            # TODO check if we can get the proper axis from the ifc file itself
            height = ifcopenshell.util.shape.get_z(product_geom)
            self.properties["BRI"] += gross_area * height

        elif product.is_a() in ["IfcWall", "IfcColumn", "IfcBeam", "IfcSlab"]:
            product_shape = ifcopenshell.geom.create_shape(self.s, inst=product)
            product_geom = product_shape.geometry

            # These elements contribute to KGF and BRI
            # pprint(f"Volume properties for {product.id()}")
            volume = ifcopenshell.util.shape.get_volume(product_geom)
            area = ifcopenshell.util.shape.get_area(product_geom)

            if (
                product.is_a("IfcSlab")
                and ifcopenshell.util.element.get_type(product) == "FLOOR"
            ):
                # Floor slabs contribute to BGF and GFA
                self.properties["BGF"] += area

            self.properties["KGF"] += area
            self.properties["BRI"] += volume

        # External spaces are not included in NGF according to ÖNORM B 1800
        if product.is_a("IfcSpace"):
            pset_common = ifcopenshell.util.element.get_pset(
                product, "Pset_SpaceCommon"
            )
            if pset_common:
                if pset_common.get("IsExternal", False):
                    # External spaces are not included in NGF according to ÖNORM B 1800
                    self.properties["NGF"] -= net_area

    def calculate_facade_area(self, product) -> None:
        if product.is_a() in ["IfcWall", "IfcCurtainWall", "IfcWindow", "IfcDoor"]:
            # pprint(ifcopenshell.util.element.get_psets(product))
            if not ifcopenshell.util.element.get_pset(product, "IsExternal"):
                product_shape = ifcopenshell.geom.create_shape(self.s, inst=product)

                product_geom = product_shape.geometry
                self.properties["Facade Area"] += ifcopenshell.util.shape.get_area(
                    product_geom
                )

    def calculate_material_cost(self, product) -> None:
        try:
            materials = ifcopenshell.util.element.get_materials(
                product, should_inherit=True
            )

            if materials:
                if len(materials) > 1:
                    pass
                    # print(f">>>>WARN MULTIPLE MATERIALS IN {product.id()} -> {materials}")

                for material in materials:
                    material_name = material.get_info()["Name"]
                    if material_name not in self.config:
                        self.material_set.add(material_name)
                    else:
                        cost = self.adjust_material_cost(
                            material_name, self.properties["Facade Area"]
                        )

                        if material_name not in self.material_dict:
                            self.material_dict[material_name] = cost
                        else:
                            self.material_dict[material_name] += cost

                        # Example of material category handling
                        if "Wall" in material_name:
                            # self.properties["Walls"].append([material_name, "m2", cost])
                            self.walls.append([material_name, "m2", cost])

                        elif "Ceiling" in material_name:
                            # self.properties["Ceiling"].append([material_name, "m2", cost])
                            self.ceiling.append([material_name, "m2", cost])

                        elif "Floor" in material_name:
                            # self.properties["Floor Panels"].append([material_name, "m2", cost])
                            self.floor.append([material_name, "m2", cost])

                        elif "Roof" in material_name:
                            # self.properties["Roof"].append([material_name, "m2", cost])
                            self.roof.append([material_name, "m2", cost])

                        if self.properties["Facade Area"] > 0:
                            # self.properties["Facade"].append([material_name, "m2", cost, self.properties["Facade Area"]])
                            self.facade.append(
                                [
                                    material_name,
                                    "m2",
                                    cost,
                                    self.properties["Facade Area"],
                                ]
                            )

        except Exception as e:
            print(f"Failed to extract materials because {e}")

    def adjust_material_cost(self, material_name, area):
        base_cost = int(self.config[material_name])

        # Apply Önormen adjustments (example - you'll need to implement actual standards)
        if "Insulation" in material_name:
            # ÖNORM B 6400 for thermal insulation
            base_cost *= 1.1  # 10% increase for meeting thermal standards
        elif "Glass" in material_name:
            # ÖNORM B 3716 for glass in building
            base_cost *= 1.2  # 20% increase for meeting glass standards
        elif "Metal" in material_name:
            # ÖNORM B 3806 for metal facades
            base_cost *= 1.15  # 15% increase for meeting metal facade standards

        return base_cost * area

    def process_products(self):
        self.start = time.time()
        # Get all objects
        # TODO add a tree traversal using IfcRoot and IfcTree on upload
        products = self.ifc_model.by_type("IfcProduct")
        product_quantity = len(products)

        # TODO Enable caching, currently broken, fix this
        # ifcopenshell.geom.utils.cache_factory = ifcopenshell.geom.utils.DiskCache

        # TODO check what this does
        # ifcopenshell.geom.utils.USE_OCCT_HANDLE = True

        # Set cache directory (optional)
        # ifcopenshell.geom.utils.cache_directory = "/path/to/cache"

        # TODO this is literally too slow smh
        # iterator = ifcopenshell.geom.iterator(self.s, self.ifc_model, multiprocessing.cpu_count())
        # if iterator.initialize():
        #     while iterator.next():
        #         self.i += 1
        #         shape = iterator.get()
        #         product = self.ifc_model.by_guid(shape.guid)

        # iterator = ifcopenshell.geom.iterator(self.s, self.ifc_model, multiprocessing.cpu_count())
        # if iterator.initialize():
        #     while iterator.next():
        #         self.i += 1

        try:
            site = self.ifc_model.by_type("IfcSite")[0]
            # Assuming there's only one site
            self.properties["TSA"] = self.get_plot_area(site)

        except Exception as e:
            print(f"Failed to extract plot area because {e}")
        try:
            # TODO Add handler for multi building projects
            # Get all building storeys
            storeys = self.ifc_model.by_type("IfcBuildingStorey")
            self.properties["Floors"] = len(storeys)

            for storey in storeys:
                self.properties["BGF"] += self.get_plot_area(storey)
                if storey.get_info()["Elevation"] == 0:
                    self.properties["BF"] = self.get_plot_area(storey)
                # pprint(storey.get_info())

        except Exception as e:
            print(f"Failed to extract floor area because {e}")

        for i, product in enumerate(products):
            if product.Representation is not None:
                # print(ifcopenshell.util.element.get_psets(product))
                try:
                    pprint(f"{i}/{product_quantity} - properties of {product.id()}")

                    # Update the flächeneffizienz values
                    self.calculate_norm1800(product=product)

                    # Get the surfae area of outside facing objects
                    self.calculate_facade_area(product=product)

                    # Update the total cost per material type
                    self.calculate_material_cost(product=product)

                except Exception as e:
                    print(f"Shape creation failed because {e}")
        self.properties["BGF/BF"] = self.properties["BGF"] / self.properties["BF"]

        # Post-process calculations and fill in other property fields
        # self.calculate_additional_properties()
        pprint(self.properties)
        # pprint(self.material_dict)
        print(
            f"\nFinished extracting {product_quantity} objects in {time.time() - self.start}s at a rate of { product_quantity / (time.time() - self.start) } objects/s."
        )

    def calculate_additional_properties(self):
        # Example calculations for additional properties
        # TODO implement me
        self.properties["EIKON"] = self.properties["Area"] * 0.1  # Example factor
        self.properties["BGF"] = self.properties["Area"] * 0.9  # Example factor
        self.properties["NF"] = self.properties["Volume"] * 0.05  # Example factor
        self.properties["BF/BGF"] = self.properties["BF"] / self.properties["BGF"]

        # Example static assignment for Energy Rating
        self.properties["Energy Rating"] = (
            "C"  # This should be calculated based on actual criteria
        )
