const workspaceSelector = '[data-plugin-workspace]';
const tabSelector = 'a[data-workspace-tab]';
const replacementStyles = new Set(['outerHTML', 'innerHTML', 'outerSync']);
const acceptedErrors = new Set([400, 409, 422]);

export function nextTabIndex(key, index, length, direction = 'ltr') {
    if (!length) return null;
    if (key === 'Home') return 0;
    if (key === 'End') return length - 1;
    if (key !== 'ArrowLeft' && key !== 'ArrowRight') return null;
    const step = (key === 'ArrowRight' ? 1 : -1) * (direction === 'rtl' ? -1 : 1);
    return (index + step + length) % length;
}

function tabsWithin(root) {
    return Array.from(root?.querySelector('[data-workspace-tablist]')?.querySelectorAll(tabSelector) || []);
}

export function enhanceWorkspace(root) {
    const nav = root?.querySelector('[data-workspace-tablist]');
    if (!nav) return;
    const tabs = tabsWithin(root);
    nav.setAttribute('role', 'tablist');
    for (const tab of tabs) {
        const selected = tab.id === root.dataset.activeTabId;
        tab.setAttribute('role', 'tab');
        tab.setAttribute('aria-selected', String(selected));
        tab.tabIndex = selected ? 0 : -1;
    }
}

function validReplacement(task, documentRoot) {
    const first = task.fragment?.firstElementChild;
    if (!first) return false;
    if (task.target === documentRoot.body) return first.tagName === 'BODY';
    if (task.swapSpec.style === 'innerHTML') {
        return !task.fragment.querySelector?.('#content-container');
    }
    return task.fragment.childElementCount === 1 && first.id === task.target.id;
}

function rejectReplacement(event, documentRoot) {
    event.preventDefault();
    const notice = documentRoot.getElementById('request-notice');
    if (notice) {
        notice.textContent = 'The page could not be displayed. Your current page has been kept; please try again.';
        notice.hidden = false;
    }
    return false;
}

// after:request has the response text and still precedes HTMX's history update.
// Parsing is inert: only the content boundary is inspected, never initialized.
export function validateWorkspaceResponse(event, documentRoot, parseHTML) {
    if (event.defaultPrevented) return false;
    const ctx = event.detail?.ctx;
    if (!ctx) return true;
    const headerNavigation = Boolean(ctx.hx?.redirect || ctx.hx?.location || ctx.hx?.refresh === 'true');
    const target = !headerNavigation && ctx.hx?.retarget ? documentRoot.querySelector(ctx.hx.retarget) : ctx.target;
    const roots = Array.from(documentRoot.querySelectorAll(workspaceSelector));
    if (!roots.some(root => target === root || target?.contains?.(root))) return true;
    const status = ctx.response?.status;
    if (ctx.request?.signal?.aborted || (status >= 400 && !acceptedErrors.has(status))) {
        return rejectReplacement(event, documentRoot);
    }
    // These header actions and native no-swap statuses are handled by HTMX
    // after after:request. They need no replacement HTML or outgoing cleanup.
    if (headerNavigation || status === 204 || status === 304) return true;
    const style = String(ctx.hx?.reswap || ctx.swap || 'innerHTML').trim().split(/\s+/)[0];
    if (!replacementStyles.has(style)) return true;
    try {
        const parsed = parseHTML(ctx.text);
        const boundaries = Array.from(parsed.querySelectorAll('#content-container'));
        let valid;
        if (target === documentRoot.body) {
            const firstTag = String(ctx.text).match(/<([a-z][^\s/>]*)/i)?.[1]?.toLowerCase();
            valid = ['html', 'body'].includes(firstTag) && boundaries.length === 1 && parsed.body.contains(boundaries[0]);
        } else if (style === 'innerHTML') {
            valid = boundaries.length === 0 && parsed.body.childElementCount > 0;
        } else {
            const select = ctx.hx?.reselect || ctx.select;
            const main = Array.from(parsed.body.children).filter(element => !element.hasAttribute('hx-swap-oob')
                && !element.hasAttribute('data-hx-swap-oob'));
            const boundary = select ? parsed.querySelector(select) : main[0];
            valid = boundaries.length === 1 && boundary?.id === target.id
                && (select || main.length === 1);
        }
        return valid ? true : rejectReplacement(event, documentRoot);
    } catch {
        return rejectReplacement(event, documentRoot);
    }
}

// HTMX 4 only emits cleanup on powered elements. The workspace boundary itself
// is deliberately passive, so dispatch once around its entire outgoing panel.
export function prepareWorkspaceSwap(event, documentRoot, dispatchCleanup) {
    const ctx = event.detail?.ctx;
    if (event.defaultPrevented) return false;
    const roots = Array.from(documentRoot.querySelectorAll(workspaceSelector));
    const tasks = (event.detail?.tasks || []).filter(task => replacementStyles.has(task.swapSpec?.style)
        && roots.some(root => task.target === root || task.target?.contains?.(root)));
    if (!tasks.length) return false;
    const status = ctx?.response?.status;
    if (ctx?.request?.signal?.aborted || (status >= 400 && !acceptedErrors.has(status))
        || tasks.some(task => !validReplacement(task, documentRoot))) {
        return rejectReplacement(event, documentRoot);
    }
    for (const root of roots) {
        if (tasks.some(task => task.target === root || task.target.contains(root))) dispatchCleanup(root);
    }
    return true;
}

export function createWorkspaceController(documentRoot, options = {}) {
    const windowRoot = documentRoot.defaultView;
    const CustomEventClass = options.CustomEvent || windowRoot?.CustomEvent || globalThis.CustomEvent;
    const direction = options.direction || (nav => windowRoot?.getComputedStyle?.(nav).direction || 'ltr');
    const parseHTML = options.parseHTML || (text => new windowRoot.DOMParser().parseFromString(text, 'text/html'));
    const cleaned = new WeakSet();
    let enhanced = new WeakSet();
    const listeners = [];
    let pendingFocus = null;
    let restoringHistory = false;

    function mount() {
        for (const root of documentRoot.querySelectorAll(workspaceSelector)) {
            if (enhanced.has(root)) continue;
            enhanceWorkspace(root);
            enhanced.add(root);
        }
    }

    function tabContext(element) {
        const tab = element?.closest?.(tabSelector);
        const root = tab?.closest?.(workspaceSelector);
        if (!root || !tabsWithin(root).includes(tab)) return null;
        return { root, tab };
    }

    function onKeydown(event) {
        if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey) return;
        const context = tabContext(event.target);
        if (!context) return;
        const { root, tab } = context;
        if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            tab.click();
            return;
        }
        const tabs = tabsWithin(root), index = nextTabIndex(event.key, tabs.indexOf(tab), tabs.length,
            direction(root.querySelector('[data-workspace-tablist]')));
        if (index === null) return;
        event.preventDefault();
        for (const item of tabs) item.tabIndex = item === tabs[index] ? 0 : -1;
        tabs[index].focus();
    }

    function onBeforeRequest(event) {
        const ctx = event.detail?.ctx;
        if (!ctx || event.defaultPrevented) return;
        const context = tabContext(ctx.sourceElement);
        if (ctx.request?.method === 'GET' && context) {
            pendingFocus = { ctx, slug: context.root.dataset.pluginWorkspace, id: context.tab.id,
                source: context.tab };
        }
    }

    function cleanup(root) {
        if (cleaned.has(root)) return;
        cleaned.add(root);
        root.dispatchEvent(new CustomEventClass('htmx:before:cleanup', { bubbles: true }));
    }

    function onAfterSettle(event) {
        mount();
        const target = event.detail?.task?.target;
        const roots = Array.from(documentRoot.querySelectorAll(workspaceSelector));
        const root = roots.find(item => target === item || target?.contains?.(item));
        if (!root) return; // Inline map/form updates do not move tab focus.
        const active = tabsWithin(root).find(tab => tab.id === root.dataset.activeTabId);
        if (!active) return;
        const activeElement = documentRoot.activeElement;
        if (pendingFocus?.slug === root.dataset.pluginWorkspace && pendingFocus.id === active.id) {
            // Do not steal focus if the user moved elsewhere while loading.
            if (!activeElement || activeElement === documentRoot.body || activeElement === pendingFocus.source
                || activeElement === active) active.focus({ preventScroll: true });
            pendingFocus = null;
        } else if (restoringHistory) {
            active.focus({ preventScroll: true });
        }
        restoringHistory = false;
    }

    function resetFocus() { pendingFocus = null; restoringHistory = false; }
    function listen(target, name, callback) {
        target?.addEventListener(name, callback);
        listeners.push(() => target?.removeEventListener(name, callback));
    }
    listen(documentRoot, 'keydown', onKeydown);
    listen(documentRoot, 'htmx:before:request', onBeforeRequest);
    listen(documentRoot, 'htmx:after:request', event => validateWorkspaceResponse(event, documentRoot, parseHTML));
    // Document bubbling runs after the shared shell's rejection handlers.
    listen(documentRoot, 'htmx:before:swap', event => prepareWorkspaceSwap(event, documentRoot, cleanup));
    listen(documentRoot, 'htmx:after:settle', onAfterSettle);
    listen(documentRoot, 'htmx:before:history:restore', () => { resetFocus(); restoringHistory = true; });
    listen(documentRoot, 'htmx:finally:request', event => {
        const ctx = event.detail?.ctx;
        if (pendingFocus?.ctx === ctx) pendingFocus = null;
        if (ctx?.request?.headers?.['HX-History-Restore-Request'] === 'true') restoringHistory = false;
    });
    listen(documentRoot, 'DOMContentLoaded', mount);
    listen(windowRoot, 'pagehide', resetFocus);
    listen(windowRoot, 'pageshow', event => { if (event.persisted) { enhanced = new WeakSet(); mount(); } });
    mount();
    return { mount, dispose() { resetFocus(); for (const remove of listeners) remove(); } };
}

if (typeof document !== 'undefined') {
    const key = Symbol.for('cadevil.pluginWorkspace');
    if (!document.defaultView[key]) document.defaultView[key] = createWorkspaceController(document);
}
