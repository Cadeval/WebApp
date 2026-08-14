const logArea = document.getElementById("logArea");

// Match the page protocol so the connection works everywhere:
//   - http://  -> ws://   (local dev, e.g. ws://127.0.0.1:8000/ws/logs/)
//   - https:// -> wss://  (production, e.g. wss://yourdomain.com/ws/logs/)
// Safari (and most browsers) throw a SecurityError when opening an insecure
// "ws://" socket from an "https://" page. Because this script runs at the top
// level, that exception aborted the whole file and the scene-transition
// throbber handlers below were never registered. Deriving the scheme and
// wrapping the setup in try/catch keeps the rest of the script running.
const wsProtocol = window.location.protocol === "https:" ? "wss://" : "ws://";

try {
    const socket = new WebSocket(wsProtocol + window.location.host + "/ws/logs/");

    socket.onmessage = function (e) {

        // Each incoming message is a single log line
        const logLine = e.data;
        console.log("Message from server:", logLine);
        if (logArea) {
            logArea.textContent += logLine + "\n";
            // optionally auto-scroll
            logArea.scrollTop = logArea.scrollHeight;
        }
    };

    socket.onopen = function (e) {
        console.log("WebSocket connection established!");
    };
    socket.onerror = function (e) {
        console.error("WebSocket error:", e);
    };
    socket.onclose = function (e) {
        console.log("WebSocket connection closed.");
    };
} catch (err) {
    console.error("WebSocket setup failed:", err);
}
document.body.addEventListener('htmx:beforeSwap', function (event) {
    if ([204, 400, 409].includes(event.detail.xhr.status)) {
        // Swap actionable validation/conflict responses so the server can
        // display safe feedback instead of leaving the user with an HTMX error.
        event.detail.shouldSwap = true;
    }
});


// Loading throbber: shown while an htmx request targeting the main content
// container is in flight, and hidden once the new content has been settled.
const throbber = document.getElementById('htmx-throbber');

function isContentRequest(detail) {
    const content = document.getElementById('content-container');
    if (!content || !detail || !detail.target) {
        return false;
    }
    return detail.target === content || content.contains(detail.target);
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
document.body.addEventListener('htmx:beforeRequest', function (e) {
    if (isContentRequest(e.detail)) {
        showThrobber();
    }
});

// Hide the throbber once the swapped-in content has been settled.
document.body.addEventListener('htmx:afterSettle', hideThrobber);

// Make sure the throbber never gets stuck if a request fails or is aborted.
document.body.addEventListener('htmx:responseError', hideThrobber);
document.body.addEventListener('htmx:sendError', hideThrobber);
document.body.addEventListener('htmx:timeout', hideThrobber);
document.body.addEventListener('htmx:afterRequest', function (e) {
    if (!e.detail.successful) {
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

document.body.addEventListener('htmx:afterSettle', initConfigEditorPanning);
document.addEventListener('DOMContentLoaded', initConfigEditorPanning);


// Contextual chrome: some persistent header/footer controls only make sense for
// a specific page that is swapped into #content-container via htmx. Toggle body
// classes based on which page is currently loaded so CSS can show/hide them:
//   - body.config-editor-active  -> the configuration editor is loaded
//     (Save/Download buttons in the footer become visible)
//   - body.model-manager-active  -> the model manager is loaded
//     (the "User Files" openbtn in the header becomes visible)
function updateContextualChrome() {
    const isConfigEditor = !!document.getElementById('config_form');
    document.body.classList.toggle('config-editor-active', isConfigEditor);

    const isModelManager = !!document.getElementById('cadevil-document-grid');
    document.body.classList.toggle('model-manager-active', isModelManager);

    const isViewer = !!document.getElementById('viewer-app');
    document.body.classList.toggle('viewer-active', isViewer);
}

document.body.addEventListener('htmx:afterSettle', updateContextualChrome);
document.addEventListener('DOMContentLoaded', updateContextualChrome);
