import {preverifiedWorker} from './fixtures/verified_worker.js';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

import {
    SnakeGameRuntime,
    validateSnapshot,
    withReloadVersion,
} from './plugins/snake_game_controller.js';
import { assertWorkerMessage } from './plugins/snake_game_worker.js';

class FakeEventTarget {
    constructor() {
        this.listeners = new Map();
    }

    addEventListener(type, listener) {
        const listeners = this.listeners.get(type) ?? new Set();
        listeners.add(listener);
        this.listeners.set(type, listeners);
    }

    removeEventListener(type, listener) {
        this.listeners.get(type)?.delete(listener);
    }

    dispatch(type, event = {}) {
        for (const listener of this.listeners.get(type) ?? []) listener(event);
    }
}

class FakeControl extends FakeEventTarget {
    constructor() {
        super();
        this.textContent = '';
        this.disabled = false;
        this.dataset = {};
    }
}

class FakeCanvas extends FakeEventTarget {
    constructor(context) {
        super();
        this.width = 400;
        this.height = 400;
        this.context = context;
    }

    getContext(kind) {
        return kind === '2d' ? this.context : null;
    }
}

class FakeGame extends FakeEventTarget {
    constructor() {
        super();
        this.isConnected = true;
        this.dataset = {
            snakeGame: '',
            workerUrl: '/static/js/plugins/snake_game_worker.js',
            wasmUrl: '/static/wasm/rust_example_plugin.wasm',
        };
        this.context = {
            clearRectCalls: [],
            fillRectCalls: [],
            strokeRectCalls: [],
            clearRect(...args) { this.clearRectCalls.push(args); },
            fillRect(...args) { this.fillRectCalls.push(args); },
            strokeRect(...args) { this.strokeRectCalls.push(args); },
        };
        this.canvas = new FakeCanvas(this.context);
        this.status = new FakeControl();
        this.score = new FakeControl();
        this.length = new FakeControl();
        this.restart = new FakeControl();
        this.directionButtons = ['up', 'right', 'down', 'left'].map((direction) => {
            const button = new FakeControl();
            button.dataset.snakeDirection = direction;
            return button;
        });
    }

    querySelector(selector) {
        return {
            '[data-snake-canvas]': this.canvas,
            '[data-snake-field="status"]': this.status,
            '[data-snake-field="score"]': this.score,
            '[data-snake-field="length"]': this.length,
            '[data-snake-action="restart"]': this.restart,
        }[selector] ?? null;
    }

    querySelectorAll(selector) {
        return selector === '[data-snake-direction]' ? this.directionButtons : [];
    }
}

class FakeWorker {
    static instances = [];

    constructor(url, options) {
        this.url = url;
        this.options = options;
        this.messages = [];
        this.terminated = false;
        FakeWorker.instances.push(this);
    }

    postMessage(message) {
        this.messages.push(message);
    }

    terminate() {
        this.terminated = true;
    }
}

const runningSnapshot = {
    width: 20,
    height: 20,
    status: 0,
    score: 0,
    length: 3,
    segments: [
        { x: 10, y: 10 },
        { x: 9, y: 10 },
        { x: 8, y: 10 },
    ],
    food: { x: 12, y: 10 },
};

function mountRuntime(options={}) {
    FakeWorker.instances = [];
    const game = new FakeGame();
    const intervalCallbacks = new Map();
    const clearedIntervals = [];
    let nextInterval = 1;
    const runtime = new SnakeGameRuntime({
        WorkerClass: FakeWorker, prepareWorker:preverifiedWorker,
        setIntervalFn(callback) {
            const id = nextInterval;
            nextInterval += 1;
            intervalCallbacks.set(id, callback);
            return id;
        },
        clearIntervalFn(id) {
            clearedIntervals.push(id);
            intervalCallbacks.delete(id);
        },
        seedFactory: () => 7,
        ...options,
    });
    runtime.mount({
        querySelectorAll(selector) {
            return selector === '[data-snake-game]' ? [game] : [];
        },
    });
    return { runtime, game, intervalCallbacks, clearedIntervals };
}

test('reload version preserves existing query parameters', () => {
    assert.equal(
        withReloadVersion('/snake-worker.js?mode=test', 2),
        '/snake-worker.js?mode=test&cadevilSnakeReload=2',
    );
});

test('snake worker accepts only the declared message protocol', () => {
    assert.doesNotThrow(() => assertWorkerMessage({
        type: 'initialize',
        wasmBytes: new ArrayBuffer(8),
        seed: 7,
    }));
    assert.doesNotThrow(() => assertWorkerMessage({ type: 'tick' }));
    assert.throws(
        () => assertWorkerMessage({ type: 'tick', unexpected: true }),
        /invalid tick message/i,
    );
    assert.throws(
        () => assertWorkerMessage({ type: 'direction', direction: 4 }),
        /invalid direction message/i,
    );
    assert.throws(
        () => assertWorkerMessage({ type: 'execute', source: 'alert(1)' }),
        /unsupported/i,
    );
});

test('snake runtime initializes, renders, ticks, and scopes direction input', () => {
    const { game, intervalCallbacks } = mountRuntime();
    const worker = FakeWorker.instances[0];

    assert.deepEqual(worker.options, { type: 'module', name: 'cadevil-rust-snake' });
    assert.deepEqual(worker.messages[0], {type:'cadevil-verified-boot',entry_url:'blob:trusted-fixture',initialize:{
        type: 'initialize',wasmBytes: new ArrayBuffer(8),seed: 7,
    }});
    game.dispatch('keydown', { key: 'ArrowUp' });
    game.directionButtons[0].dispatch('click');
    assert.equal(worker.messages.length, 1);
    assert.equal(game.directionButtons.every((button) => button.disabled), true);

    worker.onmessage({ data: { type: 'ready', state: runningSnapshot } });
    assert.equal(game.score.textContent, '0');
    assert.equal(game.length.textContent, '3');
    assert.equal(game.directionButtons.some((button) => button.disabled), false);
    assert.ok(game.context.fillRectCalls.length >= 4);

    intervalCallbacks.values().next().value();
    assert.deepEqual(worker.messages.at(-1), { type: 'tick' });

    let prevented = false;
    game.dispatch('keydown', {
        key: 'ArrowUp',
        preventDefault() { prevented = true; },
    });
    assert.equal(prevented, true);
    assert.deepEqual(worker.messages.at(-1), { type: 'direction', direction: 0 });

    game.directionButtons[3].dispatch('click');
    assert.deepEqual(worker.messages.at(-1), { type: 'direction', direction: 3 });
});

test('snake runtime rejects malformed worker state', () => {
    const { game } = mountRuntime();
    const worker = FakeWorker.instances[0];

    worker.onmessage({ data: { type: 'state', state: { ...runningSnapshot, width: 0 } } });

    assert.match(game.status.textContent, /invalid game state/i);
    assert.equal(worker.terminated, true);
    assert.equal(validateSnapshot({ ...runningSnapshot, food: { x: 30, y: 2 } }), false);
});

test('snake runtime restarts with a cache-busted worker and cleans only its subtree', () => {
    const { runtime, game, intervalCallbacks, clearedIntervals } = mountRuntime();
    const firstWorker = FakeWorker.instances[0];
    firstWorker.onmessage({ data: { type: 'ready', state: runningSnapshot } });
    const firstInterval = intervalCallbacks.keys().next().value;

    game.restart.dispatch('click');

    assert.equal(firstWorker.terminated, true);
    assert.equal(clearedIntervals.includes(firstInterval), true);
    assert.equal(FakeWorker.instances.length, 2);
    assert.match(FakeWorker.instances[1].url, /cadevilSnakeReload=2$/);

    runtime.unmountWithin({ contains() { return false; } });
    assert.equal(FakeWorker.instances[1].terminated, false);

    runtime.unmountWithin({ contains(element) { return element === game; } });
    assert.equal(FakeWorker.instances[1].terminated, true);
    assert.equal(game.listeners.get('keydown')?.size ?? 0, 0);
});

test('a passive ancestor replacement prunes the disconnected Snake worker and tick timer', () => {
    const { runtime, game, intervalCallbacks } = mountRuntime();
    const worker = FakeWorker.instances[0];
    worker.onmessage({ data: { type: 'ready', state: runningSnapshot } });
    runtime.mount({ querySelectorAll() { return []; } });
    assert.equal(worker.terminated, false, 'An unrelated inline scope retains a connected game');
    game.isConnected = false;
    runtime.mount({ querySelectorAll() { return []; } });
    assert.equal(worker.terminated, true);
    assert.equal(intervalCallbacks.size, 0);
    assert.equal(runtime.controllers.size, 0);
    assert.equal(game.listeners.get('keydown')?.size ?? 0, 0);
    runtime.mount({ querySelectorAll() { return []; } });
    assert.equal(FakeWorker.instances.length, 1);
});

test('Rust snake WebAssembly owns movement, growth, reset, and collision rules', async () => {
    const bytes = await readFile(
        new URL('../wasm/rust_example_plugin.wasm', import.meta.url),
    );
    const module = await WebAssembly.compile(bytes);
    assert.deepEqual(WebAssembly.Module.imports(module), []);
    const instance = await WebAssembly.instantiate(module);
    const game = instance.exports;

    assert.equal(game.triple, undefined);
    game.snake_reset(7);
    assert.equal(game.snake_grid_width(), 20);
    assert.equal(game.snake_grid_height(), 20);
    assert.equal(game.snake_status(), 0);
    assert.equal(game.snake_length(), 3);
    assert.deepEqual([game.snake_segment_x(0), game.snake_segment_y(0)], [10, 10]);
    assert.deepEqual([game.snake_food_x(), game.snake_food_y()], [12, 10]);

    game.snake_tick();
    assert.deepEqual([game.snake_segment_x(0), game.snake_segment_y(0)], [11, 10]);
    assert.equal(game.snake_set_direction(3), 0);
    game.snake_tick();
    assert.equal(game.snake_score(), 1);
    assert.equal(game.snake_length(), 4);
    assert.equal(game.snake_segment_x(999), -1);
    assert.equal(game.snake_segment_y(999), -1);

    const foodAfterGrowth = [game.snake_food_x(), game.snake_food_y()];
    game.snake_reset(7);
    game.snake_tick();
    game.snake_tick();
    assert.deepEqual([game.snake_food_x(), game.snake_food_y()], foodAfterGrowth);

    for (let index = 0; index < 20 && game.snake_status() === 0; index += 1) {
        game.snake_tick();
    }
    assert.equal(game.snake_status(), 1);
});

test('Snake waits for verification and disposes a late proof after cleanup',async()=>{
    let finish,signal,disposed=0;
    const env=mountRuntime({prepareWorker:options=>{signal=options.signal;return new Promise(resolve=>{finish=()=>resolve({...preverifiedWorker(options),dispose(){disposed++;}});});}});
    assert.equal(FakeWorker.instances.length,0);env.runtime.destroy();assert(signal.aborted);finish();await new Promise(resolve=>setImmediate(resolve));assert.equal(FakeWorker.instances.length,0);assert.equal(disposed,1);
});
test('Snake pauses at the CRL deadline and stops on a revoked fresh certificate',async()=>{
    let reject;
    const env=mountRuntime({prepareWorker:options=>({...preverifiedWorker(options),expires:0,recheck:()=>new Promise((resolve,failed)=>{reject=failed;})})});
    const worker=FakeWorker.instances[0];worker.onmessage({data:{type:'ready',state:runningSnapshot}});
    const tick=env.intervalCallbacks.values().next().value;tick();tick();assert.equal(worker.messages.length,1);
    reject(Error('Certificate revoked'));await new Promise(resolve=>setImmediate(resolve));assert(worker.terminated);assert.equal(env.intervalCallbacks.size,0);assert.match(env.game.status.textContent,/revoked/);env.runtime.destroy();
});
