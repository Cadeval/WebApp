import test from 'node:test';
import assert from 'node:assert/strict';
import {createWasmHandler, MAX_WASM_BYTES} from './plugins/wasm_plugin_worker.js';
function fixture(exports={calculate:value=>value*2}, response={}) {
    const messages=[];const requests=[];
    const handle=createWasmHandler({send:message=>messages.push(message),baseUrl:'http://localhost/worker.js',
        fetchWasm:async (url,options)=>{requests.push({url,options});return {ok:true,status:200,headers:{get(){return null;}},arrayBuffer:async()=>new ArrayBuffer(8),...response};},
        instantiate:async()=>({instance:{exports}})});
    return {handle,messages,requests};
}
test('WASM uses explicit calculate export and validated protocol',async()=>{
    let allocator=0;const {handle,messages,requests}=fixture({alloc(){allocator++;},calculate:value=>value*2});
    await handle({type:'initialize',wasmUrl:'/plugin.wasm'});await handle({type:'run',value:21});
    assert.deepEqual(messages,[{type:'ready'},{type:'result',value:42}]);assert.equal(allocator,0);
    assert.equal(requests[0].options.redirect,'error');assert.equal(requests[0].options.cache,'no-store');
});
test('legacy double export remains supported',async()=>{const {handle,messages}=fixture({double:value=>value*2});await handle({type:'initialize',wasmUrl:'/plugin.wasm'});await handle({type:'run',value:2});assert.equal(messages.at(-1).value,4);});
test('unrelated callable exports cannot masquerade as calculations',async()=>{const {handle,messages}=fixture({alloc(){throw new Error('must not run');}});await handle({type:'initialize',wasmUrl:'/plugin.wasm'});assert.match(messages.at(-1).message,/must export calculate/);});
test('cross-origin URLs and redirects are rejected',async()=>{
    const first=fixture();await first.handle({type:'initialize',wasmUrl:'https://outside.test/plugin.wasm'});assert.equal(first.requests.length,0);assert.match(first.messages[0].message,/same-origin/);
    const second=fixture(undefined,{redirected:true});await second.handle({type:'initialize',wasmUrl:'/plugin.wasm'});assert.equal(second.messages[0].type,'error');
});
test('WASM size is bounded before instantiate',async()=>{const {handle,messages}=fixture(undefined,{headers:{get(){return String(MAX_WASM_BYTES+1);}}});await handle({type:'initialize',wasmUrl:'/plugin.wasm'});assert.match(messages[0].message,/size limit/);});
test('malformed protocol and nonfinite results fail clearly',async()=>{
    const first=fixture();await first.handle({type:'run',value:1});assert.match(first.messages[0].message,/not initialized/);
    const second=fixture({calculate:()=>Infinity});await second.handle({type:'initialize',wasmUrl:'/plugin.wasm'});await second.handle({type:'run',value:1});assert.match(second.messages.at(-1).message,/finite/);
    const third=fixture();await third.handle({type:'initialize',wasmUrl:'/plugin.wasm',extra:true});assert.match(third.messages[0].message,/Invalid host message/);
});
