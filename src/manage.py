#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""

import gc
import os
import sys

if not gc.isenabled():
    gc.enable()


def main():
    """Run administrative tasks."""
    os.environ["DJANGO_SETTINGS_MODULE"] = "webapp.settings"
    # os.environ["PYTHONOPTIMIZE"] = "1"
    # os.environ["PYTHONMALLOC"] = "malloc"
    # os.environ["PYTHONUNBUFFERED"] = "1"
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            """
           Couldn't import Django.
           Are you sure it's installed and
           available on your PYTHONPATH environment variable?
           Did you "forget to activate a virtual environment?
           """
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
