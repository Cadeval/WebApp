// A log subscription belongs to an open popover, never to the background page.
const MAX_LINES = 1000;
const viewers = new Map();

export function formatLog(entry) {
    return `${entry.time} ${entry.level} ${entry.logger}: ${entry.message}`;
}

export function createLogViewer(root, environment = {}) {
    const Socket = environment.WebSocket || globalThis.WebSocket;
    const pageLocation = environment.location || globalThis.location;
    const later = environment.setTimeout || globalThis.setTimeout;
    const cancel = environment.clearTimeout || globalThis.clearTimeout;
    const output = root.querySelector('[data-log-output]');
    const status = root.querySelector('[data-log-status]');
    const connecting = root.querySelector('[data-log-connecting]');
    const pause = root.querySelector('[data-log-pause]');
    const clear = root.querySelector('[data-log-clear]');
    const follow = root.querySelector('[data-log-follow]');
    let socket, retry, opened = false, disposed = false, paused = false;
    let lines = [], last = 0, generation = 0;

    function setStatus(message, busy = false) {
        status.textContent = message;
        connecting.hidden = !busy;
    }

    function render() {
        if (paused) return;
        output.textContent = lines.join('\n');
        if (follow.checked) output.scrollTop = output.scrollHeight;
    }

    function retryConnection(token) {
        if (!opened || disposed || token !== generation || retry !== undefined) return;
        setStatus('Disconnected. Reconnecting…', true);
        retry = later(() => {
            if (!opened || disposed || token !== generation) return;
            retry = undefined;
            connect(token);
        }, 3000);
    }

    function connect(token) {
        if (!opened || disposed || token !== generation || socket) return;
        setStatus('Connecting…', true);
        let current;
        try {
            current = new Socket(`${pageLocation.protocol === 'https:' ? 'wss:' : 'ws:'}//${pageLocation.host}${root.dataset.socketPath}`);
        } catch {
            retryConnection(token);
            return;
        }
        socket = current;
        const isCurrent = () => opened && !disposed && token === generation && socket === current;
        current.onopen = () => {
            if (isCurrent()) setStatus('Live');
        };
        current.onmessage = event => {
            if (!isCurrent()) return;
            let data;
            try { data = JSON.parse(event.data); } catch { return; }
            if (data.type !== 'logs' || !Array.isArray(data.entries)) return;
            for (const entry of data.entries) {
                if (!entry || !Number.isSafeInteger(entry.id) || entry.id <= last
                    || !['time', 'level', 'logger', 'message'].every(key => typeof entry[key] === 'string')) continue;
                last = entry.id;
                lines.push(formatLog(entry));
            }
            lines = lines.slice(-MAX_LINES);
            render();
        };
        current.onclose = event => {
            if (!isCurrent()) return;
            socket = undefined;
            if ([4403, 1008].includes(event.code)) {
                setStatus('Access denied or session expired.');
                return;
            }
            retryConnection(token);
        };
        current.onerror = () => {
            if (isCurrent()) setStatus('Connection unavailable. Waiting to reconnect…', true);
        };
    }

    function open() {
        if (opened || disposed) return;
        opened = true;
        lines = [];
        last = 0;
        paused = false;
        pause.textContent = 'Pause display';
        pause.setAttribute('aria-pressed', 'false');
        output.textContent = '';
        connect(++generation);
    }

    function close() {
        opened = false;
        ++generation;
        cancel(retry);
        retry = undefined;
        if (socket) {
            const current = socket;
            socket = undefined;
            current.onopen = current.onmessage = current.onclose = current.onerror = null;
            try { current.close(1000, 'Log viewer closed'); } catch { /* A pending connection can already be closed. */ }
        }
        lines = [];
        output.textContent = '';
        setStatus('Open the log viewer to connect.');
    }

    const onToggle = event => event.newState === 'open' ? open() : close();
    const onPause = () => {
        paused = !paused;
        pause.textContent = paused ? 'Resume display' : 'Pause display';
        pause.setAttribute('aria-pressed', String(paused));
        render();
    };
    const onClear = () => { lines = []; output.textContent = ''; };
    root.addEventListener('toggle', onToggle);
    pause.addEventListener('click', onPause);
    clear.addEventListener('click', onClear);
    follow.addEventListener('change', render);
    if (root.matches(':popover-open')) open();
    else setStatus('Open the log viewer to connect.');

    return {
        open, close,
        dispose() {
            if (disposed) return;
            close();
            disposed = true;
            root.removeEventListener('toggle', onToggle);
            pause.removeEventListener('click', onPause);
            clear.removeEventListener('click', onClear);
            follow.removeEventListener('change', render);
        },
    };
}

function mount() {
    const roots = new Set(document.querySelectorAll('[data-admin-logs]'));
    for (const [root, viewer] of viewers) {
        if (!roots.has(root)) { viewer.dispose(); viewers.delete(root); }
    }
    for (const root of roots) {
        if (!viewers.has(root)) viewers.set(root, createLogViewer(root));
    }
}

if (typeof document !== 'undefined') {
    document.addEventListener('DOMContentLoaded', mount);
    document.addEventListener('htmx:after:settle', mount);
    document.addEventListener('htmx:before:cleanup', event => {
        for (const [root, viewer] of viewers) {
            if (event.target === root || event.target.contains?.(root)) {
                viewer.dispose(); viewers.delete(root);
            }
        }
    });
    window.addEventListener('pagehide', () => {
        for (const viewer of viewers.values()) viewer.dispose();
        viewers.clear();
    });
    // A restored back/forward cache document reconnects only if its popover remains open.
    window.addEventListener('pageshow', mount);
    mount();
}
