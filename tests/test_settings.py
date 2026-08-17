from urllib.parse import urlparse

import pytest


def _parse_db_url(url: str) -> dict:
    """Mirror the DATABASE_URL parsing logic in project/settings.py."""
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme in ("sqlite", "sqlite3"):
        path = parsed.path or ""
        if path.startswith("//"):
            path = path[1:]
        elif path.startswith("/"):
            path = path.lstrip("/")
        return {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": path or "db.sqlite3",
        }
    elif scheme in ("postgres", "postgresql"):
        return {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": parsed.path.lstrip("/"),
            "USER": parsed.username or "",
            "PASSWORD": parsed.password or "",
            "HOST": parsed.hostname or "localhost",
            "PORT": str(parsed.port or 5432),
        }
    else:
        raise ValueError(f"DATABASE_URL scheme {scheme!r} not supported")


def test_sqlite_url_engine():
    db = _parse_db_url("sqlite:///myapp.db")
    assert db["ENGINE"] == "django.db.backends.sqlite3"
    assert "myapp.db" in db["NAME"]


def test_postgres_scheme():
    db = _parse_db_url("postgres://user:pass@localhost:5432/mydb")
    assert db["ENGINE"] == "django.db.backends.postgresql"
    assert db["NAME"] == "mydb"
    assert db["USER"] == "user"
    assert db["HOST"] == "localhost"
    assert db["PORT"] == "5432"


def test_postgresql_scheme():
    db = _parse_db_url("postgresql://user:pass@db.example.com/prod")
    assert db["ENGINE"] == "django.db.backends.postgresql"
    assert db["HOST"] == "db.example.com"


def test_unknown_scheme_raises():
    with pytest.raises(ValueError, match="not supported"):
        _parse_db_url("mysql://user:pass@localhost/mydb")


def test_sqlite_absolute_path():
    # sqlite:////abs/path.db → /abs/path.db (four-slash → absolute)
    db = _parse_db_url("sqlite:////var/data/myapp.db")
    assert db["NAME"] == "/var/data/myapp.db"
