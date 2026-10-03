// Source previews stay private and load near the viewport. Two concurrent
// requests bound server work when a page lists many previously unseen models.
const SHARED = Symbol.for('cadevil.buildingThumbnails');
const SELECTOR = '[data-building-thumbnail]';
const MAX_BYTES = 2 * 1024 * 1024;

export function safeThumbnailUrl(value, origin) {
    if (typeof value !== 'string' || !value.trim()) return null;
    try {
        const url = new URL(value, origin);
        if (url.origin !== origin || !['https:', 'http:'].includes(url.protocol) || url.username || url.password || url.hash) return null;
        return url.pathname + url.search;
    } catch { return null; }
}

export function createThumbnailElement(documentRoot, url, label, className = '') {
    const host = documentRoot.createElement('span');
    host.className = ('model-thumbnail ' + className).trim();
    host.setAttribute('data-building-thumbnail', '');
    const image = documentRoot.createElement('img');
    image.setAttribute('data-thumbnail-src', url || '');
    image.setAttribute('alt', 'Preview of ' + (label || 'building model'));
    image.setAttribute('width', '320'); image.setAttribute('height', '200');
    image.setAttribute('loading', 'lazy'); image.setAttribute('decoding', 'async');
    image.hidden = true;
    const placeholder = documentRoot.createElement('span');
    placeholder.className = 'model-thumbnail-state';
    placeholder.setAttribute('data-thumbnail-state', '');
    placeholder.textContent = 'Building preview';
    host.append(image, placeholder);
    return host;
}

export function createThumbnailController(documentRoot, options = {}) {
    const windowRoot = documentRoot.defaultView;
    const fetchImage = options.fetch || windowRoot.fetch.bind(windowRoot);
    const Observer = options.IntersectionObserver ?? windowRoot.IntersectionObserver;
    const createUrl = options.createObjectURL || windowRoot.URL.createObjectURL.bind(windowRoot.URL);
    const revokeUrl = options.revokeObjectURL || windowRoot.URL.revokeObjectURL.bind(windowRoot.URL);
    const concurrency = Math.max(1, Math.min(4, options.concurrency || 2));
    const records = new Map(), jobs = new Map(), queue = [];
    let active = 0, disposed = false;

    function mark(record, state, message) {
        record.host.dataset.thumbnailState = state;
        record.placeholder.hidden = state === 'ready';
        record.placeholder.textContent = message || '';
        if (state !== 'ready') record.image.hidden = true;
    }

    function show(record, job) {
        if (!records.has(record.host) || record.job !== job || disposed) return;
        if (job.state === 'error') { mark(record, 'unavailable', 'Preview unavailable'); return; }
        if (job.state !== 'ready') { mark(record, 'loading', 'Preparing preview…'); return; }
        record.image.onload = () => {
            if (records.has(record.host) && record.job === job && !disposed) mark(record, 'ready');
        };
        record.image.onerror = () => {
            if (records.has(record.host) && record.job === job && !disposed) mark(record, 'unavailable', 'Preview unavailable');
        };
        mark(record, 'loading', 'Loading preview…');
        // The remote fetch is already bounded by the queue. Eagerly decode the
        // local blob so a hidden/lazy image cannot keep its placeholder forever.
        record.image.setAttribute('loading', 'eager');
        record.image.hidden = false;
        record.image.src = job.objectUrl;
    }

    function retire(job) {
        if (job.records.size) return;
        if (jobs.get(job.url) === job) jobs.delete(job.url);
        if (job.objectUrl) { revokeUrl(job.objectUrl); job.objectUrl = null; }
        job.abort?.abort();
        job.retired = true;
    }

    async function run(job) {
        active++;
        job.state = 'loading';
        job.abort = new AbortController();
        for (const record of job.records) show(record, job);
        try {
            const response = await fetchImage(job.url, { signal: job.abort.signal, credentials: 'same-origin', redirect: 'error', headers: { Accept: 'image/png' } });
            if (!response.ok || response.headers.get('content-type')?.split(';')[0].trim().toLowerCase() !== 'image/png') throw new Error('Preview unavailable');
            const declared = Number(response.headers.get('content-length'));
            if (Number.isFinite(declared) && declared > MAX_BYTES) throw new Error('Preview too large');
            const blob = await response.blob();
            if (!blob.size || blob.size > MAX_BYTES) throw new Error('Preview unavailable');
            if (disposed || job.retired || job.abort.signal.aborted) return;
            job.objectUrl = createUrl(blob);
            job.state = 'ready';
        } catch {
            if (disposed || job.retired || job.abort.signal.aborted) return;
            job.state = 'error';
        } finally {
            active--;
            if (!disposed && !job.retired) for (const record of job.records) show(record, job);
            pump();
        }
    }

    function pump() {
        if (disposed) return;
        while (active < concurrency && queue.length) {
            const job = queue.shift();
            if (!job.retired && job.records.size) void run(job);
        }
    }

    function activate(record) {
        if (disposed || record.job || !records.has(record.host)) return;
        observer?.unobserve(record.host);
        let job = jobs.get(record.url);
        if (!job) {
            job = { url: record.url, state: 'queued', records: new Set(), objectUrl: null, retired: false };
            jobs.set(record.url, job);
            queue.push(job);
        }
        job.records.add(record); record.job = job;
        show(record, job);
        pump();
    }

    const observer = Observer ? new Observer(entries => {
        for (const entry of entries) if (entry.isIntersecting) {
            const record = records.get(entry.target);
            if (record) activate(record);
        }
    }, { rootMargin: '200px 0px' }) : null;

    function mount(root = documentRoot) {
        if (disposed) return;
        const hosts = [...(root.matches?.(SELECTOR) ? [root] : []), ...root.querySelectorAll(SELECTOR)];
        for (const host of hosts) {
            if (records.has(host)) continue;
            const image = host.querySelector('img[data-thumbnail-src]');
            const placeholder = host.querySelector('[data-thumbnail-state]');
            if (!image || !placeholder) continue;
            const url = safeThumbnailUrl(image.getAttribute('data-thumbnail-src'), windowRoot.location.origin);
            const record = { host, image, placeholder, url, job: null };
            records.set(host, record);
            if (!url) { mark(record, 'unavailable', 'Preview unavailable'); continue; }
            mark(record, 'idle', 'Building preview');
            if (observer) observer.observe(host);
            else activate(record);
        }
    }

    function removeWithin(root) {
        for (const [host, record] of records) {
            if (host !== root && !root.contains?.(host)) continue;
            observer?.unobserve(host);
            record.image.onload = record.image.onerror = null;
            record.image.removeAttribute('src');
            record.image.hidden = true;
            records.delete(host);
            if (record.job) { record.job.records.delete(record); retire(record.job); }
        }
        pump();
    }

    return { mount, removeWithin, get active() { return active; }, get size() { return records.size; },
        dispose() {
            if (disposed) return;
            disposed = true;
            observer?.disconnect();
            for (const record of records.values()) {
                record.image.onload = record.image.onerror = null;
                record.image.removeAttribute('src'); record.image.hidden = true;
            }
            records.clear();
            for (const job of jobs.values()) { job.records.clear(); retire(job); }
            jobs.clear(); queue.length = 0;
        },
    };
}

function controller(documentRoot = globalThis.document) {
    if (!documentRoot) return null;
    const windowRoot = documentRoot.defaultView;
    return windowRoot[SHARED] ||= createThumbnailController(documentRoot);
}
export function mountBuildingThumbnailsWithin(root) {
    const documentRoot = root?.ownerDocument || root;
    controller(documentRoot)?.mount(root);
}
export function disposeBuildingThumbnailsWithin(root) {
    const documentRoot = root?.ownerDocument || root;
    documentRoot?.defaultView?.[SHARED]?.removeWithin(root);
}

if (typeof document !== 'undefined') {
    document.addEventListener('DOMContentLoaded', () => controller()?.mount());
    document.addEventListener('htmx:after:settle', () => controller()?.mount());
    document.addEventListener('htmx:before:cleanup', event => disposeBuildingThumbnailsWithin(event.target));
    window.addEventListener('pagehide', () => { window[SHARED]?.dispose(); delete window[SHARED]; });
    window.addEventListener('pageshow', () => controller()?.mount());
    controller()?.mount();
}
