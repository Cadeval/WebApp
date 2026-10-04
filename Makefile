.PHONY: install debug run migrate test test-django test-javascript test-rust \
	rebuild flush superuser

.PHONY: sbom-setup sbom sbom-check sbom-capture-tools
.PHONY: build docker-build docker-check
.PHONY: install-checkout-hook

# Use an already-prepared interpreter; installing hooks does not sync dependencies.
CHECKOUT_PYTHON ?= .venv/bin/python

# Keep export/validation tooling separate from application dependencies.
SBOM_TOOL_DIR ?= sbom
SBOM_PYTHON = $(SBOM_TOOL_DIR)/.venv/bin/python
SBOM_UV = $(SBOM_TOOL_DIR)/.venv/bin/uv

sbom-setup:
	uv venv --allow-existing --python 3.14 "$(SBOM_TOOL_DIR)/.venv"
	uv pip sync --require-hashes --python "$(SBOM_PYTHON)" sbom/tools-requirements.lock

sbom:
	"$(SBOM_PYTHON)" scripts/generate_sbom.py --uv "$(SBOM_UV)"

sbom-check:
	"$(SBOM_PYTHON)" tests/test_generate_sbom.py
	"$(SBOM_PYTHON)" scripts/generate_sbom.py --uv "$(SBOM_UV)" --check

# Refresh only dependency metadata and locks after a reviewed tool update.
# Usage: make sbom-capture-tools CADEVIL_MCP_TOOL_ROOT=/path/to/mcp_tools
sbom-capture-tools:
	@test -n "$(CADEVIL_MCP_TOOL_ROOT)" || (echo "Set CADEVIL_MCP_TOOL_ROOT to the installed MCP tool directory."; exit 1)
	"$(SBOM_PYTHON)" scripts/generate_sbom.py --uv "$(SBOM_UV)" --capture-tools "$(CADEVIL_MCP_TOOL_ROOT)"

build:
	uv build

# The helper sends an exact archive and creates local image attestations.
docker-build:
	uv run --locked --no-sync python scripts/build_docker.py

docker-check:
	uv run --locked --no-sync python docker/context.py
	uv run --locked --no-sync python scripts/check_docker_context.py

install:
	uv sync --locked
	npm ci --ignore-scripts
	$(MAKE) install-checkout-hook

debug:
	uv run python manage.py debugserver --settings=config.settings.dev --processes 4 --max-rss 512

run:
	uv run python manage.py runbolt --settings=config.settings.prod --processes 8 \
		--max-rss 512 --workers-lifetime 21600 --respawn-failed-workers

migrate:
	uv run python manage.py migrate

test: test-django test-javascript test-rust

test-django:
	uv run python manage.py test tests --settings=tests.passport_test_settings

test-javascript:
	npm test

test-rust:
	cargo test --locked --manifest-path plugins/example_plugin/Cargo.toml
	cargo test --locked --manifest-path plugins/rust_example_plugin/Cargo.toml

rebuild:
	uv build --wheel

flush:
	uv run python manage.py flush

superuser:
	uv run python manage.py createsuperuser

install-checkout-hook:
	"$(CHECKOUT_PYTHON)" -I -B scripts/install_checkout_hook.py --source .
