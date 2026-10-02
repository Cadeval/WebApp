"""Multicore IFC conversion; retain only scalar measurements, never mesh buffers."""
import os
import ifcopenshell.geom
import ifcopenshell.util.shape


def geometry_threads(value=None):
    if value is None:
        from django.conf import settings
        value = getattr(settings, 'IFC_GEOMETRY_THREADS', 4) if settings.configured else 4
    value = int(value)
    if value < 1:
        raise ValueError('IFC geometry threads must be positive.')
    return min(value, os.cpu_count() or 1, 16)


def measure_shape(shape, element):
    result = {'volume': None, 'area': None, 'length': None, 'bounds': None}
    try:
        # The owning shape must remain alive while reading its C++ buffers.
        geometry = shape.geometry
        result['volume'] = ifcopenshell.util.shape.get_volume(geometry)
        result['area'] = (ifcopenshell.util.shape.get_side_area(geometry)
            if element.is_a('IfcWall') or element.is_a('IfcCurtainWall')
            else ifcopenshell.util.shape.get_footprint_area(geometry, axis='Z'))
        result['length'] = ifcopenshell.util.shape.get_max_xy(geometry)
        vertices = ifcopenshell.util.shape.get_shape_vertices(shape, geometry)
        if len(vertices):
            result['bounds'] = (vertices.min(axis=0).tolist(), vertices.max(axis=0).tolist())
    except (RuntimeError, ValueError, TypeError, AttributeError):
        pass
    return result


def geometry_measurements(model, elements, *, threads=None):
    """Use native worker threads and shared conversion caches, with serial fallback.

    Python entity access and result consumption stay on the calling thread.
    Return STEP-ID keyed scalars so native completion order cannot alter reports.
    """
    workers = geometry_threads(threads)
    elements = list(elements)
    if not elements:
        return {}, {'threads': workers, 'converted': 0, 'serial_fallbacks': 0}
    settings = ifcopenshell.geom.settings()
    settings.set(settings.USE_WORLD_COORDS, False)
    result = {}
    try:
        iterator = ifcopenshell.geom.iterator(settings, model, workers, include=elements)
        if iterator.initialize():
            while True:
                shape = iterator.get()
                element = model.by_id(shape.id)
                result[shape.id] = measure_shape(shape, element)
                # No shape or geometry buffers are stored across iterator.next().
                del shape
                if not iterator.next():
                    break
    except (RuntimeError, ValueError, TypeError, AttributeError):
        # Preserve measurements already collected if the iterator stops early.
        pass
    converted = len(result)
    fallbacks = 0
    for element in elements:
        if element.id() in result:
            continue
        fallbacks += 1
        try:
            shape = ifcopenshell.geom.create_shape(settings, element)
            result[element.id()] = measure_shape(shape, element)
        except (RuntimeError, ValueError, TypeError, AttributeError):
            continue
    return result, {'threads': workers, 'converted': converted, 'serial_fallbacks': fallbacks}
