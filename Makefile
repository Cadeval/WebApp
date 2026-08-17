# Minimal makefile
#
# instructs make to invoke a single instance of the shell and provide it with the entire recipe,
# regardless of how many lines it contains.
.ONESHELL:

.PHONY: install debug run testrun migrate test test-django test-javascript test-rust \
	rebuild build-example-plugin-wasm build-rust-example-plugin-wasm flush superuser \
	keydb_server

install:
	uv sync --frozen

debug:
	uv run python manage.py runbolt --dev

run:
	uv run python -m uvicorn --workers 4 webapp.asgi:application --lifespan auto --log-level debug --host [::] --port 8000

migrate:
	uv run python manage.py makemigrations
	uv run python manage.py migrate --run-syncdb
#	uv run python manage.py makemigrations mycelium

test: test-django test-javascript test-rust

test-javascript:
	npm test

test-rust:
	cargo test --manifest-path src/example_plugin/Cargo.toml && \
	cargo test --manifest-path src/rust_example_plugin/Cargo.toml

rebuild: build-example-plugin-wasm build-rust-example-plugin-wasm

build-example-plugin-wasm:
	npm run build:example-plugin-wasm

build-rust-example-plugin-wasm:
	npm run build:rust-example-plugin-wasm

flush:
	uv run python manage.py flush

superuser:
	uv run python manage.py createsuperuser

#collectstatic:
#	python manage.py collectstatic && \
#	sudo chown -R 911:1000 apps/resources/collected_static && \
#	rsync -avzzpP --delete apps/resources/collected_static root@meanderingmind.me:/srv/nginx/config/www/cadevil/ && \
#	sudo rm -rfv apps/resources/collected_static

keydb_server:
	cd data && \
	keydb-server
