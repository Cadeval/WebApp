# -*- coding: utf-8 -*-
"""
Dedicated tests for ``ifc_extractor.energy``.

These tests never require the OpenStudio Python bindings or CLI to be
installed: every seam that touches ``openstudio`` (module loading, CLI
discovery, model construction, CLI invocation) is exercised through
dependency injection/monkeypatching of this module's own private
functions, never through a fake stand-in that pretends to be a real
simulation. Result parsing is tested against a real, minimally-shaped
SQLite database (matching EnergyPlus's ``eplusout.sql`` schema) built
with the stdlib ``sqlite3`` module, and geometry extraction is tested
against small, real, in-memory IfcOpenShell models, following the same
conventions as ``ifc_extractor.test_helpers``.
"""
import builtins
import math
import os
import sqlite3
import subprocess
import tempfile
from pathlib import Path
from unittest import mock

import ifcopenshell
import ifcopenshell.api
import ifcopenshell.api.aggregate
import ifcopenshell.api.context
import ifcopenshell.api.geometry
import ifcopenshell.api.project
import ifcopenshell.api.root
import ifcopenshell.api.unit
from django.test import SimpleTestCase

from apps.plugins.bim_model_manager.ifc_extractor import energy


def _real_import_openstudio_is_missing():
    """
    Simulates an environment without the ``openstudio`` package by
    making ``import openstudio`` raise ``ImportError``, regardless of
    whether it is actually installed -- this is what proves
    ``_load_openstudio_module`` is genuinely lazy and produces a typed,
    actionable error rather than a bare ``ImportError`` bubbling up.
    """
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "openstudio":
            raise ImportError("simulated missing openstudio for this test")
        return real_import(name, *args, **kwargs)

    return mock.patch("builtins.__import__", side_effect=fake_import)


class ValidateIfcPathTests(SimpleTestCase):
    """Tests for ``_validate_ifc_path``."""

    def test_empty_path_is_rejected(self):
        with self.assertRaises(energy.InvalidSimulationInputError):
            energy._validate_ifc_path("")

    def test_wrong_extension_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = os.path.join(tmp_dir, "model.txt")
            Path(path).write_text("not an ifc file")
            with self.assertRaises(energy.InvalidSimulationInputError):
                energy._validate_ifc_path(path)

    def test_missing_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = os.path.join(tmp_dir, "missing.ifc")
            with self.assertRaises(energy.InvalidSimulationInputError):
                energy._validate_ifc_path(path)

    def test_valid_ifc_file_returns_path(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = os.path.join(tmp_dir, "model.ifc")
            Path(path).write_text("ISO-10303-21;")
            result = energy._validate_ifc_path(path)
            self.assertEqual(result, Path(path))

    def test_ifczip_extension_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = os.path.join(tmp_dir, "model.ifczip")
            Path(path).write_bytes(b"PK\x03\x04")
            result = energy._validate_ifc_path(path)
            self.assertEqual(result, Path(path))


class ValidateEpwPathTests(SimpleTestCase):
    """Tests for ``_validate_epw_path``, without reading unbounded content."""

    def test_empty_path_is_rejected(self):
        with self.assertRaises(energy.InvalidSimulationInputError):
            energy._validate_epw_path("")

    def test_wrong_extension_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = os.path.join(tmp_dir, "weather.txt")
            Path(path).write_text("LOCATION,Vienna\n")
            with self.assertRaises(energy.InvalidSimulationInputError):
                energy._validate_epw_path(path)

    def test_missing_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = os.path.join(tmp_dir, "missing.epw")
            with self.assertRaises(energy.InvalidSimulationInputError):
                energy._validate_epw_path(path)

    def test_missing_location_header_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = os.path.join(tmp_dir, "weather.epw")
            Path(path).write_text("NOT,A,VALID,HEADER\n" + ("x" * 10))
            with self.assertRaises(energy.InvalidSimulationInputError):
                energy._validate_epw_path(path)

    def test_valid_location_header_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = os.path.join(tmp_dir, "weather.epw")
            Path(path).write_text(
                "LOCATION,Vienna,-,AUT,Custom Source,110360,48.2,16.37,1.0,183.0\n"
            )
            result = energy._validate_epw_path(path)
            self.assertEqual(result, Path(path))

    def test_does_not_read_unbounded_content(self):
        """
        A very large body after a valid header must not be fully read
        into memory; only a bounded prefix is inspected.
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = os.path.join(tmp_dir, "weather.epw")
            header = "LOCATION,Vienna,-,AUT,Custom Source,110360,48.2,16.37,1.0,183.0\n"
            with open(path, "w", encoding="ascii") as handle:
                handle.write(header)
                # Larger than EPW_HEADER_READ_BYTES, written in chunks so this
                # test itself stays fast/cheap while still exercising a real,
                # oversized file.
                chunk = "0" * 1_000_000
                for _ in range(2):
                    handle.write(chunk)
            result = energy._validate_epw_path(path)
            self.assertEqual(result, Path(path))


class LoadOpenStudioModuleTests(SimpleTestCase):
    """Tests for the lazy ``openstudio`` import seam."""

    def test_missing_runtime_raises_actionable_error(self):
        with _real_import_openstudio_is_missing():
            with self.assertRaises(energy.OpenStudioUnavailableError):
                energy._load_openstudio_module()

    def test_available_runtime_returns_module(self):
        fake_module = mock.MagicMock(name="openstudio")
        with mock.patch.dict("sys.modules", {"openstudio": fake_module}):
            result = energy._load_openstudio_module()
            self.assertIs(result, fake_module)


class DiscoverCliPathTests(SimpleTestCase):
    """Tests for ``_discover_cli_path``, fully mocked, no real CLI needed."""

    def test_explicit_valid_path_is_used(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            cli_path = os.path.join(tmp_dir, "openstudio")
            Path(cli_path).write_text("#!/bin/sh\n")
            os.chmod(cli_path, 0o755)
            result = energy._discover_cli_path(cli_path, openstudio_module=mock.Mock(), which=lambda _: None)
            self.assertEqual(result, cli_path)

    def test_explicit_invalid_path_raises(self):
        with self.assertRaises(energy.OpenStudioUnavailableError):
            energy._discover_cli_path("/nonexistent/openstudio", openstudio_module=mock.Mock(),
                                       which=lambda _: None)

    def test_path_lookup_is_used_when_no_explicit_path(self):
        result = energy._discover_cli_path(
            None, openstudio_module=mock.Mock(), which=lambda name: "/usr/bin/openstudio"
        )
        self.assertEqual(result, "/usr/bin/openstudio")

    def test_bundled_cli_is_used_as_last_resort(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            bundled_path = os.path.join(tmp_dir, "openstudio")
            Path(bundled_path).write_text("#!/bin/sh\n")
            os.chmod(bundled_path, 0o755)
            fake_module = mock.Mock()
            fake_module.getOpenStudioCLI.return_value = bundled_path
            result = energy._discover_cli_path(None, openstudio_module=fake_module, which=lambda _: None)
            self.assertEqual(result, bundled_path)

    def test_placeholder_bundled_cli_is_rejected(self):
        """
        When no real CLI is bundled, ``getOpenStudioCLI()`` returns a
        non-executable placeholder path (e.g. ``.``); this must not be
        mistaken for a real executable.
        """
        fake_module = mock.Mock()
        fake_module.getOpenStudioCLI.return_value = "."
        with self.assertRaises(energy.OpenStudioUnavailableError):
            energy._discover_cli_path(None, openstudio_module=fake_module, which=lambda _: None)

    def test_nothing_found_raises(self):
        fake_module = mock.Mock()
        fake_module.getOpenStudioCLI.return_value = None
        with self.assertRaises(energy.OpenStudioUnavailableError):
            energy._discover_cli_path(None, openstudio_module=fake_module, which=lambda _: None)


class ThermalZoneBoxTests(SimpleTestCase):
    """Tests for ``ThermalZoneBox`` geometric properties and degeneracy checks."""

    def test_valid_box_is_not_degenerate(self):
        box = energy.ThermalZoneBox(name="Room", min_x=0, min_y=0, min_z=0, max_x=4, max_y=3, max_z=2.5)
        self.assertFalse(box.is_degenerate())
        self.assertAlmostEqual(box.floor_area_m2, 12.0)

    def test_too_thin_box_is_degenerate(self):
        box = energy.ThermalZoneBox(name="Sliver", min_x=0, min_y=0, min_z=0, max_x=4, max_y=3, max_z=0.01)
        self.assertTrue(box.is_degenerate())

    def test_implausibly_large_box_is_degenerate(self):
        box = energy.ThermalZoneBox(name="Huge", min_x=0, min_y=0, min_z=0, max_x=10_000, max_y=3, max_z=2.5)
        self.assertTrue(box.is_degenerate())

    def test_non_finite_dimension_is_degenerate(self):
        box = energy.ThermalZoneBox(
            name="Bad", min_x=0, min_y=0, min_z=0, max_x=math.nan, max_y=3, max_z=2.5
        )
        self.assertTrue(box.is_degenerate())


class ExtractZoneBoxesTests(SimpleTestCase):
    """
    Tests for ``_extract_zone_boxes`` against small, real, in-memory
    IfcOpenShell models (no large fixtures, no mocking of geometry).
    """

    def _new_file_with_context(self):
        ifc_file = ifcopenshell.api.project.create_file(version="IFC4")
        ifcopenshell.api.root.create_entity(ifc_file, ifc_class="IfcProject", name="P")
        length_unit = ifcopenshell.api.unit.add_si_unit(ifc_file, unit_type="LENGTHUNIT")
        area_unit = ifcopenshell.api.unit.add_si_unit(ifc_file, unit_type="AREAUNIT")
        volume_unit = ifcopenshell.api.unit.add_si_unit(ifc_file, unit_type="VOLUMEUNIT")
        ifcopenshell.api.unit.assign_unit(ifc_file, units=[length_unit, area_unit, volume_unit])
        model_context = ifcopenshell.api.context.add_context(ifc_file, context_type="Model")
        body_context = ifcopenshell.api.context.add_context(
            ifc_file, context_type="Model", context_identifier="Body",
            target_view="MODEL_VIEW", parent=model_context,
        )
        return ifc_file, body_context

    def _add_storey(self, ifc_file):
        project = ifc_file.by_type("IfcProject")[0]
        site = ifcopenshell.api.root.create_entity(ifc_file, ifc_class="IfcSite", name="Site")
        building = ifcopenshell.api.root.create_entity(ifc_file, ifc_class="IfcBuilding", name="Building")
        storey = ifcopenshell.api.root.create_entity(ifc_file, ifc_class="IfcBuildingStorey", name="L1")
        ifcopenshell.api.aggregate.assign_object(ifc_file, products=[site], relating_object=project)
        ifcopenshell.api.aggregate.assign_object(ifc_file, products=[building], relating_object=site)
        ifcopenshell.api.aggregate.assign_object(ifc_file, products=[storey], relating_object=building)
        return storey

    def _add_box_space(self, ifc_file, body_context, storey, name, x_dim, y_dim, depth, origin=(0.0, 0.0, 0.0)):
        space = ifcopenshell.api.root.create_entity(ifc_file, ifc_class="IfcSpace", name=name)
        ifcopenshell.api.aggregate.assign_object(ifc_file, products=[space], relating_object=storey)
        profile = ifc_file.create_entity("IfcRectangleProfileDef", ProfileType="AREA", XDim=x_dim, YDim=y_dim)
        representation = ifcopenshell.api.geometry.add_profile_representation(
            ifc_file, context=body_context, profile=profile, depth=depth, cardinal_point="bottom left",
        )
        ifcopenshell.api.geometry.assign_representation(ifc_file, product=space, representation=representation)
        if origin != (0.0, 0.0, 0.0):
            ifcopenshell.api.geometry.edit_object_placement(ifc_file, product=space, matrix=[
                [1.0, 0.0, 0.0, origin[0]],
                [0.0, 1.0, 0.0, origin[1]],
                [0.0, 0.0, 1.0, origin[2]],
                [0.0, 0.0, 0.0, 1.0],
            ])
        return space

    def _add_wall(self, ifc_file, body_context, storey, p1, p2):
        wall = ifcopenshell.api.root.create_entity(ifc_file, ifc_class="IfcWall", name="Wall")
        ifcopenshell.api.aggregate.assign_object(ifc_file, products=[wall], relating_object=storey)
        representation = ifcopenshell.api.geometry.create_2pt_wall(
            ifc_file, element=wall, context=body_context, p1=p1, p2=p2, elevation=0.0, height=3.0, thickness=0.2,
        )
        ifcopenshell.api.geometry.assign_representation(ifc_file, product=wall, representation=representation)
        return wall

    def test_single_space_yields_one_box_with_correct_dimensions(self):
        ifc_file, body_context = self._new_file_with_context()
        storey = self._add_storey(ifc_file)
        self._add_box_space(ifc_file, body_context, storey, "Room1", x_dim=4.0, y_dim=3.0, depth=2.5)

        with tempfile.TemporaryDirectory() as tmp_dir:
            ifc_path = Path(tmp_dir) / "sample.ifc"
            ifc_file.write(str(ifc_path))
            boxes = energy._extract_zone_boxes(ifc_path)

        self.assertEqual(len(boxes), 1)
        box = boxes[0]
        self.assertEqual(box.name, "Room1")
        self.assertAlmostEqual(box.size_x, 4.0, places=3)
        self.assertAlmostEqual(box.size_y, 3.0, places=3)
        self.assertAlmostEqual(box.size_z, 2.5, places=3)
        self.assertFalse(box.is_degenerate())

    def test_multiple_spaces_yield_multiple_boxes(self):
        ifc_file, body_context = self._new_file_with_context()
        storey = self._add_storey(ifc_file)
        self._add_box_space(ifc_file, body_context, storey, "Room1", x_dim=4.0, y_dim=3.0, depth=2.5)
        self._add_box_space(
            ifc_file, body_context, storey, "Room2", x_dim=5.0, y_dim=3.0, depth=2.5, origin=(10.0, 0.0, 0.0)
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            ifc_path = Path(tmp_dir) / "sample.ifc"
            ifc_file.write(str(ifc_path))
            boxes = energy._extract_zone_boxes(ifc_path)

        self.assertEqual(len(boxes), 2)
        names = sorted(box.name for box in boxes)
        self.assertEqual(names, ["Room1", "Room2"])

    def test_no_spaces_falls_back_to_building_element_envelope(self):
        ifc_file, body_context = self._new_file_with_context()
        storey = self._add_storey(ifc_file)
        self._add_wall(ifc_file, body_context, storey, p1=(0.0, 0.0), p2=(6.0, 0.0))
        self._add_wall(ifc_file, body_context, storey, p1=(0.0, 4.0), p2=(6.0, 4.0))

        with tempfile.TemporaryDirectory() as tmp_dir:
            ifc_path = Path(tmp_dir) / "sample.ifc"
            ifc_file.write(str(ifc_path))
            boxes = energy._extract_zone_boxes(ifc_path)

        self.assertEqual(len(boxes), 1)
        envelope = boxes[0]
        self.assertEqual(envelope.name, "Envelope")
        # Union of the two wall geometries: 6m long, spanning y=0..4 (wall
        # thickness straddles the 0 and 4 wall centrelines) and 3m tall.
        self.assertGreaterEqual(envelope.size_x, 6.0)
        self.assertGreaterEqual(envelope.size_y, 4.0)
        self.assertAlmostEqual(envelope.size_z, 3.0, places=3)

    def test_degenerate_space_is_skipped_when_a_valid_one_exists(self):
        ifc_file, body_context = self._new_file_with_context()
        storey = self._add_storey(ifc_file)
        self._add_box_space(ifc_file, body_context, storey, "TinySliver", x_dim=4.0, y_dim=3.0, depth=0.001)
        self._add_box_space(
            ifc_file, body_context, storey, "RealRoom", x_dim=4.0, y_dim=3.0, depth=2.5, origin=(10.0, 0.0, 0.0)
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            ifc_path = Path(tmp_dir) / "sample.ifc"
            ifc_file.write(str(ifc_path))
            boxes = energy._extract_zone_boxes(ifc_path)

        self.assertEqual(len(boxes), 1)
        self.assertEqual(boxes[0].name, "RealRoom")

    def test_no_usable_geometry_at_all_raises(self):
        ifc_file, _body_context = self._new_file_with_context()
        self._add_storey(ifc_file)

        with tempfile.TemporaryDirectory() as tmp_dir:
            ifc_path = Path(tmp_dir) / "sample.ifc"
            ifc_file.write(str(ifc_path))
            with self.assertRaises(energy.UnsupportedIfcGeometryError):
                energy._extract_zone_boxes(ifc_path)

    def test_degenerate_envelope_fallback_raises(self):
        ifc_file, body_context = self._new_file_with_context()
        storey = self._add_storey(ifc_file)
        # A single, extremely short wall segment produces a degenerate
        # (near-zero-length) envelope with no spaces to fall back on.
        self._add_wall(ifc_file, body_context, storey, p1=(0.0, 0.0), p2=(0.05, 0.0))

        with tempfile.TemporaryDirectory() as tmp_dir:
            ifc_path = Path(tmp_dir) / "sample.ifc"
            ifc_file.write(str(ifc_path))
            with self.assertRaises(energy.UnsupportedIfcGeometryError):
                energy._extract_zone_boxes(ifc_path)


class EnergySimulationAssumptionsTests(SimpleTestCase):
    """Tests for ``EnergySimulationAssumptions`` validation and summary."""

    def test_defaults_are_valid(self):
        assumptions = energy.EnergySimulationAssumptions()
        self.assertLess(assumptions.heating_setpoint_c, assumptions.cooling_setpoint_c)

    def test_heating_above_cooling_setpoint_is_rejected(self):
        with self.assertRaises(ValueError):
            energy.EnergySimulationAssumptions(heating_setpoint_c=27.0, cooling_setpoint_c=26.0)

    def test_non_positive_density_is_rejected(self):
        with self.assertRaises(ValueError):
            energy.EnergySimulationAssumptions(lighting_power_density_w_per_m2=0.0)

    def test_out_of_range_wwr_is_rejected(self):
        with self.assertRaises(ValueError):
            energy.EnergySimulationAssumptions(window_to_wall_ratio=0.95)

    def test_is_frozen(self):
        assumptions = energy.EnergySimulationAssumptions()
        with self.assertRaises(Exception):
            assumptions.heating_setpoint_c = 10.0

    def test_summary_mentions_key_assumptions_and_is_not_labelled_an_estimate(self):
        summary = energy.EnergySimulationAssumptions().summary()
        # It must be explicitly labelled a simulation, and any mention of
        # "estimate" may only appear as part of the disclaimer that this is
        # *not* an arithmetic estimate -- never as a standalone label for
        # the result itself.
        self.assertIn("simulation", summary.lower())
        self.assertIn("not an arithmetic estimate", summary.lower())
        self.assertIn("20.0", summary)
        self.assertIn("26.0", summary)


class WriteOswTests(SimpleTestCase):
    """Tests for ``_write_osw``, pure Python, no OpenStudio dependency."""

    def test_writes_valid_json_with_seed_and_weather_file(self):
        import json

        with tempfile.TemporaryDirectory() as tmp_dir:
            work_dir = Path(tmp_dir)
            epw_path = work_dir / "weather.epw"
            epw_path.write_text("LOCATION,...\n")

            osw_path = energy._write_osw(work_dir, "in.osm", epw_path)

            self.assertTrue(osw_path.is_file())
            content = json.loads(osw_path.read_text())
            self.assertEqual(content["seed_file"], "in.osm")
            self.assertEqual(content["weather_file"], str(epw_path))
            self.assertEqual(content["steps"], [])


class BuildCliArgvTests(SimpleTestCase):
    """Tests for ``_build_cli_argv``."""

    def test_returns_argument_list_not_a_shell_string(self):
        argv = energy._build_cli_argv("/usr/bin/openstudio", Path("/tmp/work/in.osw"))
        self.assertEqual(argv, ["/usr/bin/openstudio", "run", "-w", "/tmp/work/in.osw"])
        self.assertIsInstance(argv, list)
        for item in argv:
            self.assertIsInstance(item, str)


class RunCliTests(SimpleTestCase):
    """Tests for ``_run_cli``: argv/cwd/timeout/no-shell and error mapping."""

    def test_successful_run_invokes_without_shell_and_returns_completed_process(self):
        recorded_kwargs = {}

        def fake_runner(argv, **kwargs):
            recorded_kwargs.update(kwargs)
            recorded_kwargs["argv"] = argv
            return subprocess.CompletedProcess(argv, returncode=0, stdout="ok", stderr="")

        result = energy._run_cli(
            ["openstudio", "run", "-w", "in.osw"], cwd=Path("/tmp/work"), timeout_seconds=42.0,
            runner=fake_runner,
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(recorded_kwargs["argv"], ["openstudio", "run", "-w", "in.osw"])
        self.assertEqual(recorded_kwargs["cwd"], "/tmp/work")
        self.assertEqual(recorded_kwargs["timeout"], 42.0)
        self.assertFalse(recorded_kwargs["shell"])

    def test_timeout_is_mapped_to_typed_error(self):
        def fake_runner(argv, **kwargs):
            raise subprocess.TimeoutExpired(cmd=argv, timeout=kwargs.get("timeout", 0))

        with self.assertRaises(energy.SimulationExecutionError):
            energy._run_cli(["openstudio"], cwd=Path("/tmp/work"), timeout_seconds=1.0, runner=fake_runner)

    def test_missing_executable_is_mapped_to_typed_error(self):
        def fake_runner(argv, **kwargs):
            raise OSError("executable not found")

        with self.assertRaises(energy.SimulationExecutionError):
            energy._run_cli(["openstudio"], cwd=Path("/tmp/work"), timeout_seconds=1.0, runner=fake_runner)

    def test_nonzero_exit_status_is_mapped_to_typed_error(self):
        def fake_runner(argv, **kwargs):
            return subprocess.CompletedProcess(argv, returncode=1, stdout="", stderr="boom")

        with self.assertRaises(energy.SimulationExecutionError):
            energy._run_cli(["openstudio"], cwd=Path("/tmp/work"), timeout_seconds=1.0, runner=fake_runner)


class LocateSqlFileTests(SimpleTestCase):
    """Tests for ``_locate_sql_file``."""

    def test_missing_sql_file_raises(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            with self.assertRaises(energy.SimulationResultError):
                energy._locate_sql_file(Path(tmp_dir))

    def test_present_sql_file_is_returned(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            run_dir = Path(tmp_dir) / "run"
            run_dir.mkdir()
            sql_path = run_dir / "eplusout.sql"
            sql_path.write_bytes(b"")
            result = energy._locate_sql_file(Path(tmp_dir))
            self.assertEqual(result, sql_path)


class ParseSqliteResultsTests(SimpleTestCase):
    """
    Tests for ``_parse_sqlite_results`` against a real, minimally-shaped
    SQLite database matching the relevant slice of EnergyPlus's
    ``eplusout.sql`` schema -- no OpenStudio dependency required.
    """

    def _make_sql_file(self, tmp_dir, *, total_row=("36.0", "GJ"), electricity_row=("20.0", "GJ"),
                        gas_row=("0.0", "GJ"), completed=("Y", "Y"), version="EnergyPlus, Version 23.1.0",
                        include_simulations_table=True, skip_rows=(), errors=()):
        sql_path = Path(tmp_dir) / "eplusout.sql"
        connection = sqlite3.connect(str(sql_path))
        connection.execute(
            "CREATE TABLE TabularDataWithStrings "
            "(ReportName TEXT, TableName TEXT, RowName TEXT, ColumnName TEXT, Value TEXT, Units TEXT)"
        )
        if include_simulations_table:
            connection.execute(
                "CREATE TABLE Simulations (Completed TEXT, CompletedSuccessfully TEXT, EnergyPlusVersion TEXT)"
            )
            connection.execute("INSERT INTO Simulations VALUES (?, ?, ?)", (*completed, version))
        connection.execute(
            "CREATE TABLE Errors "
            "(ErrorType INTEGER, ErrorMessage TEXT, Count INTEGER)"
        )
        for error_type, error_message, count in errors:
            connection.execute(
                "INSERT INTO Errors VALUES (?, ?, ?)",
                (error_type, error_message, count),
            )

        rows = {
            "total": ("AnnualBuildingUtilityPerformanceSummary", "Site and Source Energy",
                       "Total Site Energy", "Total Energy", *total_row),
            "electricity": ("AnnualBuildingUtilityPerformanceSummary", "End Uses",
                             "Total End Uses", "Electricity", *electricity_row),
            "gas": ("AnnualBuildingUtilityPerformanceSummary", "End Uses",
                    "Total End Uses", "Natural Gas", *gas_row),
        }
        for key, row in rows.items():
            if key in skip_rows:
                continue
            connection.execute(
                "INSERT INTO TabularDataWithStrings VALUES (?, ?, ?, ?, ?, ?)", row
            )
        connection.commit()
        connection.close()
        return sql_path

    def test_successful_parse_converts_gj_to_kwh_and_computes_eui(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sql_path = self._make_sql_file(tmp_dir)
            result = energy._parse_sqlite_results(sql_path, conditioned_floor_area_m2=100.0)

        self.assertAlmostEqual(result["annual_total_site_energy_kwh"], 36.0 * energy.GJ_TO_KWH)
        self.assertAlmostEqual(result["annual_electricity_kwh"], 20.0 * energy.GJ_TO_KWH)
        self.assertAlmostEqual(result["annual_natural_gas_kwh"], 0.0)
        self.assertAlmostEqual(
            result["energy_use_intensity_kwh_per_m2"], (36.0 * energy.GJ_TO_KWH) / 100.0
        )
        self.assertEqual(result["energyplus_version"], "EnergyPlus, Version 23.1.0")

    def test_zero_fuel_row_present_is_a_valid_zero(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sql_path = self._make_sql_file(tmp_dir, gas_row=("0.0", "GJ"))
            result = energy._parse_sqlite_results(sql_path, conditioned_floor_area_m2=50.0)
        self.assertEqual(result["annual_natural_gas_kwh"], 0.0)

    def test_missing_total_row_raises(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sql_path = self._make_sql_file(tmp_dir, skip_rows=("total",))
            with self.assertRaises(energy.SimulationResultError):
                energy._parse_sqlite_results(sql_path, conditioned_floor_area_m2=100.0)

    def test_missing_fuel_row_raises_not_treated_as_zero(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sql_path = self._make_sql_file(tmp_dir, skip_rows=("gas",))
            with self.assertRaises(energy.SimulationResultError):
                energy._parse_sqlite_results(sql_path, conditioned_floor_area_m2=100.0)

    def test_unexpected_units_raises(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sql_path = self._make_sql_file(tmp_dir, total_row=("36.0", "MJ"))
            with self.assertRaises(energy.SimulationResultError):
                energy._parse_sqlite_results(sql_path, conditioned_floor_area_m2=100.0)

    def test_non_numeric_value_raises(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sql_path = self._make_sql_file(tmp_dir, total_row=("not-a-number", "GJ"))
            with self.assertRaises(energy.SimulationResultError):
                energy._parse_sqlite_results(sql_path, conditioned_floor_area_m2=100.0)

    def test_failed_simulation_flag_raises(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sql_path = self._make_sql_file(
                tmp_dir,
                completed=("Y", "N"),
                errors=((2, "Fatal heat-balance error", 1),),
            )
            with self.assertRaises(energy.SimulationResultError):
                energy._parse_sqlite_results(sql_path, conditioned_floor_area_m2=100.0)

    def test_false_completion_flags_without_severe_errors_do_not_block_results(self):
        """EnergyPlus 25.2 can leave both SQL flags FALSE after a successful run."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            sql_path = self._make_sql_file(tmp_dir, completed=("FALSE", "FALSE"))
            result = energy._parse_sqlite_results(
                sql_path, conditioned_floor_area_m2=100.0
            )

        self.assertAlmostEqual(
            result["annual_total_site_energy_kwh"], 36.0 * energy.GJ_TO_KWH
        )

    def test_severe_error_message_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sql_path = self._make_sql_file(
                tmp_dir,
                errors=((1, "Missing required construction", 2),),
            )
            with self.assertRaisesRegex(
                energy.SimulationResultError, "Missing required construction"
            ):
                energy._parse_sqlite_results(sql_path, conditioned_floor_area_m2=100.0)

    def test_missing_simulations_table_does_not_block_parsing(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sql_path = self._make_sql_file(tmp_dir, include_simulations_table=False)
            result = energy._parse_sqlite_results(sql_path, conditioned_floor_area_m2=100.0)
        self.assertEqual(result["energyplus_version"], "unknown")

    def test_non_positive_floor_area_raises(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sql_path = self._make_sql_file(tmp_dir)
            with self.assertRaises(energy.SimulationResultError):
                energy._parse_sqlite_results(sql_path, conditioned_floor_area_m2=0.0)

    def test_non_finite_floor_area_raises(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sql_path = self._make_sql_file(tmp_dir)
            with self.assertRaises(energy.SimulationResultError):
                energy._parse_sqlite_results(sql_path, conditioned_floor_area_m2=math.inf)


class RunEnergySimulationOrchestrationTests(SimpleTestCase):
    """
    Tests for the ``run_energy_simulation`` orchestration flow itself,
    with every OpenStudio-touching seam patched out -- these confirm the
    control flow (validation order, temp-directory lifecycle, error
    propagation, result assembly), not real simulation behaviour.
    """

    def _make_valid_ifc_and_epw(self, tmp_dir):
        ifc_path = Path(tmp_dir) / "model.ifc"
        ifc_path.write_text("ISO-10303-21;")
        epw_path = Path(tmp_dir) / "weather.epw"
        epw_path.write_text("LOCATION,Vienna,-,AUT,Src,110360,48.2,16.37,1.0,183.0\n")
        return ifc_path, epw_path

    def test_invalid_ifc_input_short_circuits_before_touching_openstudio(self):
        with mock.patch.object(energy, "_load_openstudio_module") as load_mock:
            with self.assertRaises(energy.InvalidSimulationInputError):
                energy.run_energy_simulation("/nonexistent/model.ifc", "/nonexistent/weather.epw")
            load_mock.assert_not_called()

    def test_successful_run_assembles_result_and_cleans_up_temp_dir(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            ifc_path, epw_path = self._make_valid_ifc_and_epw(tmp_dir)

            captured_work_dirs = []

            def fake_locate_sql_file(work_dir):
                captured_work_dirs.append(work_dir)
                self.assertTrue(work_dir.is_dir())
                return work_dir / "run" / "eplusout.sql"

            fake_model = mock.Mock()
            fake_model.save.return_value = True
            fake_openstudio_module = mock.Mock()
            fake_openstudio_module.path.side_effect = lambda value: value

            with mock.patch.object(energy, "_load_openstudio_module", return_value=fake_openstudio_module), \
                    mock.patch.object(energy, "_discover_cli_path", return_value="/usr/bin/openstudio"), \
                    mock.patch.object(energy, "_extract_zone_boxes", return_value=[
                        energy.ThermalZoneBox(name="Room", min_x=0, min_y=0, min_z=0, max_x=4, max_y=3, max_z=2.5)
                    ]), \
                    mock.patch.object(energy, "_build_model", return_value=fake_model), \
                    mock.patch.object(energy, "_run_cli") as run_cli_mock, \
                    mock.patch.object(energy, "_locate_sql_file", side_effect=fake_locate_sql_file), \
                    mock.patch.object(energy, "_parse_sqlite_results", return_value={
                        "annual_total_site_energy_kwh": 1000.0,
                        "annual_electricity_kwh": 800.0,
                        "annual_natural_gas_kwh": 200.0,
                        "energy_use_intensity_kwh_per_m2": 83.33,
                        "energyplus_version": "EnergyPlus, Version 23.1.0",
                    }):
                result = energy.run_energy_simulation(str(ifc_path), str(epw_path))

            run_cli_mock.assert_called_once()
            self.assertEqual(result.annual_total_site_energy_kwh, 1000.0)
            self.assertEqual(result.annual_electricity_kwh, 800.0)
            self.assertEqual(result.annual_natural_gas_kwh, 200.0)
            self.assertEqual(result.zone_count, 1)
            self.assertAlmostEqual(result.conditioned_floor_area_m2, 12.0)
            self.assertEqual(result.weather_file_name, "weather.epw")
            self.assertEqual(result.energyplus_version, "EnergyPlus, Version 23.1.0")
            self.assertIn("simulation", result.assumptions_summary.lower())

            self.assertEqual(len(captured_work_dirs), 1)
            self.assertFalse(captured_work_dirs[0].exists())

    def test_temp_dir_is_cleaned_up_even_when_execution_fails(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            ifc_path, epw_path = self._make_valid_ifc_and_epw(tmp_dir)

            captured_work_dirs = []

            def fake_run_cli(argv, cwd, timeout_seconds):
                captured_work_dirs.append(Path(cwd))
                self.assertTrue(Path(cwd).is_dir())
                raise energy.SimulationExecutionError("simulated CLI failure")

            fake_model = mock.Mock()
            fake_model.save.return_value = True
            fake_openstudio_module = mock.Mock()
            fake_openstudio_module.path.side_effect = lambda value: value

            with mock.patch.object(energy, "_load_openstudio_module", return_value=fake_openstudio_module), \
                    mock.patch.object(energy, "_discover_cli_path", return_value="/usr/bin/openstudio"), \
                    mock.patch.object(energy, "_extract_zone_boxes", return_value=[
                        energy.ThermalZoneBox(name="Room", min_x=0, min_y=0, min_z=0, max_x=4, max_y=3, max_z=2.5)
                    ]), \
                    mock.patch.object(energy, "_build_model", return_value=fake_model), \
                    mock.patch.object(energy, "_run_cli", side_effect=fake_run_cli):
                with self.assertRaises(energy.SimulationExecutionError):
                    energy.run_energy_simulation(str(ifc_path), str(epw_path))

            self.assertEqual(len(captured_work_dirs), 1)
            self.assertFalse(captured_work_dirs[0].exists())

    def test_missing_openstudio_runtime_propagates_typed_error(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            ifc_path, epw_path = self._make_valid_ifc_and_epw(tmp_dir)
            with _real_import_openstudio_is_missing():
                with self.assertRaises(energy.OpenStudioUnavailableError):
                    energy.run_energy_simulation(str(ifc_path), str(epw_path))
