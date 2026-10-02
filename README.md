# CadEval

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python: 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://python.org)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![Django: 6.0+](https://img.shields.io/badge/django-6.0+-green.svg)](https://www.djangoproject.com/)
[![Django CI](https://github.com/Cadeval/WebApp/actions/workflows/django.yml/badge.svg)](https://github.com/Cadeval/WebApp/actions/workflows/django.yml)

Please note that this is a WIP,

CadEval is an open-source building analysis platform that enables architects and engineers to evaluate and compare
building parameters using IFC models. Built with Django and IFCOpenShell, it provides powerful tools for building
performance analysis, spatial optimization, and design validation. It is the product from a TU Master Thesis project.

## 🚀 Planned Features

- **IFC Model Analysis**
    - Automated property extraction
    - Geometry validation
    - Spatial relationship analysis
    - Material quantity takeoffs

- **Building Comparison**
    - Side-by-side model comparison
    - Parameter-based evaluation
    - Custom metric definition
    - Automated reporting

- **Web Interface** NOT IMPLEMENTED YET
    - Interactive 3D visualization
    - Real-time parameter updates
    - Collaborative analysis tools
    - Export capabilities

## ⚡ Quick Start

```bash
# Clone the repository
git clone https://github.com/cadeval/cadeval
cd cadeval

# Install the locked Python environment (including OpenStudio)
uv sync --frozen

# Run migrations
cd apps
uv run python manage.py migrate

# Create superuser
uv run python manage.py createsuperuser

# Run development server
uv run python manage.py runserver
```

Visit `http://localhost:8000/admin` to access the admin interface.

## 📋 Requirements

- Python 3.12+
- Django 6.0+
- IFCOpenShell 0.8.5+
- OpenStudio 3.11
- PostgreSQL 13+
- Redis (for Celery)

## 🔧 Configuration

Key settings in `.env`:

```env
DEBUG=True
SECRET_KEY=your-secret-key
DATABASE_URL=postgres://user:password@localhost:5432/cadeval
REDIS_URL=redis://localhost:6379/0
OPENSTUDIO_CLI_PATH=/optional/path/to/openstudio
OPENSTUDIO_TIMEOUT_SECONDS=900
```

### OpenStudio energy simulation

Energy calculations use a real OpenStudio/EnergyPlus workflow built from IFC space geometry. Because architectural IFC
files usually omit occupancy, schedules, constructions, and HVAC operation, the generated model uses the documented
residential defaults shown with each result and ideal-loads HVAC. Results are therefore labelled as an assumption-based
simulation.

1. Open the user page and upload one or more EnergyPlus Weather (`.epw`) files to the private **Weather file library**.
2. Upload an IFC model from the file manager.
3. Select one of your EPW files beside that model and choose **Calculate**. Select **No energy simulation** to calculate
   IFC/material/facade metrics without invoking OpenStudio.

The OpenStudio CLI is discovered from `PATH` or its Python package by default. Set `OPENSTUDIO_CLI_PATH` only when it is
installed elsewhere. Simulation failures are stored separately from IFC results so facade and material metrics remain
available.

## 🏗️ Project Structure

The repository combines the Django/ASGI application, frontend resources, a plugin framework, and two bundled
Rust/WebAssembly plugins:

```
.
├── Dockerfile                  # Application container image
├── docker-compose.yml          # Web app, PostgreSQL, and Redis services
├── Makefile                    # Development, test, and WebAssembly commands
├── package.json                # Frontend and WebAssembly build scripts
├── pyproject.toml              # Python dependencies, tooling, and plugin entry points
├── uv.lock                     # Reproducible Python dependency lockfile
├── data/                       # Runtime uploads and persistent application data
└── apps/
    ├── manage.py               # Django management entry point
    ├── webapp/                 # Django settings, URLs, ASGI/WSGI, and logging
    ├── model_manager/          # Core models, views, API, tasks, and WebSocket consumers
    │   ├── migrations/         # Database migration history
    │   └── templatetags/       # Custom template helpers
    ├── plugin_manager/         # Plugin discovery, lifecycle, uploads, API, and CLI
    │   ├── management/commands/
    │   └── migrations/
    ├── ifc_extractor/          # IFC/OpenStudio energy and plotting utilities
    ├── example_plugin/         # Rust-based IFC editor plugin and Django integration
    │   └── apps/lib.rs          # IFC editor core compiled to WebAssembly
    ├── rust_example_plugin/    # Rust Snake plugin and Django integration
    │   └── apps/lib.rs          # Game core compiled to WebAssembly
    └── resources/
        ├── templates/          # Jinja2 application and plugin templates
        └── static/             # CSS, images, JavaScript, and WebAssembly assets
```

## 🔌 API Documentation

Once this part is finished the api end points will be implemented as follows.

API documentation is available at `/api/docs/` when running the server. Key endpoints:

- `/api/models/` - IFC model management
- `/api/analysis/` - Building analysis
- `/api/compare/` - Model comparison
- `/api/reports/` - Report generation

## Rust IFC Editor Plugin

The bundled `cadevil.example.editor` plugin adds **IFC Editor** to the SPA navigation. It opens local IFC STEP files
without uploading or replacing the server model, then lets users browse entities and edit supported positional
attributes. Quoted strings, numbers, enumerations, entity references, and unset values are validated by the
dependency-free Rust library in
`src/apps/plugins/example_plugin/src/lib.rs`; nested values remain visible but read-only.

The compiled WebAssembly runs with no host imports inside a dedicated module worker. Untouched source bytes are
preserved, hot reload restarts the worker, and **Download modified IFC** creates a local Save As copy on the main
thread. The editor does not regenerate geometry, so a downloaded file must be uploaded again before its changes can
appear in the 3D viewer.

Install Rust and its WebAssembly target, then rebuild the checked-in browser artifact after changing the editor core:

```bash
rustup target add wasm32-unknown-unknown
npm run build:example-plugin-wasm
# Or run the Rust builder directly:
cargo run --manifest-path apps/example_plugin/Cargo.toml --bin build-wasm
```

### Rust Snake plugin

The bundled `cadevil.rust-example.editor` plugin adds **Rust Snake** to the SPA navigation. Selecting it loads a
script-free game fragment into the content container with HTMX. Movement, food placement, growth, scoring, and collision
rules are implemented in the dependency-free Rust library at
`src/apps/plugins/rust_example_plugin/apps/lib.rs`. The compiled WebAssembly runs inside a dedicated module worker; the
host controller only validates snapshots, draws the canvas, and forwards focused keyboard or touch input.

Install Rust and its WebAssembly target, then rebuild the checked-in browser artifact after changing the Rust source:

```bash
rustup target add wasm32-unknown-unknown
npm run build:rust-example-plugin-wasm
# Or run the Rust builder directly:
cargo run --manifest-path apps/rust_example_plugin/Cargo.toml --bin build-wasm
```

### Uploaded plugins

**Plugin Manager** (`/plugins/manage/`) is the site-wide catalog. Authenticated users can browse approved tools and
publish signed ZIP, TAR, tar.gz or tar.xz browser packages using their own registered signing key. Generate a key in
the web interface, sign your archive locally with the downloaded CLI, and upload it for administrator review. Package
metadata comes from `plugin.json`; individual JavaScript/WASM uploads are rejected.

Administrators review uploaded code and control site availability. **Plugin Store** (`/plugins/store/`) contains each
user's explicit workflow selections. Adding or removing a plugin changes only that user's collection. Navigation,
editor panels, workflow pages and uploaded worker assets require both a personal selection and current site approval.
Existing users start with an empty collection: add BIM Workspace from Manager to resume BIM workflows. Saved models
and reports remain associated with their owners.

Uploaded browser plugins have an independent worker page and do not require selecting BIM Workspace. Administrator
disable, discovery failure, environment mismatch or signing-key revocation blocks use while retaining the user's
choice in Store. Developer MCP tools remain administrator-controlled debug services and are excluded from personal
workflow collections.

Use **Reload plugins** in the Plugin Manager to discover newly installed plugins without restarting the application. The
same audited operation is available through `reload_plugins()`, `POST /plugins/reload/`, and:

```bash
uv run python manage.py plugins reload
```

Uploaded code is never imported by Python or inserted into the page. JavaScript runs only in a dedicated module worker
served with a restrictive CSP that blocks network connections, nested workers and object loading; module imports are
limited to the verified package assets. WebAssembly is instantiated with no host imports inside a host-owned worker. Worker messages are
schema-checked, execution has a time limit, and workers are terminated when unloaded or replaced. Installed Python
entry-point packages are administrator-installed trusted host integrations and must be reviewed like any other server
dependency.

The administrator-managed external repository proposal is documented in
[docs/external-plugin-repositories.md](docs/external-plugin-repositories.md). Repository fetching is not enabled yet;
the design keeps remote catalog trust, package review and personal workflow selection separate.

## 🧪 Testing

```bash
# Install the exact locked environment
make install

# Apply database migrations
make migrate

# Run Django/Python, JavaScript, and Rust tests
make test
```

If an existing checkout reports
`no such table: archicad_eval_epw_uploads`, apply the new user weather-library migration before restarting the
application:

```bash
uv run python manage.py migrate model_manager
```

Migration `0009_move_epw_uploads_to_user_library` creates the table and moves any weather files stored by the earlier
per-IFC upload implementation into the owning user's reusable library.

## 🤝 Contributing

We welcome contributions! Please see
our [Contributing Guidelines](https://github.com/Cadeval/.github/blob/main/profile/CONTRIBUTING.md)
and [Code of Conduct](https://github.com/Cadeval/.github/blob/main/profile/CODE_OF_CONDUCT.md).

## 🔒 Security

To report security vulnerabilities, please email security@cadeval.org.

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- [IFCOpenShell](http://ifcopenshell.org/) for IFC processing
- [Django](https://www.djangoproject.com/) for the web framework
- All our [contributors](CONTRIBUTORS.md)

## 📮 Contact

- Website: https://cadeval.org
- Email: info@cadeval.org

---

Made with ❤️ by the CadEval team
