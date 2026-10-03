// HTMX 4 swaps validation responses by default. Keep actionable form errors,
// but preserve the current page for unexpected server/permission failures.
document.body.addEventListener('htmx:before:swap', event => {
    const status = event.detail.ctx?.response?.status;
    if (status >= 400 && ![400, 409, 422].includes(status)) event.preventDefault();
});

// Reject failures before history changes and offer readable recovery guidance.
document.body.addEventListener('htmx:before:response', event => {
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

// Loading throbber: shown while an htmx request targeting the main content
// container is in flight, and hidden once the new content has been settled.
const throbber = document.getElementById('htmx-throbber');

function isContentRequest(detail) {
    const content = document.getElementById('content-container');
    if (!content || !detail || !detail.ctx?.target) {
        return false;
    }
    return detail.ctx.target === content || content.contains(detail.ctx.target);
}

function showThrobber() {
    if (throbber) {
        throbber.classList.add('active');
    }
}

function hideThrobber() {
    if (throbber) {
        throbber.classList.remove('active');
    }
}

// Show the throbber as soon as a request for the main content starts.
document.body.addEventListener('htmx:before:request', function (e) {
    const notice = document.getElementById('request-notice');
    if (notice) notice.hidden = true;
    if (isContentRequest(e.detail)) {
        showThrobber();
    }
});

// Hide the throbber once the swapped-in content has been settled.
document.body.addEventListener('htmx:after:settle', hideThrobber);

// Make sure the throbber never gets stuck if a request fails or is aborted.
document.body.addEventListener('htmx:response:error', hideThrobber);
document.body.addEventListener('htmx:error', hideThrobber);
document.body.addEventListener('htmx:finally:request', hideThrobber);
document.body.addEventListener('htmx:after:request', function (e) {
    if ((e.detail.ctx?.response?.status ?? 500) >= 400) {
        hideThrobber();
    }
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

document.body.addEventListener('htmx:after:settle', initConfigEditorPanning);
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
    const title = document.querySelector('#content-container h1')?.textContent.trim();
    if (title) document.title = `${title} · Cadevil`;
    const isConfigEditor = !!document.getElementById('config_form');
    document.body.classList.toggle('config-editor-active', isConfigEditor);

    const isModelManager = !!document.getElementById('cadevil-document-grid');
    document.body.classList.toggle('model-manager-active', isModelManager);

    const isViewer = !!document.getElementById('viewer-app');
    document.body.classList.toggle('viewer-active', isViewer);
}

document.body.addEventListener('htmx:after:settle', updateContextualChrome);
document.addEventListener('DOMContentLoaded', updateContextualChrome);

document.body.addEventListener('htmx:before:request', event => {
    const menu = document.getElementById('menu-popover');
    if (menu?.contains(event.detail.ctx?.sourceElement) && menu.matches(':popover-open')) menu.hidePopover();
});

function updateNavigationState() {
    const path = location.pathname;
    document.querySelectorAll('#menu-popover a[href]').forEach(link => {
        const current = new URL(link.href, location.href).pathname === path;
        if (current) link.setAttribute('aria-current', 'page');
        else link.removeAttribute('aria-current');
    });
}
document.body.addEventListener('htmx:after:settle', updateNavigationState);
document.addEventListener('DOMContentLoaded', updateNavigationState);

// Give validation feedback a predictable keyboard/screen-reader starting point.
document.body.addEventListener('htmx:after:settle', () => {
    document.querySelector('[data-form-errors]')?.focus();
});

// A new content page starts at its heading; POST updates retain reading position.
document.body.addEventListener('htmx:after:swap', event => {
    const ctx = event.detail.ctx;
    if (ctx?.request?.method === 'GET' && ctx.target?.id === 'content-container') {
        window.scrollTo({top: 0, left: 0, behavior: 'instant'});
    }
});
