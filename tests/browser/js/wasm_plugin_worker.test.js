import test from 'node:test';
import assert from 'node:assert/strict';
import {createWasmHandler, MAX_WASM_BYTES} from './plugins/wasm_plugin_worker.js';
function fixture(exports={calculate:value=>value*2}) {
    const messages=[],instantiations=[];
    const handle=createWasmHandler({send:message=>messages.push(message),instantiate:async bytes=>{instantiations.push(bytes);return {instance:{exports}};}});
    return {handle,messages,instantiations};
}
const boot=()=>({type:'initialize',wasmBytes:new ArrayBuffer(8)});
test('WASM uses verified bytes and explicit calculate export',async()=>{
    let allocator=0;const {handle,messages,instantiations}=fixture({alloc(){allocator++;},calculate:value=>value*2});
    const message=boot();await handle(message);await handle({type:'run',value:21});
    assert.deepEqual(messages,[{type:'ready'},{type:'result',value:42}]);assert.equal(allocator,0);
    assert.equal(instantiations[0],message.wasmBytes);
});
test('legacy double export remains supported',async()=>{const {handle,messages}=fixture({double:value=>value*2});await handle(boot());await handle({type:'run',value:2});assert.equal(messages.at(-1).value,4);});
test('unrelated callable exports cannot masquerade as calculations',async()=>{const {handle,messages}=fixture({alloc(){throw Error('must not run');}});await handle(boot());assert.match(messages.at(-1).message,/must export calculate/);});
test('worker never accepts an unverified URL or additional initialize fields',async()=>{
    for (const message of [{type:'initialize',wasmUrl:'/unsigned.wasm'},{...boot(),wasmUrl:'/unsigned.wasm'}]) {
        const f=fixture();await f.handle(message);assert.equal(f.instantiations.length,0);assert.match(f.messages[0].message,/Invalid host message/);
    }
});
test('verified WASM input is bounded before instantiate',async()=>{
    for (const bytes of [new ArrayBuffer(0),new ArrayBuffer(MAX_WASM_BYTES+1),new Uint8Array(8)]) {
        const f=fixture();await f.handle({type:'initialize',wasmBytes:bytes});assert.equal(f.instantiations.length,0);assert.equal(f.messages[0].type,'error');
    }
});
test('malformed protocol and nonfinite results fail clearly',async()=>{
    const first=fixture();await first.handle({type:'run',value:1});assert.match(first.messages[0].message,/not initialized/);
    const second=fixture({calculate:()=>Infinity});await second.handle(boot());await second.handle({type:'run',value:1});assert.match(second.messages.at(-1).message,/finite/);
    const third=fixture();await third.handle({...boot(),extra:true});assert.match(third.messages[0].message,/Invalid host message/);
});
