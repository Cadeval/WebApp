import dataclasses


@dataclasses.dataclass
class BuildingData:
    """
    Raw BIM Data
    TODO normalize from different import types
    """

    id: int = 0


@dataclasses.dataclass
class LocationData:
    pass


@dataclasses.dataclass
class EnergyData:
    pass


@dataclasses.dataclass
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
