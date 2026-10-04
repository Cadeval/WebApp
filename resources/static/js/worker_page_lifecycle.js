/** Bind isolated workers to the page, including passive HTMX boundaries and BFCache. */
export function bindWorkerPageLifecycle(runtime, documentRef = globalThis.document, windowRef = globalThis.window) {
    if (!documentRef) return () => {};
    const listeners = [];
    const on = (target, type, listener) => {
        target?.addEventListener(type, listener);
        if (target) listeners.push([target, type, listener]);
    };
    const mount = () => runtime.mount(documentRef);
    on(documentRef, 'DOMContentLoaded', mount);
    // HTMX only emits before:cleanup for powered nodes. mount() also prunes
    // disconnected roots after an accepted replacement of a passive ancestor.
    on(documentRef, 'htmx:after:settle', mount);
    on(documentRef, 'htmx:before:cleanup', event => runtime.unmountWithin(event.target));
    on(windowRef, 'pagehide', () => runtime.destroy());
    on(windowRef, 'pageshow', event => { if (event.persisted) mount(); });
    if (documentRef.readyState !== 'loading') mount();
    return () => {
        for (const [target, type, listener] of listeners) target.removeEventListener(type, listener);
        runtime.destroy();
    };
}

/** Release page resources after a real removal or browser navigation. */
export function bindPageResourceLifecycle(root, dispose, documentRef = root?.ownerDocument ?? globalThis.document, windowRef = documentRef?.defaultView ?? globalThis.window) {
    const listeners = [];
    const on = (target, type, listener) => {
        if (typeof target?.addEventListener !== 'function') return;
        target.addEventListener(type, listener);
        listeners.push([target, type, listener]);
    };
    on(documentRef, 'htmx:before:cleanup', event => {
        if (event.target === root || event.target?.contains?.(root)) dispose();
    });
    on(documentRef, 'htmx:after:swap', () => { if (root?.isConnected === false) dispose(); });
    on(windowRef, 'pagehide', dispose);
    return () => {
        for (const [target, type, listener] of listeners) target.removeEventListener(type, listener);
        listeners.length = 0;
    };
}

/** Fetch the maintained crypto/parser bundle only when a plugin worker is opened. */
export function prepareVerifiedWorker(options) {
    return import('./verified_plugin_worker.js').then(module => module.loadVerifiedWorker(options));
}
export function settleVerification(value,success,failure) {
    if (value && typeof value.then === 'function') value.then(success).catch(failure);
    else {try {success(value);} catch(error) {failure(error);}}
}
