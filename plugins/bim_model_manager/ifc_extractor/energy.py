# -*- coding: utf-8 -*-
"""
OpenStudio/EnergyPlus building energy simulation from IFC geometry.

This module drives a *real* OpenStudio/EnergyPlus simulation; it never
falls back to an arithmetic estimate. If the OpenStudio runtime is
unavailable, or the IFC model does not carry enough usable geometry, a
typed exception is raised instead of silently returning a number.

Architecture
------------
1. ``ifc_path``/``epw_path`` are validated cheaply (extension + a bounded
   header read for the EPW), never reading the whole file up front.
2. The ``openstudio`` package is imported lazily (see
   ``_load_openstudio_module``) so importing this module never requires
   OpenStudio to be installed; only calling :func:`run_energy_simulation`
   does.
3. Thermal-zone geometry is derived directly from real IFC geometry via
   IfcOpenShell (see :func:`_extract_zone_boxes`): one axis-aligned box
   per ``IfcSpace`` (in world coordinates), or -- if the model has no
   usable spaces -- a single box that unions the geometry of every
   represented ``IfcBuildingElement``. There is *no* use of a
   hypothetical ``ModelTranslator``/``loadIFCFile`` IFC-to-OpenStudio
   translator; the OpenStudio model is built up programmatically from
   these boxes using documented OpenStudio 3.11 APIs
   (``Space.fromFloorPrint``, ``ThermalZone``, ``ThermostatSetpointDualSetpoint``,
   ``People``/``Lights``/``ElectricEquipment``/``SpaceInfiltrationDesignFlowRate``,
   ``Construction``/``StandardOpaqueMaterial``/``SimpleGlazing``).
4. Because raw architectural IFC carries no HVAC/occupancy/schedule
   data, generic residential defaults are applied -- see
   :class:`EnergySimulationAssumptions`. These are explicit, documented,
   returned as a human-readable summary on the result, and are never
   described as an "estimate": the output is a genuine EnergyPlus
   simulation, only its inputs are assumption-based where the IFC does
   not provide them.
5. The model + weather file are saved into a uniquely named
   ``tempfile.TemporaryDirectory`` (guaranteed cleanup, no shared paths
   across concurrent calls) as an OSM + a minimal OSW workflow file, and
   the OpenStudio CLI is invoked as an argument list (never
   ``shell=True``, never string interpolation) with a hard timeout.
6. Results are parsed directly from EnergyPlus's ``run/eplusout.sql``
   via the read-only stdlib ``sqlite3`` module (the
   ``TabularDataWithStrings`` table backing the "Annual Building
   Utility Performance Summary" report), so parsing never itself
   requires the ``openstudio`` package to be installed.
"""
from __future__ import annotations

import dataclasses
import json
import math
import os
import shutil
import sqlite3
import subprocess
import tempfile
import time
from pathlib import Path
from types import ModuleType
from typing import Callable, Optional, Sequence

import ifcopenshell
import ifcopenshell.geom

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Default hard wall-clock timeout for a single simulation run.
DEFAULT_TIMEOUT_SECONDS = 900.0

#: Any thermal-zone box dimension outside this range is rejected as
#: degenerate (too thin/zero-sized, or implausibly large for a single
#: zone derived from one IfcSpace/the whole building envelope).
MIN_ZONE_DIMENSION_M = 0.5
MAX_ZONE_DIMENSION_M = 200.0

#: 1 GJ = 1e9 J; 1 kWh = 3.6e6 J.
GJ_TO_KWH = 1.0e9 / 3.6e6

EPW_HEADER_READ_BYTES = 4096
EPW_LOCATION_HEADER_PREFIX = "LOCATION,"

#: Where the OpenStudio CLI's "run" subcommand writes EnergyPlus's SQL
#: output relative to the OSW's root directory.
OSW_RUN_SUBDIR = "run"
OSW_SQL_FILENAME = "eplusout.sql"

_ABUPS_REPORT_NAME = "AnnualBuildingUtilityPerformanceSummary"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class EnergySimulationError(Exception):
    """Base class for every error raised by this module."""


class OpenStudioUnavailableError(EnergySimulationError):
    """
    Raised when the OpenStudio Python bindings or the OpenStudio CLI
    executable cannot be found/used. Carries an actionable message
    (e.g. what to install or which argument to pass) rather than a bare
    ``ImportError``/``FileNotFoundError``.
    """


class InvalidSimulationInputError(EnergySimulationError):
    """Raised for invalid, missing, or unsupported IFC/EPW input files."""


class UnsupportedIfcGeometryError(EnergySimulationError):
    """
    Raised when no usable, non-degenerate thermal-zone geometry could
    be derived from the IFC model.
    """


class SimulationExecutionError(EnergySimulationError):
    """
    Raised when building/saving the OpenStudio model fails, or invoking
    the OpenStudio CLI times out, cannot be started, or exits with a
    non-zero status.
    """


class SimulationResultError(EnergySimulationError):
    """
    Raised when the EnergyPlus/OpenStudio results are missing, cannot
    be opened, or cannot be parsed reliably (e.g. an expected report row
    is absent, or its units are not what this module expects).
    """


# ---------------------------------------------------------------------------
# Assumptions & result value objects
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class EnergySimulationAssumptions:
    """
    Generic residential default assumptions applied whenever the IFC
    model does not itself carry HVAC/occupancy/schedule/construction
    data (which raw architectural IFC essentially never does). All
    units are SI unless the field name says otherwise (``_c`` for
    degrees Celsius).

    These are deliberately simple, constant-in-time defaults (ideal
    loads HVAC, always-on internal-load schedules). They are an
    explicit, documented modeling choice, not a fallback: the resulting
    numbers still come from a real EnergyPlus simulation of a real
    (assumption-completed) model, never from arithmetic estimation.
    """

    heating_setpoint_c: float = 20.0
    cooling_setpoint_c: float = 26.0
    occupant_density_m2_per_person: float = 25.0
    lighting_power_density_w_per_m2: float = 5.0
    equipment_power_density_w_per_m2: float = 5.0
    infiltration_air_changes_per_hour: float = 0.5
    wall_u_value_w_per_m2k: float = 0.30
    roof_u_value_w_per_m2k: float = 0.20
    floor_u_value_w_per_m2k: float = 0.30
    window_u_value_w_per_m2k: float = 1.40
    window_solar_heat_gain_coefficient: float = 0.40
    window_to_wall_ratio: float = 0.20
    wall_thickness_m: float = 0.30
    roof_thickness_m: float = 0.25
    floor_thickness_m: float = 0.20

    def __post_init__(self) -> None:
        if self.heating_setpoint_c >= self.cooling_setpoint_c:
            raise ValueError(
                "heating_setpoint_c must be strictly below cooling_setpoint_c."
            )
        if self.occupant_density_m2_per_person <= 0:
            raise ValueError("occupant_density_m2_per_person must be positive.")
        for name in (
                "lighting_power_density_w_per_m2",
                "equipment_power_density_w_per_m2",
                "infiltration_air_changes_per_hour",
                "wall_u_value_w_per_m2k",
                "roof_u_value_w_per_m2k",
                "floor_u_value_w_per_m2k",
                "window_u_value_w_per_m2k",
                "wall_thickness_m",
                "roof_thickness_m",
                "floor_thickness_m",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive.")
        if not 0.0 <= self.window_solar_heat_gain_coefficient <= 1.0:
            raise ValueError("window_solar_heat_gain_coefficient must be in [0, 1].")
        if not 0.0 <= self.window_to_wall_ratio < 0.9:
            raise ValueError("window_to_wall_ratio must be in [0, 0.9).")

    def summary(self) -> str:
        """Human-readable summary, included verbatim on every result."""
        return (
            "Assumption-based OpenStudio/EnergyPlus simulation (generic residential "
            "defaults applied where the IFC model provides no HVAC/occupancy/schedule/"
            "construction data, not an arithmetic estimate): "
            f"heating/cooling setpoints {self.heating_setpoint_c:.1f}/"
            f"{self.cooling_setpoint_c:.1f} °C, ideal-loads HVAC, "
            f"{self.occupant_density_m2_per_person:.1f} m²/person occupancy, "
            f"{self.lighting_power_density_w_per_m2:.1f} W/m² lighting, "
            f"{self.equipment_power_density_w_per_m2:.1f} W/m² equipment, "
            f"{self.infiltration_air_changes_per_hour:.2f} ACH infiltration, "
            f"opaque U-values wall/roof/floor "
            f"{self.wall_u_value_w_per_m2k:.2f}/{self.roof_u_value_w_per_m2k:.2f}/"
            f"{self.floor_u_value_w_per_m2k:.2f} W/m²K, "
            f"glazing U-value {self.window_u_value_w_per_m2k:.2f} W/m²K with SHGC "
            f"{self.window_solar_heat_gain_coefficient:.2f} at "
            f"{self.window_to_wall_ratio * 100:.0f}% window-to-wall ratio, "
            "constant (always-on) internal-load schedules."
        )


@dataclasses.dataclass(frozen=True)
class ThermalZoneBox:
    """
    A single finite, axis-aligned SI-unit (metres) bounding box used as
    one OpenStudio thermal zone. Either derived from one ``IfcSpace``,
    or -- as a fallback -- the union envelope of every represented
    ``IfcBuildingElement`` in the model.
    """

    name: str
    min_x: float
    min_y: float
    min_z: float
    max_x: float
    max_y: float
    max_z: float

    @property
    def size_x(self) -> float:
        return self.max_x - self.min_x

    @property
    def size_y(self) -> float:
        return self.max_y - self.min_y

    @property
    def size_z(self) -> float:
        return self.max_z - self.min_z

    @property
    def floor_area_m2(self) -> float:
        return self.size_x * self.size_y

    def is_degenerate(self) -> bool:
        """
        True if any dimension is non-finite, too small to be a real
        room/building (below :data:`MIN_ZONE_DIMENSION_M`), or
        implausibly large (above :data:`MAX_ZONE_DIMENSION_M`, which
        would indicate bad/unit-mismatched geometry rather than a real
        room).
        """
        for dimension in (self.size_x, self.size_y, self.size_z):
            if not math.isfinite(dimension):
                return True
            if dimension < MIN_ZONE_DIMENSION_M or dimension > MAX_ZONE_DIMENSION_M:
                return True
        return False


@dataclasses.dataclass(frozen=True)
class EnergySimulationResult:
    """Immutable, typed result of a successful simulation run."""

    annual_total_site_energy_kwh: float
    annual_electricity_kwh: float
    annual_natural_gas_kwh: float
    energy_use_intensity_kwh_per_m2: float
    conditioned_floor_area_m2: float
    zone_count: int
    weather_file_name: str
    energyplus_version: str
    assumptions_summary: str
    simulation_duration_seconds: float


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

def _validate_ifc_path(ifc_path: str) -> Path:
    if not ifc_path:
        raise InvalidSimulationInputError("An IFC file path must be provided.")
    path = Path(ifc_path)
    if path.suffix.lower() not in (".ifc", ".ifczip"):
        raise InvalidSimulationInputError(
            f"Unsupported IFC file extension {path.suffix!r}; expected '.ifc' or '.ifczip'."
        )
    if not path.is_file():
        raise InvalidSimulationInputError(f"IFC file not found: {ifc_path!r}")
    return path


def _validate_epw_path(epw_path: str) -> Path:
    if not epw_path:
        raise InvalidSimulationInputError("An EPW weather file path must be provided.")
    path = Path(epw_path)
    if path.suffix.lower() != ".epw":
        raise InvalidSimulationInputError(
            f"Weather file must have a '.epw' extension, got {path.suffix!r}."
        )
    if not path.is_file():
        raise InvalidSimulationInputError(f"EPW weather file not found: {epw_path!r}")

    try:
        with open(path, "r", encoding="ascii", errors="replace") as handle:
            header = handle.read(EPW_HEADER_READ_BYTES)
    except OSError as exc:
        raise InvalidSimulationInputError(f"Could not read EPW file {epw_path!r}: {exc}") from exc

    if not header.lstrip("\ufeff").upper().startswith(EPW_LOCATION_HEADER_PREFIX):
        raise InvalidSimulationInputError(
            f"EPW file {epw_path!r} does not start with a valid 'LOCATION,' header line."
        )
    return path


# ---------------------------------------------------------------------------
# Lazy OpenStudio loading / CLI discovery
# ---------------------------------------------------------------------------

def _load_openstudio_module() -> ModuleType:
    """
    Import ``openstudio`` on demand. Kept as its own function so tests
    can patch it to simulate an unavailable runtime without needing
    OpenStudio installed, and so importing this module never requires it.
    """
    try:
        import openstudio  # noqa: PLC0415 - deliberately lazy, see module docstring.
    except ImportError as exc:
        raise OpenStudioUnavailableError(
            "The 'openstudio' Python package is not installed or could not be "
            "imported. Install the pinned 'openstudio' dependency (see pyproject.toml) "
            "to run energy simulations."
        ) from exc
    return openstudio


def _discover_cli_path(
        explicit_cli_path: Optional[str],
        openstudio_module: ModuleType,
        which: Callable[[str], Optional[str]] = shutil.which,
) -> str:
    """
    Resolve an executable OpenStudio CLI path, in order of preference:
    an explicitly configured path, ``PATH`` lookup of ``openstudio``,
    then the bindings' own ``getOpenStudioCLI()`` (which is a plausible
    "." placeholder, not a real executable, when the CLI is not bundled).
    """
    if explicit_cli_path:
        if which(explicit_cli_path) or (
                Path(explicit_cli_path).is_file()
                and os.access(explicit_cli_path, os.X_OK)
        ):
            return explicit_cli_path
        raise OpenStudioUnavailableError(
            f"Configured OpenStudio CLI path is not an executable file: {explicit_cli_path!r}"
        )

    found = which("openstudio")
    if found:
        return found

    try:
        bundled = openstudio_module.getOpenStudioCLI()
    except Exception:  # pragma: no cover - defensive, SDK/platform dependent.
        bundled = None
    if bundled:
        bundled_path = Path(str(bundled))
        if bundled_path.is_file() and os.access(bundled_path, os.X_OK):
            return str(bundled_path)

    raise OpenStudioUnavailableError(
        "Could not locate an OpenStudio CLI executable (checked the explicit "
        "'cli_path' argument, PATH, and the bindings' bundled CLI location). Install "
        "the OpenStudio CLI, or pass 'cli_path' explicitly."
    )


# ---------------------------------------------------------------------------
# IFC geometry -> thermal zone boxes
# ---------------------------------------------------------------------------

def _make_geometry_settings():
    settings = ifcopenshell.geom.settings()
    settings.set(settings.USE_WORLD_COORDS, True)
    return settings


def _bounding_box_of_product(product, settings) -> Optional[tuple]:
    """
    Returns ``(min_x, min_y, min_z, max_x, max_y, max_z)`` in metres for
    a single IFC product's real (world-coordinate) geometry, or
    ``None`` if the product has no representation or its shape cannot
    be created.
    """
    if getattr(product, "Representation", None) is None:
        return None
    try:
        shape = ifcopenshell.geom.create_shape(settings, product)
    except Exception:
        return None
    verts = shape.geometry.verts
    if not verts:
        return None
    xs = verts[0::3]
    ys = verts[1::3]
    zs = verts[2::3]
    return min(xs), min(ys), min(zs), max(xs), max(ys), max(zs)


def _extract_zone_boxes(ifc_path: Path) -> list[ThermalZoneBox]:
    """
    Derives one :class:`ThermalZoneBox` per usable ``IfcSpace`` (in
    world coordinates). If no space yields usable, non-degenerate
    geometry, falls back to a single box that unions the geometry of
    every represented ``IfcBuildingElement`` instead.
    """
    ifc_file = ifcopenshell.open(str(ifc_path))
    settings = _make_geometry_settings()

    zone_boxes: list[ThermalZoneBox] = []
    for index, space in enumerate(ifc_file.by_type("IfcSpace")):
        box = _bounding_box_of_product(space, settings)
        if box is None:
            continue
        name = getattr(space, "Name", None) or f"Space_{index + 1}"
        zone_box = ThermalZoneBox(name=str(name), min_x=box[0], min_y=box[1], min_z=box[2],
                                   max_x=box[3], max_y=box[4], max_z=box[5])
        if zone_box.is_degenerate():
            continue
        zone_boxes.append(zone_box)

    if zone_boxes:
        return zone_boxes

    element_boxes = [
        box for box in (
            _bounding_box_of_product(element, settings)
            for element in ifc_file.by_type("IfcBuildingElement")
        )
        if box is not None
    ]
    if not element_boxes:
        raise UnsupportedIfcGeometryError(
            "No usable IfcSpace or IfcBuildingElement geometry found in the IFC model; "
            "cannot derive a thermal-zone envelope to simulate."
        )

    envelope = ThermalZoneBox(
        name="Envelope",
        min_x=min(box[0] for box in element_boxes),
        min_y=min(box[1] for box in element_boxes),
        min_z=min(box[2] for box in element_boxes),
        max_x=max(box[3] for box in element_boxes),
        max_y=max(box[4] for box in element_boxes),
        max_z=max(box[5] for box in element_boxes),
    )
    if envelope.is_degenerate():
        raise UnsupportedIfcGeometryError(
            f"Derived building envelope is degenerate (size "
            f"{envelope.size_x:.3f} x {envelope.size_y:.3f} x {envelope.size_z:.3f} m); "
            "cannot run a meaningful energy simulation."
        )
    return [envelope]


# ---------------------------------------------------------------------------
# OpenStudio model construction
# ---------------------------------------------------------------------------

def _footprint_points(openstudio_module: ModuleType, zone_box: ThermalZoneBox):
    """
    Builds the four floor-print corner points for ``zone_box`` in the
    clockwise-from-above winding order that
    ``openstudio.model.Space.fromFloorPrint`` requires (outward normal
    pointing down); the reverse order crashes the OpenStudio C++
    bindings instead of raising a Python exception.
    """
    os_ = openstudio_module
    return os_.Point3dVector([
        os_.Point3d(zone_box.min_x, zone_box.max_y, zone_box.min_z),
        os_.Point3d(zone_box.max_x, zone_box.max_y, zone_box.min_z),
        os_.Point3d(zone_box.max_x, zone_box.min_y, zone_box.min_z),
        os_.Point3d(zone_box.min_x, zone_box.min_y, zone_box.min_z),
    ])


def _build_opaque_construction(openstudio_module: ModuleType, model, thickness_m: float,
                                u_value_w_per_m2k: float):
    material = openstudio_module.model.StandardOpaqueMaterial(model)
    material.setThickness(thickness_m)
    # Simplified single-layer construction: conductivity chosen so the
    # layer alone matches the target U-value, ignoring surface-film
    # resistances (documented simplification, not a measured material).
    material.setConductivity(u_value_w_per_m2k * thickness_m)
    construction = openstudio_module.model.Construction(model)
    construction.insertLayer(0, material)
    return construction


def _build_glazing_construction(openstudio_module: ModuleType, model,
                                 assumptions: EnergySimulationAssumptions):
    glazing = openstudio_module.model.SimpleGlazing(model)
    glazing.setUFactor(assumptions.window_u_value_w_per_m2k)
    glazing.setSolarHeatGainCoefficient(assumptions.window_solar_heat_gain_coefficient)
    construction = openstudio_module.model.Construction(model)
    construction.insertLayer(0, glazing)
    return construction


def _build_model(openstudio_module: ModuleType, zone_boxes: Sequence[ThermalZoneBox],
                  epw_path: Path, assumptions: EnergySimulationAssumptions):
    """
    Programmatically builds a real OpenStudio model from ``zone_boxes``,
    applying ``assumptions`` for everything raw IFC geometry cannot
    provide. There is no use of a hypothetical IFC-to-OpenStudio
    translator here.
    """
    os_ = openstudio_module
    model = os_.model.Model()

    wall_construction = _build_opaque_construction(
        os_, model, assumptions.wall_thickness_m, assumptions.wall_u_value_w_per_m2k)
    roof_construction = _build_opaque_construction(
        os_, model, assumptions.roof_thickness_m, assumptions.roof_u_value_w_per_m2k)
    floor_construction = _build_opaque_construction(
        os_, model, assumptions.floor_thickness_m, assumptions.floor_u_value_w_per_m2k)
    window_construction = _build_glazing_construction(os_, model, assumptions)

    heating_schedule = os_.model.ScheduleConstant(model)
    heating_schedule.setValue(assumptions.heating_setpoint_c)
    cooling_schedule = os_.model.ScheduleConstant(model)
    cooling_schedule.setValue(assumptions.cooling_setpoint_c)
    always_on_schedule = os_.model.ScheduleConstant(model)
    always_on_schedule.setValue(1.0)
    activity_schedule = os_.model.ScheduleConstant(model)
    activity_schedule.setValue(120.0)  # W/person, default sedentary metabolic rate.

    spaces = []
    for zone_box in zone_boxes:
        footprint = _footprint_points(os_, zone_box)
        optional_space = os_.model.Space.fromFloorPrint(footprint, zone_box.size_z, model)
        if not optional_space.is_initialized():
            raise UnsupportedIfcGeometryError(
                f"OpenStudio could not build a space from thermal-zone box {zone_box.name!r}."
            )
        space = optional_space.get()
        space.setName(zone_box.name)

        thermal_zone = os_.model.ThermalZone(model)
        thermal_zone.setName(f"{zone_box.name} Zone")
        space.setThermalZone(thermal_zone)
        thermal_zone.setUseIdealAirLoads(True)

        thermostat = os_.model.ThermostatSetpointDualSetpoint(model)
        thermostat.setHeatingSetpointTemperatureSchedule(heating_schedule)
        thermostat.setCoolingSetpointTemperatureSchedule(cooling_schedule)
        thermal_zone.setThermostat(thermostat)

        people_definition = os_.model.PeopleDefinition(model)
        people_definition.setPeopleperSpaceFloorArea(1.0 / assumptions.occupant_density_m2_per_person)
        people = os_.model.People(people_definition)
        people.setNumberofPeopleSchedule(always_on_schedule)
        people.setActivityLevelSchedule(activity_schedule)
        people.setSpace(space)

        lights_definition = os_.model.LightsDefinition(model)
        lights_definition.setWattsperSpaceFloorArea(assumptions.lighting_power_density_w_per_m2)
        lights = os_.model.Lights(lights_definition)
        lights.setSchedule(always_on_schedule)
        lights.setSpace(space)

        equipment_definition = os_.model.ElectricEquipmentDefinition(model)
        equipment_definition.setWattsperSpaceFloorArea(assumptions.equipment_power_density_w_per_m2)
        equipment = os_.model.ElectricEquipment(equipment_definition)
        equipment.setSchedule(always_on_schedule)
        equipment.setSpace(space)

        infiltration = os_.model.SpaceInfiltrationDesignFlowRate(model)
        infiltration.setAirChangesperHour(assumptions.infiltration_air_changes_per_hour)
        infiltration.setSchedule(always_on_schedule)
        infiltration.setSpace(space)

        for surface in space.surfaces():
            surface_type = surface.surfaceType()
            if surface_type == "Wall":
                surface.setConstruction(wall_construction)
            elif surface_type == "RoofCeiling":
                surface.setConstruction(roof_construction)
            elif surface_type == "Floor":
                surface.setConstruction(floor_construction)

        spaces.append(space)

    # Turns shared walls between adjacent zone boxes into interior
    # "Surface" adjacency (and floors resting on other zones into
    # interior floors) instead of leaving every wall/floor as exterior,
    # which would double count envelope losses for multi-space models.
    os_.model.matchSurfaces(os_.model.SpaceVector(spaces))

    if assumptions.window_to_wall_ratio > 0:
        for space in spaces:
            for surface in space.surfaces():
                if surface.surfaceType() == "Wall" and surface.outsideBoundaryCondition() == "Outdoors":
                    optional_sub_surface = surface.setWindowToWallRatio(assumptions.window_to_wall_ratio)
                    if optional_sub_surface.is_initialized():
                        optional_sub_surface.get().setConstruction(window_construction)

    epw_file = os_.EpwFile(os_.path(str(epw_path)))
    optional_weather_file = os_.model.WeatherFile.setWeatherFile(model, epw_file)
    if not optional_weather_file.is_initialized():
        raise SimulationExecutionError(f"Could not assign EPW weather file {epw_path} to the model.")

    simulation_control = model.getSimulationControl()
    simulation_control.setRunSimulationforSizingPeriods(False)
    simulation_control.setRunSimulationforWeatherFileRunPeriods(True)

    for meter_name in ("Electricity:Facility", "NaturalGas:Facility"):
        meter = os_.model.OutputMeter(model)
        meter.setName(meter_name)
        meter.setReportingFrequency("Annual")

    return model


# ---------------------------------------------------------------------------
# OSW / CLI invocation
# ---------------------------------------------------------------------------

def _write_osw(work_dir: Path, osm_filename: str, epw_path: Path) -> Path:
    """
    Writes a minimal, valid OpenStudio Workflow (OSW) JSON file with no
    measures: just a seed model and a weather file, which is all
    ``openstudio run`` needs to execute a plain EnergyPlus simulation.
    Written with plain ``json``, not ``openstudio.WorkflowJSON``, so
    this step never itself requires the OpenStudio bindings.
    """
    osw_path = work_dir / "in.osw"
    workflow = {
        "seed_file": osm_filename,
        "weather_file": str(epw_path),
        "steps": [],
    }
    with open(osw_path, "w", encoding="utf-8") as handle:
        json.dump(workflow, handle)
    return osw_path


def _build_cli_argv(cli_path: str, osw_path: Path) -> list[str]:
    return [cli_path, "run", "-w", str(osw_path)]


def _run_cli(
        argv: list[str],
        cwd: Path,
        timeout_seconds: float,
        runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> subprocess.CompletedProcess:
    """
    Invokes the OpenStudio CLI as an argument list (never a shell
    string) with a hard timeout, in an isolated working directory.
    """
    try:
        completed = runner(
            argv,
            cwd=str(cwd),
            timeout=timeout_seconds,
            capture_output=True,
            text=True,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise SimulationExecutionError(
            f"OpenStudio CLI simulation timed out after {timeout_seconds:.0f} seconds."
        ) from exc
    except OSError as exc:
        raise SimulationExecutionError(f"Could not execute the OpenStudio CLI: {exc}") from exc

    if completed.returncode != 0:
        stderr_excerpt = (completed.stderr or "")[-2000:]
        raise SimulationExecutionError(
            f"OpenStudio CLI exited with status {completed.returncode}: {stderr_excerpt}"
        )
    return completed


def _locate_sql_file(work_dir: Path) -> Path:
    sql_path = work_dir / OSW_RUN_SUBDIR / OSW_SQL_FILENAME
    if not sql_path.is_file():
        raise SimulationResultError(
            f"Expected EnergyPlus SQL results file not found at {sql_path}; "
            "the simulation may not have completed successfully."
        )
    return sql_path


# ---------------------------------------------------------------------------
# Result parsing (stdlib sqlite3 only, no OpenStudio dependency)
# ---------------------------------------------------------------------------

def _query_tabular_row(
        connection: sqlite3.Connection, report_name: str, table_name: str,
        row_name: str, column_name: str,
) -> Optional[tuple]:
    """Returns ``(value_str, units_str)`` for one ABUPS-style row, or ``None``."""
    cursor = connection.execute(
        "SELECT Value, Units FROM TabularDataWithStrings "
        "WHERE ReportName = ? AND TableName = ? AND RowName = ? AND ColumnName = ?",
        (report_name, table_name, row_name, column_name),
    )
    return cursor.fetchone()


def _gj_row_to_kwh(row: tuple, *, context: str) -> float:
    value_str, units_str = row
    if units_str != "GJ":
        raise SimulationResultError(f"Unexpected units {units_str!r} for {context}; expected 'GJ'.")
    try:
        value_gj = float(value_str)
    except (TypeError, ValueError) as exc:
        raise SimulationResultError(f"Non-numeric value for {context}: {value_str!r}") from exc
    if not math.isfinite(value_gj):
        raise SimulationResultError(f"Non-finite value for {context}: {value_str!r}")
    return value_gj * GJ_TO_KWH


def _check_simulation_completed(connection: sqlite3.Connection) -> None:
    """Reject recorded severe/fatal errors.

    EnergyPlus 25.2 can leave ``Simulations.Completed`` and
    ``CompletedSuccessfully`` as the strings ``FALSE`` even when its own error
    summary says the run completed successfully. Those flags therefore cannot
    be authoritative. The ``Errors`` table plus the required annual result
    rows parsed below provide a stable success check across versions.
    """
    try:
        cursor = connection.execute(
            "SELECT ErrorMessage, Count FROM Errors "
            "WHERE ErrorType > 0 AND COALESCE(Count, 1) > 0 "
            "ORDER BY ErrorType DESC, rowid LIMIT 5"
        )
    except sqlite3.OperationalError:
        # Older schemas may omit Errors; required result rows still provide a
        # strong completion check.
        return
    errors = cursor.fetchall()
    if errors:
        details = "; ".join(
            f"{message} (count: {count})" for message, count in errors
        )
        raise SimulationResultError(f"EnergyPlus reported severe errors: {details}")


def _get_energyplus_version(connection: sqlite3.Connection) -> str:
    try:
        cursor = connection.execute("SELECT EnergyPlusVersion FROM Simulations")
    except sqlite3.OperationalError:
        return "unknown"
    row = cursor.fetchone()
    if row is None or row[0] is None:
        return "unknown"
    return str(row[0])


def _parse_sqlite_results(sql_path: Path, conditioned_floor_area_m2: float) -> dict:
    """
    Parses ``eplusout.sql`` directly via read-only stdlib ``sqlite3``
    (no OpenStudio dependency). A missing total-site-energy or
    per-fuel "Total End Uses" row is always an error; a present row
    with a genuine zero value is a valid result.
    """
    if not math.isfinite(conditioned_floor_area_m2) or conditioned_floor_area_m2 <= 0:
        raise SimulationResultError(
            f"Conditioned floor area must be finite and positive, got {conditioned_floor_area_m2!r}."
        )

    try:
        connection = sqlite3.connect(f"file:{Path(sql_path).as_posix()}?mode=ro", uri=True)
    except sqlite3.OperationalError as exc:
        raise SimulationResultError(f"Could not open EnergyPlus SQL results: {exc}") from exc

    try:
        _check_simulation_completed(connection)
        energyplus_version = _get_energyplus_version(connection)

        total_row = _query_tabular_row(
            connection, _ABUPS_REPORT_NAME, "Site and Source Energy",
            "Total Site Energy", "Total Energy",
        )
        if total_row is None:
            raise SimulationResultError("Missing 'Total Site Energy' row in simulation results.")
        total_site_energy_kwh = _gj_row_to_kwh(total_row, context="Total Site Energy")

        electricity_row = _query_tabular_row(
            connection, _ABUPS_REPORT_NAME, "End Uses", "Total End Uses", "Electricity",
        )
        if electricity_row is None:
            raise SimulationResultError("Missing 'Electricity' end-use row in simulation results.")
        electricity_kwh = _gj_row_to_kwh(electricity_row, context="Electricity end use")

        gas_row = _query_tabular_row(
            connection, _ABUPS_REPORT_NAME, "End Uses", "Total End Uses", "Natural Gas",
        )
        if gas_row is None:
            raise SimulationResultError("Missing 'Natural Gas' end-use row in simulation results.")
        natural_gas_kwh = _gj_row_to_kwh(gas_row, context="Natural gas end use")
    finally:
        connection.close()

    return {
        "annual_total_site_energy_kwh": total_site_energy_kwh,
        "annual_electricity_kwh": electricity_kwh,
        "annual_natural_gas_kwh": natural_gas_kwh,
        "energy_use_intensity_kwh_per_m2": total_site_energy_kwh / conditioned_floor_area_m2,
        "energyplus_version": energyplus_version,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_energy_simulation(
        ifc_path: str,
        epw_path: str,
        *,
        cli_path: Optional[str] = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        assumptions: Optional[EnergySimulationAssumptions] = None,
) -> EnergySimulationResult:
    """
    Runs a real OpenStudio/EnergyPlus simulation of ``ifc_path`` against
    ``epw_path`` and returns its parsed annual results.

    :param ifc_path: Path to a ``.ifc``/``.ifczip`` file to derive thermal
        zone geometry from.
    :param epw_path: Path to an EPW weather file (e.g. a per-model
        ``EpwUpload``'s stored file path).
    :param cli_path: Optional explicit path to the OpenStudio CLI
        executable; otherwise discovered from ``PATH`` or the bindings.
    :param timeout_seconds: Hard wall-clock timeout for the CLI run.
    :param assumptions: Overrides for :class:`EnergySimulationAssumptions`;
        defaults are used when omitted.
    :raises InvalidSimulationInputError: invalid/missing IFC or EPW input.
    :raises OpenStudioUnavailableError: OpenStudio bindings/CLI unavailable.
    :raises UnsupportedIfcGeometryError: no usable thermal-zone geometry.
    :raises SimulationExecutionError: model build/save/CLI execution failure.
    :raises SimulationResultError: results missing/unparseable.
    """
    assumptions = assumptions or EnergySimulationAssumptions()

    validated_ifc_path = _validate_ifc_path(ifc_path)
    validated_epw_path = _validate_epw_path(epw_path)

    openstudio_module = _load_openstudio_module()
    resolved_cli_path = _discover_cli_path(cli_path, openstudio_module)

    zone_boxes = _extract_zone_boxes(validated_ifc_path)
    conditioned_floor_area_m2 = sum(zone_box.floor_area_m2 for zone_box in zone_boxes)

    start_time = time.monotonic()

    with tempfile.TemporaryDirectory(prefix="cadevil_energy_") as work_dir_name:
        work_dir = Path(work_dir_name)

        model = _build_model(openstudio_module, zone_boxes, validated_epw_path, assumptions)

        osm_path = work_dir / "in.osm"
        if not model.save(openstudio_module.path(str(osm_path)), True):
            raise SimulationExecutionError(f"Could not save the OpenStudio model to {osm_path}.")

        osw_path = _write_osw(work_dir, osm_path.name, validated_epw_path)

        argv = _build_cli_argv(resolved_cli_path, osw_path)
        _run_cli(argv, cwd=work_dir, timeout_seconds=timeout_seconds)

        sql_path = _locate_sql_file(work_dir)
        parsed_results = _parse_sqlite_results(sql_path, conditioned_floor_area_m2)

    simulation_duration_seconds = time.monotonic() - start_time

    return EnergySimulationResult(
        annual_total_site_energy_kwh=parsed_results["annual_total_site_energy_kwh"],
        annual_electricity_kwh=parsed_results["annual_electricity_kwh"],
        annual_natural_gas_kwh=parsed_results["annual_natural_gas_kwh"],
        energy_use_intensity_kwh_per_m2=parsed_results["energy_use_intensity_kwh_per_m2"],
        conditioned_floor_area_m2=conditioned_floor_area_m2,
        zone_count=len(zone_boxes),
        weather_file_name=validated_epw_path.name,
        energyplus_version=parsed_results["energyplus_version"],
        assumptions_summary=assumptions.summary(),
        simulation_duration_seconds=simulation_duration_seconds,
    )
