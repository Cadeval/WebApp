"""Check the local HTTP worker without exposing response bodies or secrets."""
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def main():
    # Match the configured host allowlist without requiring a public DNS lookup.
    host = next((host.strip() for host in os.environ.get("ALLOWED_HOSTS", "").split(",") if host.strip()), "localhost")
    if host.startswith("."):
        host = host[1:]
    if host == "*" or any(character in host for character in "\r\n"):
        host = "localhost"
    try:
        request = Request("http://127.0.0.1:8000/healthz", headers={"Host": host})
        with urlopen(request, timeout=3) as response:
            return 0 if response.status == 200 and response.read(128) == b'{"status": "ready"}' else 1
    except (OSError, ValueError, HTTPError, URLError):
        return 1


if __name__ == "__main__":
    sys.exit(main())
