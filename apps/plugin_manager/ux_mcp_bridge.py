"""Bounded, local-only adapter around the pinned UI/UX Suite MCP server.

The debug supervisor owns this process. Inputs cannot select a filesystem path,
URL, browser mode, knowledge filename, shell command, or output destination.
Only frontend source is copied into a temporary audit snapshot; the upstream
static analyzer never sees uploads, credentials, databases, or server code.
"""
import argparse
import json
import os
from pathlib import Path
import selectors
import signal
import stat
import subprocess
import sys
import tempfile
import time

VERSION = "0.6.1"
AUDIT_TOOL = "uiux_audit_local"
GUIDANCE_TOOL = "uiux_guidance"
TOPICS = {"accessibility", "forms", "navigation", "errors", "layout", "motion"}
FRONTEND_ROOTS = (
    "resources/static/css", "resources/templates", "apps/shared/templates",
    "apps/plugin_manager/templates", "apps/plugins/bim_model_manager/templates",
    "apps/plugins/example_plugin/templates", "apps/plugins/rust_example_plugin/templates",
)
SOURCE_SUFFIXES = {".css", ".scss", ".sass", ".html", ".htm", ".jinja2"}
MAX_FILES = 512
MAX_FILE_BYTES = 512 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024
MAX_REQUEST_BYTES = 16 * 1024
MAX_REPLY_BYTES = 4 * 1024 * 1024
ANNOTATIONS = {"readOnlyHint": True, "destructiveHint": False,
               "idempotentHint": True, "openWorldHint": False}
TOOLS = [
    {"name": AUDIT_TOOL,
     "description": "Run UI/UX Suite's static audit over this checkout's bounded CSS and Django template source. No browser or network access. Findings need rendered-page verification.",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
     "annotations": ANNOTATIONS},
    {"name": GUIDANCE_TOOL,
     "description": "Read local UI/UX Suite guidance for accessibility, forms, navigation, errors, layout or motion.",
     "inputSchema": {"type": "object", "properties": {"topic": {"type": "string", "enum": sorted(TOPICS)}},
                     "required": ["topic"], "additionalProperties": False},
     "annotations": ANNOTATIONS},
]


def validate_arguments(name, arguments):
    if not isinstance(arguments, dict):
        raise ValueError("Tool arguments must be an object.")
    if name == AUDIT_TOOL:
        if arguments:
            raise ValueError("The audit accepts no path, URL, mode or output options.")
        return {}
    if name == GUIDANCE_TOOL:
        if set(arguments) != {"topic"} or not isinstance(arguments["topic"], str) or arguments["topic"] not in TOPICS:
            raise ValueError("Choose one of the listed UI/UX topics.")
        topic = arguments["topic"]
        if topic in {"accessibility", "layout"}:
            return {"category": topic}
        return {"search": {"forms": "form", "navigation": "navigation",
                           "errors": "error", "motion": "reduced-motion"}[topic]}
    raise ValueError("This UI/UX tool is not enabled.")


def source_snapshot(project_root, snapshot_root):
    """Copy only regular frontend files, preserving relative source locations."""
    root = Path(project_root).resolve(strict=True)
    snapshot = Path(snapshot_root)
    aliases = {}
    total = 0
    for relative in FRONTEND_ROOTS:
        source_root = root / relative
        if not source_root.is_dir() or source_root.is_symlink():
            continue
        if not source_root.resolve().is_relative_to(root):
            continue
        for folder, directories, filenames in os.walk(source_root, followlinks=False):
            directories[:] = sorted(name for name in directories
                                    if not name.startswith(".") and not (Path(folder) / name).is_symlink())
            for filename in sorted(filenames):
                source = Path(folder) / filename
                if source.suffix.lower() not in SOURCE_SUFFIXES or source.is_symlink():
                    continue
                if not source.resolve().is_relative_to(root):
                    continue
                descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
                with os.fdopen(descriptor, "rb") as stream:
                    info = os.fstat(stream.fileno())
                    if not stat.S_ISREG(info.st_mode):
                        continue
                    if info.st_size > MAX_FILE_BYTES:
                        raise ValueError("A frontend source file exceeds the audit size limit.")
                    content = stream.read(MAX_FILE_BYTES + 1)
                total += len(content)
                if len(content) > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES or len(aliases) >= MAX_FILES:
                    raise ValueError("Frontend source exceeds the bounded audit size/file limit.")
                relative_source = source.relative_to(root)
                # The vendor supports HTML but not Django's .jinja2 suffix.
                target_relative = relative_source.with_name(relative_source.name + ".html") if source.suffix == ".jinja2" else relative_source
                destination = snapshot / target_relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(content)
                aliases[target_relative.as_posix()] = relative_source.as_posix()
    return aliases


def rewrite_result(result, snapshot, aliases):
    prefix = str(snapshot) + os.sep

    def rewrite(value):
        if isinstance(value, dict):
            return {key: rewrite(item) for key, item in value.items()}
        if isinstance(value, list):
            return [rewrite(item) for item in value]
        if isinstance(value, str):
            value = value.removeprefix(prefix)
            return aliases.get(value, value)
        return value

    for block in result.get("content", []):
        if block.get("type") == "text":
            data = rewrite(json.loads(block["text"]))
            data["projectPath"] = "fixed frontend source snapshot"
            data["sourceFiles"] = len(aliases)
            data["analysisScope"] = "Static CSS/HTML/Django templates only; suggestions require live UI verification. No source is transmitted."
            block["text"] = json.dumps(data, ensure_ascii=True)
    return result


class Upstream:
    def __init__(self, command):
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=sys.stderr, bufsize=0)
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ)
        self.buffer = b""
        self.sequence = 0

    def call(self, method, params=None):
        self.sequence += 1
        request = {"jsonrpc": "2.0", "id": self.sequence, "method": method}
        if params is not None:
            request["params"] = params
        self.process.stdin.write(json.dumps(request).encode() + b"\n")
        self.process.stdin.flush()
        deadline = time.monotonic() + 30
        while True:
            if b"\n" in self.buffer:
                line, self.buffer = self.buffer.split(b"\n", 1)
                if len(line) > MAX_REPLY_BYTES:
                    raise ValueError("UI/UX output exceeds the reply limit.")
                response = json.loads(line)
                if response.get("id") == self.sequence:
                    return response
                continue
            if len(self.buffer) > MAX_REPLY_BYTES:
                raise ValueError("UI/UX output exceeds the reply limit.")
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not self.selector.select(remaining):
                raise TimeoutError("UI/UX source analysis timed out.")
            chunk = os.read(self.process.stdout.fileno(), 65536)
            if not chunk:
                raise RuntimeError("The UI/UX analyzer stopped.")
            self.buffer += chunk

    def close(self):
        self.selector.close()
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
        self.process.stdin.close()
        self.process.stdout.close()


def tool_result(upstream, name, arguments, project_root):
    validated = validate_arguments(name, arguments)
    if name == GUIDANCE_TOOL:
        return upstream.call("tools/call", {"name": "uiux_knowledge_query", "arguments": validated})["result"]
    with tempfile.TemporaryDirectory(prefix="cadevil-uiux-") as folder:
        aliases = source_snapshot(project_root, folder)
        result = upstream.call("tools/call", {"name": "uiux_audit_run", "arguments": {
            "projectPath": folder, "depth": "quick", "format": "json"}})["result"]
        return rewrite_result(result, folder, aliases)


def serve(upstream, project_root, incoming=sys.stdin, outgoing=sys.stdout):
    for line in iter(lambda: incoming.readline(MAX_REQUEST_BYTES + 1), ""):
        request = None
        try:
            if len(line.encode()) > MAX_REQUEST_BYTES:
                raise ValueError("The request exceeds the UI/UX input limit.")
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("A JSON-RPC request object is required.")
            method = request.get("method")
            if "id" not in request:
                continue
            response = {"jsonrpc": "2.0", "id": request["id"]}
            if method == "initialize":
                response = upstream.call(method, request.get("params"))
                response["id"] = request["id"]
                response["result"]["serverInfo"] = {"name": "cadevil-ui-ux-suite", "version": VERSION}
            elif method == "tools/list":
                response["result"] = {"tools": TOOLS}
            elif method == "tools/call":
                params = request.get("params", {})
                response["result"] = tool_result(upstream, params.get("name"), params.get("arguments", {}), project_root)
            elif method == "ping":
                response["result"] = {}
            else:
                response["error"] = {"code": -32601, "message": "Method is not enabled by this debug adapter."}
        except (ValueError, TypeError, KeyError, AttributeError, OSError, RuntimeError, TimeoutError):
            response = {"jsonrpc": "2.0", "id": request.get("id") if isinstance(request, dict) else None,
                        "result": {"content": [{"type": "text", "text": "UI/UX request rejected or bounded local analysis failed."}], "isError": True}}
        outgoing.write(json.dumps(response) + "\n")
        outgoing.flush()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node", required=True)
    parser.add_argument("--server", required=True)
    parser.add_argument("--project", required=True)
    args = parser.parse_args()
    upstream = Upstream([args.node, args.server])
    def stop(*_):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, stop)
    try:
        serve(upstream, args.project)
    finally:
        upstream.close()


if __name__ == "__main__":
    main()
