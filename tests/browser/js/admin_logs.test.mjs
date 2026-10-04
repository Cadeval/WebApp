import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
const source = readFileSync(new URL('./admin_logs.js', import.meta.url), 'utf8');
const {createLogViewer} = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));

function element(properties = {}) {
    const listeners = new Map();
    return {
        textContent: '', hidden: false, attributes: {}, ...properties,
        addEventListener(name, callback) { listeners.set(name, callback); },
        removeEventListener(name, callback) { if (listeners.get(name) === callback) listeners.delete(name); },
        setAttribute(name, value) { this.attributes[name] = value; },
        emit(name, data = {}) { listeners.get(name)?.(data); },
        listenerCount() { return listeners.size; },
    };
}
function setup({initiallyOpen = false, constructionFails = false} = {}) {
    const fields = {};
    for (const key of ['output', 'status', 'connecting', 'pause', 'clear', 'follow']) {
        fields[key] = element({checked: true, scrollTop: 0, scrollHeight: 20});
    }
    const root = element({
        dataset: {socketPath: '/ws/logs'},
        querySelector: selector => fields[selector.match(/data-log-(\w+)/)[1]],
        matches: selector => selector === ':popover-open' && initiallyOpen,
    });
    const sockets = [], timers = new Map();
    let timerId = 0;
    const environment = {
        WebSocket: class {
            constructor(url) {
                if (constructionFails) throw new Error('Unavailable');
                this.url = url;
                sockets.push(this);
            }
            close(code, reason) { this.closed = true; this.code = code; this.reason = reason; }
        },
        location: {protocol: 'https:', host: 'testserver'},
        setTimeout(callback, delay) { const id = ++timerId; timers.set(id, {callback, delay}); return id; },
        clearTimeout(id) { timers.delete(id); },
    };
    const viewer = createLogViewer(root, environment);
    return {root, fields, sockets, timers, viewer, runTimer() {
        const [id, timer] = timers.entries().next().value;
        timers.delete(id);
        timer.callback();
    }};
}
const entry = (id, message) => ({id, time: 'now', level: 'INFO', logger: 'app', message});
function logs(socket, entries) { socket.onmessage({data: JSON.stringify({type: 'logs', entries})}); }

test('hidden popover does not subscribe; native opening subscribes once with connection feedback', () => {
    const {root, viewer, fields, sockets} = setup();
    assert.equal(sockets.length, 0);
    assert.equal(fields.connecting.hidden, true);
    root.emit('toggle', {newState: 'open'});
    assert.equal(sockets.length, 1);
    assert.equal(sockets[0].url, 'wss://testserver/ws/logs');
    assert.equal(fields.status.textContent, 'Connecting…');
    assert.equal(fields.connecting.hidden, false);
    viewer.open(); root.emit('toggle', {newState: 'open'});
    assert.equal(sockets.length, 1);
    sockets[0].onopen();
    assert.equal(fields.status.textContent, 'Live');
    assert.equal(fields.connecting.hidden, true);
    viewer.dispose();
});

test('display uses plain text, pauses, clears, deduplicates and bounds its buffer', () => {
    const {viewer, fields, sockets} = setup(); viewer.open();
    const socket = sockets[0];
    logs(socket, [entry(1, '<script>literal log</script>')]);
    assert.equal(fields.output.textContent, 'now INFO app: <script>literal log</script>');
    fields.pause.emit('click');
    assert.equal(fields.pause.attributes['aria-pressed'], 'true');
    logs(socket, [entry(2, 'Paused update')]);
    assert(!fields.output.textContent.includes('Paused update'));
    fields.pause.emit('click');
    assert(fields.output.textContent.includes('Paused update'));
    assert.equal(fields.pause.attributes['aria-pressed'], 'false');
    logs(socket, [entry(2, 'Duplicate')]);
    assert(!fields.output.textContent.includes('Duplicate'));
    logs(socket, Array.from({length: 1100}, (_, index) => entry(index + 3, 'line')));
    assert.equal(fields.output.textContent.split('\n').length, 1000);
    fields.clear.emit('click');
    assert.equal(fields.output.textContent, '');
    logs(socket, [entry(1102, 'Already seen')]);
    assert.equal(fields.output.textContent, '');
    viewer.dispose();
});

test('follow latest can be disabled and re-enabled without losing entries', () => {
    const {viewer, fields, sockets} = setup(); viewer.open();
    fields.follow.checked = false;
    logs(sockets[0], [entry(1, 'First')]);
    assert.equal(fields.output.scrollTop, 0);
    fields.follow.checked = true; fields.follow.emit('change');
    assert.equal(fields.output.scrollTop, 20);
    viewer.dispose();
});

test('Escape, close button and outside dismissal all stop and clear the connection via native toggle', () => {
    const {viewer, root, fields, sockets, timers} = setup(); viewer.open();
    const socket = sockets[0];
    const delayedMessage = socket.onmessage;
    logs(socket, [entry(8, 'Previous session')]);
    root.emit('toggle', {newState: 'closed'});
    assert.equal(socket.closed, true);
    assert.equal(socket.code, 1000);
    assert.equal(fields.output.textContent, '');
    assert.equal(timers.size, 0);
    delayedMessage({data: JSON.stringify({type: 'logs', entries: [entry(9, 'Late event')]})});
    assert.equal(fields.output.textContent, '');
    viewer.dispose();
});

test('reopening begins a clean recent-log buffer and ignores stale connection events', () => {
    const {viewer, fields, sockets} = setup(); viewer.open();
    const previousOpen = sockets[0].onopen;
    logs(sockets[0], [entry(9, 'Old buffer')]); fields.pause.emit('click');
    viewer.close(); viewer.open();
    assert.equal(sockets.length, 2);
    previousOpen();
    assert.equal(fields.status.textContent, 'Connecting…');
    logs(sockets[1], [entry(1, 'Fresh recent entry')]);
    assert.equal(fields.output.textContent, 'now INFO app: Fresh recent entry');
    assert.equal(fields.pause.textContent, 'Pause display');
    assert.equal(fields.pause.attributes['aria-pressed'], 'false');
    viewer.dispose();
});

test('reconnect is unique, runs only while open, and dismissal cancels pending work', () => {
    const {viewer, fields, sockets, timers, runTimer} = setup(); viewer.open();
    const disconnected = sockets[0].onclose;
    disconnected({code: 1006}); disconnected({code: 1006});
    assert.equal(timers.size, 1);
    assert.equal(fields.connecting.hidden, false);
    assert.equal([...timers.values()][0].delay, 3000);
    runTimer();
    assert.equal(sockets.length, 2);
    sockets[1].onclose({code: 1006});
    const delayedRetry = [...timers.values()][0].callback;
    viewer.close();
    assert.equal(timers.size, 0);
    delayedRetry();
    assert.equal(sockets.length, 2);
    viewer.dispose();
});

test('permission expiry does not reconnect or continue streaming', () => {
    for (const code of [4403, 1008]) {
        const {viewer, fields, sockets, timers} = setup(); viewer.open();
        sockets[0].onclose({code});
        assert.equal(fields.status.textContent, 'Access denied or session expired.');
        assert.equal(fields.connecting.hidden, true);
        assert.equal(timers.size, 0);
        viewer.open(); assert.equal(sockets.length, 1);
        viewer.dispose();
    }
});

test('a delayed previous-session retry cannot hide a new session timer from dismissal', () => {
    const {viewer, sockets, timers} = setup(); viewer.open();
    sockets[0].onclose({code: 1006});
    const oldRetry = [...timers.values()][0].callback;
    viewer.close(); viewer.open();
    sockets[1].onclose({code: 1006});
    assert.equal(timers.size, 1);
    oldRetry();
    viewer.close();
    assert.equal(timers.size, 0);
    assert.equal(sockets.length, 2);
    viewer.dispose();
});

test('connection construction failure schedules bounded retry that is cancelled on close', () => {
    const {viewer, fields, timers} = setup({constructionFails: true}); viewer.open();
    assert.equal(timers.size, 1);
    assert.equal(fields.status.textContent, 'Disconnected. Reconnecting…');
    viewer.close(); assert.equal(timers.size, 0);
    viewer.dispose();
});

test('malformed records do not corrupt the cursor or enter the text buffer', () => {
    const {viewer, fields, sockets} = setup(); viewer.open();
    sockets[0].onmessage({data: 'not json'});
    sockets[0].onmessage({data: '{"type":"other","entries":[]}'});
    logs(sockets[0], [null, {...entry(100, 'Bad'), time: {}}, entry('200', 'Bad ID'), entry(1, 'Valid')]);
    assert.equal(fields.output.textContent, 'now INFO app: Valid');
    viewer.dispose();
});

test('already-open restored popover subscribes and disposal removes controls and stops future opens', () => {
    const {viewer, fields, sockets, root} = setup({initiallyOpen: true});
    assert.equal(sockets.length, 1);
    viewer.dispose(); viewer.dispose();
    assert.equal(root.listenerCount(), 0);
    for (const key of ['pause', 'clear', 'follow']) assert.equal(fields[key].listenerCount(), 0);
    viewer.open(); assert.equal(sockets.length, 1);
});

test('persistent shell survives content swaps, while full cleanup and page hide stop its subscription', async () => {
    const fixture = setup(); fixture.viewer.dispose();
    const documentEvents = {}, windowEvents = {}, sockets = [];
    globalThis.document = {
        querySelectorAll: () => [fixture.root],
        addEventListener: (name, callback) => { documentEvents[name] = callback; },
    };
    globalThis.window = {addEventListener: (name, callback) => { windowEvents[name] = callback; }};
    globalThis.location = {protocol: 'http:', host: 'testserver'};
    globalThis.WebSocket = class {
        constructor(url) { this.url = url; sockets.push(this); }
        close() { this.closed = true; }
    };
    try {
        await import('data:text/javascript;base64,' + Buffer.from(source + '\n// shell lifecycle test').toString('base64'));
        assert.equal(sockets.length, 0);
        fixture.root.emit('toggle', {newState: 'open'});
        assert.equal(sockets.length, 1);
        assert.equal(sockets[0].url, 'ws://testserver/ws/logs');
        documentEvents['htmx:after:settle'](); documentEvents['htmx:after:settle']();
        assert.equal(sockets.length, 1);
        documentEvents['htmx:before:cleanup']({target: {contains: () => false}});
        assert.equal(sockets[0].closed, undefined);
        documentEvents['htmx:before:cleanup']({target: {contains: item => item === fixture.root}});
        assert.equal(sockets[0].closed, true);
        documentEvents['htmx:after:settle']();
        fixture.root.emit('toggle', {newState: 'open'});
        assert.equal(sockets.length, 2);
        windowEvents.pagehide(); assert.equal(sockets[1].closed, true);
        windowEvents.pageshow();
        assert.equal(sockets.length, 2);
    } finally {
        for (const key of ['document', 'window', 'location', 'WebSocket']) delete globalThis[key];
    }
});
