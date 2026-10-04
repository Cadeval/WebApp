"""Exact jurisdiction, official provenance and nonfatal planning failures."""
import copy
from datetime import datetime, timezone
import json
import unittest
from unittest.mock import Mock

from plugins.bim_model_manager.location_planning import HEIGHT_SOURCE, MAX_FEATURES, PlanningDataError, WFS_URL, WMS_URL, _matching_features, _plan_rows, planning_lookup
from shapely.geometry import Point


NOW = datetime(2026, 10, 3, 13, 0, tzinfo=timezone.utc)


def rectangle(west=16, south=48, east=17, north=49):
    return {"type": "Polygon", "coordinates": [[[west, south], [east, south],
        [east, north], [west, north], [west, south]]]}


def collection(properties, geometry=None, identifier="official.1"):
    return {"type": "FeatureCollection", "totalFeatures": 1,
        "timeStamp": "2026-10-03T12:45:16Z",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::4326"}},
        "features": [{"type": "Feature", "id": identifier,
            "geometry": geometry or rectangle(), "properties": properties}]}


def district(geometry=None):
    return collection({"BEZNR": 10, "NAMEK": "Favoriten", "AKT_TIMESTAMP": "2026-09-01Z"}, geometry)


def zoning(code="W3", geometry=None):
    return collection({"WIDMUNG": code, "WIDMUNG_TXT": "Wohngebiet Bauklasse " + code[-1],
        "WIDMUNGSKLASSE": "WO", "WIDMUNGSKLASSE_TXT": "Wohngebiet",
        "BEZIRK": "1, 9", "BEFRISTUNG_DATUM": "2027-12-31", "BEFRISTUNG_PD": "8091"}, geometry)


PLANS = b'''<?xml version="1.0" encoding="UTF-8"?>
<FeatureInfoResponse xmlns="http://www.esri.com/wms">
<FIELDS PLANNUMMER="8091" PLANTYP="10" PLANTYP_TXT="Festsetzung" KUNDMACHUNG="22.01.2015" />
</FeatureInfoResponse>'''


class PlanningLookupTests(unittest.TestCase):
    def transport(self, districts=None, zones=None, plans=PLANS):
        def fetch(url, params):
            if url == WMS_URL:
                return plans
            self.assertEqual(url, WFS_URL)
            return districts if params["typeName"].endswith("BEZIRKSGRENZEOGD") else zones
        return Mock(side_effect=fetch)

    def lookup(self, fetch, **kwargs):
        return planning_lookup(fetch, latitude=kwargs.pop("latitude", 48.5),
            longitude=kwargs.pop("longitude", 16.5), now=NOW, **kwargs)

    def test_verified_jurisdiction_and_class_context_are_not_parcel_limits(self):
        fetch = self.transport(district(), zoning())
        result = self.lookup(fetch)
        self.assertEqual(result["status"], "available")
        self.assertTrue(result["jurisdiction"]["known"])
        self.assertEqual(result["jurisdiction"]["country"], "AT")
        self.assertEqual([d["number"] for d in result["jurisdiction"]["districts"]], [10])
        self.assertEqual(result["jurisdiction"]["districts"][0]["updated_at"], "2026-09-01")
        context = result["height_context"][0]
        self.assertEqual((context["min_m"], context["max_m"]), (9, 16))
        self.assertFalse(context["site_specific"])
        self.assertEqual(context["source_url"], HEIGHT_SOURCE)
        self.assertEqual(context["effective_from"], "2018-12-22")
        self.assertEqual(result["zoning"][0]["expires_on"], "2027-12-31")
        plan = result["plans"][0]
        self.assertEqual(plan["published_on"], "2015-01-22")
        self.assertIsNone(plan["effective_on"])
        self.assertEqual(plan["match"], "map_identify")
        self.assertIn("pnr=8091", plan["plan_url"])
        self.assertIn("isPlan=false", plan["text_url"])
        self.assertIn("Setbacks and building lines", result["missing_information"])
        self.assertEqual(len(result["sources"]), 3)
        self.assertTrue(all(source["retrieved_at"] == NOW.isoformat() for source in result["sources"]))

    def test_bbox_candidate_cannot_establish_jurisdiction_in_polygon_hole(self):
        geometry = rectangle()
        geometry["coordinates"].append([[16.4,48.4],[16.4,48.6],[16.6,48.6],[16.6,48.4],[16.4,48.4]])
        fetch = self.transport(district(geometry), zoning())
        result = self.lookup(fetch)
        self.assertEqual(result["status"], "unsupported")
        self.assertFalse(result["jurisdiction"]["known"])
        self.assertIsNone(result["jurisdiction"]["country"])
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(result["zoning"], [])

    def test_polygon_hole_excludes_zoning_without_losing_verified_district(self):
        geometry = rectangle(); geometry["coordinates"].append([
            [16.4,48.4],[16.4,48.6],[16.6,48.6],[16.6,48.4],[16.4,48.4]])
        result = self.lookup(self.transport(district(), zoning(geometry=geometry)))
        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["jurisdiction"]["known"])
        self.assertEqual(result["height_context"], [])

    def test_boundary_candidates_are_all_reported_without_selecting_one(self):
        zones = zoning(geometry=rectangle(east=16.5))
        second = copy.deepcopy(zoning("GB5", geometry=rectangle(west=16.5))["features"][0])
        second["id"] = "official.2"; second["properties"]["WIDMUNG_TXT"] = "Gemischtes Baugebiet Bauklasse 5"
        zones["features"].append(second); zones["totalFeatures"] = 2
        result = self.lookup(self.transport(district(), zones))
        self.assertEqual(result["status"], "ambiguous")
        self.assertEqual([row["building_class"] for row in result["zoning"]], [3, 5])
        self.assertTrue(all(row["boundary"] for row in result["zoning"]))
        self.assertEqual(len(result["height_context"]), 2)

    def test_bad_coords_and_unsupported_points_make_no_zoning_requests(self):
        fetch = Mock()
        for latitude in [float("nan"), float("inf"), 91, True, 10**1000]:
            self.assertEqual(self.lookup(fetch, latitude=latitude)["status"], "unavailable")
        fetch.assert_not_called()
        empty = {"type": "FeatureCollection", "features": [], "totalFeatures": 0}
        fetch = self.transport(empty, zoning())
        self.assertEqual(self.lookup(fetch)["status"], "unsupported")
        self.assertEqual(fetch.call_count, 1)

    def test_network_errors_and_bad_schema_are_nonfatal_and_fail_closed(self):
        for payload in [None, {"type": "Exception"}, district({"type":"Point","coordinates":[16.5,48.5]})]:
            result = self.lookup(self.transport(payload, zoning()))
            self.assertEqual(result["status"], "unavailable")
            self.assertFalse(result["jurisdiction"]["known"])
        self.assertEqual(self.lookup(Mock(side_effect=TimeoutError()))["status"], "unavailable")
        result = self.lookup(self.transport(district(), None))
        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["jurisdiction"]["known"])
        self.assertEqual(result["height_context"], [])

    def test_no_height_for_unverified_code_or_inconsistent_label(self):
        for code in ["GE3", "WIII", "GB5 26m", "W0", "W7"]:
            result = self.lookup(self.transport(district(), zoning(code)))
            self.assertEqual(result["height_context"], [])
        zones = zoning("W3"); zones["features"][0]["properties"]["WIDMUNG_TXT"] = "Wohngebiet Bauklasse 4"
        self.assertEqual(self.lookup(self.transport(district(), zones))["height_context"], [])
        result = self.lookup(self.transport(district(), zoning("W6")))
        self.assertIsNone(result["height_context"][0]["max_m"])

    def test_malformed_or_truncated_geometry_does_not_claim_known_region(self):
        for mutate in [lambda d: d.update(crs={"properties":{"name":"EPSG:3857"}}),
                       lambda d: d.update(totalFeatures=2),
                       lambda d: d["features"][0]["geometry"].update(coordinates=[[[float("nan"),48]]]),
                       lambda d: d["features"][0]["properties"].update(BEZNR=True),
                       lambda d: d.update(features=d["features"] * MAX_FEATURES)]:
            data = district(); mutate(data)
            self.assertFalse(self.lookup(self.transport(data, zoning()))["jurisdiction"]["known"])

    def test_plan_xml_rejects_entities_and_untrusted_identifiers(self):
        for raw in [b'<!DOCTYPE x [<!ENTITY x "oops">]><x/>',
                    '<!doctype x><x/>'.encode("utf-16"), b'<exception/>',
                    PLANS.replace(b'8091', b'https://evil.invalid/x'), b'x' * 65537]:
            with self.assertRaises(PlanningDataError):
                _plan_rows(raw)
            result = self.lookup(self.transport(district(), zoning(), plans=raw))
            self.assertEqual(result["status"], "partial")
            self.assertEqual(result["plans"], [])
        self.assertEqual(_plan_rows(PLANS + b'') [0]["number"], "8091")

    def test_multipolygon_matches_actual_component_not_overall_bounds(self):
        geometry = {"type":"MultiPolygon", "coordinates":[rectangle(east=16.3)["coordinates"], rectangle(west=16.7)["coordinates"]]}
        self.assertEqual(_matching_features(district(geometry), Point(16.5,48.5)), [])
        self.assertEqual(len(_matching_features(district(geometry), Point(16.2,48.5))), 1)

    def test_output_properties_are_bounded_and_original_payload_is_unchanged(self):
        zones = zoning(); zones["features"][0]["properties"]["WIDMUNG_DETAIL"] = "x" * 20_000
        original = copy.deepcopy(zones)
        result = self.lookup(self.transport(district(), zones))
        self.assertEqual(len(result["zoning"][0]["detail"]), 512)
        self.assertEqual(zones, original)
        self.assertLess(len(json.dumps(result)), 12_000)

    def test_naive_provenance_time_is_a_caller_error(self):
        with self.assertRaisesRegex(ValueError, "aware"):
            planning_lookup(Mock(), latitude=48.5, longitude=16.5, now=datetime(2026,10,3))
