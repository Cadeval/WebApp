import ifcopenshell.util.element
from .material_assessment import number

def coerce_float(value):
    return number(value) or 0.0

GERMAN_COMPONENT_VOLUME_KEY = "Schicht/Komponenten Volumen (brutto)"

def resolve_qto_volume(quantities: dict, geometry_volume: float | None = None) -> float:
    """
    Pure helper resolving an element's overall volume from its standard
    IFC quantity sets (as returned by
    ``ifcopenshell.util.element.get_psets(element, qtos_only=True)``).

    Prefers a finite, strictly positive ``GrossVolume`` over a finite,
    strictly positive ``NetVolume`` across all quantity sets. An invalid,
    missing, zero or negative quantity value never short-circuits the
    resolution; it is simply skipped in favour of the next candidate.

    When neither quantity yields a usable value, falls back to
    ``geometry_volume`` (typically the element's current computed
    geometry volume) when it is a finite, non-negative number, so a
    normal element with geometry but no usable Qto still gets a volume
    instead of silently becoming zero. Returns ``0.0`` when nothing
    usable is available at all.
    """
    quantities = quantities or {}
    for key in ("GrossVolume", "NetVolume"):
        for pset_values in quantities.values():
            value = (pset_values or {}).get(key)
            if value in (None, ""):
                continue
            coerced = coerce_float(value)
            if coerced > 0:
                return coerced

    if geometry_volume is not None:
        coerced_geometry = coerce_float(geometry_volume)
        if coerced_geometry >= 0:
            return coerced_geometry

    return 0.0


def resolve_component_volume(
    subdict: dict, fallback_volume: float | None = 0.0
) -> float | None:
    """
    Pure helper resolving the volume of a single material component/layer
    from its "Component Quantities" sub-dictionary.

    Prefers the German "Schicht/Komponenten Volumen (brutto)" quantity
    (either directly on the sub-dict, or nested under "properties" for
    complex quantities), falling back to ``fallback_volume`` (typically the
    element's standard Qto volume) when it is missing.
    """
    subdict = subdict or {}
    value = subdict.get(GERMAN_COMPONENT_VOLUME_KEY)
    if value in (None, ""):
        properties = subdict.get("properties") or {}
        value = properties.get(GERMAN_COMPONENT_VOLUME_KEY)
    if value in (None, ""):
        return fallback_volume
    return coerce_float(value)


def _material_component_name(material, fallback_name, index: int) -> str:
    """
    Pure helper deriving a display name for a single material component,
    preferring the material's own ``Name``, then a caller-supplied
    ``fallback_name`` (e.g. a layer/constituent/profile ``Name``), and
    finally a positional placeholder so a component is never dropped just
    because it is unnamed in the source IFC file.
    """
    name = getattr(material, "Name", None) if material is not None else None
    if not name:
        name = fallback_name
    if not name:
        name = f"Material {index + 1}"
    return name


def _accumulate_named_volumes(names, volumes) -> dict[str, float]:
    """
    Pure helper summing ``volumes`` into a dict keyed by ``names``,
    accumulating (rather than overwriting) entries that share the same
    name, e.g. two layers made of the same material.
    """
    result: dict[str, float] = {}
    for name, volume in zip(names, volumes):
        result[name] = result.get(name, 0.0) + volume
    return result


def resolve_material_component_volumes(
    material, total_volume: float
) -> dict[str, float]:
    """
    Pure helper allocating ``total_volume`` across the individual
    materials of an element's associated ``IfcMaterial`` (as returned by
    ``ifcopenshell.util.element.get_material(element, should_skip_usage=True)``),
    used as a fallback for elements that carry no German "Component
    Quantities" pset.

    - A single ``IfcMaterial`` receives the entire ``total_volume``.
    - ``IfcMaterialLayerSet`` allocates proportionally to each layer's
      ``LayerThickness`` when the layers carry usable (positive) thickness
      data, otherwise (and for all other set types) the volume is split
      evenly across the set's members.
    - Returns an empty dict when there is no associated material.
    """
    if material is None:
        return {}

    if material.is_a("IfcMaterialLayerSet"):
        layers = material.MaterialLayers or ()
        if not layers:
            return {}
        thicknesses = [
            coerce_float(getattr(layer, "LayerThickness", None)) for layer in layers
        ]
        total_thickness = sum(thicknesses)
        names = [
            _material_component_name(
                layer.Material, getattr(layer, "Name", None), index
            )
            for index, layer in enumerate(layers)
        ]
        if total_thickness > 0:
            volumes = [
                total_volume * (thickness / total_thickness)
                for thickness in thicknesses
            ]
        else:
            volumes = [total_volume / len(layers)] * len(layers)
        return _accumulate_named_volumes(names, volumes)

    if material.is_a("IfcMaterialProfileSet"):
        profiles = material.MaterialProfiles or ()
        if not profiles:
            return {}
        names = [
            _material_component_name(
                profile.Material, getattr(profile, "Name", None), index
            )
            for index, profile in enumerate(profiles)
        ]
        volumes = [total_volume / len(profiles)] * len(profiles)
        return _accumulate_named_volumes(names, volumes)

    if material.is_a("IfcMaterialConstituentSet"):
        constituents = material.MaterialConstituents or ()
        if not constituents:
            return {}
        names = [
            _material_component_name(
                constituent.Material, getattr(constituent, "Name", None), index
            )
            for index, constituent in enumerate(constituents)
        ]
        volumes = [total_volume / len(constituents)] * len(constituents)
        return _accumulate_named_volumes(names, volumes)

    if material.is_a("IfcMaterialList"):
        materials = material.Materials or ()
        if not materials:
            return {}
        names = [
            _material_component_name(single_material, None, index)
            for index, single_material in enumerate(materials)
        ]
        volumes = [total_volume / len(materials)] * len(materials)
        return _accumulate_named_volumes(names, volumes)

    # Plain single IfcMaterial (or an unexpected/unknown material type).
    name = _material_component_name(material, None, 0)
    return {name: total_volume}


def _distribute_volumes(
    component_data: list[tuple[str, float | None]], total_volume: float
) -> dict[str, float]:
    """
    Pure helper that preserves explicit non-None volumes and distributes
    any remaining volume (at least 0) evenly among components with None
    volume, accumulating duplicate names to conserve total volume.
    """
    explicit_names = []
    explicit_volumes = []
    missing_names = []

    for name, volume in component_data:
        if volume is None:
            missing_names.append(name)
        else:
            explicit_names.append(name)
            explicit_volumes.append(volume)

    if not missing_names:
        return _accumulate_named_volumes(explicit_names, explicit_volumes)

    sum_explicit = sum(explicit_volumes)
    remaining = max(total_volume - sum_explicit, 0.0)
    shared = remaining / len(missing_names)

    all_names = explicit_names + missing_names
    all_volumes = explicit_volumes + [shared] * len(missing_names)

    return _accumulate_named_volumes(all_names, all_volumes)


def resolve_element_components(element, qto_volume: float) -> dict[str, float]:
    """
    Resolves the named material components (and their volumes) of a
    single IFC element, preferring the German "Component Quantities"
    pset when present, and falling back to the element's associated
    ``IfcMaterial`` (single material, layer set, profile set,
    constituent set or material list) otherwise so a plain element that
    only carries a standard Qto volume still yields a material instead
    of being silently skipped.
    """
    pset_dict = ifcopenshell.util.element.get_pset(
        element=element, name="Component Quantities"
    )
    if pset_dict:
        data: list[tuple[str, float | None]] = []
        for ifc_name, subdict in pset_dict.items():
            # Filter out the "id" of the "Component Quantities" object as it contains an int,
            # and we are only interested in the quantities of the sub elements.
            if ifc_name != "id":
                data.append(
                    (
                        ifc_name,
                        resolve_component_volume(subdict=subdict, fallback_volume=None),
                    )
                )
        # An empty "Component Quantities" pset is still truthy (it always
        # carries an "id" key), so it must not be treated as authoritative
        # when it contains no actual components; fall through to the
        # material association below instead of suppressing it.
        if data:
            return _distribute_volumes(data, qto_volume)

    material = ifcopenshell.util.element.get_material(element, should_skip_usage=True)
    return resolve_material_component_volumes(
        material=material, total_volume=qto_volume
    )


