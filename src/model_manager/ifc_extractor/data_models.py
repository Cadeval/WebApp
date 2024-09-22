from dataclasses import dataclass, field
import asyncio
import csv
import time
from pprint import pprint
from typing import Optional, Dict, Any, Set, List

import ifcopenshell

from model_manager.ifc_extractor.helpers import get_plot_area, read_config


@dataclass
class BuildingData:
    """
    Raw BIM Data
    TODO normalize from different import types
    """

    id: int = 0


@dataclass
class LocationData:
    pass


@dataclass
class EnergyData:
    pass


@dataclass
class BuildingMetrics:
    """
    Calculated Building Properties
    """

    tsa: float = 0  # Total something FIXME
    bf: float = 0  # Total Area of the plot Brutto Fläche
    bgf: float = 0  # BruttGo-Grundfläche (ÖNORM B 1800)
    bgf_bf: float = 0  # Ratio of BF to BGF
    ngf: float = 0  # Netto-Grundfläche (ÖNORM B 1800)
    nf: float = 0  # Nutzfläche (ÖNORM B 1800)
    kgf: float = 0  # Konstruktions-Grundfläche (ÖNORM B 1800)
    bri: float = 0  # Brutto-Rauminhalt (ÖNORM B 1800)
    eikon: float = 0  # EIKON value (example placeholder)
    energy_rating = "Unknown"  # Energy rating of the building
    floors: float = 0  # Number of floors
    facade_area: float = 0

@dataclass
class IfcExtractor:
    ifc_file_path: str
    weather_file_path: str = "../schema/AUT_Vienna.Schwechat.110360_IWEC.epw"
    config_file: str = "../schema/prices.csv"

    start: Optional[float] = field(init=False, default=None)
    config: Dict[str, Any] = field(init=False)
    ifc_model: ifcopenshell.file = field(init=False)
    application: Any = field(init=False)
    material_dict: Dict[str, Any] = field(default_factory=dict, init=False)
    material_set: Set[str] = field(default_factory=set, init=False)
    s: ifcopenshell.geom.settings = field(init=False)
    properties: Dict[str, Any] = field(
        default_factory=lambda: {
            "TSA": 0,  # Total site area
            "BF": 0,  # Total Area of the plot Brutto Fläche
            "BGF": 0,  # Brutto-Grundfläche (ÖNORM B 1800)
            "BGF/BF": 0,  # Ratio of BF to BGF
            "NGF": 0,  # Netto-Grundfläche (ÖNORM B 1800)
            "NF": 0,  # Nutzfläche (ÖNORM B 1800)
            "KGF": 0,  # Konstruktions-Grundfläche (ÖNORM B 1800)
            "BRI": 0,  # Brutto-Rauminhalt (ÖNORM B 1800)
            "EIKON": 0,  # EIKON value (example placeholder)
            "Energy Rating": "Unknown",  # Energy rating of the building
            "Floors": 0,  # Number of floors
            "Facade Area": 0,
        },
        init=False,
    )
    walls: List[Any] = field(default_factory=list, init=False)
    ceiling: List[Any] = field(default_factory=list, init=False)
    roof: List[Any] = field(default_factory=list, init=False)
    facade: List[Any] = field(default_factory=list, init=False)

    # Locks for thread safety
    properties_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    material_dict_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    material_set_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    walls_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    ceiling_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    roof_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    facade_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    def __post_init__(self):
        self.start = None
        self.config = read_config(self.config_file)
        self.ifc_model = ifcopenshell.open(self.ifc_file_path)
        pprint(f"Schema used: {self.ifc_model.schema}")
        self.application = self.ifc_model.by_type("IfcApplication")
        self.s = ifcopenshell.geom.settings()
        self.s.set(self.s.USE_MATERIAL_NAMES, True)
        self.s.set(self.s.WELD_VERTICES, True)
        self.s.set(self.s.USE_WORLD_COORDS, True)
        self.s.set(self.s.GENERATE_UVS, True)
        self.s.set(self.s.APPLY_DEFAULT_MATERIALS, True)

    async def get_ifc_units(self):
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
            except AttributeError:
                pprint("An error occurred while retrieving units.")
        return units

    async def calculate_norm1800(self, product) -> None:
        if product.is_a("IfcSpace"):
            product_shape = await asyncio.to_thread(ifcopenshell.geom.create_shape, self.s, product)
            product_geom = product_shape.geometry
            net_area = ifcopenshell.util.shape.get_footprint_area(product_geom)

            async with self.properties_lock:
                self.properties["NGF"] += net_area

            gross_area = ifcopenshell.util.shape.get_area(product_geom)
            async with self.properties_lock:
                self.properties["BGF"] += gross_area
                self.properties["KGF"] += gross_area - net_area

            height = ifcopenshell.util.shape.get_z(product_geom)
            async with self.properties_lock:
                self.properties["BRI"] += gross_area * height

        elif product.is_a() in ["IfcWall", "IfcColumn", "IfcBeam", "IfcSlab"]:
            product_shape = await asyncio.to_thread(ifcopenshell.geom.create_shape, self.s, product)
            product_geom = product_shape.geometry
            volume = ifcopenshell.util.shape.get_volume(product_geom)
            area = ifcopenshell.util.shape.get_area(product_geom)

            if (
                product.is_a("IfcSlab")
                and ifcopenshell.util.element.get_type(product) == "FLOOR"
            ):
                async with self.properties_lock:
                    self.properties["BGF"] += area

            async with self.properties_lock:
                self.properties["KGF"] += area
                self.properties["BRI"] += volume

        if product.is_a("IfcSpace"):
            pset_common = ifcopenshell.util.element.get_pset(
                product, "Pset_SpaceCommon"
            )
            if pset_common:
                if pset_common.get("IsExternal", False):
                    async with self.properties_lock:
                        self.properties["NGF"] -= net_area

    async def calculate_facade_area(self, product) -> None:
        if product.is_a() in ["IfcWall", "IfcCurtainWall", "IfcWindow", "IfcDoor"]:
            if not ifcopenshell.util.element.get_pset(product, "IsExternal"):
                product_shape = await asyncio.to_thread(ifcopenshell.geom.create_shape, self.s, product)
                product_geom = product_shape.geometry
                area = ifcopenshell.util.shape.get_area(product_geom)
                async with self.properties_lock:
                    self.properties["Facade Area"] += area

    async def calculate_material_cost(self, product) -> None:
        try:
            materials = ifcopenshell.util.element.get_materials(
                product, should_inherit=True
            )
            if materials:
                if len(materials) > 1:
                    pass
                for material in materials:
                    material_name = material.get_info()["Name"]
                    if material_name not in self.config:
                        async with self.material_set_lock:
                            self.material_set.add(material_name)
                    else:
                        cost = self.adjust_material_cost(
                            material_name, self.properties["Facade Area"]
                        )
                        async with self.material_dict_lock:
                            self.material_dict[material_name] = (
                                self.material_dict.get(material_name, 0) + cost
                            )

                        if "Wall" in material_name:
                            async with self.walls_lock:
                                self.walls.append([material_name, "m2", cost])
                        elif "Ceiling" in material_name:
                            async with self.ceiling_lock:
                                self.ceiling.append([material_name, "m2", cost])
                        elif "Floor" in material_name:
                            async with self.walls_lock:
                                self.floor.append([material_name, "m2", cost])
                        elif "Roof" in material_name:
                            async with self.roof_lock:
                                self.roof.append([material_name, "m2", cost])

                        if self.properties["Facade Area"] > 0:
                            async with self.facade_lock:
                                self.facade.append(
                                    [
                                        material_name,
                                        "m2",
                                        cost,
                                        self.properties["Facade Area"],
                                    ]
                                )

        except Exception as e:
            print(f"Failed to extract materials because {e}.")

    def adjust_material_cost(self, material_name, area) -> int:
        base_cost = int(self.config[material_name])
        if "Insulation" in material_name:
            base_cost *= 1.1
        elif "Glass" in material_name:
            base_cost *= 1.2
        elif "Metal" in material_name:
            base_cost *= 1.15
        return base_cost * area

    async def process_product(self, product, i, product_quantity, progress_recorder):
        try:
            pprint(f"{i}/{product_quantity} - properties of {product.id()}")
            pprint(f"Calculating Norm 1800 for {product.id()}")
            await self.calculate_norm1800(product=product)
            pprint(f"Calculating Facade Area for {product.id()}")
            await self.calculate_facade_area(product=product)
            pprint(f"Calculating Material Cost for {product.id()}")
            await self.calculate_material_cost(product=product)
        except Exception as e:
            pprint(f"Shape creation failed because {e}.")
        if progress_recorder:
            progress_recorder.set_progress(
                i + 1, product_quantity, description="Processing products"
            )

    async def process_products(self, progress_recorder=None) -> None:
        self.start = time.time()
        products = self.ifc_model.by_type("IfcProduct")
        product_quantity = len(products)

        try:
            site = self.ifc_model.by_type("IfcSite")[0]
            self.properties["TSA"] = await asyncio.to_thread(get_plot_area, site)
        except Exception:
            print(f"Failed to extract plot area")

        try:
            storeys = self.ifc_model.by_type("IfcBuildingStorey")
            self.properties["Floors"] = len(storeys)

            for storey in storeys:
                area = await asyncio.to_thread(get_plot_area, storey)
                async with self.properties_lock:
                    self.properties["BGF"] += area
                if storey.get_info()["Elevation"] == 0:
                    async with self.properties_lock:
                        self.properties["BF"] = area
        except Exception:
            print(f"Failed to extract floor area.")

        tasks = []
        for i, product in enumerate(products):
            if product.Representation is not None:
                task = asyncio.create_task(
                    self.process_product(product, i, product_quantity, progress_recorder)
                )
                tasks.append(task)

        await asyncio.gather(*tasks)

        if self.properties["BF"] != 0:
            self.properties["BGF/BF"] = self.properties["BGF"] / self.properties["BF"]

        print(
            f"\nFinished extracting {product_quantity} objects in {time.time() - self.start}s at a rate of {product_quantity / (time.time() - self.start)} objects/s."
        )

    def calculate_additional_properties(self):
        self.properties["EIKON"] = self.properties.get("Area", 0) * 0.1
        self.properties["BGF"] = self.properties.get("Area", 0) * 0.9
        self.properties["NF"] = self.properties.get("Volume", 0) * 0.05
        if self.properties["BGF"] != 0:
            self.properties["BF/BGF"] = self.properties["BF"] / self.properties["BGF"]
        self.properties["Energy Rating"] = "C"
