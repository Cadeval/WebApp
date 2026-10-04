"""Exec the native production server; initialization remains an explicit task."""
import os
import sys


def integer_setting(name, default, minimum, maximum):
    try:
        value = int(os.environ.get(name, default))
    except ValueError:
        raise SystemExit(f"{name} must be an integer.") from None
    if not minimum <= value <= maximum:
        raise SystemExit(f"{name} must be between {minimum} and {maximum}.")
    return str(value)


def main():
    arguments = sys.argv[1:] or ["serve"]
    if arguments == ["serve"]:
        arguments = [
            sys.executable, "/app/manage.py", "runbolt", "--settings=config.settings.container",
            "--host=0.0.0.0", "--port=8000",
            "--processes=" + integer_setting("CADEVIL_HTTP_PROCESSES", "2", 1, 32),
            "--max-rss=" + integer_setting("CADEVIL_HTTP_MAX_RSS", "512", 128, 16384),
            "--workers-lifetime=21600", "--respawn-failed-workers",
        ]
    os.execvp(arguments[0], arguments)


if __name__ == "__main__":
    main()
