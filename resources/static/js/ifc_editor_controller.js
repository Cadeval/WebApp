const DEFAULT_PAGE_SIZE = 25;
const MAX_LIST_LIMIT = 100;
const MAX_CLIENT_IFC_BYTES = 32 * 1024 * 1024;
const OPERATION_TIMEOUT_MS = 8000;

const KIND_LABELS = Object.freeze([
    'Read-only',
    'String',
    'Number',
    'Enum / logical',
    'Reference',
    'Unset',
]);

const RESPONSE_TYPE_BY_REQUEST = Object.freeze({
    load: 'loaded',
    list: 'entities',
    inspect: 'entity',
    update: 'updated',
    export: 'exported',
});

export function withReloadVersion(url, generation) {
    const separator = url.includes('?') ? '&' : '?';
    return `${url}${separator}cadevilIfcReload=${generation}`;
}

function requireSameOrigin(url) {
    if (typeof globalThis.location === 'undefined') return url;
    const resolved = new URL(url, globalThis.location.href);
    if (resolved.origin !== globalThis.location.origin) {
        throw new Error('IFC editor assets must use the application origin.');
    }
    return url;
}

function isBoundedInteger(value, minimum, maximum) {
    return Number.isInteger(value) && value >= minimum && value <= maximum;
}

function isEntitySummary(value) {
    return Boolean(value)
        && typeof value === 'object'
        && isBoundedInteger(value.index, 0, Number.MAX_SAFE_INTEGER)
        && isBoundedInteger(value.id, 0, Number.MAX_SAFE_INTEGER)
        && typeof value.entityType === 'string';
}

function isAttribute(value) {
    return Boolean(value)
        && typeof value === 'object'
        && isBoundedInteger(value.index, 0, Number.MAX_SAFE_INTEGER)
        && isBoundedInteger(value.kind, 0, 5)
        && typeof value.value === 'string';
}

export function isValidLoadedMessage(message) {
    return Boolean(message)
        && typeof message.filename === 'string'
        && message.filename.length > 0
        && isBoundedInteger(message.entityCount, 0, Number.MAX_SAFE_INTEGER);
}

export function isValidEntitiesMessage(message) {
    return Boolean(message)
        && isBoundedInteger(message.offset, 0, Number.MAX_SAFE_INTEGER)
        && isBoundedInteger(message.total, 0, Number.MAX_SAFE_INTEGER)
        && Array.isArray(message.entities)
        && message.entities.length <= MAX_LIST_LIMIT
        && message.entities.every(isEntitySummary);
}

export function isValidEntityMessage(message) {
    return Boolean(message)
        && isEntitySummary({ index: message.index, id: message.id, entityType: message.entityType })
        && Array.isArray(message.attributes)
        && message.attributes.every(isAttribute);
}

export function isValidUpdatedMessage(message) {
    return Boolean(message)
        && isBoundedInteger(message.entityId, 0, Number.MAX_SAFE_INTEGER)
        && isBoundedInteger(message.attributeIndex, 0, Number.MAX_SAFE_INTEGER)
        && isBoundedInteger(message.kind, 0, 5)
        && typeof message.value === 'string';
}

export function isValidExportedMessage(message) {
    return Boolean(message)
        && typeof message.filename === 'string'
        && message.bytes instanceof ArrayBuffer;
}

function kindLabel(kind) {
    return KIND_LABELS[kind] ?? 'Unknown';
}

function control(root, kind, name) {
    return root.querySelector(`[data-ifc-${kind}="${name}"]`);
}

/**
 * Runs the host side of the sandboxed IFC editor worker: same-origin, cache-busted hot reload,
 * worker/generation/request identity guards, per-operation timeouts, and scoped HTMX lifecycle
 * cleanup. Dependencies are injectable so the full flow can be exercised deterministically in tests.
 */
export class IfcEditorRuntime {
    constructor({
        WorkerClass = globalThis.Worker,
        setTimer = globalThis.setTimeout?.bind(globalThis),
        clearTimer = globalThis.clearTimeout?.bind(globalThis),
        operationTimeoutMs = OPERATION_TIMEOUT_MS,
        documentRef = globalThis.document,
        BlobClass = globalThis.Blob,
        createObjectUrl = (blob) => globalThis.URL.createObjectURL(blob),
        revokeObjectUrl = (url) => globalThis.URL.revokeObjectURL(url),
    } = {}) {
        this.WorkerClass = WorkerClass;
        this.setTimer = setTimer;
        this.clearTimer = clearTimer;
        this.operationTimeoutMs = operationTimeoutMs;
        this.documentRef = documentRef;
        this.BlobClass = BlobClass;
        this.createObjectUrl = createObjectUrl;
        this.revokeObjectUrl = revokeObjectUrl;
        this.controllers = new Map();
    }

    mount(scope) {
        const roots = [];
        if (scope?.matches?.('[data-ifc-editor]')) roots.push(scope);
        for (const root of scope?.querySelectorAll?.('[data-ifc-editor]') ?? []) roots.push(root);
        for (const root of roots) {
            if (!this.controllers.has(root)) this.mountEditor(root);
        }
    }

    mountEditor(root) {
        const elements = {
            status: control(root, 'field', 'status'),
            filenameField: control(root, 'field', 'filename'),
            countField: control(root, 'field', 'count'),
            generationField: control(root, 'field', 'generation'),
            pageField: control(root, 'field', 'page'),
            entityIdField: control(root, 'field', 'entity-id'),
            entityTypeField: control(root, 'field', 'entity-type'),
            inspectorPlaceholder: control(root, 'field', 'inspector-placeholder'),
            validationField: control(root, 'field', 'validation'),
            fileInput: root.querySelector('[data-ifc-file-input]'),
            dropzone: root.querySelector('[data-ifc-dropzone]'),
            reloadButton: control(root, 'action', 'reload'),
            downloadButton: control(root, 'action', 'download'),
            prevButton: control(root, 'action', 'prev-page'),
            nextButton: control(root, 'action', 'next-page'),
            jumpButton: control(root, 'action', 'jump'),
            jumpInput: root.querySelector('[data-ifc-jump-input]'),
            entityTable: root.querySelector('[data-ifc-entity-list]'),
            attributeTable: root.querySelector('[data-ifc-attribute-list]'),
        };
        if (Object.values(elements).some((element) => !element)
            || !root.dataset.workerUrl || !root.dataset.wasmUrl) {
            return;
        }
        const entityBody = elements.entityTable.querySelector('tbody');
        const attributeBody = elements.attributeTable.querySelector('tbody');
        if (!entityBody || !attributeBody) return;

        const controller = {
            root,
            ...elements,
            entityBody,
            attributeBody,
            generation: 0,
            worker: null,
            ready: false,
            requestCounter: 0,
            pending: new Map(),
            selectedBytes: null,
            selectedFilename: '',
            entityCount: 0,
            pageOffset: 0,
            pageSize: DEFAULT_PAGE_SIZE,
            selectedEntityIndex: null,
            selectedEntityId: null,
            jumpTargetId: null,
            activeDownloadUrl: null,
            downloadTimer: null,
            disposed: false,
            listeners: [],
        };

        this.wireControls(controller);
        this.controllers.set(root, controller);
        this.startWorker(controller);
    }

    on(controller, target, type, handler) {
        target.addEventListener(type, handler);
        controller.listeners.push({ target, type, handler });
    }

    wireControls(controller) {
        this.on(controller, controller.fileInput, 'change', () => {
            this.selectFile(controller, controller.fileInput.files?.[0]);
        });
        this.on(controller, controller.dropzone, 'dragover', (event) => event.preventDefault());
        this.on(controller, controller.dropzone, 'drop', (event) => {
            event.preventDefault();
            this.selectFile(controller, event.dataTransfer?.files?.[0]);
        });
        this.on(controller, controller.dropzone, 'keydown', (event) => {
            if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                controller.fileInput.click();
            }
        });
        this.on(controller, controller.reloadButton, 'click', () => this.startWorker(controller));
        this.on(controller, controller.downloadButton, 'click', () => this.requestExport(controller));
        this.on(controller, controller.prevButton, 'click', () => this.changePage(controller, -1));
        this.on(controller, controller.nextButton, 'click', () => this.changePage(controller, 1));
        this.on(controller, controller.jumpButton, 'click', () => this.jumpToId(controller));
        this.on(controller, controller.jumpInput, 'keydown', (event) => {
            if (event.key === 'Enter') {
                event.preventDefault();
                this.jumpToId(controller);
            }
        });
        this.on(controller, controller.entityBody, 'click', (event) => {
            const row = event.target?.closest?.('[data-index]');
            if (!row) return;
            this.selectEntity(controller, Number(row.dataset.index));
        });
        this.on(controller, controller.attributeBody, 'change', (event) => {
            const input = event.target?.closest?.('[data-ifc-attribute-input]');
            if (!input) return;
            this.updateAttribute(controller, Number(input.dataset.index), input.value);
        });
    }

    startWorker(controller) {
        this.stopWorker(controller);
        controller.generation += 1;
        controller.generationField.textContent = String(controller.generation);
        controller.status.textContent = 'Loading isolated IFC worker…';
        this.setControlsEnabled(controller, false);
        try {
            const workerUrl = requireSameOrigin(controller.root.dataset.workerUrl);
            const wasmUrl = requireSameOrigin(controller.root.dataset.wasmUrl);
            const worker = new this.WorkerClass(
                withReloadVersion(workerUrl, controller.generation),
                { type: 'module', name: 'cadevil-ifc-editor' },
            );
            controller.worker = worker;
            worker.onmessage = (event) => {
                if (controller.worker === worker) this.handleMessage(controller, event.data);
            };
            worker.onerror = () => {
                if (controller.worker === worker) this.fail(controller, 'The IFC worker stopped unexpectedly.');
            };
            worker.onmessageerror = () => {
                if (controller.worker === worker) this.fail(controller, 'The IFC worker sent an unreadable message.');
            };
            worker.postMessage({ type: 'initialize', wasmUrl });
        } catch (error) {
            this.fail(controller, error instanceof Error ? error.message : 'Unable to start the IFC worker.');
        }
    }

    postRequest(controller, message, transfer = []) {
        if (!controller.worker) return null;
        controller.requestCounter += 1;
        const requestId = `ifc-${controller.generation}-${controller.requestCounter}`;
        const timer = this.setTimer(() => this.handleTimeout(controller, requestId), this.operationTimeoutMs);
        controller.pending.set(requestId, { type: message.type, timer });
        controller.worker.postMessage({ ...message, requestId }, transfer);
        return requestId;
    }

    handleTimeout(controller, requestId) {
        if (!controller.pending.has(requestId)) return;
        controller.pending.delete(requestId);
        this.fail(controller, 'The IFC worker timed out and was stopped. Reload the worker to continue.');
    }

    onReady(controller) {
        controller.ready = true;
        this.setControlsEnabled(controller, true);
        if (controller.selectedBytes) {
            controller.status.textContent = 'Loading file…';
            this.loadSelectedBytes(controller);
        } else {
            controller.status.textContent = 'Worker ready. Choose a local .ifc file.';
        }
    }

    loadSelectedBytes(controller) {
        const copy = controller.selectedBytes.slice(0);
        this.postRequest(
            controller,
            { type: 'load', filename: controller.selectedFilename, bytes: copy },
            [copy],
        );
    }

    selectFile(controller, file) {
        if (!file) return;
        if (!file.name?.toLowerCase().endsWith('.ifc')) {
            controller.status.textContent = 'Choose a file with the .ifc extension.';
            return;
        }
        if (file.size > MAX_CLIENT_IFC_BYTES) {
            controller.status.textContent = 'That file exceeds the 32 MiB size limit.';
            return;
        }
        controller.jumpTargetId = null;
        file.arrayBuffer()
            .then((bytes) => {
                controller.selectedBytes = bytes;
                controller.selectedFilename = file.name;
                if (controller.ready) {
                    controller.status.textContent = 'Loading file…';
                    this.loadSelectedBytes(controller);
                } else {
                    controller.status.textContent = 'Waiting for the IFC worker to finish loading…';
                }
            })
            .catch(() => {
                controller.status.textContent = 'Unable to read the selected file.';
            });
    }

    handleMessage(controller, message) {
        if (!message || typeof message !== 'object' || Array.isArray(message)) {
            this.fail(controller, 'The IFC worker returned an invalid message.');
            return;
        }
        if (message.type === 'error') {
            this.handleWorkerError(controller, message);
            return;
        }
        if (message.type === 'ready') {
            this.onReady(controller);
            return;
        }
        const entry = message.requestId ? controller.pending.get(message.requestId) : undefined;
        if (!entry) return;
        controller.pending.delete(message.requestId);
        this.clearTimer?.(entry.timer);

        if (message.type !== RESPONSE_TYPE_BY_REQUEST[entry.type]) {
            this.fail(controller, 'The IFC worker returned an unexpected message.');
            return;
        }
        switch (message.type) {
            case 'loaded':
                if (!isValidLoadedMessage(message)) this.fail(controller, 'The IFC worker returned invalid load data.');
                else this.handleLoaded(controller, message);
                return;
            case 'entities':
                if (!isValidEntitiesMessage(message)) this.fail(controller, 'The IFC worker returned an invalid entity list.');
                else this.handleEntities(controller, message);
                return;
            case 'entity':
                if (!isValidEntityMessage(message)) this.fail(controller, 'The IFC worker returned invalid entity data.');
                else this.handleEntity(controller, message);
                return;
            case 'updated':
                if (!isValidUpdatedMessage(message)) {
                    controller.validationField.textContent = 'The IFC worker returned an invalid update.';
                } else {
                    this.handleUpdated(controller, message);
                }
                return;
            case 'exported':
                if (!isValidExportedMessage(message)) this.fail(controller, 'The IFC worker returned an invalid export.');
                else this.handleExported(controller, message);
                return;
            default:
                this.fail(controller, 'The IFC worker returned an unexpected message.');
        }
    }

    handleWorkerError(controller, message) {
        const text = typeof message.message === 'string' ? message.message : 'The IFC worker reported an error.';
        const entry = message.requestId ? controller.pending.get(message.requestId) : undefined;
        if (entry) {
            controller.pending.delete(message.requestId);
            this.clearTimer?.(entry.timer);
            if (entry.type === 'update') controller.validationField.textContent = text;
            else controller.status.textContent = text;
            return;
        }
        this.fail(controller, text);
    }

    handleLoaded(controller, message) {
        controller.filenameField.textContent = message.filename;
        controller.countField.textContent = String(message.entityCount);
        controller.entityCount = message.entityCount;
        controller.pageOffset = 0;
        controller.selectedEntityIndex = null;
        controller.selectedEntityId = null;
        this.resetInspector(controller);
        controller.status.textContent = `Loaded ${message.filename} (${message.entityCount} entities).`;
        this.setControlsEnabled(controller, true);
        this.requestList(controller);
    }

    requestList(controller) {
        if (!controller.ready) return;
        this.postRequest(controller, { type: 'list', offset: controller.pageOffset, limit: controller.pageSize });
    }

    handleEntities(controller, message) {
        controller.entityBody.textContent = '';
        for (const entity of message.entities) {
            const row = this.documentRef.createElement('tr');
            row.dataset.index = String(entity.index);
            if (entity.index === controller.selectedEntityIndex) row.classList.add('is-selected');
            const indexCell = this.documentRef.createElement('td');
            indexCell.textContent = String(entity.index);
            const idCell = this.documentRef.createElement('td');
            idCell.textContent = String(entity.id);
            const typeCell = this.documentRef.createElement('td');
            typeCell.textContent = entity.entityType;
            row.append(indexCell, idCell, typeCell);
            controller.entityBody.appendChild(row);
        }
        const shown = message.entities.length;
        controller.pageField.textContent = message.total === 0
            ? 'No entities loaded'
            : `Entities ${message.offset + 1}-${message.offset + shown} of ${message.total}`;
        controller.prevButton.disabled = message.offset <= 0;
        controller.nextButton.disabled = message.offset + shown >= message.total;

        if (controller.jumpTargetId !== null) {
            this.continueJump(controller, message);
        }
    }

    continueJump(controller, message) {
        const match = message.entities.find((entity) => entity.id === controller.jumpTargetId);
        if (match) {
            controller.jumpTargetId = null;
            this.selectEntity(controller, match.index);
            return;
        }
        if (message.offset + message.entities.length < message.total) {
            controller.pageOffset = message.offset + controller.pageSize;
            this.requestList(controller);
            return;
        }
        const targetId = controller.jumpTargetId;
        controller.jumpTargetId = null;
        controller.pageOffset = 0;
        controller.status.textContent = `No entity with STEP id ${targetId} was found.`;
        this.requestList(controller);
    }

    changePage(controller, direction) {
        const nextOffset = controller.pageOffset + direction * controller.pageSize;
        if (nextOffset < 0 || nextOffset >= Math.max(controller.entityCount, 1)) return;
        controller.pageOffset = nextOffset;
        this.requestList(controller);
    }

    jumpToId(controller) {
        const parsed = Number(controller.jumpInput.value);
        if (!Number.isInteger(parsed) || parsed < 0) {
            controller.status.textContent = 'Enter a valid, non-negative STEP id to jump to.';
            return;
        }
        if (!controller.ready || controller.entityCount === 0) return;
        controller.jumpTargetId = parsed;
        controller.pageOffset = 0;
        controller.status.textContent = `Searching for STEP id ${parsed}…`;
        this.requestList(controller);
    }

    selectEntity(controller, entityIndex) {
        if (!controller.ready || !Number.isInteger(entityIndex)) return;
        this.postRequest(controller, { type: 'inspect', entityIndex });
    }

    handleEntity(controller, message) {
        controller.selectedEntityIndex = message.index;
        controller.selectedEntityId = message.id;
        controller.entityIdField.textContent = String(message.id);
        controller.entityTypeField.textContent = message.entityType;
        controller.inspectorPlaceholder.hidden = true;
        controller.attributeTable.hidden = false;
        controller.validationField.textContent = '';
        controller.attributeBody.textContent = '';
        for (const attribute of message.attributes) {
            controller.attributeBody.appendChild(this.renderAttributeRow(attribute));
        }
        for (const row of controller.entityBody.querySelectorAll('[data-index]')) {
            row.classList.toggle('is-selected', Number(row.dataset.index) === message.index);
        }
    }

    renderAttributeRow(attribute) {
        const row = this.documentRef.createElement('tr');
        row.dataset.index = String(attribute.index);
        const indexCell = this.documentRef.createElement('td');
        indexCell.textContent = String(attribute.index);
        const kindCell = this.documentRef.createElement('td');
        kindCell.textContent = kindLabel(attribute.kind);
        const valueCell = this.documentRef.createElement('td');
        if (attribute.kind === 0) {
            valueCell.textContent = attribute.value;
        } else {
            const input = this.documentRef.createElement('input');
            input.type = 'text';
            input.value = attribute.value;
            input.dataset.ifcAttributeInput = 'true';
            input.dataset.index = String(attribute.index);
            input.setAttribute('aria-label', `Attribute ${attribute.index} value`);
            valueCell.appendChild(input);
        }
        row.append(indexCell, kindCell, valueCell);
        return row;
    }

    updateAttribute(controller, attributeIndex, value) {
        if (controller.selectedEntityId === null || !controller.ready) return;
        controller.validationField.textContent = 'Saving…';
        this.postRequest(controller, {
            type: 'update',
            entityId: controller.selectedEntityId,
            attributeIndex,
            value,
        });
    }

    handleUpdated(controller, message) {
        if (message.entityId !== controller.selectedEntityId) return;
        const row = controller.attributeBody.querySelector(`tr[data-index="${message.attributeIndex}"]`);
        const input = row?.querySelector('[data-ifc-attribute-input]');
        const kindCell = row?.querySelector('td:nth-child(2)');
        if (input) input.value = message.value;
        if (kindCell) kindCell.textContent = kindLabel(message.kind);
        controller.validationField.textContent = 'Saved.';
    }

    resetInspector(controller) {
        controller.entityIdField.textContent = '—';
        controller.entityTypeField.textContent = '';
        controller.inspectorPlaceholder.hidden = false;
        controller.attributeTable.hidden = true;
        controller.attributeBody.textContent = '';
        controller.validationField.textContent = '';
    }

    requestExport(controller) {
        if (!controller.ready || controller.entityCount === 0) return;
        controller.status.textContent = 'Preparing download…';
        this.postRequest(controller, { type: 'export' });
    }

    handleExported(controller, message) {
        const blob = new this.BlobClass([message.bytes], { type: 'application/octet-stream' });
        const url = this.createObjectUrl(blob);
        this.revokeActiveDownload(controller);
        controller.activeDownloadUrl = url;
        const link = this.documentRef.createElement('a');
        link.href = url;
        link.download = message.filename || controller.selectedFilename || 'model.ifc';
        link.rel = 'noopener';
        this.documentRef.body.appendChild(link);
        link.click();
        link.remove();
        controller.downloadTimer = this.setTimer(() => this.revokeActiveDownload(controller), 0);
        controller.status.textContent = 'Download ready.';
    }

    revokeActiveDownload(controller) {
        if (controller.downloadTimer !== null) {
            this.clearTimer?.(controller.downloadTimer);
            controller.downloadTimer = null;
        }
        if (controller.activeDownloadUrl !== null) {
            this.revokeObjectUrl(controller.activeDownloadUrl);
            controller.activeDownloadUrl = null;
        }
    }

    setControlsEnabled(controller, enabled) {
        const hasEntities = enabled && controller.entityCount > 0;
        controller.downloadButton.disabled = !hasEntities;
        controller.jumpButton.disabled = !hasEntities;
        controller.jumpInput.disabled = !hasEntities;
        controller.prevButton.disabled = !hasEntities || controller.pageOffset <= 0;
        controller.nextButton.disabled = !hasEntities;
    }

    fail(controller, message) {
        this.stopWorker(controller);
        controller.status.textContent = message;
        this.setControlsEnabled(controller, false);
    }

    stopWorker(controller) {
        for (const entry of controller.pending.values()) this.clearTimer?.(entry.timer);
        controller.pending.clear();
        controller.ready = false;
        if (!controller.worker) return;
        controller.worker.onmessage = null;
        controller.worker.onerror = null;
        controller.worker.onmessageerror = null;
        controller.worker.terminate();
        controller.worker = null;
    }

    unmountWithin(cleanupElement) {
        if (!cleanupElement) return;
        for (const [root, controller] of [...this.controllers]) {
            if (cleanupElement === root || cleanupElement.contains?.(root)) {
                this.dispose(controller);
            }
        }
    }

    dispose(controller) {
        if (controller.disposed) return;
        controller.disposed = true;
        this.stopWorker(controller);
        this.revokeActiveDownload(controller);
        for (const { target, type, handler } of controller.listeners) {
            target.removeEventListener(type, handler);
        }
        controller.listeners = [];
        this.controllers.delete(controller.root);
    }

    destroy() {
        for (const controller of [...this.controllers.values()]) this.dispose(controller);
    }
}

if (typeof document !== 'undefined' && typeof globalThis.Worker !== 'undefined') {
    const runtime = new IfcEditorRuntime();
    const mount = (scope) => runtime.mount(scope ?? document);
    document.addEventListener('DOMContentLoaded', () => mount(document));
    document.body?.addEventListener('htmx:after:settle', () => mount(document));
    document.body?.addEventListener('htmx:before:cleanup', (event) => {
        runtime.unmountWithin(event.target);
    });
}
