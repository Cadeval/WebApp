from dataclasses import dataclass, field
from pprint import pprint
from typing import Any

try:
    import ifcopenshell
    import ifcopenshell.geom
    import ifcopenshell.util
    import ifcopenshell.util.element
    import ifcopenshell.util.shape
except ImportError:
    pprint("""Cannot import ifcopenshell. This is necessary to run the program.
           Please install it via pip or conda and retry.""")

# from model_manager.ifc_extractor.helpers import read_config


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

# The spacial indicators can be split into these:
# BGF (Brutto-Grundfläche / Gross Floor Area)
# └── KF (Konstruktionsfläche / Construction Area)
# └── NGF (Netto-Grundfläche) = NRF (Netto-Raumfläche)
#     ├── NUF (Nutzungsfläche / Usable Area)
#     ├── VF (Verkehrsfläche / Circulation Area)
#     └── TF (Technische Funktionsfläche / Technical Area)
# TODO: Move to own dataclass or smth
gebäude_kenndaten: dict[str, list[float | str] | float | str] = {
    # Flächen des Grundstückes
    "GF":  [0.0, "m2"],               # Grundstücksfläche
    "BF":  [0.0, "m2"],               # Bebaute Fläche
    "UF":  [0.0, "m2"],               # Unbebaute Fläche

    # Brutto Rauminhalt/Grundfläche des Bauwerkes
    "BRI": [0.0, "m3"],               # Brutto-Rauminhalt (ÖNORM B 1800)
    "BGF": [0.0, "m2"],               # Brutto-Grundfläche (ÖNORM B 1800)

    # Grundfl.0ächen des Bauwerkes, summ up to BGF
    "KGF": [0.0, "m2"],               # Konstruktions-Grundfläche (ÖNORM B 1800)
    "NRF": [0.0, "m2"],               # Netto-Raumfläche (ÖNORM B 1800)

    # Summ up to NRF
    # "NUF": [0, "m2"],               # Nutzungsfläche (ÖNORM B 1800)
    # "TF":  [0, "m2"],               # Technikfläche (ÖNORM B 1800)
    # "VF":  [0, "m2"],               # Verkehrsfläche (ÖNORM B 1800)


    # Andere Ratios bzw Werte
    "BGF/BF":  [0.0],
    "BRI/BGF": [0.0],

    # "BRI/NUF": [0],

    "Facade Area": [0.0, "m2"],
    "Stockwerke": 0.0,                # Number of floors
    "Energy Rating": "Unknown",     # Energy rating of the building

}

material_name_translation_dict: dict[str,str] = {
    'Belag, Fliesen': "Belag Fliesen",

    'Belag, Parkett': "Parkett",
    # Beton
    "Beton, Stahlbeton":       "Stahlbeton",
    "Beton, Stahlbeton Wand":  "Stahlbeton",
    "Beton, Stahlbeton Decke": "Stahlbeton",

    "Beton, Fertigteil": "Beton Fertigteil",

    'Boden, Pflaster': "Boden Pflaster",
    'Dränschicht': "Dränschicht",

    # Dämmung
    "Dämmung, hart XPS": "Dämmung hart XPS",

    'Dämmung, Holzwolledämmplatte (1)': "Dämmung Holzwolledämmplatte",
    'Dämmung, Holzwolledämmplatte': "Dämmung Holzwolledämmplatte",

    'Estrich': "Estrich",

    'Holz, Holzwerkstoff': "Holzwerkstoff",

    'Dämmung, weich Zellulose (1)': "Dämmung weich Zellulose",
    'Dämmung, weich Zellulose': "Dämmung weich Zellulose",

    'Dämmung, Mineralwolle': "Dämmung Mineralwolle",
    'Dämmung, weich Mineralwolle (1)': "Dämmung weich Mineralwolle",
    'Dämmung, weich Mineralwolle IW02': "Dämmung weich Mineralwolle",
    'Dämmung, weich Mineralwolle IW03': "Dämmung weich Mineralwolle",
    'Dämmung, weich Mineralwolle': "Dämmung weich Mineralwolle",
    'Dämmung, Trittschall': "Trittschalldämmung",

    'Dämmung, hart EPS': "Dämmung hart EPS",

    'Gipskarton (1)': "Gipskarton",
    'Gipskarton': "Gipskarton",

    'Glas, Normalglas': "Glas Normalglass",

    # Holz
    "Holz, Bauholz": "Holz Lattung/Bauholz",
    "Holz, Lattung": "Holz Lattung/Bauholz",

    'Holz, Brettschichtholz HOHE PRIO': "Brettschichtholz",
    'Holz, Brettschichtholz': "Brettschichtholz",

    'Holz, OSB (1)': "Holz OSB",
    'Holz, OSB': "Holz OSB",

   'Dämmung, hart EPS': "Dämmung hart EPS",
   'Dämmung, weich Glaswolle (1)': "Dämmung weich Mineralwolle",
   'Dämmung, hart Mineralwolle (1)': "Dämmung hart Mineralwolle",
   'Dämmung, hart Mineralwolle': "Dämmung hart Mineralwolle",

   'Metall,Blech Grau': "Metall Blech Grau",

   'Metall, Stahl': "Stahl",

   'Maschendrat': "Maschendrat",

   'Sperrschicht 2': "Sperrschicht",
   'Sperrschicht': "Sperrschicht",
   'Sperrschicht': "Sperrschicht",
   'Sperrschicht, Folie (1)': "Sperrschicht Folie",
   'Sperrschicht, Folie': "Sperrschicht Folie",
   'FOLIE, PAE FOLIE': "Sperrschicht Folie",
   'Verputz, Kunstharz': "Kunstharzputz",
   'Verputz, Gips': "Verputz Gips",

   'Verputz, Kalk (1)': "Verputz Kalk",
   'Verputz, Kalk HOLZ Fasade': "Verputz Kalk",
}






# 'IW 30cm STB tragend 2s',
# 'IW 30cm STB tragend',
# 'IW02 Trennwand',
# 'IW03 Innenwand',
# '16 WDVS Aufbau unten',
# 'D00 Begehbar',
# 'D00 Begehbar-Dachterasse',
# 'D01 Aufbauten Oben DG',
# 'D01 Aufbauten Oben Nassraum',
# 'D01 Aufbauten Oben',
# 'D01 Aufbauten unten',
# 'D02 Extensiv Begrünt',
# 'Belag, Gras',
# 'Bauteil - Attika BIM 1,2 (1)',
# 'Bauteil - Attika BIM 1,2',
# 'Vegetation',
# '1 AW Holzrahmenbauweise',
# '1 Bodenaufbau Holz',
# '1 Bodenaufbau Küche/Bad',
# '1 Flachdach Kies-Kopie',
# '1 IW 15,5 Holzrahmen',
# '1 IW Holzrahmenbauweise',
# 'ALLGEMEIN - BAUTEIL',
# 'ALLGEMEIN - BAUTEIL',
# 'ALLGEMEIN - BEKLEIDUNG',
# 'ALLGEMEIN - NIEDRIGE PRIORITÄT',
# 'ALLGEMEIN - TRAGENDE BAUTEILE',
# 'ALLGEMEIN - UMGEBUNG',
# 'ATIKA 3',
# 'AW 01 BIM 1OG',
# 'AW 01 BIM DG',
# 'AW 01 BIM',
# 'AW 25 STB + 18 WDVS',
# 'Atika 2,1',
# 'Atika 4',
# 'Atika',

@dataclass
class material_accumulator(dict):
    volume: float = field(default=0.0, init=False)
    mass: float = field(default=0.0, init=False)
    penrt_ml: float = field(default=0.0, init=False)
    gwp_ml: float = field(default=0.0, init=False)
    ap_ml: float = field(default=0.0, init=False)
    recyclable_mass: float = field(default=0.0, init=False)
    waste_mass: float = field(default=0.0, init=False)


@dataclass
class material_passport_element(dict):

    volume: float = field(default=0.0, init=False)
    density: float = field(default=0.0, init=False)

    mass: float = field(default=0.0, init=False)

    recyclable_grade: float = field(default=0.0, init=False)
    waste_grade: float = field(default=0.0, init=False)

    recyclable_mass: float = field(default=0.0, init=False)
    waste_mass: float = field(default=0.0, init=False)

    penrt: float = field(default=0.0, init=False)
    gwp: float = field(default=0.0, init=False)
    ap: float = field(default=0.0, init=False)

    penrt_ml: float = field(default=0.0, init=False)
    gwp_ml: float = field(default=0.0, init=False)
    ap_ml: float = field(default=0.0, init=False)


@dataclass
class IfcExtractor:
    """
    Method and data holder for interacting with ifc formatted files
    """

    ifc_file_path: str
    weather_file_path: str = "../schema/AUT_Vienna.Schwechat.110360_IWEC.epw"
    config_file: str = "../schema/prices.csv"

    start: float = field(init=False, default=0.0)
    config: dict[str, Any] = field(default_factory=dict, init=False)
    material_dict: dict[str, Any] = field(default_factory=dict, init=False)
    material_set: set[str] = field(default_factory=set, init=False)
    properties = gebäude_kenndaten

    def __post_init__(self):
        self.config = read_config(self.config_file)
        self.ifc_model = ifcopenshell.open(self.ifc_file_path)
        pprint(f"Schema used: {self.ifc_model.schema}")
        pprint(f"Opened file: {self.ifc_file_path}")
        self.s = ifcopenshell.geom.settings()
        try:
            self.s.set(self.s.USE_MATERIAL_NAMES, True)
            self.s.set(self.s.WELD_VERTICES, True)
            self.s.set(self.s.USE_WORLD_COORDS, True)
            self.s.set(self.s.GENERATE_UVS, True)
            self.s.set(self.s.APPLY_DEFAULT_MATERIALS, True)
            self.s.set(self.s.USE_MATERIAL_NAMES, True)
            self.s.set(self.s.ENABLE_LAYERSET_SLICING, True)
            self.s.set(self.s.ELEMENT_HIERARCHY, True)
            # self.s.set(self.s.USE_PYTHON_OPENCASCADE, True) # Apparently this one does not exist??
        except AttributeError:
            pprint("This attribute does not seem to exist.")
        pprint(self.s)