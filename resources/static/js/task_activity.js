// Keep task feedback outside the swapped page. Durations are elapsed time,
// not estimates of server progress or a promise of completion.
export function elapsedLabel(milliseconds) {
    const seconds = Math.max(0, Math.floor(milliseconds / 1000));
    return seconds < 60 ? `${seconds}s elapsed` : `${Math.floor(seconds / 60)}m ${seconds % 60}s elapsed`;
}

export function requestLabel(ctx) {
    const source = ctx?.request?.submitter || ctx?.sourceElement;
    const annotated = source?.closest?.('[data-task-label]') || ctx?.sourceElement?.closest?.('[data-task-label]');
    return annotated?.dataset?.taskLabel || (ctx?.request?.method === 'GET' ? 'Loading page' : 'Saving changes');
}

export function createActivityController(documentRoot, options = {}) {
    const windowRoot = documentRoot.defaultView;
    const now = options.now || (() => Date.now());
    const setTimer = options.setInterval || windowRoot.setInterval.bind(windowRoot);
    const clearTimer = options.clearInterval || windowRoot.clearInterval.bind(windowRoot);
    const defer = options.defer || ((callback) => queueMicrotask(callback));
    const tasks = new Map();
    const requests = new Map();
    const busyTargets = new Map();
    const busyForms = new Map();
    const listeners = [];
    let sequence = 0;
    let timer = null;

    function setBusy(target, active) {
        if (!target?.setAttribute) return;
        const state = busyTargets.get(target);
        if (active) {
            if (state) state.count += 1;
            else busyTargets.set(target, { count: 1, previous: target.getAttribute('aria-busy') });
            target.setAttribute('aria-busy', 'true');
        } else if (state && --state.count === 0) {
            if (state.previous === null) target.removeAttribute('aria-busy');
            else target.setAttribute('aria-busy', state.previous);
            busyTargets.delete(target);
        }
    }

    function markForm(form, active) {
        if (!form?.matches?.('form[data-task-label]')) return;
        let state = busyForms.get(form);
        if (active) {
            if (state) { state.count += 1; return; }
            state = { count: 1, controls: new Map() };
            busyForms.set(form, state);
            form.setAttribute('data-task-running', 'true');
            for (const control of form.querySelectorAll('button[type="submit"], button:not([type]), input[type="submit"], input[type="image"]')) {
                state.controls.set(control, control.getAttribute('aria-disabled'));
                // Disabling successful controls can remove their names/values
                // from form submissions. Prevent repeats at the event boundary.
                control.setAttribute('aria-disabled', 'true');
            }
        } else if (state && --state.count === 0) {
            for (const [control, previous] of state.controls) {
                if (previous === null) control.removeAttribute('aria-disabled');
                else control.setAttribute('aria-disabled', previous);
            }
            form.removeAttribute('data-task-running');
            busyForms.delete(form);
        }
    }

    function render() {
        const panel = documentRoot.getElementById('task-activity');
        if (!panel) return;
        panel.hidden = tasks.size === 0;
        const status = panel.querySelector('.task-activity-status');
        const summary = tasks.size === 1 ? tasks.values().next().value.label
            : tasks.size ? `${tasks.size} tasks in progress` : '';
        // Only announce task changes; reading a stopwatch every second is noisy.
        if (status && status.textContent !== summary) status.textContent = summary;
        const list = panel.querySelector('.task-activity-list');
        if (!list) return;
        list.replaceChildren(...Array.from(tasks.values(), (task) => {
            const item = documentRoot.createElement('li');
            item.className = 'task-activity-item';
            const spinner = documentRoot.createElement('span');
            spinner.className = 'task-activity-spinner';
            spinner.setAttribute('aria-hidden', 'true');
            const label = documentRoot.createElement('span');
            label.className = 'task-activity-label';
            label.textContent = task.label;
            const elapsed = documentRoot.createElement('span');
            elapsed.className = 'task-activity-elapsed';
            elapsed.textContent = elapsedLabel(now() - task.started);
            item.append(spinner, label, elapsed);
            return item;
        }));
    }

    function start({ label = 'Working', target = null, source = null } = {}) {
        const token = ++sequence;
        const form = source?.matches?.('form') ? source : source?.form || source?.closest?.('form');
        tasks.set(token, { label, target, form, started: now(), cleanup: [] });
        setBusy(target, true);
        markForm(form, true);
        if (timer === null) timer = setTimer(render, 1000);
        render();
        return token;
    }

    function update(token, values = {}) {
        const task = tasks.get(token);
        if (task && typeof values.label === 'string' && values.label) {
            task.label = values.label;
            render();
        }
    }

    function finish(token) {
        const task = tasks.get(token);
        if (!task) return;
        tasks.delete(token);
        for (const cleanup of task.cleanup) cleanup();
        setBusy(task.target, false);
        markForm(task.form, false);
        if (tasks.size === 0 && timer !== null) { clearTimer(timer); timer = null; }
        render();
    }

    function finishRequest(ctx) {
        const token = requests.get(ctx);
        requests.delete(ctx);
        finish(token);
    }

    function onBeforeRequest(event) {
        const ctx = event.detail?.ctx;
        if (!ctx || event.defaultPrevented || requests.has(ctx)) return;
        const source = ctx.sourceElement;
        const annotated = source?.closest?.('[data-task-label]');
        const content = documentRoot.getElementById('content-container');
        if (!annotated && ctx.target !== content && !content?.contains(ctx.target)) return;
        const token = start({ label: requestLabel(ctx), target: ctx.target, source });
        requests.set(ctx, token);
        const done = (completion) => {
            if (completion.detail?.ctx === ctx) finishRequest(ctx);
        };
        // Track completion on the original source as well as the document;
        // the request context identifies it across outerHTML replacements.
        source?.addEventListener('htmx:finally:request', done);
        const aborted = () => finishRequest(ctx);
        ctx.request?.signal?.addEventListener('abort', aborted, { once: true });
        tasks.get(token).cleanup.push(() => {
            source?.removeEventListener('htmx:finally:request', done);
            ctx.request?.signal?.removeEventListener('abort', aborted);
            requests.delete(ctx);
        });
        if (ctx.request?.signal?.aborted) finishRequest(ctx);
    }

    function onSubmit(event) {
        const form = event.target;
        if (!form?.matches?.('form[data-task-label]')) return;
        if (busyForms.has(form)) {
            event.preventDefault();
            event.stopImmediatePropagation();
            return;
        }
        // Native fallback remains useful without HTMX. Its navigation owns
        // completion; pageshow resets state when returning from browser history.
        defer(() => {
            if (event.defaultPrevented || (form.target && form.target !== '_self')) return;
            start({ label: form.dataset.taskLabel, target: form, source: form });
        });
    }

    function onConfigRequest(event) {
        const ctx = event.detail?.ctx;
        const source = ctx?.sourceElement;
        const form = source?.matches?.('form') ? source : source?.form || source?.closest?.('form');
        if (busyForms.has(form)) event.preventDefault();
    }

    function onDownload(event) {
        const link = event.target.closest?.('[data-download-notice]');
        if (!link || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        const notice = documentRoot.getElementById('request-notice');
        if (notice) {
            // Native downloads do not expose transfer completion to this page.
            notice.textContent = 'Your browser will handle this download. If it does not start, try the link again.';
            notice.hidden = false;
        }
    }

    function reset() {
        for (const token of Array.from(tasks.keys())) finish(token);
    }

    function listen(target, name, handler, capture = false) {
        target.addEventListener(name, handler, capture);
        listeners.push(() => target.removeEventListener(name, handler, capture));
    }
    listen(documentRoot, 'htmx:before:request', onBeforeRequest);
    listen(documentRoot, 'htmx:finally:request', (event) => finishRequest(event.detail?.ctx));
    listen(documentRoot, 'htmx:config:request', onConfigRequest);
    listen(documentRoot, 'submit', onSubmit, true);
    listen(documentRoot, 'click', onDownload);
    listen(windowRoot, 'pageshow', (event) => { if (event.persisted) reset(); });
    listen(windowRoot, 'pagehide', reset);
    return { start, update, finish, reset, render, get size() { return tasks.size; },
        dispose() { reset(); for (const remove of listeners) remove(); } };
}

let controller = null;
function activeController() {
    if (!controller && typeof document !== 'undefined') {
        const sharedKey = Symbol.for('cadevil.taskActivity');
        controller = document.defaultView?.[sharedKey] || createActivityController(document);
        if (document.defaultView) document.defaultView[sharedKey] = controller;
    }
    return controller;
}
export function startTask(options) { return activeController()?.start(options); }
export function updateTask(token, values) { activeController()?.update(token, values); }
export function finishTask(token) { activeController()?.finish(token); }

if (typeof window !== 'undefined' && typeof document !== 'undefined') {
    window.CadevilActivity = { start: startTask, update: updateTask, finish: finishTask };
    activeController();
}
