"""Debug-only Docker MCP adapter: fixed Engine scope and sanitized read tools.

The independently installed mcp-server-docker handlers run in a bounded child.
Its upstream MCP app, mutation handlers, prompts and resources are never mounted.
The adapter rejects caller-selected hosts, labels, paths and commands.
"""
import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

PROVIDER_VERSION = "0.3.0"
DEFAULT_HOST = "ssh://codex@meanderingmind.me:25519"
PROJECT = "cadevil-v014"
PROJECT_LABEL = "com.docker.compose.project=" + PROJECT
INFO_TOOL = "docker_provider_info"
READ_TOOLS = ("docker_list_containers", "docker_list_images", "docker_list_networks")
TOOL_NAMES = (INFO_TOOL, *READ_TOOLS)
MAX_REQUEST_BYTES = 8192
MAX_OUTPUT_BYTES = 256 * 1024
SECONDS = 20
ANNOTATIONS = {"readOnlyHint": True, "destructiveHint": False,
               "idempotentHint": True, "openWorldHint": False}
TOOLS = [{"name": name, "description": description, "annotations": ANNOTATIONS,
          "inputSchema": {"type": "object", "additionalProperties": False,
                          "properties": {} if name == INFO_TOOL else {
                              "limit": {"type": "integer", "minimum": 1,
                                        "maximum": 100, "default": 20}}}}
         for name, description in (
             (INFO_TOOL, "Describe the pinned debug Docker provider, fixed Engine and Compose scope."),
             (READ_TOOLS[0], "List sanitized container health and image summaries in the Cadevil Compose project."),
             (READ_TOOLS[1], "List image identities and sizes referenced by the Cadevil Compose project."),
             (READ_TOOLS[2], "List sanitized networks owned by the Cadevil Compose project."))]


def validate_host(host):
    if host == DEFAULT_HOST or (isinstance(host, str)
            and re.fullmatch(r"unix:///[A-Za-z0-9_./-]+\.sock", host)
            and ".." not in Path(host.removeprefix("unix://")).parts):
        return host
    raise ValueError("Choose the reviewed Cadevil SSH Engine or an explicit local Unix Docker socket.")


def validate_arguments(name, arguments):
    if name not in TOOL_NAMES or not isinstance(arguments, dict):
        raise ValueError("Choose an enabled read-only Docker tool.")
    if name == INFO_TOOL:
        if arguments:
            raise ValueError("Provider info takes no arguments.")
        return {}
    if set(arguments) - {"limit"}:
        raise ValueError("Hosts, commands, IDs, labels, logs and custom filters are not accepted.")
    limit = arguments.get("limit", 20)
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Choose an integer limit from 1 to 100.")
    return {"limit": limit}


def safe_text(value, maximum=160):
    return value[:maximum] if isinstance(value, str) and not any(ord(c) < 32 for c in value) else None


def container_summary(value):
    labels, state = value.get("labels") or {}, value.get("state") or {}
    if labels.get("com.docker.compose.project") != PROJECT:
        raise ValueError("Docker returned an object outside the fixed Compose project.")
    image = value.get("image") or {}
    return {"id": safe_text(value.get("id")), "name": safe_text(value.get("name")),
            "service": safe_text(labels.get("com.docker.compose.service")),
            "status": safe_text(value.get("status")), "image_id": safe_text(image.get("id")),
            "health": safe_text((state.get("Health") or {}).get("Status")),
            "restart_count": value.get("restart_count") if type(value.get("restart_count")) is int else None}


def image_summary(value):
    return {"id": safe_text(value.get("id")),
            "tags": [tag for item in (value.get("tags") or [])[:20]
                     if (tag := safe_text(item)) is not None],
            "bytes": value.get("size") if type(value.get("size")) is int else None}


def network_summary(value):
    if (value.get("labels") or {}).get("com.docker.compose.project") != PROJECT:
        raise ValueError("Docker returned an object outside the fixed Compose project.")
    return {key: safe_text(value.get(key)) for key in ("id", "name", "driver", "scope")}


class ScopedImages:
    """Only resolve image IDs already belonging to scoped containers."""
    def __init__(self, client):
        self.client = client

    def list(self, **_ignored):
        containers = self.client.containers.list(all=True, filters={"label": PROJECT_LABEL})
        if len(containers) > 100:
            raise ValueError("Compose project exceeds the 100-container inspection bound.")
        identities = sorted({container.attrs.get("Image") for container in containers})
        if any(not isinstance(value, str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", value)
               for value in identities):
            raise ValueError("The Engine returned an invalid image identity.")
        return [self.client.images.get(value) for value in identities]


def engine_read(name, arguments, host, *, client=None, upstream=None):
    """Invoke reviewed upstream list handlers with a forced scope, then project output."""
    arguments = validate_arguments(name, arguments)
    host = validate_host(host)
    installed = importlib.metadata.version("mcp-server-docker")
    if installed != PROVIDER_VERSION:
        raise ValueError("The installed Docker MCP provider does not match the reviewed version.")
    if name == INFO_TOOL:
        return {"provider": "mcp-server-docker", "version": installed,
                "license": "GPL-3.0-only", "engine": "reviewed SSH Engine" if host == DEFAULT_HOST else "local Unix socket",
                "compose_project": PROJECT, "tools": list(TOOL_NAMES),
                "read_only": True, "sanitized": True}
    owned = client is None
    if upstream is None:
        from mcp_server_docker import server as upstream
    if client is None:
        import docker
        # Environment-selected hosts, TLS certificates and proxy settings are ignored.
        client = docker.DockerClient(base_url=host, timeout=8, use_ssh_client=True)
    try:
        scoped = SimpleNamespace(containers=client.containers, networks=client.networks,
                                 images=ScopedImages(client))
        context = SimpleNamespace(request_context=SimpleNamespace(
            lifespan_context=upstream.AppContext(docker=scoped)))
        if name == READ_TOOLS[0]:
            raw = upstream.list_containers(context, all=True,
                filters=upstream.ListContainersFilters(label=[PROJECT_LABEL]))
            normalize = container_summary
        elif name == READ_TOOLS[1]:
            raw = upstream.list_images(context)
            normalize = image_summary
        else:
            raw = upstream.list_networks(context,
                filters=upstream.ListNetworksFilter(label=[PROJECT_LABEL]))
            normalize = network_summary
        if not isinstance(raw, list) or len(raw) > 100:
            raise ValueError("Compose project exceeds the 100-object inspection bound.")
        values = sorted((normalize(value) for value in raw), key=lambda value: value.get("id") or "")
        return {"compose_project": PROJECT, "total": len(values),
                "items": values[:arguments["limit"]], "truncated": len(values) > arguments["limit"]}
    finally:
        if owned:
            client.close()


def bounded_worker(command, **options):
    timeout = options.pop("timeout")
    options.pop("capture_output")
    options.pop("check")
    options.pop("text")
    payload = options.pop("input")
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, start_new_session=True, **options)
    output = bytearray()
    deadline = time.monotonic() + timeout
    try:
        process.stdin.write(payload.encode())
        process.stdin.close()
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, timeout)
                for key, _ in selector.select(min(remaining, .2)):
                    chunk = os.read(key.fileobj.fileno(), min(65536, MAX_OUTPUT_BYTES - len(output) + 1))
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    output.extend(chunk)
                    if len(output) > MAX_OUTPUT_BYTES:
                        raise ValueError("Docker output exceeded its bounded read limit.")
        process.wait(timeout=max(.001, deadline - time.monotonic()))
        return SimpleNamespace(returncode=process.returncode, stdout=output.decode())
    finally:
        # SSH transports share this newly owned group; never leave orphan clients.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        if process.poll() is None:
            process.wait(timeout=2)
        process.stdout.close()
        if not process.stdin.closed:
            process.stdin.close()


class DockerProvider:
    def __init__(self, host, runtime, runner=bounded_worker):
        self.host, self.runtime, self.runner = validate_host(host), Path(runtime), runner

    def call(self, name, arguments):
        arguments = validate_arguments(name, arguments)
        # Each request owns a child, whose deadline also bounds SSH establishment.
        environment = {"PATH": str(self.runtime / "bin") + ":/usr/bin:/bin", "LANG": "C.UTF-8",
                       "CADEVIL_DEBUG_MCP": "1", "HOME": str(Path.home())}
        if os.environ.get("SSH_AUTH_SOCK"):
            environment["SSH_AUTH_SOCK"] = os.environ["SSH_AUTH_SOCK"]
        process = self.runner([sys.executable, str(Path(__file__).resolve()), "--worker", name,
                               "--host", self.host, "--runtime", str(self.runtime)],
                              input=json.dumps(arguments), text=True, capture_output=True,
                              env=environment, timeout=SECONDS, check=False)
        if process.returncode or len(process.stdout.encode()) > MAX_OUTPUT_BYTES:
            raise ValueError("The scoped Docker read failed or exceeded its output limit; private diagnostics are withheld.")
        return json.loads(process.stdout)


def serve(provider, incoming=sys.stdin, outgoing=sys.stdout):
    for line in iter(lambda: incoming.readline(MAX_REQUEST_BYTES + 1), ""):
        request = None
        try:
            if len(line.encode()) > MAX_REQUEST_BYTES:
                raise ValueError("Docker request exceeds the input limit.")
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("A JSON-RPC request object is required.")
            if "id" not in request:
                continue
            response = {"jsonrpc": "2.0", "id": request["id"]}
            method = request.get("method")
            if method == "initialize":
                response["result"] = {"protocolVersion": request.get("params", {}).get("protocolVersion", "2025-11-25"),
                    "capabilities": {"tools": {}}, "serverInfo": {"name": "cadevil-docker-readonly", "version": PROVIDER_VERSION}}
            elif method == "tools/list":
                response["result"] = {"tools": TOOLS}
            elif method == "tools/call":
                params = request.get("params", {})
                value = provider.call(params.get("name"), params.get("arguments", {}))
                response["result"] = {"content": [{"type": "text", "text": json.dumps(value)}]}
            elif method == "ping":
                response["result"] = {}
            else:
                response["error"] = {"code": -32601, "message": "Only allowlisted Docker tools are enabled."}
        except (ValueError, TypeError, KeyError, AttributeError, OSError, subprocess.TimeoutExpired):
            response = {"jsonrpc": "2.0", "id": request.get("id") if isinstance(request, dict) else None,
                        "result": {"content": [{"type": "text", "text": "Docker read rejected or unavailable; private diagnostics are withheld."}], "isError": True}}
        outgoing.write(json.dumps(response) + "\n")
        outgoing.flush()


def main():
    if os.environ.get("CADEVIL_DEBUG_MCP") != "1":
        raise SystemExit("Docker MCP must be started by the debug launcher.")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", required=True)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--worker", choices=TOOL_NAMES)
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    if args.worker:
        try:
            arguments = json.loads(sys.stdin.read(MAX_REQUEST_BYTES + 1))
            print(json.dumps(engine_read(args.worker, arguments, args.host)))
        except Exception:
            raise SystemExit(1) from None
    else:
        serve(DockerProvider(args.host, args.runtime))


if __name__ == "__main__":
    main()
