export const MAX_WASM_BYTES = 2 * 1024 * 1024;
export const MAX_IFC_BYTES = 32 * 1024 * 1024;
export const MAX_STRING_BYTES = 1 * 1024 * 1024;
export const MAX_LIST_LIMIT = 100;
export const MAX_FILENAME_LENGTH = 255;
export const MAX_REQUEST_ID_LENGTH = 128;

// Numeric ErrorCode values exported by plugins/example_plugin/src/lib.rs.
// ifc_last_error does not write text into the module's shared output buffer.
const IFC_ERROR_MESSAGES = Object.freeze({
    2: 'The entity or attribute index is out of range.',
    3: 'No entity with that STEP id was found.',
    4: 'That attribute is read-only.',
    5: 'The IFC data contains an invalid editable STEP value.',
    6: 'The IFC data exceeds the 32 MiB size limit.',
    7: 'The IFC file exceeds the 100,000 entity limit.',
    8: 'The IFC data exceeds the allowed nesting depth of 64.',
    9: 'The IFC file is missing required STEP section markers.',
    10: 'The IFC file contains an unterminated string.',
    11: 'The IFC file contains an unterminated comment.',
    12: 'The IFC file contains unbalanced parentheses.',
    13: 'The IFC file contains duplicate STEP ids.',
    14: 'The IFC file contains an entity without a STEP id.',
    15: 'The IFC file contains a malformed STEP id.',
    16: 'The IFC file contains a STEP id outside the unsigned 32-bit range.',
    17: 'The IFC file contains a truncated STEP statement.',
});

export const REQUIRED_EXPORTS = [
    'memory',
    'ifc_input_reserve',
    'ifc_load',
    'ifc_entity_count',
    'ifc_entity_id',
    'ifc_entity_type',
    'ifc_entity_attribute_count',
    'ifc_entity_attribute_kind',
    'ifc_entity_attribute',
    'ifc_find_entity',
    'ifc_update_attribute',
    'ifc_serialize',
    'ifc_output_ptr',
    'ifc_last_error',
];

function hasExactKeys(value, keys) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
    const actual = Object.keys(value).sort();
    const expected = [...keys].sort();
    return actual.length === expected.length
        && actual.every((key, index) => key === expected[index]);
}

function isBoundedInteger(value, minimum, maximum) {
    return Number.isInteger(value) && value >= minimum && value <= maximum;
}

function isBoundedString(value, minLength, maxLength) {
    return typeof value === 'string' && value.length >= minLength && value.length <= maxLength;
}

function isRequestId(value) {
    return isBoundedString(value, 1, MAX_REQUEST_ID_LENGTH);
}

export function assertHostMessage(message) {
    switch (message?.type) {
        case 'initialize':
            if (!hasExactKeys(message, ['type', 'wasmUrl'])
                || !isBoundedString(message.wasmUrl, 1, 2048)) {
                throw new Error('Invalid initialize message.');
            }
            break;
        case 'load':
            if (!hasExactKeys(message, ['type', 'bytes', 'filename', 'requestId'])
                || !(message.bytes instanceof ArrayBuffer)
                || message.bytes.byteLength > MAX_IFC_BYTES
                || !isBoundedString(message.filename, 1, MAX_FILENAME_LENGTH)
                || !isRequestId(message.requestId)) {
                throw new Error('Invalid load message.');
            }
            break;
        case 'list':
            if (!hasExactKeys(message, ['type', 'offset', 'limit', 'requestId'])
                || !isBoundedInteger(message.offset, 0, Number.MAX_SAFE_INTEGER)
                || !isBoundedInteger(message.limit, 1, MAX_LIST_LIMIT)
                || !isRequestId(message.requestId)) {
                throw new Error('Invalid list message.');
            }
            break;
        case 'inspect':
            if (!hasExactKeys(message, ['type', 'entityIndex', 'requestId'])
                || !isBoundedInteger(message.entityIndex, 0, Number.MAX_SAFE_INTEGER)
                || !isRequestId(message.requestId)) {
                throw new Error('Invalid inspect message.');
            }
            break;
        case 'update':
            if (!hasExactKeys(message, ['type', 'entityId', 'attributeIndex', 'value', 'requestId'])
                || !isBoundedInteger(message.entityId, 0, Number.MAX_SAFE_INTEGER)
                || !isBoundedInteger(message.attributeIndex, 0, Number.MAX_SAFE_INTEGER)
                || typeof message.value !== 'string'
                || message.value.length > MAX_STRING_BYTES
                || !isRequestId(message.requestId)) {
                throw new Error('Invalid update message.');
            }
            break;
        case 'export':
            if (!hasExactKeys(message, ['type', 'requestId']) || !isRequestId(message.requestId)) {
                throw new Error('Invalid export message.');
            }
            break;
        default:
            throw new Error('Unsupported IFC editor worker message.');
    }
}

/**
 * Wraps an already-instantiated IFC WebAssembly ABI (see the Rust ABI contract) and exposes
 * host-friendly, bounds-checked operations. Kept separate from message plumbing so it can be
 * exercised directly against a fake ABI in tests without going through fetch/WebAssembly.
 */
export class IfcEditorEngine {
    constructor() {
        this.exports = null;
        this.memory = null;
        this.loaded = false;
        this.filename = '';
        this.entityCount = 0;
    }

    attach(exportsObject) {
        this.exports = exportsObject;
        this.memory = exportsObject.memory;
        this.loaded = false;
        this.filename = '';
        this.entityCount = 0;
    }

    readOutputBytes(length, maxLength = MAX_STRING_BYTES) {
        if (!isBoundedInteger(length, 0, maxLength)) {
            throw new Error('The IFC module returned an invalid output length.');
        }
        const ptr = this.exports.ifc_output_ptr();
        if (!Number.isInteger(ptr) || ptr < 0) {
            throw new Error('The IFC module returned an invalid output pointer.');
        }
        const buffer = this.memory.buffer;
        if (ptr + length > buffer.byteLength) {
            throw new Error('The IFC module output exceeds its own memory.');
        }
        return new Uint8Array(buffer, ptr, length).slice();
    }

    readOutputString(length, maxLength = MAX_STRING_BYTES) {
        const bytes = this.readOutputBytes(length, maxLength);
        return new TextDecoder('utf-8', { fatal: true }).decode(bytes);
    }

    readLastError() {
        try {
            const code = this.exports.ifc_last_error();
            return isBoundedInteger(code, 2, 17) ? IFC_ERROR_MESSAGES[code]
                : 'The IFC module reported an error.';
        } catch {
            return 'The IFC module reported an error.';
        }
    }

    fail(action) {
        throw new Error(`${action} failed: ${this.readLastError()}`);
    }

    requireLoaded() {
        if (!this.loaded) throw new Error('No IFC file is loaded.');
    }

    writeInput(bytes) {
        const ptr = this.exports.ifc_input_reserve(bytes.byteLength);
        if (!Number.isInteger(ptr) || ptr < 0) this.fail('Reserving the input buffer');
        const buffer = this.memory.buffer;
        if (ptr + bytes.byteLength > buffer.byteLength) {
            throw new Error('The IFC module input buffer is too small.');
        }
        new Uint8Array(buffer, ptr, bytes.byteLength).set(bytes);
        return ptr;
    }

    load(bytes, filename) {
        if (bytes.byteLength > MAX_IFC_BYTES) throw new Error('The IFC file exceeds the size limit.');
        this.writeInput(new Uint8Array(bytes));
        const result = this.exports.ifc_load(bytes.byteLength);
        if (!Number.isInteger(result) || result < 0) this.fail('Loading the IFC file');

        const entityCount = this.exports.ifc_entity_count();
        if (!isBoundedInteger(entityCount, 0, Number.MAX_SAFE_INTEGER)) {
            this.fail('Reading the entity count');
        }
        this.loaded = true;
        this.filename = filename;
        this.entityCount = entityCount;
        return { filename, entityCount };
    }

    readEntitySummary(index) {
        const id = this.exports.ifc_entity_id(index);
        if (!isBoundedInteger(id, 0, Number.MAX_SAFE_INTEGER)) this.fail('Reading the entity id');
        const typeLength = this.exports.ifc_entity_type(index);
        if (!Number.isInteger(typeLength) || typeLength < 0) this.fail('Reading the entity type');
        const entityType = this.readOutputString(typeLength);
        return { index, id, entityType };
    }

    list(offset, limit) {
        this.requireLoaded();
        const total = this.entityCount;
        const entities = [];
        const end = Math.min(offset + limit, total);
        for (let index = offset; index < end; index += 1) {
            entities.push(this.readEntitySummary(index));
        }
        return { offset, total, entities };
    }

    inspect(entityIndex) {
        this.requireLoaded();
        if (entityIndex >= this.entityCount) throw new Error('The entity index is out of range.');
        const summary = this.readEntitySummary(entityIndex);
        const attributeCount = this.exports.ifc_entity_attribute_count(entityIndex);
        if (!isBoundedInteger(attributeCount, 0, Number.MAX_SAFE_INTEGER)) {
            this.fail('Reading the attribute count');
        }
        const attributes = [];
        for (let attribute = 0; attribute < attributeCount; attribute += 1) {
            attributes.push(this.readAttribute(entityIndex, attribute));
        }
        return { ...summary, attributes };
    }

    readAttribute(entityIndex, attributeIndex) {
        const kind = this.exports.ifc_entity_attribute_kind(entityIndex, attributeIndex);
        if (!isBoundedInteger(kind, 0, 5)) this.fail('Reading the attribute kind');
        const valueLength = this.exports.ifc_entity_attribute(entityIndex, attributeIndex);
        if (!Number.isInteger(valueLength) || valueLength < 0) this.fail('Reading the attribute value');
        const value = this.readOutputString(valueLength);
        return { index: attributeIndex, kind, value };
    }

    update(entityId, attributeIndex, value) {
        this.requireLoaded();
        const entityIndex = this.exports.ifc_find_entity(entityId);
        if (!Number.isInteger(entityIndex) || entityIndex < 0) {
            throw new Error('No entity with that STEP id was found.');
        }
        const attributeCount = this.exports.ifc_entity_attribute_count(entityIndex);
        if (!isBoundedInteger(attributeCount, 0, Number.MAX_SAFE_INTEGER)) {
            this.fail('Reading the attribute count');
        }
        if (attributeIndex >= attributeCount) throw new Error('The attribute index is out of range.');
        const currentKind = this.exports.ifc_entity_attribute_kind(entityIndex, attributeIndex);
        if (!isBoundedInteger(currentKind, 0, 5)) this.fail('Reading the attribute kind');
        if (currentKind === 0) throw new Error('That attribute is read-only.');

        const encoded = new TextEncoder().encode(value);
        if (encoded.byteLength > MAX_STRING_BYTES) throw new Error('The attribute value exceeds the size limit.');
        this.writeInput(encoded);
        const result = this.exports.ifc_update_attribute(entityIndex, attributeIndex, encoded.byteLength);
        if (!Number.isInteger(result) || result < 0) this.fail('Updating the attribute');

        const stored = this.readAttribute(entityIndex, attributeIndex);
        return { entityId, attributeIndex, kind: stored.kind, value: stored.value };
    }

    exportStep() {
        this.requireLoaded();
        const length = this.exports.ifc_serialize();
        if (!Number.isInteger(length) || length < 0) this.fail('Serializing the IFC file');
        if (length > MAX_IFC_BYTES) throw new Error('The serialized IFC file exceeds the size limit.');
        const bytes = this.readOutputBytes(length, MAX_IFC_BYTES);
        return { filename: this.filename, bytes };
    }
}

function resolveSameOrigin(url) {
    if (typeof globalThis.location === 'undefined') return url;
    const resolved = new URL(url, globalThis.location.href);
    if (resolved.origin !== globalThis.location.origin) {
        throw new Error('IFC editor assets must use the application origin.');
    }
    return url;
}

function hasWorkingMemory(candidate) {
    return Boolean(candidate) && candidate.buffer instanceof ArrayBuffer;
}

/**
 * Orchestrates the message protocol: fetching/validating/instantiating the sandboxed
 * WebAssembly module, and delegating parsed requests to an `IfcEditorEngine`. Dependencies are
 * injectable so the full protocol can be exercised deterministically against a fake ABI in tests.
 */
export class IfcEditorWorker {
    constructor({
        postMessage = () => {},
        fetchFn = globalThis.fetch ? globalThis.fetch.bind(globalThis) : undefined,
        webAssembly = globalThis.WebAssembly,
        engine = new IfcEditorEngine(),
    } = {}) {
        this.postMessage = postMessage;
        this.fetchFn = fetchFn;
        this.webAssembly = webAssembly;
        this.engine = engine;
        this.initialized = false;
    }

    async handleMessage(message) {
        try {
            assertHostMessage(message);
            switch (message.type) {
                case 'initialize':
                    await this.initialize(message.wasmUrl);
                    this.postMessage({ type: 'ready' });
                    return;
                case 'load': {
                    const result = this.engine.load(message.bytes, message.filename);
                    this.postMessage({
                        type: 'loaded',
                        requestId: message.requestId,
                        filename: result.filename,
                        entityCount: result.entityCount,
                    });
                    return;
                }
                case 'list': {
                    const result = this.engine.list(message.offset, message.limit);
                    this.postMessage({ type: 'entities', requestId: message.requestId, ...result });
                    return;
                }
                case 'inspect': {
                    const result = this.engine.inspect(message.entityIndex);
                    this.postMessage({ type: 'entity', requestId: message.requestId, ...result });
                    return;
                }
                case 'update': {
                    const result = this.engine.update(message.entityId, message.attributeIndex, message.value);
                    this.postMessage({ type: 'updated', requestId: message.requestId, ...result });
                    return;
                }
                case 'export': {
                    const result = this.engine.exportStep();
                    const buffer = result.bytes.buffer;
                    this.postMessage(
                        {
                            type: 'exported',
                            requestId: message.requestId,
                            filename: result.filename,
                            bytes: buffer,
                        },
                        [buffer],
                    );
                    return;
                }
                default:
                    return;
            }
        } catch (error) {
            this.postMessage({
                type: 'error',
                requestId: message && typeof message === 'object' ? message.requestId : undefined,
                message: error instanceof Error ? error.message : 'IFC editor worker failed.',
            });
        }
    }

    async initialize(wasmUrl) {
        if (this.initialized) throw new Error('The IFC editor worker is already initialized.');
        if (typeof this.fetchFn !== 'function') throw new Error('No fetch implementation is available.');
        const assetUrl = resolveSameOrigin(wasmUrl);
        const response = await this.fetchFn(assetUrl, { credentials: 'same-origin', redirect: 'error' });
        if (!response.ok) throw new Error(`Unable to load the IFC WebAssembly module (${response.status}).`);
        const declaredSize = Number(response.headers?.get?.('Content-Length') || 0);
        if (declaredSize > MAX_WASM_BYTES) throw new Error('The IFC WebAssembly module exceeds the size limit.');
        const bytes = await response.arrayBuffer();
        if (bytes.byteLength > MAX_WASM_BYTES) throw new Error('The IFC WebAssembly module exceeds the size limit.');

        const module = await this.webAssembly.compile(bytes);
        const imports = this.webAssembly.Module.imports(module);
        if (imports.length !== 0) throw new Error('The IFC WebAssembly module must not declare any imports.');

        const result = await this.webAssembly.instantiate(module, {});
        const exportsObject = result?.instance?.exports ?? result?.exports;
        const missingExport = REQUIRED_EXPORTS.some(
            (name) => name !== 'memory' && typeof exportsObject?.[name] !== 'function',
        );
        if (!exportsObject || missingExport || !hasWorkingMemory(exportsObject.memory)) {
            throw new Error('The IFC WebAssembly module does not implement the required ABI.');
        }

        this.engine.attach(exportsObject);
        this.initialized = true;
    }
}

if (typeof self !== 'undefined' && typeof self.addEventListener === 'function' && typeof self.postMessage === 'function') {
    const worker = new IfcEditorWorker({ postMessage: self.postMessage.bind(self) });
    self.addEventListener('message', (event) => {
        worker.handleMessage(event.data);
    });
}
