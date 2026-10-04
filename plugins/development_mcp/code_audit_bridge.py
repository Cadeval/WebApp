"""Read-only, offline code audits owned by the development MCP supervisor.

Official Semgrep and Ruff CLI binaries inspect a bounded source snapshot. MCP
arguments cannot choose commands, paths, URLs, rules, credentials or writes.
Neither analyzer executes project code or applies fixes.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import sys
import tempfile
import time

VERSION = "1.0.0"
SEMGREP_VERSION = "1.179.0"
RUFF_VERSION = "0.16.10"
RULES_COMMIT = "a84ff9cc2453ca91d581380de4b8b3f272f6f4be"
AUDIT_TOOL = "code_audit_local"
INFO_TOOL = "code_audit_info"
SOURCE_ROOTS = ("shared", "mycelium", "plugin_manager", "plugins", "config", "scripts", "docker", "resources/static/js",
                "resources/static/css", "resources/styles", "resources/templates", "tests", ".github/workflows")
TOP_LEVEL_FILES = {"manage.py", "pyproject.toml", "Makefile", "package.json", "package-lock.json",
                   "uv.lock", "Dockerfile", "docker-compose.yml", "compose.yml", "compose.yaml",
                   "hatch_build.py", "rust-toolchain.toml"}
SOURCE_SUFFIXES = {".py", ".js", ".mjs", ".jsx", ".ts", ".tsx", ".rs",
                   ".html", ".jinja2", ".css", ".toml", ".json", ".yaml", ".yml", ".sh"}
EXCLUDED_DIRECTORIES = {"data", "node_modules", "vendor", "target", "dist", "build", "__pycache__"}
VENDOR_FILES = {"resources/static/js/htmx.js", "resources/static/js/plotly-2.35.3.min.js",
                "resources/static/js/svg-pan-zoom.min.js", "resources/static/css/normalize.css"}
MAX_FILES = 2000
MAX_FILE_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024
MAX_REQUEST_BYTES = 16 * 1024
MAX_OUTPUT_BYTES = 16 * 1024 * 1024
SCAN_SECONDS = 45
ANNOTATIONS = {"readOnlyHint": True, "destructiveHint": False,
               "idempotentHint": True, "openWorldHint": False}
TOOLS = [
    {"name": AUDIT_TOOL, "description": "Audit this fixed checkout offline using pinned Semgrep community rules and Ruff. Results identify review candidates, including unused imports/variables; never removes or fixes code. Snapshot coverage and analyzer errors are reported.",
     "inputSchema": {"type": "object", "properties": {
         "engine": {"type": "string", "enum": ["all", "semgrep", "ruff"], "default": "all"},
         "offset": {"type": "integer", "minimum": 0, "maximum": 100000, "default": 0},
         "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 100},
         "snapshot_hash": {"type": "string", "pattern": "^[a-f0-9]{64}$",
             "description": "Required when offset is greater than zero; use coverage.sha256 from the first page. Changed source rejects pagination instead of mixing audits."}},
         "additionalProperties": False}, "annotations": ANNOTATIONS},
    {"name": INFO_TOOL, "description": "Read the local code-audit versions, pinned rules provenance, fixed source scope and limitations.",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
     "annotations": ANNOTATIONS},
]


def validate_arguments(name, arguments):
    if not isinstance(arguments, dict):
        raise ValueError("Tool arguments must be an object.")
    if name == INFO_TOOL and not arguments:
        return {}
    if name != AUDIT_TOOL or set(arguments) - {"engine", "offset", "limit", "snapshot_hash"}:
        raise ValueError("Choose an enabled audit tool; paths, commands, URLs and custom rules are not accepted.")
    engine = arguments.get("engine", "all")
    offset, limit = arguments.get("offset", 0), arguments.get("limit", 100)
    if engine not in ("all", "semgrep", "ruff"):
        raise ValueError("Choose all, semgrep or ruff.")
    if type(offset) is not int or not 0 <= offset <= 100000 or type(limit) is not int or not 1 <= limit <= 200:
        raise ValueError("Use an integer offset and a limit from 1 to 200.")
    snapshot_hash = arguments.get("snapshot_hash")
    if (snapshot_hash is not None and (not isinstance(snapshot_hash, str) or not re.fullmatch(r"[a-f0-9]{64}", snapshot_hash))) or (offset and snapshot_hash is None):
        raise ValueError("Pagination requires the first page's 64-character source snapshot hash.")
    return {"engine": engine, "offset": offset, "limit": limit, "snapshot_hash": snapshot_hash}


def read_source(root, relative):
    """Open each path component without following links, including race swaps."""
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for name in relative.parts[:-1]:
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        child = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
        with os.fdopen(child, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("The audit only reads regular source files.")
            if info.st_size > MAX_FILE_BYTES:
                raise ValueError("A source file exceeds the 1 MiB audit limit.")
            content = stream.read(MAX_FILE_BYTES + 1)
        if len(content) > MAX_FILE_BYTES:
            raise ValueError("A source file exceeds the 1 MiB audit limit.")
        content.decode("utf-8")
        return content
    finally:
        os.close(descriptor)


def source_snapshot(project_root, snapshot_root):
    root = Path(project_root).resolve(strict=True)
    snapshot = Path(snapshot_root)
    files, skipped, total = [], [], 0
    digest = hashlib.sha256()
    candidates = [root / name for name in TOP_LEVEL_FILES
                  if (root / name).exists() or (root / name).is_symlink()]
    for relative in SOURCE_ROOTS:
        source_root = root / relative
        if not source_root.is_dir() or source_root.is_symlink():
            continue
        if (not source_root.resolve().is_relative_to(root)
                or any((root / Path(*Path(relative).parts[:index])).is_symlink()
                       for index in range(1, len(Path(relative).parts)))):
            continue
        for folder, directories, filenames in os.walk(source_root, followlinks=False):
            directories[:] = sorted(name for name in directories if not name.startswith(".")
                and name not in EXCLUDED_DIRECTORIES and not (Path(folder) / name).is_symlink())
            candidates.extend(Path(folder) / name for name in sorted(filenames))
    for source in sorted(set(candidates)):
        relative = source.relative_to(root)
        if ((source.suffix.lower() not in SOURCE_SUFFIXES and relative.as_posix() not in TOP_LEVEL_FILES) or source.name.startswith(".")
                or relative.as_posix() in VENDOR_FILES):
            continue
        if source.is_symlink() or not source.exists():
            skipped.append({"path": relative.as_posix(), "reason": "link or missing file"})
            continue
        try:
            content = read_source(root, relative)
        except (OSError, UnicodeError) as error:
            skipped.append({"path": relative.as_posix(), "reason": type(error).__name__})
            continue
        total += len(content)
        if len(files) >= MAX_FILES or total > MAX_TOTAL_BYTES:
            raise ValueError("Source exceeds the 2000-file/32 MiB bounded audit limit.")
        destination = snapshot / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        digest.update(relative.as_posix().encode() + b"\0" + content + b"\0")
        files.append(relative.as_posix())
    digest.update(b"skipped\0" + json.dumps(skipped, sort_keys=True).encode())
    return {"files": files, "skipped": skipped, "bytes": total, "sha256": digest.hexdigest()}


def analyzer_environment(runtime, state):
    runtime, state = Path(runtime), Path(state)
    state.mkdir(parents=True, exist_ok=True)
    # No inherited Semgrep tokens, project environment, proxies or user settings.
    certificates = next(runtime.glob(".venv/lib/python*/site-packages/certifi/cacert.pem"))
    return {"PATH": str(runtime / ".venv/bin") + os.pathsep + "/usr/bin:/bin", "LANG": "C.UTF-8",
            "TMPDIR": str(state), "SSL_CERT_FILE": str(certificates),
            "SEMGREP_SEND_METRICS": "off", "SEMGREP_ENABLE_VERSION_CHECK": "0",
            "SEMGREP_MCP_DISABLE_TRACING": "true", "USE_SEMGREP_RPC": "false",
            "SEMGREP_LOG_FILE": str(state / "semgrep.log"),
            "SEMGREP_SETTINGS_FILE": str(state / "settings.yml"),
            "SEMGREP_VERSION_CACHE_PATH": str(state / "version-cache")}


def bounded_process(command, *, cwd, env, timeout=SCAN_SECONDS, max_output=MAX_OUTPUT_BYTES):
    process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    output = {"stdout": bytearray(), "stderr": bytearray()}
    deadline = time.monotonic() + timeout
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, "stdout")
            selector.register(process.stderr, selectors.EVENT_READ, "stderr")
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("The bounded local analyzer timed out.")
                for key, _ in selector.select(min(remaining, 1)):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    output[key.data].extend(chunk)
                    if sum(map(len, output.values())) > max_output:
                        raise ValueError("Analyzer output exceeds the 16 MiB audit limit.")
            code = process.wait(timeout=max(.01, deadline - time.monotonic()))
            return code, bytes(output["stdout"]), bytes(output["stderr"])
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=2)
        process.stdout.close()
        process.stderr.close()


def analyzer_command(engine, runtime, snapshot):
    runtime, snapshot = Path(runtime), Path(snapshot)
    if sys.platform != "darwin" or not Path("/usr/bin/sandbox-exec").is_file():
        raise ValueError("This audit runtime requires the reviewed macOS no-network sandbox.")
    isolation = ["/usr/bin/sandbox-exec", "-p", "(version 1) (allow default) (deny network*)"]
    if engine == "semgrep":
        return isolation + [str(runtime / ".venv/bin/semgrep"), "scan", "--config", str(runtime / "upstream-rules"),
                "--metrics=off", "--disable-version-check", "--no-trace", "--json", "--quiet",
                "--jobs=2", "--max-memory=256", "--timeout=5", "--timeout-threshold=3",
                "--max-target-bytes=" + str(MAX_FILE_BYTES), "--no-git-ignore", str(snapshot)]
    return isolation + [str(runtime / ".venv/bin/ruff"), "check", "--isolated", "--no-cache",
            "--select=E4,E7,E9,F,B,ASYNC,S", "--output-format=json", str(snapshot)]


def normalize_report(engine, raw, snapshot, coverage, returncode):
    findings, errors = [], []
    prefix = str(snapshot) + os.sep
    if engine == "semgrep":
        entries = raw.get("results", [])
        for entry in entries:
            extra = entry.get("extra", {})
            rule = entry.get("check_id", "")
            if ".upstream-rules." in rule:
                rule = "semgrep-rules/" + rule.partition(".upstream-rules.")[2]
            findings.append({"engine": engine, "rule": rule,
                "path": entry.get("path", "").removeprefix(prefix), "line": entry.get("start", {}).get("line", 1),
                "column": entry.get("start", {}).get("col", 1), "severity": extra.get("severity", "WARNING"),
                "message": extra.get("message", "Review this finding.")})
        for entry in raw.get("errors", []):
            errors.append({"engine": engine, "type": entry.get("type", "Analyzer error"),
                           "message": str(entry.get("message", ""))[:1000].replace(prefix, "")})
        scanned = [str(path).removeprefix(prefix) for path in raw.get("paths", {}).get("scanned", [])]
    else:
        for entry in raw:
            findings.append({"engine": engine, "rule": entry.get("code", "syntax"),
                "path": entry.get("filename", "").removeprefix(prefix), "line": entry.get("location", {}).get("row", 1),
                "column": entry.get("location", {}).get("column", 1), "severity": "WARNING",
                "message": entry.get("message", "Review this finding.")})
        scanned = [path for path in coverage["files"] if path.endswith(".py")]
    if returncode not in (0, 1):
        errors.append({"engine": engine, "type": "Analyzer exit", "message": f"Analyzer returned {returncode}; review partial results and errors."})
    return {"findings": findings, "errors": errors, "scanned": scanned}


def audit_info():
    return {"adapterVersion": VERSION, "semgrepVersion": SEMGREP_VERSION, "ruffVersion": RUFF_VERSION,
            "rulesCommit": RULES_COMMIT, "ruleSource": "https://github.com/semgrep/semgrep-rules",
            "sourceRoots": list(SOURCE_ROOTS), "topLevelFiles": sorted(TOP_LEVEL_FILES),
            "analysisLanguages": {"semgrepRules": ["Python", "JavaScript", "TypeScript", "Rust", "generic patterns"],
                                  "ruff": ["Python"]},
            "coverageMeaning": "The source inventory includes safe build metadata and frontend styles. filesScanned means reported scanner coverage, not proof that every source language has an applicable rule; unscanned lists make gaps explicit.",
            "excluded": ["reference archives", "uploads/data", "databases", ".env and hidden files",
                         "symlinks", "virtual environments", "vendor/build outputs", "binary assets"],
            "limits": {"files": MAX_FILES, "fileBytes": MAX_FILE_BYTES, "totalBytes": MAX_TOTAL_BYTES,
                       "analyzerSeconds": SCAN_SECONDS, "semgrepJobs": 2, "semgrepMemoryMiBPerJob": 256},
            "networkIsolation": "Analyzer subprocesses run in a fixed macOS sandbox denying all network access. Other hosts fail closed until an equivalent isolation adapter is reviewed.",
            "limitations": "Community static rules and Ruff are review aids, not proof of security or safe removals. No cross-file Pro analysis, dependency-vulnerability feed, code execution or automatic fixes. No source is transmitted."}


class LocalAudit:
    def __init__(self, project, runtime, runner=bounded_process):
        self.project, self.runtime, self.runner = Path(project), Path(runtime), runner
        self.cached = None

    def call(self, name, arguments):
        arguments = validate_arguments(name, arguments)
        if name == INFO_TOOL:
            return audit_info()
        with tempfile.TemporaryDirectory(prefix="cadevil-code-audit-") as folder:
            folder = Path(folder)
            snapshot = folder / "source"
            snapshot.mkdir()
            coverage = source_snapshot(self.project, snapshot)
            if arguments["snapshot_hash"] and arguments["snapshot_hash"] != coverage["sha256"]:
                raise ValueError("Source changed since the first page. Restart the audit at offset zero.")
            cache_key = (coverage["sha256"], arguments["engine"])
            if self.cached and self.cached[0] == cache_key:
                report = self.cached[1]
            else:
                report = {"coverage": coverage, "findings": [], "errors": [], "analyzers": []}
                engines = ("semgrep", "ruff") if arguments["engine"] == "all" else (arguments["engine"],)
                env = analyzer_environment(self.runtime, folder / "state")
                for engine in engines:
                    started = time.monotonic()
                    try:
                        code, stdout, _ = self.runner(analyzer_command(engine, self.runtime, snapshot), cwd=snapshot, env=env)
                        normalized = normalize_report(engine, json.loads(stdout), snapshot, coverage, code)
                        report["findings"].extend(normalized["findings"])
                        report["errors"].extend(normalized["errors"])
                        report["analyzers"].append({"engine": engine, "filesScanned": len(normalized["scanned"]),
                            "scanned": normalized["scanned"],
                            "unscanned": sorted(set(coverage["files"]) - set(normalized["scanned"])),
                            "seconds": round(time.monotonic() - started, 3), "returncode": code})
                    except (OSError, ValueError, TypeError, KeyError, TimeoutError, subprocess.TimeoutExpired) as error:
                        report["errors"].append({"engine": engine, "type": type(error).__name__,
                                                 "message": "Local analysis failed or exceeded its limit; no complete result is claimed."})
                report["findings"].sort(key=lambda entry: (entry["path"], entry["line"], entry["engine"], entry["rule"]))
                if not report["errors"]:
                    self.cached = (cache_key, report)
            offset, limit = arguments["offset"], arguments["limit"]
            return {**audit_info(), **report, "findings": report["findings"][offset:offset + limit],
                    "totalFindings": len(report["findings"]), "offset": offset,
                    "nextOffset": offset + limit if offset + limit < len(report["findings"]) else None,
                    "complete": not report["errors"] and not coverage["skipped"]}


def serve(audit, incoming=sys.stdin, outgoing=sys.stdout):
    for line in iter(lambda: incoming.readline(MAX_REQUEST_BYTES + 1), ""):
        request = None
        try:
            if len(line.encode()) > MAX_REQUEST_BYTES:
                raise ValueError("Request exceeds the audit input limit.")
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("A JSON-RPC request object is required.")
            if "id" not in request:
                continue
            method = request.get("method")
            response = {"jsonrpc": "2.0", "id": request["id"]}
            if method == "initialize":
                response["result"] = {"protocolVersion": request.get("params", {}).get("protocolVersion", "2025-11-25"),
                                      "capabilities": {"tools": {}}, "serverInfo": {"name": "cadevil-code-audit", "version": VERSION}}
            elif method == "tools/list":
                response["result"] = {"tools": TOOLS}
            elif method == "tools/call":
                params = request.get("params", {})
                result = audit.call(params.get("name"), params.get("arguments", {}))
                response["result"] = {"content": [{"type": "text", "text": json.dumps(result)}]}
            elif method == "ping":
                response["result"] = {}
            else:
                response["error"] = {"code": -32601, "message": "Method is not enabled by this debug adapter."}
        except (ValueError, TypeError, KeyError, AttributeError, OSError) as error:
            message = (str(error) if isinstance(error, ValueError) else "Audit request rejected or bounded source snapshot failed.")
            response = {"jsonrpc": "2.0", "id": request.get("id") if isinstance(request, dict) else None,
                        "result": {"content": [{"type": "text", "text": message}], "isError": True}}
        outgoing.write(json.dumps(response) + "\n")
        outgoing.flush()


def main():
    if os.environ.get("CADEVIL_DEBUG_MCP") != "1":
        raise SystemExit("Code audit MCP must be started by the debug launcher.")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--runtime", required=True)
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    serve(LocalAudit(args.project, args.runtime))


if __name__ == "__main__":
    main()
