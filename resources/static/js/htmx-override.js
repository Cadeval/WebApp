// HTMX 4 swaps validation responses by default. Keep actionable form errors,
// but preserve the current page for unexpected server/permission failures.
document.addEventListener('htmx:before:swap', event => {
    const status = event.detail.ctx?.response?.status;
    if (status >= 400 && ![400, 409, 422].includes(status)) event.preventDefault();
});

// Reject failures before history changes and offer readable recovery guidance.
document.addEventListener('htmx:before:response', event => {
    const status = event.detail.ctx?.response?.status;
    if (status >= 400 && ![400, 409, 422].includes(status)) {
        event.preventDefault();
        const notice = document.getElementById('request-notice');
        if (notice) {
            notice.textContent = status === 403 ? 'The request was not permitted. Refresh the page and check your access.'
                : status === 404 ? 'This page or tool is unavailable. Choose another link from the navigation.'
                : 'The request could not be completed. Your current page has been kept; please try again.';
            notice.hidden = false;
        }
    }
});

// New work supersedes previous request feedback. Task activity is managed in
// task_activity.js, which keeps each overlapping request's lifecycle separate.
document.addEventListener('htmx:before:request', function () {
    const notice = document.getElementById('request-notice');
    if (notice) notice.hidden = true;
});


// Config editor panning: enable smooth click-and-drag (grab) panning across the
// wide configuration table. The container handles smooth scrolling/momentum via
// CSS; this adds mouse drag-to-pan to complement touch scrolling. Initialization
// runs after each htmx swap (the editor is injected dynamically) and on load.
function initConfigEditorPanning() {
    const pans = document.querySelectorAll('.config-editor-pan');
    pans.forEach(function (pan) {
        if (pan.dataset.panInitialized === 'true') {
            return;
        }
        pan.dataset.panInitialized = 'true';

        let isDown = false;
        let startX = 0;
        let startY = 0;
        let startScrollLeft = 0;
        let startScrollTop = 0;

        pan.addEventListener('pointerdown', function (e) {
            // Don't hijack interactions with the editable inputs.
            if (e.target.closest('input, textarea, select, button, a, label')) {
                return;
            }
            isDown = true;
            startX = e.clientX;
            startY = e.clientY;
            startScrollLeft = pan.scrollLeft;
            startScrollTop = pan.scrollTop;
            pan.classList.add('panning');
            pan.setPointerCapture(e.pointerId);
        });

        pan.addEventListener('pointermove', function (e) {
            if (!isDown) {
                return;
            }
            e.preventDefault();
            // Pan along both axes so the editor can be dragged diagonally.
            pan.scrollLeft = startScrollLeft - (e.clientX - startX);
            pan.scrollTop = startScrollTop - (e.clientY - startY);
        });

        function endPan(e) {
            if (!isDown) {
                return;
            }
            isDown = false;
            pan.classList.remove('panning');
            if (e && e.pointerId !== undefined && pan.hasPointerCapture(e.pointerId)) {
                pan.releasePointerCapture(e.pointerId);
            }
        }

        pan.addEventListener('pointerup', endPan);
        pan.addEventListener('pointercancel', endPan);
        pan.addEventListener('pointerleave', endPan);
    });
}

document.addEventListener('htmx:after:settle', initConfigEditorPanning);
document.addEventListener('DOMContentLoaded', initConfigEditorPanning);


// Contextual chrome: some persistent header/footer controls only make sense for
// a specific page that is swapped into #content-container via htmx. Toggle body
// classes based on which page is currently loaded so CSS can show/hide them:
//   - body.config-editor-active  -> the configuration editor is loaded
//     (Save/Download buttons in the footer become visible)
//   - body.model-manager-active  -> the model manager is loaded
//     (the "User Files" openbtn in the header becomes visible)
function updateContextualChrome() {
    const theme = document.querySelector('[data-user-theme]')?.dataset.userTheme;
    if (theme) document.body.dataset.theme = theme;
    const title = document.querySelector('#viewer-app')?.dataset.pageTitle
        || document.querySelector('#content-container h1')?.textContent.trim();
    if (title) document.title = `${title} · Cadevil`;
    const isConfigEditor = !!document.getElementById('config_form');
    document.body.classList.toggle('config-editor-active', isConfigEditor);

    const isModelManager = !!document.getElementById('cadevil-document-grid');
    document.body.classList.toggle('model-manager-active', isModelManager);

    const isViewer = !!document.getElementById('viewer-app');
    document.body.classList.toggle('viewer-active', isViewer);
}

document.addEventListener('htmx:after:settle', updateContextualChrome);
document.addEventListener('DOMContentLoaded', updateContextualChrome);

const pageFocusOrigins = new WeakMap();
document.addEventListener('htmx:before:request', event => {
    const menu = document.getElementById('menu-popover');
    if (menu?.contains(event.detail.ctx?.sourceElement) && menu.matches(':popover-open')) menu.hidePopover();
    const ctx = event.detail.ctx;
    if (ctx?.request?.method === 'GET' && ['content-container', 'main-content'].includes(ctx.target?.id)) {
        pageFocusOrigins.set(ctx, document.activeElement);
    }
});

function updateNavigationState() {
    const path = location.pathname;
    const workspaceUrl = document.querySelector('[data-plugin-workspace]')?.dataset.pluginWorkspaceUrl;
    document.querySelectorAll('#menu-popover a[href]').forEach(link => {
        const current = workspaceUrl && link.dataset.pluginWorkspaceUrl
            ? new URL(link.dataset.pluginWorkspaceUrl, location.href).pathname === new URL(workspaceUrl, location.href).pathname
            : new URL(link.href, location.href).pathname === path;
        if (current) link.setAttribute('aria-current', 'page');
        else link.removeAttribute('aria-current');
    });
}
document.addEventListener('htmx:after:settle', updateNavigationState);
document.addEventListener('DOMContentLoaded', updateNavigationState);

function availableForPageFocus(element) {
    if (!element || element.closest?.('[hidden], [inert], [aria-hidden="true"]')) return false;
    // A heading inside a display:none responsive toolbar has no rendered
    // rectangles. Calling focus on it silently leaves focus on the body.
    if (element.getClientRects && element.getClientRects().length === 0) return false;
    const visibility = window.getComputedStyle?.(element)?.visibility;
    return visibility !== 'hidden' && visibility !== 'collapse';
}

// Only new validation feedback receives focus; an unrelated panel refresh must
// not move the user back to an older form error elsewhere on the page.
document.addEventListener('htmx:after:settle', event => {
    const errors = event.detail?.task?.target?.querySelector?.('[data-form-errors]');
    if (availableForPageFocus(errors)) errors.focus();
});
document.addEventListener('DOMContentLoaded', () => {
    const errors = document.querySelector('[data-form-errors]');
    if (availableForPageFocus(errors)) errors.focus();
});

// HTMX 4 settles before after:swap. At this point the final page (including a
// history-restored main landmark) exists and keyboard focus can follow it.
// Successful POST updates keep the current reading position.
document.addEventListener('htmx:after:swap', event => {
    const ctx = event.detail.ctx;
    if (ctx?.request?.method === 'GET' && ['content-container', 'main-content'].includes(ctx.target?.id)) {
        updateContextualChrome();
        updateNavigationState();
        const content = document.getElementById('content-container');
        const summary = content?.querySelector('[data-form-errors]');
        const errors = availableForPageFocus(summary) ? summary : null;
        // The workspace controller deliberately restores tab focus while
        // settling. Preserve that manual tab-navigation contract.
        const activeTab = document.activeElement?.closest?.('[data-workspace-tab]');
        if (!errors && activeTab && content?.contains(activeTab)) return;
        const active = document.activeElement;
        // A user can move to persistent navigation or another tool while a
        // request runs. Do not pull focus away from that newer interaction.
        if (!errors && pageFocusOrigins.has(ctx) && active && active !== document.body
            && active !== pageFocusOrigins.get(ctx) && !content?.contains(active)) return;
        const heading = Array.from(content?.querySelectorAll?.('h1') || []).find(availableForPageFocus);
        const target = errors || heading
            || document.getElementById('main-content');
        if (target) {
            target.setAttribute('tabindex', '-1');
            target.focus({preventScroll: true});
        }
        window.scrollTo({top: 0, left: 0, behavior: 'instant'});
    }
});
