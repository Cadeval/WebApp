/** Explicit trusted fixture for lifecycle unit tests; production always uses X.509 verification. */
export function preverifiedWorker(options) {
    return {bootstrapURL:options.workerUrl,entryURL:'blob:trusted-fixture',wasmBytes:new ArrayBuffer(8),
        expires:Infinity,recheck:()=>true,dispose(){}};
}
