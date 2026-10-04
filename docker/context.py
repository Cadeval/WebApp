"""Validate and archive an exact, reviewed Docker context; never discover inputs."""
import argparse
import json
from pathlib import Path, PurePosixPath
import tarfile

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = "docker/image-files.json"
PUBLIC_IFC = {"apps/plugins/bim_model_manager/static/bim-demo/example-a.ifc", "apps/plugins/bim_model_manager/static/bim-demo/example-b.ifc"}
FORBIDDEN_PARTS = {
    ".git", ".env", ".venv", "node_modules", "__pycache__", "data", "media", "reference",
    "target", "tests", "test", "src", ".ssh", ".aws", ".codex", ".agents", "backups",
}
FORBIDDEN_FILES = {
    "debugserver.py", "debug_processes.py", "mcp_bridge.py", "ux_mcp_bridge.py", "code_audit_bridge.py",
    "dev.py", "cadevil-development.cdx.json", "package_fixtures.py",
}


def read_manifest(root=ROOT):
    document = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
    if set(document) != {"version", "runtime", "build_only"} or document["version"] != 1:
        raise ValueError("Unsupported Docker input manifest.")
    paths = []
    for group in ("runtime", "build_only"):
        if not isinstance(document[group], list):
            raise ValueError("Docker input groups must be lists.")
        for value in document[group]:
            if not isinstance(value, str) or not value or any(character in value for character in "\\*?[]") or any(ord(character) < 32 or ord(character) == 127 for character in value):
                raise ValueError("Docker inputs must be exact, control-free relative paths.")
            path = PurePosixPath(value)
            if path.is_absolute() or path.as_posix() != value or any(part in {".", ".."} for part in path.parts):
                raise ValueError("Docker input path is not canonical.")
            if any(part in FORBIDDEN_PARTS or (part.startswith(".") and part != ".well-known") for part in path.parts):
                if value != ".dockerignore":
                    raise ValueError("Private or development files cannot be Docker inputs.")
            if path.name in FORBIDDEN_FILES or path.name.startswith("test") or ".test." in path.name:
                raise ValueError("Test and debug-only files cannot be Docker inputs.")
            if path.suffix.lower() in {".pem", ".key", ".sqlite", ".sqlite3", ".db", ".log", ".zip", ".tar", ".gz", ".xlsx", ".csv", ".rs"}:
                raise ValueError("Credentials, state, source archives, and source toolchains cannot be Docker inputs.")
            if path.suffix.lower() == ".ifc" and value not in PUBLIC_IFC:
                raise ValueError("Only the two public controlled demo IFC fixtures may be included.")
            paths.append(value)
    if len(paths) != len(set(paths)) or MANIFEST not in paths or "Dockerfile" not in paths or ".dockerignore" not in paths:
        raise ValueError("Docker inputs must be unique and include the context controls.")
    return document


def dockerignore(document):
    # Re-exclude the contents of every reopened directory. Merely adding
    # !apps/ after ** can also reopen descendants under Docker's parent rules.
    paths = set(document["runtime"] + document["build_only"])
    directories = {str(parent) for value in paths for parent in PurePosixPath(value).parents if str(parent) != "."}
    lines = ["# Generated from docker/image-files.json; edit that reviewed list, then run", "# python docker/context.py --write-ignore. Unknown files are never included.", "**"]
    def emit(directory):
        for child in sorted(value for value in directories if str(PurePosixPath(value).parent) == (directory or ".")):
            lines.extend(["!" + child + "/", child + "/**"])
            emit(child)
        for value in sorted(value for value in paths if str(PurePosixPath(value).parent) == (directory or ".")):
            lines.append("!" + value)
    emit("")
    return "\n".join(lines) + "\n"


def regular_file(root, relative):
    current = root
    for part in PurePosixPath(relative).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("Symlinks are not permitted in the reviewed Docker context: " + relative)
    if not current.is_file():
        raise ValueError("Required Docker input is missing or not a regular file: " + relative)
    return current


def validate(root=ROOT, received=False):
    document = read_manifest(root)
    expected_ignore = dockerignore(document)
    all_paths = set(document["runtime"] + document["build_only"])
    if received:
        # Docker does not COPY Dockerfile/.dockerignore; they stay build controls.
        copied = all_paths - {"Dockerfile", ".dockerignore"}
        observed = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file() or path.is_symlink()}
        if observed != copied:
            raise ValueError("Received Docker source does not match the reviewed input list.")
        for value in observed:
            regular_file(root, value)
    else:
        for value in all_paths:
            regular_file(root, value)
        if (root / ".dockerignore").read_text(encoding="utf-8") != expected_ignore:
            raise ValueError(".dockerignore differs from the reviewed input list; regenerate it.")
    return document


def archive(destination, root=ROOT):
    document = validate(root)
    paths = sorted(document["runtime"] + document["build_only"])
    output = Path(destination).resolve()
    if output == root.resolve() or root.resolve() in output.parents:
        raise ValueError("Write the build context archive outside the source checkout.")
    with tarfile.open(output, "w", format=tarfile.PAX_FORMAT) as bundle:
        for value in paths:
            source = regular_file(root, value)
            info = tarfile.TarInfo(value)
            info.size = source.stat().st_size
            info.mode = 0o644
            info.uid = info.gid = info.mtime = 0
            info.uname = info.gname = ""
            with source.open("rb") as handle:
                bundle.addfile(info, handle)
    return len(paths)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-ignore", action="store_true")
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--received-context", type=Path)
    args = parser.parse_args()
    try:
        if args.write_ignore:
            (ROOT / ".dockerignore").write_text(dockerignore(read_manifest()), encoding="utf-8")
        if args.archive:
            count = archive(args.archive)
        else:
            document = validate(args.received_context or ROOT, received=bool(args.received_context))
            count = len(document["runtime"]) + len(document["build_only"])
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, str(error) + "\n")
    print(f"Verified {count} reviewed Docker inputs.")


if __name__ == "__main__":
    main()
