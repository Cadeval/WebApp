# -*- coding: utf-8 -*-
"""
Dedicated tests for ``ifc_extractor.helpers``.

These tests focus on the small, pure numeric/quantity helpers used while
walking an IFC model (``float_or_zero``, storey selection, ratio
calculation and material volume resolution), plus a couple of
integration-style checks using small real, in-memory IfcOpenShell
objects instead of large fixtures or broad mocks.

``ifc_extractor`` is intentionally not part of ``INSTALLED_APPS``, but
Django's test discovery (``manage.py test``) still finds ``test*.py``
modules under ``src`` by path, so this module is picked up automatically.
"""
import math
import os
import tempfile

import ifcopenshell
import ifcopenshell.api
import ifcopenshell.api.aggregate
import ifcopenshell.api.context
import ifcopenshell.api.feature
import ifcopenshell.api.geometry
import ifcopenshell.api.material
import ifcopenshell.api.project
import ifcopenshell.api.pset
import ifcopenshell.api.root
import ifcopenshell.api.spatial
import ifcopenshell.api.unit
import ifcopenshell.util.element
from django.test import SimpleTestCase

from apps.shared.ifc_extractor.helpers import (
    float_or_zero,
    ifc_product_walk,
    is_external,
    iterate_geometry,
    resolve_component_volume,
    resolve_element_components,
    resolve_material_component_volumes,
    resolve_qto_volume,
    safe_ratio,
    select_ground_floor,
)


class FloatOrZeroCoreTests(SimpleTestCase):
    """Core/happy-path behaviour of ``float_or_zero``."""

    def test_missing_key_returns_zero(self):
        self.assertEqual(float_or_zero({}, "Dichte"), 0.0)

    def test_none_value_returns_zero(self):
        self.assertEqual(float_or_zero({"Dichte": None}, "Dichte"), 0.0)

    def test_plain_dot_string_is_parsed(self):
        self.assertEqual(float_or_zero({"Dichte": "12.5"}, "Dichte"), 12.5)

    def test_comma_decimal_string_is_parsed(self):
        self.assertEqual(float_or_zero({"Dichte": "12,5"}, "Dichte"), 12.5)

    def test_existing_int_is_coerced_to_float(self):
        value = float_or_zero({"Dichte": 7}, "Dichte")
        self.assertEqual(value, 7.0)
        self.assertIsInstance(value, float)

    def test_existing_float_is_returned_as_float(self):
        self.assertEqual(float_or_zero({"Dichte": 7.5}, "Dichte"), 7.5)


class FloatOrZeroNegativeTests(SimpleTestCase):
    """Negative/invalid inputs must never raise and must coerce to 0."""

    def test_empty_string_returns_zero(self):
        self.assertEqual(float_or_zero({"Dichte": ""}, "Dichte"), 0.0)

    def test_whitespace_only_string_returns_zero(self):
        self.assertEqual(float_or_zero({"Dichte": "   "}, "Dichte"), 0.0)

    def test_invalid_string_returns_zero_without_raising(self):
        self.assertEqual(float_or_zero({"Dichte": "Volumen"}, "Dichte"), 0.0)

    def test_bool_true_is_invalid_and_returns_zero(self):
        # bool is a subclass of int in Python, but a bare True/False is not
        # a valid numeric quantity in this domain.
        self.assertEqual(float_or_zero({"Dichte": True}, "Dichte"), 0.0)

    def test_bool_false_is_invalid_and_returns_zero(self):
        self.assertEqual(float_or_zero({"Dichte": False}, "Dichte"), 0.0)

    def test_nan_string_is_rejected_as_non_finite(self):
        self.assertEqual(float_or_zero({"Dichte": "nan"}, "Dichte"), 0.0)

    def test_infinity_string_is_rejected_as_non_finite(self):
        self.assertEqual(float_or_zero({"Dichte": "inf"}, "Dichte"), 0.0)

    def test_float_nan_value_is_rejected_as_non_finite(self):
        self.assertEqual(float_or_zero({"Dichte": float("nan")}, "Dichte"), 0.0)

    def test_float_infinity_value_is_rejected_as_non_finite(self):
        self.assertEqual(float_or_zero({"Dichte": float("inf")}, "Dichte"), 0.0)

    def test_list_value_returns_zero_without_raising(self):
        self.assertEqual(float_or_zero({"Dichte": [1, 2, 3]}, "Dichte"), 0.0)


class FloatOrZeroEdgeCaseTests(SimpleTestCase):
    """Thousands-separator / mixed formatting edge cases."""

    def test_zero_value_is_preserved_not_treated_as_missing(self):
        self.assertEqual(float_or_zero({"Dichte": 0}, "Dichte"), 0.0)
        self.assertEqual(float_or_zero({"Dichte": "0"}, "Dichte"), 0.0)

    def test_negative_number_string_is_parsed(self):
        self.assertEqual(float_or_zero({"Dichte": "-3,5"}, "Dichte"), -3.5)

    def test_german_thousands_dot_and_comma_decimal(self):
        self.assertEqual(float_or_zero({"Dichte": "1.234,56"}, "Dichte"), 1234.56)

    def test_english_thousands_comma_and_dot_decimal(self):
        self.assertEqual(float_or_zero({"Dichte": "1,234.56"}, "Dichte"), 1234.56)

    def test_space_as_thousands_separator(self):
        self.assertEqual(float_or_zero({"Dichte": "1 234,56"}, "Dichte"), 1234.56)

    def test_multiple_commas_are_treated_as_thousands_grouping(self):
        self.assertEqual(float_or_zero({"Dichte": "1,234,567"}, "Dichte"), 1234567.0)


class SafeRatioTests(SimpleTestCase):
    """Pure ratio helper used for bgf_bf_ratio / bri_bgf_ratio."""

    def test_normal_ratio(self):
        self.assertEqual(safe_ratio(10.0, 2.0), 5.0)

    def test_zero_denominator_returns_zero(self):
        self.assertEqual(safe_ratio(10.0, 0.0), 0.0)

    def test_zero_numerator_and_denominator_returns_zero(self):
        self.assertEqual(safe_ratio(0.0, 0.0), 0.0)

    def test_negative_denominator_still_divides(self):
        self.assertEqual(safe_ratio(10.0, -2.0), -5.0)


class SelectGroundFloorTests(SimpleTestCase):
    """Ground floor storey selection, using real in-memory IFC storeys."""

    def _make_storey(self, ifc_file, name, elevation):
        storey = ifcopenshell.api.run(
            "root.create_entity", ifc_file, ifc_class="IfcBuildingStorey", name=name
        )
        storey.Elevation = elevation
        return storey

    def test_no_storeys_returns_none(self):
        self.assertIsNone(select_ground_floor([]))
        self.assertIsNone(select_ground_floor(None))

    def test_selects_storey_with_exact_zero_elevation(self):
        ifc_file = ifcopenshell.file(schema="IFC4")
        basement = self._make_storey(ifc_file, "UG01", -3.0)
        ground = self._make_storey(ifc_file, "EG", 0.0)
        upper = self._make_storey(ifc_file, "OG1", 3.0)

        selected = select_ground_floor([basement, ground, upper])

        self.assertEqual(selected, ground)

    def test_falls_back_to_first_storey_when_none_is_exactly_zero(self):
        ifc_file = ifcopenshell.file(schema="IFC4")
        first = self._make_storey(ifc_file, "UG01", -3.0)
        second = self._make_storey(ifc_file, "OG1", 3.0)

        selected = select_ground_floor([first, second])

        self.assertEqual(selected, first)


class ResolveQtoVolumeTests(SimpleTestCase):
    """Standard Qto_*BaseQuantities fallback, using a real IfcWall."""

    def _make_wall_with_qto(self, properties):
        ifc_file = ifcopenshell.file(schema="IFC4")
        wall = ifcopenshell.api.run(
            "root.create_entity", ifc_file, ifc_class="IfcWall", name="Wall1"
        )
        qto = ifcopenshell.api.run(
            "pset.add_qto", ifc_file, product=wall, name="Qto_WallBaseQuantities"
        )
        ifcopenshell.api.run("pset.edit_qto", ifc_file, qto=qto, properties=properties)
        return wall

    def test_no_quantities_returns_zero(self):
        self.assertEqual(resolve_qto_volume({}), 0.0)
        self.assertEqual(resolve_qto_volume(None), 0.0)

    def test_prefers_gross_volume_over_net_volume(self):
        wall = self._make_wall_with_qto({"GrossVolume": 12.5, "NetVolume": 10.0})
        qtos = ifcopenshell.util.element.get_psets(wall, qtos_only=True)

        self.assertEqual(resolve_qto_volume(qtos), 12.5)

    def test_falls_back_to_net_volume_when_gross_missing(self):
        wall = self._make_wall_with_qto({"NetVolume": 8.25})
        qtos = ifcopenshell.util.element.get_psets(wall, qtos_only=True)

        self.assertEqual(resolve_qto_volume(qtos), 8.25)

    def test_returns_zero_when_neither_volume_present(self):
        wall = self._make_wall_with_qto({"Length": 3.0})
        qtos = ifcopenshell.util.element.get_psets(wall, qtos_only=True)

        self.assertEqual(resolve_qto_volume(qtos), 0.0)

    def test_no_qto_falls_back_to_geometry_volume(self):
        self.assertEqual(resolve_qto_volume({}, geometry_volume=4.2), 4.2)
        self.assertEqual(resolve_qto_volume(None, geometry_volume=4.2), 4.2)

    def test_zero_gross_volume_falls_back_to_valid_net_volume(self):
        wall = self._make_wall_with_qto({"GrossVolume": 0.0, "NetVolume": 8.25})
        qtos = ifcopenshell.util.element.get_psets(wall, qtos_only=True)

        self.assertEqual(resolve_qto_volume(qtos, geometry_volume=99.0), 8.25)

    def test_negative_gross_volume_falls_back_to_valid_net_volume(self):
        wall = self._make_wall_with_qto({"GrossVolume": -3.0, "NetVolume": 8.25})
        qtos = ifcopenshell.util.element.get_psets(wall, qtos_only=True)

        self.assertEqual(resolve_qto_volume(qtos, geometry_volume=99.0), 8.25)

    def test_invalid_gross_and_net_volumes_fall_back_to_geometry_volume(self):
        wall = self._make_wall_with_qto({"GrossVolume": 0.0, "NetVolume": -1.0})
        qtos = ifcopenshell.util.element.get_psets(wall, qtos_only=True)

        self.assertEqual(resolve_qto_volume(qtos, geometry_volume=3.75), 3.75)

    def test_negative_geometry_volume_fallback_is_rejected(self):
        self.assertEqual(resolve_qto_volume({}, geometry_volume=-1.0), 0.0)

    def test_no_geometry_volume_fallback_given_returns_zero(self):
        self.assertEqual(resolve_qto_volume({}), 0.0)


class ResolveComponentVolumeTests(SimpleTestCase):
    """Per-component (German pset) volume resolution with Qto fallback."""

    def test_uses_direct_german_key_when_present(self):
        subdict = {"Schicht/Komponenten Volumen (brutto)": 3.2}
        self.assertEqual(resolve_component_volume(subdict, fallback_volume=99.0), 3.2)

    def test_uses_nested_properties_german_key_when_present(self):
        subdict = {"properties": {"Schicht/Komponenten Volumen (brutto)": 4.4}}
        self.assertEqual(resolve_component_volume(subdict, fallback_volume=99.0), 4.4)

    def test_falls_back_to_qto_volume_when_missing(self):
        subdict = {}
        self.assertEqual(resolve_component_volume(subdict, fallback_volume=7.0), 7.0)

    def test_does_not_crash_when_properties_key_absent(self):
        # Regression: previously indexing subdict["properties"] directly
        # could raise KeyError when the sub quantity was not complex.
        subdict = {"SomeOtherKey": 1}
        self.assertEqual(resolve_component_volume(subdict, fallback_volume=5.0), 5.0)

    def test_zero_volume_is_preserved_not_treated_as_missing(self):
        subdict = {"Schicht/Komponenten Volumen (brutto)": 0}
        self.assertEqual(resolve_component_volume(subdict, fallback_volume=99.0), 0.0)

    def test_none_subdict_falls_back_safely(self):
        self.assertEqual(resolve_component_volume(None, fallback_volume=1.5), 1.5)


class ResolveMaterialComponentVolumesTests(SimpleTestCase):
    """
    Material-association fallback used when an element has no
    "Component Quantities" pset, using small real in-memory materials.
    """

    def setUp(self):
        self.ifc_file = ifcopenshell.file(schema="IFC4")

    def test_no_material_returns_empty_dict(self):
        self.assertEqual(resolve_material_component_volumes(None, total_volume=12.5), {})

    def test_single_material_gets_full_volume(self):
        material = ifcopenshell.api.material.add_material(self.ifc_file, name="Concrete")

        result = resolve_material_component_volumes(material, total_volume=12.5)

        self.assertEqual(result, {"Concrete": 12.5})

    def test_unnamed_material_falls_back_to_placeholder_name(self):
        # ``Name`` is a mandatory attribute on a real IFC4 IfcMaterial, so a
        # genuinely nameless material cannot be constructed via the API;
        # use a narrow stand-in exposing just the ``is_a``/``Name``
        # protocol that ``resolve_material_component_volumes`` relies on.
        class _UnnamedMaterial:
            Name = None

            @staticmethod
            def is_a(_ifc_class):
                return False

        result = resolve_material_component_volumes(_UnnamedMaterial(), total_volume=4.0)

        self.assertEqual(result, {"Material 1": 4.0})

    def test_layer_set_allocates_proportionally_to_thickness(self):
        layer_set = ifcopenshell.api.material.add_material_set(
            self.ifc_file, name="LS1", set_type="IfcMaterialLayerSet"
        )
        insulation = ifcopenshell.api.material.add_material(self.ifc_file, name="Insulation")
        brick = ifcopenshell.api.material.add_material(self.ifc_file, name="Brick")
        layer1 = ifcopenshell.api.material.add_layer(self.ifc_file, layer_set=layer_set, material=insulation)
        ifcopenshell.api.material.edit_layer(self.ifc_file, layer=layer1, attributes={"LayerThickness": 0.1})
        layer2 = ifcopenshell.api.material.add_layer(self.ifc_file, layer_set=layer_set, material=brick)
        ifcopenshell.api.material.edit_layer(self.ifc_file, layer=layer2, attributes={"LayerThickness": 0.3})

        result = resolve_material_component_volumes(layer_set, total_volume=8.0)

        self.assertEqual(set(result.keys()), {"Insulation", "Brick"})
        self.assertAlmostEqual(result["Insulation"], 2.0)
        self.assertAlmostEqual(result["Brick"], 6.0)

    def test_layer_set_without_thickness_splits_evenly(self):
        layer_set = ifcopenshell.api.material.add_material_set(
            self.ifc_file, name="LS2", set_type="IfcMaterialLayerSet"
        )
        wood = ifcopenshell.api.material.add_material(self.ifc_file, name="Wood")
        steel = ifcopenshell.api.material.add_material(self.ifc_file, name="Steel")
        ifcopenshell.api.material.add_layer(self.ifc_file, layer_set=layer_set, material=wood)
        ifcopenshell.api.material.add_layer(self.ifc_file, layer_set=layer_set, material=steel)

        result = resolve_material_component_volumes(layer_set, total_volume=10.0)

        self.assertEqual(result, {"Wood": 5.0, "Steel": 5.0})

    def test_constituent_set_splits_evenly(self):
        constituent_set = ifcopenshell.api.material.add_material_set(
            self.ifc_file, name="CS1", set_type="IfcMaterialConstituentSet"
        )
        steel = ifcopenshell.api.material.add_material(self.ifc_file, name="Steel")
        glass = ifcopenshell.api.material.add_material(self.ifc_file, name="Glass")
        ifcopenshell.api.material.add_constituent(self.ifc_file, constituent_set=constituent_set, material=steel)
        ifcopenshell.api.material.add_constituent(self.ifc_file, constituent_set=constituent_set, material=glass)

        result = resolve_material_component_volumes(constituent_set, total_volume=9.0)

        self.assertEqual(result, {"Steel": 4.5, "Glass": 4.5})

    def test_profile_set_splits_evenly(self):
        profile_set = ifcopenshell.api.material.add_material_set(
            self.ifc_file, name="PS1", set_type="IfcMaterialProfileSet"
        )
        aluminum = ifcopenshell.api.material.add_material(self.ifc_file, name="Aluminum")
        ifcopenshell.api.material.add_profile(self.ifc_file, profile_set=profile_set, material=aluminum)

        result = resolve_material_component_volumes(profile_set, total_volume=6.0)

        self.assertEqual(result, {"Aluminum": 6.0})

    def test_duplicate_material_names_are_accumulated(self):
        layer_set = ifcopenshell.api.material.add_material_set(
            self.ifc_file, name="LS3", set_type="IfcMaterialLayerSet"
        )
        concrete_a = ifcopenshell.api.material.add_material(self.ifc_file, name="Concrete")
        concrete_b = ifcopenshell.api.material.add_material(self.ifc_file, name="Concrete")
        layer1 = ifcopenshell.api.material.add_layer(self.ifc_file, layer_set=layer_set, material=concrete_a)
        ifcopenshell.api.material.edit_layer(self.ifc_file, layer=layer1, attributes={"LayerThickness": 0.2})
        layer2 = ifcopenshell.api.material.add_layer(self.ifc_file, layer_set=layer_set, material=concrete_b)
        ifcopenshell.api.material.edit_layer(self.ifc_file, layer=layer2, attributes={"LayerThickness": 0.2})

        result = resolve_material_component_volumes(layer_set, total_volume=10.0)

        self.assertEqual(result, {"Concrete": 10.0})


class ResolveElementComponentsTests(SimpleTestCase):
    """
    Wiring helper combining the German "Component Quantities" pset with
    the standard IFC material association fallback, using a real
    in-memory IfcWall.
    """

    def test_prefers_component_quantities_pset_when_present(self):
        ifc_file = ifcopenshell.file(schema="IFC4")
        wall = ifcopenshell.api.run(
            "root.create_entity", ifc_file, ifc_class="IfcWall", name="Wall1"
        )
        qto = ifcopenshell.api.run(
            "pset.add_qto", ifc_file, product=wall, name="Component Quantities"
        )
        ifcopenshell.api.run(
            "pset.edit_qto",
            ifc_file,
            qto=qto,
            properties={
                "Beton": {
                    "Discrimination": "layer",
                    "HasQuantities": {"Schicht/Komponenten Volumen (brutto)": 3.5},
                },
            },
        )

        result = resolve_element_components(wall, qto_volume=99.0)

        self.assertEqual(result, {"Beton": 3.5})

    def test_falls_back_to_associated_material_when_pset_missing(self):
        ifc_file = ifcopenshell.file(schema="IFC4")
        wall = ifcopenshell.api.run(
            "root.create_entity", ifc_file, ifc_class="IfcWall", name="Wall2"
        )
        qto = ifcopenshell.api.run(
            "pset.add_qto", ifc_file, product=wall, name="Qto_WallBaseQuantities"
        )
        ifcopenshell.api.run("pset.edit_qto", ifc_file, qto=qto, properties={"GrossVolume": 12.5})
        material = ifcopenshell.api.material.add_material(ifc_file, name="Concrete")
        ifcopenshell.api.material.assign_material(
            ifc_file, products=[wall], type="IfcMaterial", material=material
        )

        result = resolve_element_components(wall, qto_volume=12.5)

        self.assertEqual(result, {"Concrete": 12.5})

    def test_returns_empty_dict_when_neither_pset_nor_material_present(self):
        ifc_file = ifcopenshell.file(schema="IFC4")
        wall = ifcopenshell.api.run(
            "root.create_entity", ifc_file, ifc_class="IfcWall", name="Wall3"
        )

        result = resolve_element_components(wall, qto_volume=5.0)

        self.assertEqual(result, {})

    def test_falls_back_to_material_when_pset_has_no_actual_components(self):
        # An empty "Component Quantities" pset is still truthy (it always
        # carries an "id" key), so it must not be treated as authoritative
        # when it contains no real components; a plain wall relying on its
        # material association should still get a material assigned.
        ifc_file = ifcopenshell.file(schema="IFC4")
        wall = ifcopenshell.api.run(
            "root.create_entity", ifc_file, ifc_class="IfcWall", name="Wall4"
        )
        ifcopenshell.api.run(
            "pset.add_qto", ifc_file, product=wall, name="Component Quantities"
        )
        material = ifcopenshell.api.material.add_material(ifc_file, name="Concrete")
        ifcopenshell.api.material.assign_material(
            ifc_file, products=[wall], type="IfcMaterial", material=material
        )

        result = resolve_element_components(wall, qto_volume=7.5)

        self.assertEqual(result, {"Concrete": 7.5})

    def test_legacy_pset_allocates_remaining_volume_among_missing_components(self):
        ifc_file = ifcopenshell.file(schema="IFC4")
        wall = ifcopenshell.api.run("root.create_entity", ifc_file, ifc_class="IfcWall", name="Wall")
        qto = ifcopenshell.api.run("pset.add_qto", ifc_file, product=wall, name="Component Quantities")
        ifcopenshell.api.run("pset.edit_qto", ifc_file, qto=qto, properties={
            "Comp1": {"Discrimination": "layer", "HasQuantities": {}},
            "Comp2": {"Discrimination": "layer", "HasQuantities": {}},
        })
        # Currently each gets 10.0 (total 20.0). Should get 5.0 each.
        result = resolve_element_components(wall, qto_volume=10.0)
        self.assertEqual(result, {"Comp1": 5.0, "Comp2": 5.0})

    def test_legacy_pset_mixes_explicit_and_missing_volumes(self):
        ifc_file = ifcopenshell.file(schema="IFC4")
        wall = ifcopenshell.api.run("root.create_entity", ifc_file, ifc_class="IfcWall", name="Wall")
        qto = ifcopenshell.api.run("pset.add_qto", ifc_file, product=wall, name="Component Quantities")
        ifcopenshell.api.run("pset.edit_qto", ifc_file, qto=qto, properties={
            "Explicit": {
                "Discrimination": "layer",
                "HasQuantities": {"Schicht/Komponenten Volumen (brutto)": 6.0}
            },
            "Missing": {"Discrimination": "layer", "HasQuantities": {}},
        })
        # 10.0 - 6.0 = 4.0 remaining for "Missing"
        result = resolve_element_components(wall, qto_volume=10.0)
        self.assertEqual(result, {"Explicit": 6.0, "Missing": 4.0})

    def test_legacy_pset_explicit_zero_remains_zero(self):
        ifc_file = ifcopenshell.file(schema="IFC4")
        wall = ifcopenshell.api.run("root.create_entity", ifc_file, ifc_class="IfcWall", name="Wall")
        qto = ifcopenshell.api.run("pset.add_qto", ifc_file, product=wall, name="Component Quantities")
        ifcopenshell.api.run("pset.edit_qto", ifc_file, qto=qto, properties={
            "Zero": {
                "Discrimination": "layer",
                "HasQuantities": {"Schicht/Komponenten Volumen (brutto)": 0.0}
            },
            "Missing": {"Discrimination": "layer", "HasQuantities": {}},
        })
        # 10.0 - 0.0 = 10.0 remaining for "Missing"
        result = resolve_element_components(wall, qto_volume=10.0)
        self.assertEqual(result, {"Zero": 0.0, "Missing": 10.0})

    def test_legacy_pset_negative_remainder_is_not_allocated(self):
        ifc_file = ifcopenshell.file(schema="IFC4")
        wall = ifcopenshell.api.run("root.create_entity", ifc_file, ifc_class="IfcWall", name="Wall")
        qto = ifcopenshell.api.run("pset.add_qto", ifc_file, product=wall, name="Component Quantities")
        ifcopenshell.api.run("pset.edit_qto", ifc_file, qto=qto, properties={
            "Explicit": {
                "Discrimination": "layer",
                "HasQuantities": {"Schicht/Komponenten Volumen (brutto)": 15.0}
            },
            "Missing": {"Discrimination": "layer", "HasQuantities": {}},
        })
        # 10.0 - 15.0 = -5.0. Missing should get 0.0.
        result = resolve_element_components(wall, qto_volume=10.0)
        self.assertEqual(result, {"Explicit": 15.0, "Missing": 0.0})

    def test_distribute_volumes_accumulates_duplicate_names(self):
        # Even if get_pset returns a dict, _distribute_volumes is pure and
        # supports lists of tuples to conserve volume if duplicates are present.
        from .helpers import _distribute_volumes
        data = [("Comp", 1.0), ("Comp", None)]
        # Total 10.0. Explicit 1.0. Remaining 9.0 for the missing "Comp".
        # Result should be {"Comp": 1.0 + 9.0 = 10.0}
        result = _distribute_volumes(data, total_volume=10.0)
        self.assertEqual(result, {"Comp": 10.0})


class _FakeGeometryIterator:
    """
    Narrow test double mimicking IfcOpenShell's geometry iterator
    protocol: after ``initialize()`` the iterator is already positioned
    on the first item (retrievable via ``get()``), and ``next()``
    advances to the following one, returning ``False`` once exhausted.
    """

    def __init__(self, items):
        self._items = items
        self._index = 0

    def get(self):
        return self._items[self._index]

    def next(self):
        self._index += 1
        return self._index < len(self._items)


class IterateGeometryTests(SimpleTestCase):
    """
    Regression tests for the iterator-skips-first-item bug: a naive
    ``while iterator.next(): iterator.get()`` loop drops the first item
    that ``initialize()`` already positioned the iterator on.
    """

    def test_yields_all_items_including_the_first(self):
        fake_iterator = _FakeGeometryIterator(["first", "second", "third"])

        self.assertEqual(list(iterate_geometry(fake_iterator)), ["first", "second", "third"])

    def test_single_item_is_yielded_exactly_once(self):
        fake_iterator = _FakeGeometryIterator(["only"])

        self.assertEqual(list(iterate_geometry(fake_iterator)), ["only"])


class IfcProductWalkEndToEndTests(SimpleTestCase):
    """
    Compact end-to-end check of ``ifc_product_walk`` against a small,
    real in-memory IFC4 file: a full project/site/building/storey
    hierarchy with a single building storey whose ``Elevation`` is
    deliberately non-zero (exercising the "no storey is exactly 0"
    fallback), and exactly one geometrically represented wall (so an
    iterator that silently skips its first item would yield no
    materials at all instead of merely a wrong one).
    """

    def _build_sample_ifc(self, path: str) -> None:
        ifc_file = ifcopenshell.api.project.create_file(version="IFC4")
        project = ifcopenshell.api.root.create_entity(ifc_file, ifc_class="IfcProject", name="TestProject")

        length_unit = ifcopenshell.api.unit.add_si_unit(ifc_file, unit_type="LENGTHUNIT")
        area_unit = ifcopenshell.api.unit.add_si_unit(ifc_file, unit_type="AREAUNIT")
        volume_unit = ifcopenshell.api.unit.add_si_unit(ifc_file, unit_type="VOLUMEUNIT")
        ifcopenshell.api.unit.assign_unit(ifc_file, units=[length_unit, area_unit, volume_unit])

        model_context = ifcopenshell.api.context.add_context(ifc_file, context_type="Model")
        body_context = ifcopenshell.api.context.add_context(
            ifc_file, context_type="Model", context_identifier="Body",
            target_view="MODEL_VIEW", parent=model_context,
        )

        site = ifcopenshell.api.root.create_entity(ifc_file, ifc_class="IfcSite", name="Site")
        building = ifcopenshell.api.root.create_entity(ifc_file, ifc_class="IfcBuilding", name="Building")
        storey = ifcopenshell.api.root.create_entity(ifc_file, ifc_class="IfcBuildingStorey", name="Level1")
        storey.Elevation = 3.0

        ifcopenshell.api.aggregate.assign_object(ifc_file, products=[site], relating_object=project)
        ifcopenshell.api.aggregate.assign_object(ifc_file, products=[building], relating_object=site)
        ifcopenshell.api.aggregate.assign_object(ifc_file, products=[storey], relating_object=building)

        wall = ifcopenshell.api.root.create_entity(ifc_file, ifc_class="IfcWall", name="Wall1")
        ifcopenshell.api.spatial.assign_container(ifc_file, products=[wall], relating_structure=storey)

        representation = ifcopenshell.api.geometry.create_2pt_wall(
            ifc_file, element=wall, context=body_context,
            p1=(0.0, 0.0), p2=(5.0, 0.0), elevation=0.0, height=3.0, thickness=0.2,
        )
        ifcopenshell.api.geometry.assign_representation(ifc_file, product=wall, representation=representation)

        material = ifcopenshell.api.material.add_material(ifc_file, name="Concrete")
        ifcopenshell.api.material.assign_material(
            ifc_file, products=[wall], type="IfcMaterial", material=material
        )

        # A GrossVolume deliberately distinct from the wall's actual
        # geometry volume (5m x 0.2m x 3m = 3.0 m3), so the assertions
        # below only pass if the material volume/mass genuinely comes
        # from this standard Qto quantity rather than from stale/reused
        # geometry data.
        qto = ifcopenshell.api.pset.add_qto(ifc_file, product=wall, name="Qto_WallBaseQuantities")
        ifcopenshell.api.pset.edit_qto(ifc_file, qto=qto, properties={"GrossVolume": 4.5})

        ifc_file.write(path)

    def test_full_walk_computes_material_and_metrics_from_real_ifc(self):
        user_config = {
            "Concrete": {
                # A mix of already-numeric and comma-decimal values, as
                # real user config data may contain either.
                "Dichte": "2,5",
                "GWP": "0,1",
                "AP": 0.01,
                "PENRT": "1,2",
                # A single uncombined material uses base recovery percentages.
                "Abfallreduktion": 10,
                "Recycling": 20,
                "Preis Multiplikator": "Volumen",
                "Globaler Brutto Preis": "10",
                "Lokaler Brutto Preis": "8",
                "Lokaler Netto Preis": "7",
            }
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            ifc_path = os.path.join(tmp_dir, "sample.ifc")
            self._build_sample_ifc(ifc_path)

            materials, metrics = ifc_product_walk(
                user_id="test-user", user_config=user_config, ifc_file_path=ifc_path
            )

        self.assertIn("Concrete", materials)
        material_properties = materials["Concrete"]

        # Volume/mass must come from the standard Qto GrossVolume (4.5),
        # not from the wall's actual geometry volume (3.0), confirming
        # the geometry iterator did not skip this (only) element.
        self.assertAlmostEqual(material_properties.volume, 4.5)
        # Material costing uses the 5 x 3 m wall side, while building
        # floor metrics below retain the 5 x 0.2 m footprint.
        self.assertAlmostEqual(material_properties.area, 15.0)
        self.assertAlmostEqual(material_properties.length, 5.0)
        self.assertAlmostEqual(material_properties.mass, 11.25)

        self.assertAlmostEqual(material_properties.gwp_ml_a1_a3, 1.125)
        self.assertAlmostEqual(material_properties.ap_ml_a1_a3, 0.1125)
        self.assertAlmostEqual(material_properties.penrt_ml_a1_a3, 13.5)

        self.assertAlmostEqual(material_properties.waste_mass, 1.125)
        self.assertAlmostEqual(material_properties.recyclable_mass, 2.25)

        self.assertEqual(metrics.stockwerke, 1)
        self.assertAlmostEqual(metrics.brutto_rauminhalt, 3.0)
        self.assertAlmostEqual(metrics.brutto_grundfläche, 1.0)

        # There is no IfcSlab on this ground floor, so bebaute_fläche is
        # a degenerate zero denominator for bgf_bf_ratio; it must resolve
        # safely to 0.0 instead of raising ZeroDivisionError, and every
        # ratio returned must be finite.
        self.assertEqual(metrics.bebaute_fläche, 0.0)
        self.assertEqual(metrics.bgf_bf_ratio, 0.0)
        self.assertTrue(math.isfinite(metrics.bri_bgf_ratio))
        self.assertAlmostEqual(metrics.bri_bgf_ratio, 3.0)

class FacadeExtractionTests(SimpleTestCase):
    """Tests for facade area and opening area extraction."""

    def _build_facade_ifc(self, path: str) -> None:
        ifc_file = ifcopenshell.api.project.create_file(version="IFC4")
        project = ifcopenshell.api.root.create_entity(ifc_file, ifc_class="IfcProject", name="TestProject")
        
        length_unit = ifcopenshell.api.unit.add_si_unit(ifc_file, unit_type="LENGTHUNIT")
        area_unit = ifcopenshell.api.unit.add_si_unit(ifc_file, unit_type="AREAUNIT")
        volume_unit = ifcopenshell.api.unit.add_si_unit(ifc_file, unit_type="VOLUMEUNIT")
        ifcopenshell.api.unit.assign_unit(ifc_file, units=[length_unit, area_unit, volume_unit])

        model_context = ifcopenshell.api.context.add_context(ifc_file, context_type="Model")
        body_context = ifcopenshell.api.context.add_context(
            ifc_file, context_type="Model", context_identifier="Body",
            target_view="MODEL_VIEW", parent=model_context,
        )

        site = ifcopenshell.api.root.create_entity(ifc_file, ifc_class="IfcSite", name="Site")
        building = ifcopenshell.api.root.create_entity(ifc_file, ifc_class="IfcBuilding", name="Building")
        storey = ifcopenshell.api.root.create_entity(ifc_file, ifc_class="IfcBuildingStorey", name="Level1")
        ifcopenshell.api.aggregate.assign_object(ifc_file, products=[site], relating_object=project)
        ifcopenshell.api.aggregate.assign_object(ifc_file, products=[building], relating_object=site)
        ifcopenshell.api.aggregate.assign_object(ifc_file, products=[storey], relating_object=building)

        # 1. External Wall with Qto GrossSideArea=50.0
        wall_ext = ifcopenshell.api.root.create_entity(ifc_file, ifc_class="IfcWall", name="WallExt")
        ifcopenshell.api.spatial.assign_container(ifc_file, products=[wall_ext], relating_structure=storey)
        ifcopenshell.api.pset.add_pset(ifc_file, product=wall_ext, name="Pset_WallCommon")
        ifcopenshell.api.pset.edit_pset(ifc_file, pset=ifc_file.by_type("IfcPropertySet")[-1], properties={"IsExternal": True})
        qto_wall = ifcopenshell.api.pset.add_qto(ifc_file, product=wall_ext, name="Qto_WallBaseQuantities")
        ifcopenshell.api.pset.edit_qto(ifc_file, qto=qto_wall, properties={"GrossSideArea": 50.0})
        # Add minimal geometry so it's picked up by the iterator
        rep_wall = ifcopenshell.api.geometry.create_2pt_wall(ifc_file, element=wall_ext, context=body_context, p1=(0,0), p2=(1,0), elevation=0.0, height=1.0, thickness=0.2)
        ifcopenshell.api.geometry.assign_representation(ifc_file, product=wall_ext, representation=rep_wall)

        # 2. External Window with Qto Area=10.0
        window = ifcopenshell.api.root.create_entity(ifc_file, ifc_class="IfcWindow", name="Window")
        ifcopenshell.api.spatial.assign_container(ifc_file, products=[window], relating_structure=storey)
        ifcopenshell.api.pset.add_pset(ifc_file, product=window, name="Pset_WindowCommon")
        ifcopenshell.api.pset.edit_pset(ifc_file, pset=ifc_file.by_type("IfcPropertySet")[-1], properties={"IsExternal": True})
        qto_win = ifcopenshell.api.pset.add_qto(ifc_file, product=window, name="Qto_WindowBaseQuantities")
        ifcopenshell.api.pset.edit_qto(ifc_file, qto=qto_win, properties={"Area": 10.0})
        # Add minimal geometry
        rep_win = ifcopenshell.api.geometry.create_2pt_wall(ifc_file, element=window, context=body_context, p1=(0,0), p2=(0.5,0), elevation=0.0, height=0.5, thickness=0.1)
        ifcopenshell.api.geometry.assign_representation(ifc_file, product=window, representation=rep_win)

        # 3. Internal Wall (should be ignored for facade)
        wall_int = ifcopenshell.api.root.create_entity(ifc_file, ifc_class="IfcWall", name="WallInt")
        ifcopenshell.api.spatial.assign_container(ifc_file, products=[wall_int], relating_structure=storey)
        ifcopenshell.api.pset.add_pset(ifc_file, product=wall_int, name="Pset_WallCommon")
        ifcopenshell.api.pset.edit_pset(ifc_file, pset=ifc_file.by_type("IfcPropertySet")[-1], properties={"IsExternal": False})
        qto_wall_int = ifcopenshell.api.pset.add_qto(ifc_file, product=wall_int, name="Qto_WallBaseQuantities")
        ifcopenshell.api.pset.edit_qto(ifc_file, qto=qto_wall_int, properties={"GrossSideArea": 100.0})
        # Add minimal geometry
        rep_int = ifcopenshell.api.geometry.create_2pt_wall(ifc_file, element=wall_int, context=body_context, p1=(0,1), p2=(1,1), elevation=0.0, height=1.0, thickness=0.2)
        ifcopenshell.api.geometry.assign_representation(ifc_file, product=wall_int, representation=rep_int)

        # 4. Standalone Curtain Wall with Qto
        curtain = ifcopenshell.api.root.create_entity(ifc_file, ifc_class="IfcCurtainWall", name="Curtain")
        ifcopenshell.api.spatial.assign_container(ifc_file, products=[curtain], relating_structure=storey)
        ifcopenshell.api.pset.add_pset(ifc_file, product=curtain, name="Pset_CurtainWallCommon")
        ifcopenshell.api.pset.edit_pset(ifc_file, pset=ifc_file.by_type("IfcPropertySet")[-1], properties={"IsExternal": True})
        qto_curtain = ifcopenshell.api.pset.add_qto(ifc_file, product=curtain, name="Qto_CurtainWallBaseQuantities")
        ifcopenshell.api.pset.edit_qto(ifc_file, qto=qto_curtain, properties={"GrossSideArea": 20.0})
        # Add minimal geometry
        rep_curtain = ifcopenshell.api.geometry.create_2pt_wall(ifc_file, element=curtain, context=body_context, p1=(1,0), p2=(2,0), elevation=0.0, height=1.0, thickness=0.2)
        ifcopenshell.api.geometry.assign_representation(ifc_file, product=curtain, representation=rep_curtain)

        ifc_file.write(path)

    def test_facade_area_calculation(self):
        user_config = {}
        with tempfile.TemporaryDirectory() as tmp_dir:
            ifc_path = os.path.join(tmp_dir, "facade.ifc")
            self._build_facade_ifc(ifc_path)
            # We need some dummy config because ifc_product_walk expects it
            materials, metrics = ifc_product_walk(user_id="test", user_config=user_config, ifc_file_path=ifc_path)

        # Expecting:
        # Gross Facade Area = 50.0 (WallExt) + 20.0 (Curtain) = 70.0
        # Opening Area = 10.0 (Window)
        # Opaque Area = 70.0 - 10.0 = 60.0
        # WWR = 10.0 / 70.0 = 0.142857...

        self.assertAlmostEqual(metrics.fassadenflaeche, 70.0)
        self.assertAlmostEqual(getattr(metrics, "fassaden_oeffnungsflaeche", 0.0), 10.0)
        self.assertAlmostEqual(getattr(metrics, "fassaden_opake_flaeche", 0.0), 60.0)
        self.assertAlmostEqual(getattr(metrics, "fenster_wand_verhaeltnis", 0.0), 10.0 / 70.0)

    def test_facade_geometry_fallback(self):
        ifc_file = ifcopenshell.api.project.create_file(version="IFC4")
        ifcopenshell.api.root.create_entity(
            ifc_file, ifc_class="IfcProject", name="TestProject"
        )
        length_unit = ifcopenshell.api.unit.add_si_unit(ifc_file, unit_type="LENGTHUNIT")
        area_unit = ifcopenshell.api.unit.add_si_unit(ifc_file, unit_type="AREAUNIT")
        volume_unit = ifcopenshell.api.unit.add_si_unit(ifc_file, unit_type="VOLUMEUNIT")
        ifcopenshell.api.unit.assign_unit(ifc_file, units=[length_unit, area_unit, volume_unit])
        model_context = ifcopenshell.api.context.add_context(ifc_file, context_type="Model")
        body_context = ifcopenshell.api.context.add_context(
            ifc_file, context_type="Model", context_identifier="Body",
            target_view="MODEL_VIEW", parent=model_context,
        )
        storey = ifcopenshell.api.root.create_entity(ifc_file, ifc_class="IfcBuildingStorey", name="Level1")
        
        # External wall: no Qto, 5 m long and 3 m high. Gross facade
        # means one exposed elevation face, not both wall faces or end caps.
        wall = ifcopenshell.api.root.create_entity(ifc_file, ifc_class="IfcWall", name="WallFallback")
        ifcopenshell.api.spatial.assign_container(ifc_file, products=[wall], relating_structure=storey)
        ifcopenshell.api.pset.add_pset(ifc_file, product=wall, name="Pset_WallCommon")
        ifcopenshell.api.pset.edit_pset(ifc_file, pset=ifc_file.by_type("IfcPropertySet")[-1], properties={"IsExternal": True})
        
        rep = ifcopenshell.api.geometry.create_2pt_wall(ifc_file, element=wall, context=body_context, p1=(0,0), p2=(5,0), elevation=0.0, height=3.0, thickness=0.2)
        ifcopenshell.api.geometry.assign_representation(ifc_file, product=wall, representation=rep)

        with tempfile.TemporaryDirectory() as tmp_dir:
            ifc_path = os.path.join(tmp_dir, "fallback.ifc")
            ifc_file.write(ifc_path)
            _, metrics = ifc_product_walk(user_id="test", user_config={}, ifc_file_path=ifc_path)

        self.assertAlmostEqual(metrics.fassadenflaeche, 15.0)

    def test_window_inherits_external_status_from_host_wall(self):
        ifc_file = ifcopenshell.api.project.create_file(version="IFC4")
        wall = ifcopenshell.api.root.create_entity(ifc_file, ifc_class="IfcWall")
        opening = ifcopenshell.api.root.create_entity(
            ifc_file, ifc_class="IfcOpeningElement"
        )
        window = ifcopenshell.api.root.create_entity(ifc_file, ifc_class="IfcWindow")

        wall_pset = ifcopenshell.api.pset.add_pset(
            ifc_file, product=wall, name="Pset_WallCommon"
        )
        ifcopenshell.api.pset.edit_pset(
            ifc_file, pset=wall_pset, properties={"IsExternal": True}
        )
        ifcopenshell.api.feature.add_feature(
            ifc_file, feature=opening, element=wall
        )
        ifcopenshell.api.feature.add_filling(
            ifc_file, opening=opening, element=window
        )

        self.assertTrue(is_external(window))
