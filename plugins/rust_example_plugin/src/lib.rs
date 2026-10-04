use std::cell::RefCell;

const GRID_WIDTH: i32 = 20;
const GRID_HEIGHT: i32 = 20;
const MAX_SEGMENTS: usize = (GRID_WIDTH * GRID_HEIGHT) as usize;
const RUNNING: i32 = 0;
const GAME_OVER: i32 = 1;
const WON: i32 = 2;
const UP: i32 = 0;
const RIGHT: i32 = 1;
const DOWN: i32 = 2;
const LEFT: i32 = 3;

#[derive(Clone, Copy, PartialEq, Eq)]
struct Point {
    x: i32,
    y: i32,
}

impl Point {
    const ORIGIN: Self = Self { x: 0, y: 0 };
}

struct Game {
    segments: [Point; MAX_SEGMENTS],
    length: usize,
    direction: i32,
    pending_direction: i32,
    food: Point,
    score: i32,
    status: i32,
    random_state: u32,
}

impl Game {
    const fn new() -> Self {
        Self {
            segments: [Point::ORIGIN; MAX_SEGMENTS],
            length: 0,
            direction: RIGHT,
            pending_direction: RIGHT,
            food: Point::ORIGIN,
            score: 0,
            status: RUNNING,
            random_state: 1,
        }
    }

    fn reset(&mut self, seed: u32) {
        self.segments.fill(Point::ORIGIN);
        self.segments[0] = Point { x: 10, y: 10 };
        self.segments[1] = Point { x: 9, y: 10 };
        self.segments[2] = Point { x: 8, y: 10 };
        self.length = 3;
        self.direction = RIGHT;
        self.pending_direction = RIGHT;
        self.food = Point { x: 12, y: 10 };
        self.score = 0;
        self.status = RUNNING;
        self.random_state = if seed == 0 { 0x6d2b_79f5 } else { seed };
    }

    fn set_direction(&mut self, direction: i32) -> i32 {
        if self.status != RUNNING
            || !(UP..=LEFT).contains(&direction)
            || is_opposite(self.direction, direction)
        {
            return 0;
        }
        self.pending_direction = direction;
        1
    }

    fn tick(&mut self) -> i32 {
        if self.status != RUNNING {
            return self.status;
        }

        self.direction = self.pending_direction;
        let head = self.segments[0];
        let next_head = match self.direction {
            UP => Point {
                x: head.x,
                y: head.y - 1,
            },
            RIGHT => Point {
                x: head.x + 1,
                y: head.y,
            },
            DOWN => Point {
                x: head.x,
                y: head.y + 1,
            },
            LEFT => Point {
                x: head.x - 1,
                y: head.y,
            },
            _ => head,
        };
        let grows = next_head == self.food;
        let collision_length = self.length.saturating_sub(usize::from(!grows));
        if next_head.x < 0
            || next_head.x >= GRID_WIDTH
            || next_head.y < 0
            || next_head.y >= GRID_HEIGHT
            || self.segments[..collision_length].contains(&next_head)
        {
            self.status = GAME_OVER;
            return self.status;
        }

        let next_length = if grows {
            (self.length + 1).min(MAX_SEGMENTS)
        } else {
            self.length
        };
        for index in (1..next_length).rev() {
            self.segments[index] = self.segments[index - 1];
        }
        self.segments[0] = next_head;
        self.length = next_length;

        if grows {
            self.score += 1;
            self.place_food();
        }
        self.status
    }

    fn place_food(&mut self) {
        if self.length >= MAX_SEGMENTS {
            self.status = WON;
            return;
        }
        self.random_state = self
            .random_state
            .wrapping_mul(1_664_525)
            .wrapping_add(1_013_904_223);
        let start = self.random_state as usize % MAX_SEGMENTS;
        for offset in 0..MAX_SEGMENTS {
            let index = (start + offset) % MAX_SEGMENTS;
            let candidate = Point {
                x: (index as i32) % GRID_WIDTH,
                y: (index as i32) / GRID_WIDTH,
            };
            if !self.segments[..self.length].contains(&candidate) {
                self.food = candidate;
                return;
            }
        }
        self.status = WON;
    }
}

fn is_opposite(first: i32, second: i32) -> bool {
    matches!(
        (first, second),
        (UP, DOWN) | (DOWN, UP) | (RIGHT, LEFT) | (LEFT, RIGHT)
    )
}

thread_local! {
    static GAME: RefCell<Game> = const { RefCell::new(Game::new()) };
}

#[no_mangle]
pub extern "C" fn snake_reset(seed: u32) {
    GAME.with(|game| game.borrow_mut().reset(seed));
}

#[no_mangle]
pub extern "C" fn snake_set_direction(direction: i32) -> i32 {
    GAME.with(|game| game.borrow_mut().set_direction(direction))
}

#[no_mangle]
pub extern "C" fn snake_tick() -> i32 {
    GAME.with(|game| game.borrow_mut().tick())
}

#[no_mangle]
pub extern "C" fn snake_grid_width() -> i32 {
    GRID_WIDTH
}

#[no_mangle]
pub extern "C" fn snake_grid_height() -> i32 {
    GRID_HEIGHT
}

#[no_mangle]
pub extern "C" fn snake_status() -> i32 {
    GAME.with(|game| game.borrow().status)
}

#[no_mangle]
pub extern "C" fn snake_score() -> i32 {
    GAME.with(|game| game.borrow().score)
}

#[no_mangle]
pub extern "C" fn snake_length() -> i32 {
    GAME.with(|game| game.borrow().length as i32)
}

#[no_mangle]
pub extern "C" fn snake_segment_x(index: i32) -> i32 {
    GAME.with(|game| {
        let game = game.borrow();
        usize::try_from(index)
            .ok()
            .filter(|index| *index < game.length)
            .map_or(-1, |index| game.segments[index].x)
    })
}

#[no_mangle]
pub extern "C" fn snake_segment_y(index: i32) -> i32 {
    GAME.with(|game| {
        let game = game.borrow();
        usize::try_from(index)
            .ok()
            .filter(|index| *index < game.length)
            .map_or(-1, |index| game.segments[index].y)
    })
}

#[no_mangle]
pub extern "C" fn snake_food_x() -> i32 {
    GAME.with(|game| game.borrow().food.x)
}

#[no_mangle]
pub extern "C" fn snake_food_y() -> i32 {
    GAME.with(|game| game.borrow().food.y)
}

#[cfg(test)]
#[path = "../../../tests/rust/rust_example_plugin/lib.rs"]
mod tests;
