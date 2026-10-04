const dialogSelector = '[data-plugin-detail-dialog]';
const linkSelector = 'a[data-plugin-details]';

export function validDetailHTML(text, parseHTML) {
    try {
        const parsed = parseHTML(text);
        const children = Array.from(parsed.body.children);
        return children.length === 1 && children[0].hasAttribute('data-plugin-detail-panel')
            && !parsed.querySelector('#content-container') && !parsed.querySelector('script');
    } catch { return false; }
}

/** Native modal semantics keep focus and make the background inert while open. */
export function createPluginDetailsController(documentRoot, options = {}) {
    const windowRoot = documentRoot.defaultView;
    const parseHTML = options.parseHTML || (text => new windowRoot.DOMParser().parseFromString(text, 'text/html'));
    const navigate = options.navigate || (url => windowRoot.location.assign(url));
    const listeners = [];
    let active = null;
    let opener = null;
    let pending = null;

    function status(message, busy = false) {
        const node = active?.querySelector('[data-plugin-detail-loading]');
        if (node) { node.textContent = message; node.hidden = !message; }
        active?.querySelector('#plugin-detail-body')?.setAttribute('aria-busy', String(busy));
    }
    function close({restoreFocus = true} = {}) {
        const previous = active;
        pending?.request?.abort?.();
        pending = null;
        active = null;
        if (previous?.open) previous.close();
        if (restoreFocus && opener?.isConnected) opener.focus({preventScroll: true});
        opener = null;
    }
    function onClick(event) {
        const closeButton = event.target?.closest?.('[data-plugin-detail-close]');
        if (closeButton && active?.contains(closeButton)) { event.preventDefault(); close(); return; }
        if (event.target === active) {
            const bounds = active.getBoundingClientRect();
            if (event.clientX < bounds.left || event.clientX > bounds.right
                || event.clientY < bounds.top || event.clientY > bounds.bottom) close();
            return;
        }
        const card = event.target?.closest?.('[data-plugin-card]');
        if (card && !event.target?.closest?.('a, button, input, select, textarea, label, form')) {
            // Keep every nested action independent; the ordinary anchor remains
            // the keyboard and no-JavaScript path to the same detail page.
            if (windowRoot?.getSelection?.()?.toString()) return;
            card.querySelector(linkSelector)?.click();
        }
    }
    function beforeRequest(event) {
        const ctx = event.detail?.ctx;
        const link = ctx?.sourceElement?.closest?.(linkSelector);
        if (event.defaultPrevented || !link || ctx.target?.id !== 'plugin-detail-body') return;
        const dialog = ctx.target.closest(dialogSelector);
        if (!dialog || typeof dialog.showModal !== 'function') {
            event.preventDefault(); navigate(link.href); return;
        }
        if (active && active !== dialog) close({restoreFocus: false});
        active = dialog; opener = link; pending = ctx;
        active.setAttribute('aria-label', link.textContent.trim() || 'Plugin details');
        status('Loading plugin details…', true);
        if (!dialog.open) dialog.showModal();
        dialog.querySelector('[data-plugin-detail-close]')?.focus({preventScroll: true});
    }
    function afterRequest(event) {
        const ctx = event.detail?.ctx;
        if (!ctx || ctx.target?.id !== 'plugin-detail-body') return;
        if (event.defaultPrevented || ctx !== pending || !active?.open || ctx.request?.signal?.aborted) {
            event.preventDefault(); return;
        }
        if (ctx.hx?.redirect || ctx.hx?.location || ctx.hx?.refresh === 'true') { close({restoreFocus: false}); return; }
        if (ctx.response?.status !== 200 || !validDetailHTML(ctx.text, parseHTML)) {
            event.preventDefault();
            status('Plugin details could not be loaded. Close this panel and try again.');
        }
    }
    function afterSettle(event) {
        if (event.detail?.task?.target?.id === 'plugin-detail-body' && active?.open) status('');
    }
    function afterSwap(event) {
        const ctx=event.detail?.ctx;
        if (ctx === pending && ctx?.target?.id === 'plugin-detail-body' && active?.open
            && !ctx.request?.signal?.aborted && active.querySelector('#plugin-detail-body')?.querySelector('[data-plugin-detail-panel]')) status('');
    }
    function cleanup(event) {
        if (active && (event.target === active || event.target?.contains?.(active))) close({restoreFocus: false});
    }
    function onNativeClose(event) { if (event.target === active) close(); }
    function onCancel(event) {
        if (event.target === active) { event.preventDefault(); close(); }
    }
    function listen(target, name, callback, capture = false) {
        target?.addEventListener(name, callback, capture);
        listeners.push(() => target?.removeEventListener(name, callback, capture));
    }
    listen(documentRoot, 'click', onClick);
    listen(documentRoot, 'cancel', onCancel, true);
    listen(documentRoot, 'close', onNativeClose, true);
    listen(documentRoot, 'htmx:before:request', beforeRequest);
    listen(documentRoot, 'htmx:after:request', afterRequest);
    listen(documentRoot, 'htmx:after:settle', afterSettle);
    listen(documentRoot, 'htmx:after:swap', afterSwap);
    listen(documentRoot, 'htmx:before:cleanup', cleanup);
    listen(documentRoot, 'htmx:after:swap', () => { if (active?.isConnected === false) close({restoreFocus: false}); });
    listen(documentRoot, 'htmx:before:history:restore', () => close({restoreFocus: false}));
    listen(documentRoot, 'htmx:before:history:update', () => close({restoreFocus: false}));
    listen(documentRoot, 'htmx:finally:request', event => {
        if (pending === event.detail?.ctx) {
            pending = null;
            if (active?.querySelector('#plugin-detail-body')?.getAttribute('aria-busy') === 'true') {
                // HTMX4 completes its awaited swap before setting this status.
                // Use the request completion too: target settle events may be
                // delivered on a replaced node and never reach the document.
                if (event.detail.ctx.status === 'swapped' && active.querySelector('[data-plugin-detail-panel]')) status('');
                else status('Plugin details could not be loaded. Close this panel and try again.');
            }
        }
    });
    listen(windowRoot, 'pagehide', () => close({restoreFocus: false}));
    return {close, dispose() { close({restoreFocus: false}); for (const remove of listeners) remove(); }};
}

if (typeof document !== 'undefined') {
    const key = Symbol.for('cadevil.pluginDetails');
    if (!document.defaultView[key]) document.defaultView[key] = createPluginDetailsController(document);
}
