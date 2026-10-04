import { bindWorkerPageLifecycle } from '../worker_page_lifecycle.js';

const DIRECTION_BY_NAME = Object.freeze({ up: 0, right: 1, down: 2, left: 3 });
const DIRECTION_BY_KEY = Object.freeze({
    ArrowUp: 0,
    w: 0,
    ArrowRight: 1,
    d: 1,
    ArrowDown: 2,
    s: 2,
    ArrowLeft: 3,
    a: 3,
});

export function withReloadVersion(url, generation) {
    const separator = url.includes('?') ? '&' : '?';
    return `${url}${separator}cadevilSnakeReload=${generation}`;
}

function isBoundedInteger(value, minimum, maximum) {
    return Number.isInteger(value) && value >= minimum && value <= maximum;
}

export function validateSnapshot(state) {
    if (!state || typeof state !== 'object' || Array.isArray(state)) return false;
    const { width, height, status, score, length, segments, food } = state;
    if (!isBoundedInteger(width, 1, 100)
        || !isBoundedInteger(height, 1, 100)
        || !isBoundedInteger(status, 0, 2)
        || !isBoundedInteger(score, 0, width * height)
        || !isBoundedInteger(length, 1, width * height)
        || !Array.isArray(segments)
        || segments.length !== length
        || !food
        || !isBoundedInteger(food.x, 0, width - 1)
        || !isBoundedInteger(food.y, 0, height - 1)) {
        return false;
    }
    const occupied = new Set();
    for (const segment of segments) {
        if (!segment
            || !isBoundedInteger(segment.x, 0, width - 1)
            || !isBoundedInteger(segment.y, 0, height - 1)) {
            return false;
        }
        const key = `${segment.x}:${segment.y}`;
        if (occupied.has(key)) return false;
        occupied.add(key);
    }
    return status === 2 || !occupied.has(`${food.x}:${food.y}`);
}

function requireSameOrigin(url) {
    if (typeof globalThis.location === 'undefined') return url;
    const resolved = new URL(url, globalThis.location.href);
    if (resolved.origin !== globalThis.location.origin) {
        throw new Error('Snake plugin assets must use the application origin.');
    }
    return url;
}

function canvasColors(game) {
    if (typeof globalThis.getComputedStyle !== 'function') {
        return {
            background: '#15181f',
            grid: '#2f3642',
            snake: '#69a7ff',
            head: '#8dc0ff',
            food: '#ff6b6b',
        };
    }
    const styles = globalThis.getComputedStyle(game);
    const value = (name, fallback) => styles.getPropertyValue(name).trim() || fallback;
    return {
        background: value('--snake-board-bg', '#15181f'),
        grid: value('--snake-grid-color', '#2f3642'),
        snake: value('--snake-body-color', '#69a7ff'),
        head: value('--snake-head-color', '#8dc0ff'),
        food: value('--snake-food-color', '#ff6b6b'),
    };
}

export class SnakeGameRuntime {
    constructor({
        WorkerClass = globalThis.Worker,
        setIntervalFn = globalThis.setInterval?.bind(globalThis),
        clearIntervalFn = globalThis.clearInterval?.bind(globalThis),
        seedFactory = () => globalThis.crypto?.getRandomValues?.(new Uint32Array(1))[0]
            ?? Date.now() >>> 0,
    } = {}) {
        this.WorkerClass = WorkerClass;
        this.setIntervalFn = setIntervalFn;
        this.clearIntervalFn = clearIntervalFn;
        this.seedFactory = seedFactory;
        this.controllers = new Map();
    }

    mount(scope) {
        for (const [game, controller] of this.controllers) {
            if (game.isConnected === false) this.dispose(controller);
        }
        const games = [];
        if (scope?.matches?.('[data-snake-game]')) games.push(scope);
        for (const game of scope?.querySelectorAll?.('[data-snake-game]') ?? []) games.push(game);
        for (const game of games) {
            if (!this.controllers.has(game)) this.mountGame(game);
        }
    }

    mountGame(game) {
        const canvas = game.querySelector('[data-snake-canvas]');
        const context = canvas?.getContext?.('2d');
        const status = game.querySelector('[data-snake-field="status"]');
        const score = game.querySelector('[data-snake-field="score"]');
        const length = game.querySelector('[data-snake-field="length"]');
        const restart = game.querySelector('[data-snake-action="restart"]');
        const directionButtons = [...game.querySelectorAll('[data-snake-direction]')];
        if (!canvas || !context || !status || !score || !length || !restart
            || !game.dataset.workerUrl || !game.dataset.wasmUrl) {
            return;
        }

        const controller = {
            game,
            canvas,
            context,
            status,
            score,
            length,
            restart,
            directionButtons,
            generation: 0,
            worker: null,
            interval: null,
            running: false,
            disposed: false,
        };
        controller.keyHandler = (event) => {
            const normalizedKey = event.key?.length === 1 ? event.key.toLowerCase() : event.key;
            const direction = DIRECTION_BY_KEY[normalizedKey];
            if (direction === undefined || !controller.running || !controller.worker) return;
            event.preventDefault?.();
            controller.worker.postMessage({ type: 'direction', direction });
        };
        controller.focusHandler = () => game.focus?.({ preventScroll: true });
        controller.restartHandler = () => this.restart(controller);
        controller.directionHandlers = directionButtons.map((button) => {
            const direction = DIRECTION_BY_NAME[button.dataset.snakeDirection];
            const handler = () => {
                if (direction !== undefined && controller.running && controller.worker) {
                    controller.worker.postMessage({ type: 'direction', direction });
                }
            };
            button.addEventListener('click', handler);
            return { button, handler };
        });
        for (const button of directionButtons) button.disabled = true;
        game.addEventListener('keydown', controller.keyHandler);
        canvas.addEventListener('pointerdown', controller.focusHandler);
        restart.addEventListener('click', controller.restartHandler);
        this.controllers.set(game, controller);
        this.restart(controller);
    }

    restart(controller) {
        this.stopWorker(controller);
        controller.generation += 1;
        controller.running = false;
        for (const button of controller.directionButtons) button.disabled = true;
        controller.status.textContent = 'Loading isolated game worker…';
        try {
            const workerUrl = requireSameOrigin(controller.game.dataset.workerUrl);
            const wasmUrl = requireSameOrigin(controller.game.dataset.wasmUrl);
            const worker = new this.WorkerClass(
                withReloadVersion(workerUrl, controller.generation),
                { type: 'module', name: 'cadevil-rust-snake' },
            );
            controller.worker = worker;
            worker.onmessage = (event) => {
                if (controller.worker === worker) this.handleMessage(controller, event.data);
            };
            worker.onerror = () => {
                if (controller.worker === worker) {
                    this.fail(controller, 'The Snake worker stopped unexpectedly.');
                }
            };
            worker.onmessageerror = () => {
                if (controller.worker === worker) {
                    this.fail(controller, 'The Snake worker sent an unreadable message.');
                }
            };
            worker.postMessage({
                type: 'initialize',
                wasmUrl,
                seed: Number(this.seedFactory()) >>> 0,
            });
        } catch (error) {
            this.fail(controller, error instanceof Error ? error.message : 'Unable to start Snake.');
        }
    }

    handleMessage(controller, message) {
        if (!message || typeof message !== 'object' || Array.isArray(message)) {
            this.fail(controller, 'Snake returned an invalid message.');
            return;
        }
        if (message.type === 'error' && typeof message.message === 'string') {
            this.fail(controller, message.message);
            return;
        }
        if ((message.type !== 'ready' && message.type !== 'state')
            || (message.type === 'state' && !controller.running)
            || !validateSnapshot(message.state)) {
            this.fail(controller, 'Snake returned invalid game state.');
            return;
        }

        this.render(controller, message.state);
        controller.running = message.state.status === 0;
        for (const button of controller.directionButtons) {
            button.disabled = !controller.running;
        }
        if (controller.running) this.startTimer(controller);
        else this.clearTimer(controller);
    }

    startTimer(controller) {
        if (controller.interval !== null || typeof this.setIntervalFn !== 'function') return;
        controller.interval = this.setIntervalFn(() => {
            controller.worker?.postMessage({ type: 'tick' });
        }, 140);
    }

    clearTimer(controller) {
        if (controller.interval === null) return;
        this.clearIntervalFn?.(controller.interval);
        controller.interval = null;
    }

    render(controller, state) {
        const { canvas, context } = controller;
        const cellWidth = canvas.width / state.width;
        const cellHeight = canvas.height / state.height;
        const colors = canvasColors(controller.game);
        context.fillStyle = colors.background;
        context.fillRect(0, 0, canvas.width, canvas.height);
        context.strokeStyle = colors.grid;
        context.lineWidth = 1;
        for (let y = 0; y < state.height; y += 1) {
            for (let x = 0; x < state.width; x += 1) {
                context.strokeRect(x * cellWidth, y * cellHeight, cellWidth, cellHeight);
            }
        }
        context.fillStyle = colors.food;
        context.fillRect(
            state.food.x * cellWidth + 2,
            state.food.y * cellHeight + 2,
            cellWidth - 4,
            cellHeight - 4,
        );
        state.segments.forEach((segment, index) => {
            context.fillStyle = index === 0 ? colors.head : colors.snake;
            context.fillRect(
                segment.x * cellWidth + 1,
                segment.y * cellHeight + 1,
                cellWidth - 2,
                cellHeight - 2,
            );
        });
        controller.score.textContent = String(state.score);
        controller.length.textContent = String(state.length);
        controller.status.textContent = [
            'Game running. Use arrow or WASD keys to steer.',
            `Game over with a score of ${state.score}. Restart to play again.`,
            `Board cleared with a score of ${state.score}.`,
        ][state.status];
    }

    fail(controller, message) {
        this.stopWorker(controller);
        controller.status.textContent = message;
    }

    stopWorker(controller) {
        this.clearTimer(controller);
        controller.running = false;
        for (const button of controller.directionButtons) button.disabled = true;
        if (!controller.worker) return;
        controller.worker.onmessage = null;
        controller.worker.onerror = null;
        controller.worker.onmessageerror = null;
        controller.worker.terminate();
        controller.worker = null;
    }

    unmountWithin(cleanupElement) {
        if (!cleanupElement) return;
        for (const [game, controller] of [...this.controllers]) {
            if (cleanupElement === game || cleanupElement.contains?.(game)) {
                this.dispose(controller);
            }
        }
    }

    dispose(controller) {
        if (controller.disposed) return;
        controller.disposed = true;
        this.stopWorker(controller);
        controller.game.removeEventListener('keydown', controller.keyHandler);
        controller.canvas.removeEventListener('pointerdown', controller.focusHandler);
        controller.restart.removeEventListener('click', controller.restartHandler);
        for (const { button, handler } of controller.directionHandlers) {
            button.removeEventListener('click', handler);
        }
        this.controllers.delete(controller.game);
    }

    destroy() {
        for (const controller of [...this.controllers.values()]) this.dispose(controller);
    }
}

if (typeof document !== 'undefined' && typeof globalThis.Worker !== 'undefined') {
    bindWorkerPageLifecycle(new SnakeGameRuntime());
}
