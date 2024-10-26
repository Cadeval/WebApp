# CadEval

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python: 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://python.org)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![Django: 4.2+](https://img.shields.io/badge/django-4.2+-green.svg)](https://www.djangoproject.com/)

CadEval is an open-source building analysis platform that enables architects and engineers to evaluate and compare building parameters using IFC models. Built with Django and IFCOpenShell, it provides powerful tools for building performance analysis, spatial optimization, and design validation.

## 🚀 Features

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

- **Web Interface**
  - Interactive 3D visualization
  - Real-time parameter updates
  - Collaborative analysis tools
  - Export capabilities

## ⚡ Quick Start

```bash
# Clone the repository
git clone https://github.com/cadeval/cadeval
cd cadeval

# Set up virtual environment
python -m venv venv
source venv/bin/activate  # Unix
.\venv\Scripts\activate   # Windows

# Install requirements
pip install -r requirements.txt

# Set up environment variables
cp .env.example .env
# Edit .env with your settings

# Run migrations
python manage.py migrate

# Create superuser
python manage.py createsuperuser

# Run development server
python manage.py runserver
```

Visit `http://localhost:8000/admin` to access the admin interface.

## 📋 Requirements

- Python 3.9+
- Django 4.2+
- IFCOpenShell 0.7+
- PostgreSQL 13+
- Redis (for Celery)

## 🔧 Configuration

Key settings in `.env`:

```env
DEBUG=True
SECRET_KEY=your-secret-key
DATABASE_URL=postgres://user:password@localhost:5432/cadeval
REDIS_URL=redis://localhost:6379/0
```

## 🏗️ Project Structure

```
├── src/
│   ├── manage.py                   # Django management script
│   ├── model_manager/              # Main application module
│   │   ├── ifc_extractor/          # IFC file processing module
│   │   │   ├── data_models.py      # Python dataclass models
│   │   │   ├── energy_modeling.py
│   │   │   └── energy.py
│   │   ├── models.py               # Database models
│   │   ├── views.py                # View controllers
│   │   └── urls.py                 # URL routing
│   └── webapp/                     # Web application configuration
│       ├── settings.py
│       └── urls.py
```

## 🔌 API Documentation

Once this part is finished the api end points will be implemented as follows.
API documentation is available at `/api/docs/` when running the server. Key endpoints:

- `/api/models/` - IFC model management
- `/api/analysis/` - Building analysis
- `/api/compare/` - Model comparison
- `/api/reports/` - Report generation

## 🧪 Testing

```bash
# Clone the repository
git clone https://github.com/cadeval/[project-name]
gh repo clone Cadeval/[project-name]

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Unix
.\venv\Scripts\activate   # Windows

# Install dependencies
pip install -r requirements.txt

# Or using uv by [astral](https://docs.astral.sh/uv)
uv venv

# Please note that the venv dir in that case is .venv
# Activation follows the same pattern as above

# A Python version can be requested, e.g., to create a virtual environment with Python 3.11:
# Note this requires the requested Python version to be available on the system. However, if unavailable, uv will download Python for you. See the Python version documentation for more details.
uv venv --python 3.11

# Install a package in the new virtual environment
uv pip install ruff

# Run migrations
python manage.py migrate

# Start development server
python manage.py runserver
```

## 🤝 Contributing

We welcome contributions! Please see our [Contributing Guidelines](CONTRIBUTING.md) and [Code of Conduct](CODE_OF_CONDUCT.md).

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