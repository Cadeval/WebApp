import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { IfcEditorEngine, IfcEditorWorker, MAX_IFC_BYTES } from './plugins/example_plugin_worker.js';

const wasm = await readFile(new URL('../wasm/example_plugin.wasm', import.meta.url));
const module = await WebAssembly.compile(wasm);
const encoder = new TextEncoder(), decoder = new TextDecoder();

function document(firstId = 10, secondId = 20) {
    return `ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION(('Editor ABI regression'),'2;1');\nFILE_SCHEMA(('IFC4'));\nENDSEC;\nDATA;\n#${firstId}=IFCMATERIAL('First material',$,$);\n#${secondId}=IFCMATERIAL('Second material',$,$);\nENDSEC;\nEND-ISO-10303-21;\n`;
}

async function engine(source = document()) {
    const instance = await WebAssembly.instantiate(module, {}), engine = new IfcEditorEngine();
    engine.attach(instance.exports);
    engine.load(encoder.encode(source).buffer, 'materials.ifc');
    return { engine, exports: instance.exports, source };
}

for (const [firstId, secondId] of [[1, 2], [10, 20]]) {
    test(`rebuilt IFC WASM updates STEP #${firstId} without altering #${secondId} and round-trips exported IFC`, async () => {
        const fixture = await engine(document(firstId, secondId)), runtime = fixture.engine;
        assert.deepEqual(WebAssembly.Module.imports(module), []);
        assert.deepEqual(runtime.list(0, 25).entities, [
            { index: 0, id: firstId, entityType: 'IFCMATERIAL' },
            { index: 1, id: secondId, entityType: 'IFCMATERIAL' },
        ]);
        assert.equal(decoder.decode(runtime.exportStep().bytes), fixture.source);
        assert.deepEqual(runtime.update(firstId, 0, "'Updated first'"), {
            entityId: firstId, attributeIndex: 0, kind: 1, value: "'Updated first'",
        });
        assert.equal(runtime.inspect(0).attributes[0].value, "'Updated first'");
        assert.equal(runtime.inspect(1).attributes[0].value, "'Second material'");
        const firstExpected = fixture.source.replace("'First material'", "'Updated first'");
        assert.equal(decoder.decode(runtime.exportStep().bytes), firstExpected);
        runtime.update(secondId, 0, "'O''Brien – Grüße'");
        assert.equal(runtime.inspect(0).attributes[0].value, "'Updated first'");
        const exported = runtime.exportStep();
        assert.equal(exported.filename, 'materials.ifc');
        assert.equal(decoder.decode(exported.bytes), firstExpected.replace("'Second material'", "'O''Brien – Grüße'"));
        const reloaded = await engine(decoder.decode(exported.bytes));
        assert.equal(reloaded.engine.inspect(0).attributes[0].value, "'Updated first'");
        assert.equal(reloaded.engine.inspect(1).attributes[0].value, "'O''Brien – Grüße'");
    });
}

test('invalid WASM updates return a readable numeric error and leave both entities unchanged', async () => {
    const { engine: runtime, exports, source } = await engine();
    // Seed the shared output with a previous value: it must never become error text.
    runtime.inspect(1);
    assert.throws(() => runtime.update(10, 0, '1,2'), /Updating the attribute failed: The IFC data contains an invalid editable STEP value\./);
    assert.equal(exports.ifc_last_error(), 5);
    assert.equal(decoder.decode(runtime.exportStep().bytes), source);
    assert.equal(runtime.inspect(0).attributes[0].value, "'First material'");
    assert.equal(runtime.inspect(1).attributes[0].value, "'Second material'");
    assert.throws(() => runtime.update(999, 0, "'Missing'"), /No entity with that STEP id was found/);
    assert.throws(() => runtime.update(10, 99, "'Missing attribute'"), /attribute index is out of range/);
});

test('a failed WASM load maps its error code and preserves the previously loaded document', async () => {
    const { engine: runtime, exports, source } = await engine();
    runtime.inspect(0);
    assert.throws(() => runtime.load(encoder.encode('INVALID;').buffer, 'invalid.ifc'), /missing required STEP section markers/);
    assert.equal(exports.ifc_last_error(), 9);
    assert.equal(runtime.filename, 'materials.ifc');
    assert.equal(decoder.decode(runtime.exportStep().bytes), source);
});

test('oversized rebuilt-WASM reservations reject before memory growth and preserve loaded IFC/input', async () => {
    const { engine: runtime, exports, source } = await engine();
    const memoryBytes = exports.memory.buffer.byteLength;
    for (const requested of [MAX_IFC_BYTES + 1, 0xffffffff]) {
        assert.equal(exports.ifc_input_reserve(requested), -1);
        assert.equal(exports.ifc_last_error(), 6);
        assert.match(runtime.readLastError(), /32 MiB size limit/);
        assert.equal(exports.memory.buffer.byteLength, memoryBytes);
        assert.equal(exports.ifc_load(encoder.encode(source).byteLength), 0, 'Rejected reserve retains the original input buffer');
        assert.equal(decoder.decode(runtime.exportStep().bytes), source);
    }
    assert.throws(() => runtime.writeInput(new Uint8Array(MAX_IFC_BYTES + 1)), /Reserving the input buffer failed:.*32 MiB size limit/);
    assert.equal(exports.memory.buffer.byteLength, memoryBytes);
    assert.equal(decoder.decode(runtime.exportStep().bytes), source);
});

test('worker message protocol initializes the real WASM and returns correct update/export/rollback messages', async () => {
    const messages = [];
    const worker = new IfcEditorWorker({
        postMessage: (message, transfer) => messages.push({ message, transfer }),

    });
    await worker.handleMessage({ type: 'initialize', wasmBytes: wasm.buffer.slice(wasm.byteOffset, wasm.byteOffset + wasm.byteLength) });
    assert.equal(messages.at(-1).message.type, 'ready');
    await worker.handleMessage({ type: 'load', bytes: encoder.encode(document()).buffer, filename: 'materials.ifc', requestId: 'load-1' });
    assert.deepEqual(messages.at(-1).message, { type: 'loaded', requestId: 'load-1', filename: 'materials.ifc', entityCount: 2 });
    await worker.handleMessage({ type: 'update', entityId: 10, attributeIndex: 0, value: "'Worker edit'", requestId: 'update-1' });
    assert.deepEqual(messages.at(-1).message, { type: 'updated', requestId: 'update-1', entityId: 10, attributeIndex: 0, kind: 1, value: "'Worker edit'" });
    await worker.handleMessage({ type: 'update', entityId: 20, attributeIndex: 0, value: '1,2', requestId: 'bad-update' });
    assert.equal(messages.at(-1).message.requestId, 'bad-update');
    assert.equal(messages.at(-1).message.type, 'error');
    assert.match(messages.at(-1).message.message, /invalid editable STEP value/);
    await worker.handleMessage({ type: 'export', requestId: 'export-1' });
    const { message, transfer } = messages.at(-1);
    assert.equal(message.type, 'exported');
    assert.equal(message.requestId, 'export-1');
    assert.deepEqual(transfer, [message.bytes]);
    assert.equal(decoder.decode(message.bytes), document().replace("'First material'", "'Worker edit'"));
});
