// Page lifecycle owns its socket: no subscription outside the staff viewer.
let active;
export function formatLog(entry) {
    return `${entry.time} ${entry.level} ${entry.logger}: ${entry.message}`;
}
function mount() {
    const root = document.querySelector('[data-admin-logs]');
    if (active?.root === root) return;
    active?.dispose(); active = null;
    if (!root) return;
    const output = root.querySelector('[data-log-output]');
    const status = root.querySelector('[data-log-status]');
    const pause = root.querySelector('[data-log-pause]');
    const follow = root.querySelector('[data-log-follow]');
    let socket, retry, stopped = false, paused = false, lines = [], last = 0;
    function render() {
        if (paused) return;
        output.textContent = lines.join('\n');
        if (follow.checked) output.scrollTop = output.scrollHeight;
    }
    function connect() {
        if (stopped) return;
        socket = new WebSocket(`${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}${root.dataset.socketPath}`);
        socket.onopen = () => { status.textContent = 'Live'; };
        socket.onmessage = event => {
            let data;
            try { data = JSON.parse(event.data); } catch { return; }
            if (data.type !== 'logs' || !Array.isArray(data.entries)) return;
            for (const entry of data.entries) {
                if (entry.id <= last) continue;
                last = entry.id; lines.push(formatLog(entry));
            }
            lines = lines.slice(-1000); render();
        };
        socket.onclose = event => {
            if (stopped) return;
            if ([4403, 1008].includes(event.code)) { status.textContent = 'Access denied or session expired.'; return; }
            status.textContent = 'Disconnected. Reconnecting…';
            retry = setTimeout(connect, 3000);
        };
        socket.onerror = () => { status.textContent = 'Connection unavailable.'; };
    }
    pause.onclick = () => { paused = !paused; pause.textContent = paused ? 'Resume display' : 'Pause display'; render(); };
    root.querySelector('[data-log-clear]').onclick = () => { lines = []; output.textContent = ''; };
    active = {root, dispose() { stopped = true; clearTimeout(retry); socket?.close(); }};
    connect();
}
if (typeof document !== 'undefined') {
    document.addEventListener('DOMContentLoaded', mount);
    document.addEventListener('htmx:afterSettle', mount);
    document.addEventListener('htmx:beforeCleanupElement', event => {
        if (active && (event.detail.elt === active.root || event.detail.elt.contains(active.root))) {
            active.dispose(); active = null;
        }
    });
    window.addEventListener('pagehide', () => active?.dispose());
    mount();
}
