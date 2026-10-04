.PHONY: install debug run migrate test test-django test-javascript test-rust \
	rebuild build-example-plugin-wasm build-rust-example-plugin-wasm flush superuser

install:
	uv sync --locked
	npm ci --ignore-scripts

debug:
	uv run python manage.py debugserver --settings=config.settings.dev --processes 4 --max-rss 512

run:
	uv run python manage.py runbolt --settings=config.settings.prod --processes 8 \
		--max-rss 512 --workers-lifetime 21600 --respawn-failed-workers

migrate:
	uv run python manage.py migrate

test: test-django test-javascript test-rust

test-django:
	uv run python manage.py test apps tests --settings=tests.passport_test_settings

test-javascript:
	npm test

test-rust:
	cargo test --locked --manifest-path apps/plugins/example_plugin/Cargo.toml
	cargo test --locked --manifest-path apps/plugins/rust_example_plugin/Cargo.toml

rebuild: build-example-plugin-wasm build-rust-example-plugin-wasm

build-example-plugin-wasm:
	cargo run --locked --manifest-path apps/plugins/example_plugin/Cargo.toml --bin build-wasm

build-rust-example-plugin-wasm:
	cargo run --locked --manifest-path apps/plugins/rust_example_plugin/Cargo.toml --bin build-wasm

flush:
	uv run python manage.py flush

superuser:
	uv run python manage.py createsuperuser
