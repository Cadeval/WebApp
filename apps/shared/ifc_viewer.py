"""Read-only IFC tessellation for visual inspection, independent of assessment."""
import os
import json
import re
import struct
import tempfile
from pathlib import Path

import ifcopenshell
import ifcopenshell.geom

from .ifc_extractor.material_assessment import file_hash
from .ifc_extractor.element_identity import element_reference, label_references


def model_glb(source, cache_directory):
    source = Path(source)
    cache = Path(cache_directory)
    cache.mkdir(parents=True, exist_ok=True)
    destination = cache / ('identity-v2-' + file_hash(source) + '.glb')
    if destination.exists():
        return destination
    model = ifcopenshell.open(str(source))
    settings = ifcopenshell.geom.settings()
    settings.set(settings.USE_WORLD_COORDS, True)
    settings.set(settings.WELD_VERTICES, True)
    serializer_settings = ifcopenshell.geom.serializer_settings()
    with tempfile.NamedTemporaryFile(suffix='.glb', dir=cache, delete=False) as temporary:
        temporary_path = temporary.name
    try:
        serializer = ifcopenshell.geom.serializers.gltf(temporary_path, settings, serializer_settings)
        serializer.setFile(model)
        serializer.writeHeader()
        iterator = ifcopenshell.geom.iterator(settings, model, min(os.cpu_count() or 1, 4))
        count = 0
        if iterator.initialize():
            while True:
                serializer.write(iterator.get())
                count += 1
                if not iterator.next():
                    break
        serializer.finalize()
        # The native serializer closes/flushed its file when released.
        del serializer
        if not count:
            raise ValueError('The IFC contains no renderable geometry.')
        attach_identity(temporary_path, model)
        os.replace(temporary_path, destination)
        return destination
    finally:
        Path(temporary_path).unlink(missing_ok=True)


def attach_identity(path, model):
    """Retain IFC identity on tessellated nodes without changing binary geometry."""
    with open(path, 'rb') as source:
        header = source.read(20)
        json_length = struct.unpack_from('<I', header, 12)[0]
        document = json.loads(source.read(json_length))
        binary_chunks = source.read()
    references={e['element_id']:e for e in label_references([element_reference(e) for e in model.by_type('IfcProduct')])}
    for node in document.get('nodes', []):
        match = re.search(r'product-([0-9a-fA-F-]{36})', node.get('name', ''))
        if not match:
            continue
        try:
            identifier = ifcopenshell.guid.compress(match.group(1).replace('-', ''))
            element = model.by_guid(identifier)
        except (RuntimeError, ValueError):
            continue
        node['extras'] = {**node.get('extras', {}), 'GlobalId': element.GlobalId,
                          'ifcType': element.is_a(), 'Name': references[element.GlobalId]['display_name'], 'step_id':element.id()}
    encoded = json.dumps(document, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    encoded += b' ' * (-len(encoded) % 4)
    total = 20 + len(encoded) + len(binary_chunks)
    with open(path, 'wb') as output:
        output.write(struct.pack('<4sII', b'glTF', 2, total))
        output.write(struct.pack('<I4s', len(encoded), b'JSON'))
        output.write(encoded)
        output.write(binary_chunks)
