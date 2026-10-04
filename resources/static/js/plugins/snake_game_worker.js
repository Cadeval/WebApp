const MAX_WASM_BYTES = 2 * 1024 * 1024;
const REQUIRED_EXPORTS = [
    'snake_reset',
    'snake_set_direction',
    'snake_tick',
    'snake_grid_width',
    'snake_grid_height',
    'snake_status',
    'snake_score',
    'snake_length',
    'snake_segment_x',
    'snake_segment_y',
    'snake_food_x',
    'snake_food_y',
];

let game = null;

function hasExactKeys(value, keys) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
    const actual = Object.keys(value).sort();
    const expected = [...keys].sort();
    return actual.length === expected.length
        && actual.every((key, index) => key === expected[index]);
}

export function assertWorkerMessage(message) {
    switch (message?.type) {
        case 'initialize':
            if (!hasExactKeys(message, ['type', 'wasmBytes', 'seed'])
                || !(message.wasmBytes instanceof ArrayBuffer)
                || message.wasmBytes.byteLength < 8 || message.wasmBytes.byteLength > MAX_WASM_BYTES
                || !Number.isInteger(message.seed)
                || message.seed < 0
                || message.seed > 0xffffffff) {
                throw new Error('Invalid initialize message.');
            }
            break;
        case 'tick':
            if (!hasExactKeys(message, ['type'])) throw new Error('Invalid tick message.');
            break;
        case 'direction':
            if (!hasExactKeys(message, ['type', 'direction'])
                || !Number.isInteger(message.direction)
                || message.direction < 0
                || message.direction > 3) {
                throw new Error('Invalid direction message.');
            }
            break;
        case 'reset':
            if (!hasExactKeys(message, ['type', 'seed'])
                || !Number.isInteger(message.seed)
                || message.seed < 0
                || message.seed > 0xffffffff) {
                throw new Error('Invalid reset message.');
            }
            break;
        default:
            throw new Error('Unsupported Snake worker message.');
    }
}

function assertReady() {
    if (!game) throw new Error('Snake worker is not initialized.');
}

function readSnapshot() {
    assertReady();
    const width = game.snake_grid_width();
    const height = game.snake_grid_height();
    const length = game.snake_length();
    const status = game.snake_status();
    if (!Number.isInteger(width) || width <= 0 || width > 100
        || !Number.isInteger(height) || height <= 0 || height > 100
        || !Number.isInteger(length) || length <= 0 || length > width * height
        || !Number.isInteger(status) || status < 0 || status > 2) {
        throw new Error('Rust Snake returned invalid scalar state.');
    }

    const segments = [];
    for (let index = 0; index < length; index += 1) {
        const x = game.snake_segment_x(index);
        const y = game.snake_segment_y(index);
        if (!Number.isInteger(x) || x < 0 || x >= width
            || !Number.isInteger(y) || y < 0 || y >= height) {
            throw new Error('Rust Snake returned an invalid segment.');
        }
        segments.push({ x, y });
    }
    const food = { x: game.snake_food_x(), y: game.snake_food_y() };
    if (!Number.isInteger(food.x) || food.x < 0 || food.x >= width
        || !Number.isInteger(food.y) || food.y < 0 || food.y >= height) {
        throw new Error('Rust Snake returned invalid food coordinates.');
    }
    return {
        width,
        height,
        status,
        score: game.snake_score(),
        length,
        segments,
        food,
    };
}

async function initialize(bytes, seed) {
    if (game) throw new Error('Snake worker is already initialized.');
    if (!(bytes instanceof ArrayBuffer) || bytes.byteLength < 8 || bytes.byteLength > MAX_WASM_BYTES) throw new Error('Invalid verified Snake WebAssembly bytes.');

    const result = await WebAssembly.instantiate(bytes, {});
    const exports = result.instance?.exports ?? result.exports;
    if (!exports || REQUIRED_EXPORTS.some((name) => typeof exports[name] !== 'function')) {
        throw new Error('Snake WebAssembly does not implement the required game ABI.');
    }
    game = exports;
    game.snake_reset(seed);
    self.postMessage({ type: 'ready', state: readSnapshot() });
}

if (typeof self !== 'undefined') {
    self.addEventListener('message', async (event) => {
        try {
            const message = event.data;
            assertWorkerMessage(message);
            switch (message.type) {
                case 'initialize':
                    await initialize(message.wasmBytes, message.seed);
                    return;
                case 'tick':
                    assertReady();
                    game.snake_tick();
                    break;
                case 'direction':
                    assertReady();
                    game.snake_set_direction(message.direction);
                    break;
                case 'reset':
                    assertReady();
                    game.snake_reset(message.seed);
                    break;
                default:
                    return;
            }
            self.postMessage({ type: 'state', state: readSnapshot() });
        } catch (error) {
            self.postMessage({
                type: 'error',
                message: error instanceof Error ? error.message : 'Snake worker failed.',
            });
        }
    });
}
