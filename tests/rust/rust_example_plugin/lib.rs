use super::*;

#[test]
fn reset_starts_a_deterministic_running_game() {
    snake_reset(7);
    assert_eq!(snake_status(), 0);
    assert_eq!(snake_length(), 3);
    assert_eq!((snake_segment_x(0), snake_segment_y(0)), (10, 10));
    assert_eq!((snake_food_x(), snake_food_y()), (12, 10));
}

#[test]
fn snake_grows_and_rejects_an_immediate_reverse() {
    snake_reset(7);
    assert_eq!(snake_set_direction(3), 0);
    snake_tick();
    snake_tick();
    assert_eq!(snake_score(), 1);
    assert_eq!(snake_length(), 4);
}

#[test]
fn wall_and_self_collisions_end_the_game() {
    snake_reset(7);
    for _ in 0..20 {
        snake_tick();
    }
    assert_eq!(snake_status(), 1);

    GAME.with(|game| {
        let mut game = game.borrow_mut();
        game.reset(7);
        game.length = 5;
        game.segments[..5].copy_from_slice(&[
            Point { x: 5, y: 5 },
            Point { x: 5, y: 6 },
            Point { x: 4, y: 6 },
            Point { x: 4, y: 5 },
            Point { x: 4, y: 4 },
        ]);
        game.direction = LEFT;
        game.pending_direction = LEFT;
    });
    snake_tick();
    assert_eq!(snake_status(), 1);
}

#[test]
fn invalid_segment_indices_are_safe() {
    snake_reset(7);
    assert_eq!(snake_segment_x(999), -1);
    assert_eq!(snake_segment_y(999), -1);
}
