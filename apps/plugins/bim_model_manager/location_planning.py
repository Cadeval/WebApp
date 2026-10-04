"""Official Vienna planning context at a marker, never parcel compliance.

WFS district and generalized zoning candidates are filtered against their
actual polygons. WMS plan identification returns references, not parcel
geometry. Binding provisions require both the published plan and its text.
The injected transport permits only the two fixed official URLs below.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
import re
from urllib.parse import urlencode
import xml.etree.ElementTree as ET

from shapely.geometry import Point, shape


PROVIDER_VERSION = "vienna-ogd-v1"
WFS_URL = "https://data.wien.gv.at/daten/geo"
WMS_URL = "https://data.wien.gv.at/daten/wms"
ZONING_DATASET = "https://www.data.gv.at/katalog/dataset/stadt-wien_generalisierteflchenwidmungwien"
PLAN_DATASET = "https://www.data.gv.at/datasets/dc82da19-6878-46e8-a653-4966fa4dd108?locale=de"
PLANNING_HELP = "https://www.wien.gv.at/stadtplanung/flaechenwidmung-bebauungsplan-hilfe"
HEIGHT_SOURCE = "https://ris.bka.gv.at/eli/lgbl/WI/1930/11/P75/LWI40013122"
MEASUREMENT_SOURCE = "https://www.ris.bka.gv.at/eli/lgbl/WI/1930/11/P81/LWI40018199"
SETBACK_SOURCE = "https://ris.bka.gv.at/eli/lgbl/WI/1930/11/P79/LWI40018198"
VERIFIED_AS_OF = "2026-10-03"
MAX_FEATURES = 20
MAX_COORDINATES = 100_000
HEIGHT_RANGES = {1: (2.5, 9.0), 2: (2.5, 12.0), 3: (9.0, 16.0),
                 4: (12.0, 21.0), 5: (16.0, 26.0), 6: (21.0, None)}


class PlanningDataError(ValueError):
    """An upstream response cannot establish trustworthy location context."""


def _text(value, limit=512):
    return value[:limit].strip() if isinstance(value, str) else ""


def _date(value):
    value = _text(value, 40)
    for pattern in ("%d.%m.%Y", "%Y-%m-%d", "%Y-%m-%dZ"):
        try:
            return datetime.strptime(value, pattern).date().isoformat()
        except ValueError:
            continue
    return None


def _coordinate(value):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def _point_geometry(geometry):
    if not isinstance(geometry, dict) or geometry.get("type") not in {"Polygon", "MultiPolygon"}:
        raise PlanningDataError("The official layer returned a non-polygon geometry.")
    coordinates = geometry.get("coordinates")
    pending, count = [(coordinates, 0)], 0
    while pending:
        item, depth = pending.pop()
        if not isinstance(item, list) or depth > 5:
            raise PlanningDataError("The official polygon coordinates are malformed.")
        if item and not isinstance(item[0], list):
            if len(item) not in (2, 3) or not all(_coordinate(v) for v in item):
                raise PlanningDataError("The official polygon coordinates are invalid.")
            if not -180 <= item[0] <= 180 or not -90 <= item[1] <= 90:
                raise PlanningDataError("The official polygon uses an unexpected coordinate system.")
            count += 1
            if count > MAX_COORDINATES:
                raise PlanningDataError("The official polygon exceeds the processing limit.")
        else:
            pending.extend((child, depth + 1) for child in item)
    try:
        polygon = shape(geometry)
        if polygon.is_empty or not polygon.is_valid:
            raise PlanningDataError("The official polygon is empty or invalid.")
        return polygon
    except PlanningDataError:
        raise
    except Exception as error:
        raise PlanningDataError("The official polygon could not be read.") from error


def _matching_features(payload, point):
    if not isinstance(payload, dict) or payload.get("type") != "FeatureCollection":
        raise PlanningDataError("The official service returned an unexpected feature response.")
    crs = payload.get("crs")
    if crs is not None:
        name = crs.get("properties", {}).get("name") if isinstance(crs, dict) else None
        if name not in {"urn:ogc:def:crs:EPSG::4326", "EPSG:4326", "urn:ogc:def:crs:OGC:1.3:CRS84"}:
            raise PlanningDataError("The official service returned an unexpected coordinate system.")
    features = payload.get("features")
    if not isinstance(features, list) or len(features) >= MAX_FEATURES:
        raise PlanningDataError("The official service returned too many candidates for an unambiguous lookup.")
    for field in ("totalFeatures", "numberMatched"):
        count = payload.get(field)
        if isinstance(count, int) and not isinstance(count, bool) and count > len(features):
            raise PlanningDataError("The official candidate response is truncated.")
    matched = []
    for feature in features:
        if not isinstance(feature, dict) or not isinstance(feature.get("properties"), dict):
            raise PlanningDataError("The official feature properties are malformed.")
        polygon = _point_geometry(feature.get("geometry"))
        if polygon.covers(point):
            matched.append((feature, polygon.boundary.distance(point) <= 1e-8))
    return matched


def _wfs_params(layer, latitude, longitude):
    delta = 1e-7
    return {"service": "WFS", "version": "1.1.0", "request": "GetFeature",
            "typeName": "ogdwien:" + layer, "srsName": "EPSG:4326", "outputFormat": "json",
            "maxFeatures": str(MAX_FEATURES),
            "bbox": f"{longitude-delta:.8f},{latitude-delta:.8f},{longitude+delta:.8f},{latitude+delta:.8f},EPSG:4326"}


def _plan_params(latitude, longitude):
    delta = 1e-5
    return {"service": "WMS", "version": "1.1.1", "request": "GetFeatureInfo",
            "layers": "SCHNITTMUSTEROGD", "query_layers": "SCHNITTMUSTEROGD", "styles": "",
            "srs": "EPSG:4326",
            "bbox": f"{longitude-delta:.8f},{latitude-delta:.8f},{longitude+delta:.8f},{latitude+delta:.8f}",
            "width": "101", "height": "101", "x": "50", "y": "50", "format": "image/png",
            "info_format": "application/vnd.ogc.gml", "feature_count": str(MAX_FEATURES)}


def _plan_rows(payload):
    # WMS cannot supply JSON: the fixed transport returns bounded XML bytes.
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    if not isinstance(payload, bytes) or len(payload) > 64 * 1024:
        raise PlanningDataError("The official plan response is not bounded XML.")
    upper = payload.upper().replace(b"\x00", b"")
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise PlanningDataError("The official plan response contains an unsupported XML declaration.")
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as error:
        raise PlanningDataError("The official plan response could not be read.") from error
    if root.tag != "{http://www.esri.com/wms}FeatureInfoResponse":
        raise PlanningDataError("The official plan service returned an unexpected response.")
    fields = list(root.findall("{http://www.esri.com/wms}FIELDS"))
    if len(fields) >= MAX_FEATURES:
        raise PlanningDataError("The plan query returned too many document candidates.")
    plans, seen = [], set()
    for field in fields:
        number = _text(field.attrib.get("PLANNUMMER"), 20)
        if not re.fullmatch(r"[1-9][0-9]{0,9}", number):
            raise PlanningDataError("The official planning document identifier is unsupported.")
        if number in seen:
            continue
        seen.add(number)
        params = {"appTitle": "Flächenwidmungs- und Bebauungsplan", "planDpi": "0", "pnr": number}
        base = "https://www.wien.gv.at/BauplatzWebservice/public/GetPlanDok.aspx?"
        plans.append({"number": number, "type": _text(field.attrib.get("PLANTYP_TXT"), 80),
                      "type_code": _text(field.attrib.get("PLANTYP"), 20),
                      "published_on": _date(field.attrib.get("KUNDMACHUNG")),
                      "effective_on": None, "match": "map_identify",
                      "plan_url": base + urlencode({**params, "isPlan": "true"}),
                      "text_url": base + urlencode({**params, "isPlan": "false"})})
    return plans


def _building_class(properties):
    # Only verified residential/mixed-use codes get §75 context. Other zoning
    # categories and free-form detail descriptions cannot supply a class here.
    match = re.fullmatch(r"(?:W|GB)([1-6])", _text(properties.get("WIDMUNG"), 20))
    if not match:
        return None
    value = int(match[1])
    label = _text(properties.get("WIDMUNG_TXT"))
    if not re.search(rf"\bBauklasse\s+{value}\b", label):
        return None
    return value


def _source(layer, url, params, retrieved_at, payload=None):
    result = {"provider": "Stadt Wien", "layer": layer, "url": url + "?" + urlencode(params),
              "retrieved_at": retrieved_at, "effective_on": None,
              "license": "CC BY 4.0", "attribution": "Datenquelle: Stadt Wien – https://data.wien.gv.at"}
    if isinstance(payload, dict):
        result["response_timestamp"] = _text(payload.get("timeStamp"), 80) or None
    return result


def planning_lookup(fetch_json, *, latitude, longitude, now):
    """Look up context using fixed URLs and an injected bounded transport.

    ``fetch_json(url, params)`` returns a GeoJSON dict for /daten/geo, or XML
    bytes for /daten/wms. Failures are nonfatal; no result asserts permission,
    compliance, parcel boundaries, or the IFC's surveyed position.
    """
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Planning provenance requires an aware retrieval time.")
    retrieved_at = now.astimezone(timezone.utc).isoformat()
    result = {"schema": 1, "provider_version": PROVIDER_VERSION, "status": "unavailable",
              "jurisdiction": {"known": False, "country": None, "state": None, "districts": []},
              "zoning": [], "plans": [], "height_context": [], "sources": [],
              "missing_information": ["Parcel-specific building height", "Setbacks and building lines",
                  "Coverage limits", "Special plan provisions and building permissions"],
              "message": "Planning context is unavailable for this marker.",
              "notice": "Generalized zoning and map-identify plan references are information only. "
                  "The plan, its text and the Bauordnung govern site-specific limits. "
                  "A marker does not establish the building's parcel or permission to build.",
              "reference_links": [{"label": "Official planning help", "url": PLANNING_HELP},
                  {"label": "Height classes (§75)", "url": HEIGHT_SOURCE},
                  {"label": "Height measurement (§81)", "url": MEASUREMENT_SOURCE},
                  {"label": "Setbacks (§79)", "url": SETBACK_SOURCE}]}
    if not (_coordinate(latitude) and _coordinate(longitude) and -90 <= latitude <= 90 and -180 <= longitude <= 180):
        result["message"] = "Choose a finite WGS84 location to retrieve planning context."
        return result
    point = Point(longitude, latitude)
    params = _wfs_params("BEZIRKSGRENZEOGD", latitude, longitude)
    try:
        payload = fetch_json(WFS_URL, params)
        districts = _matching_features(payload, point)
        result["sources"].append(_source("BEZIRKSGRENZEOGD", WFS_URL, params, retrieved_at, payload))
        if not districts:
            result.update(status="unsupported", message="This point is outside the verified Vienna districts. "
                "A provider for its local planning authority is required.")
            return result
        for feature, boundary in districts:
            props = feature["properties"]
            number = props.get("BEZNR")
            if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= 23:
                raise PlanningDataError("The official district identifier is invalid.")
            result["jurisdiction"]["districts"].append({"number": number, "name": _text(props.get("NAMEK"), 100),
                "feature_id": _text(feature.get("id"), 100), "updated_at": _date(props.get("AKT_TIMESTAMP")),
                "boundary": boundary})
        result["jurisdiction"].update(known=True, country="AT", state="Vienna")
    except Exception:
        result["jurisdiction"]["districts"] = []
        result["message"] = "The official district lookup could not verify this point. Try again later."
        return result

    issues = []
    ambiguous = len(districts) > 1 or any(boundary for _, boundary in districts)
    params = _wfs_params("GENFLWIDMUNGOGD", latitude, longitude)
    try:
        payload = fetch_json(WFS_URL, params)
        zoning = _matching_features(payload, point)
        result["sources"].append(_source("GENFLWIDMUNGOGD", WFS_URL, params, retrieved_at, payload))
        ambiguous |= len(zoning) > 1 or any(boundary for _, boundary in zoning)
        for feature, boundary in zoning:
            props = feature["properties"]
            klass = _building_class(props)
            result["zoning"].append({"code": _text(props.get("WIDMUNG"), 80),
                "label": _text(props.get("WIDMUNG_TXT")), "class_code": _text(props.get("WIDMUNGSKLASSE"), 80),
                "class_label": _text(props.get("WIDMUNGSKLASSE_TXT")), "building_class": klass,
                "detail": _text(props.get("WIDMUNG_DETAIL")), "expires_on": _date(props.get("BEFRISTUNG_DATUM")),
                "expiry_plan": _text(props.get("BEFRISTUNG_PD"), 80),
                "feature_id": _text(feature.get("id"), 100), "boundary": boundary})
        if not zoning:
            issues.append("No generalized zoning polygon contains this marker.")
    except Exception:
        issues.append("The generalized zoning lookup is unavailable.")

    params = _plan_params(latitude, longitude)
    try:
        result["plans"] = _plan_rows(fetch_json(WMS_URL, params))
        result["sources"].append(_source("SCHNITTMUSTEROGD", WMS_URL, params, retrieved_at))
        if not result["plans"]:
            issues.append("No planning document was returned for this marker.")
    except Exception:
        issues.append("The planning document lookup is unavailable.")

    classes = sorted({row["building_class"] for row in result["zoning"] if row["building_class"]})
    for klass in classes:
        minimum, maximum = HEIGHT_RANGES[klass]
        result["height_context"].append({"building_class": klass, "min_m": minimum, "max_m": maximum,
            "basis": "statutory_class_context", "site_specific": False, "source_url": HEIGHT_SOURCE,
            "effective_from": "2018-12-22", "verified_as_of": VERIFIED_AS_OF,
            "qualification": "Basic §75 class range only. The plan, §75(4–6) and §81 may change the "
                "permitted height or its measurement. Class VI requires plan-specific height bounds."})
    result["status"] = "ambiguous" if ambiguous else "partial" if issues else "available"
    result["message"] = ("The marker lies on, or matches more than one, planning boundary. Review all candidates. "
        if ambiguous else "Official Vienna planning context was retrieved for this marker. ") + " ".join(issues)
    result["message"] = result["message"].strip()
    return result
