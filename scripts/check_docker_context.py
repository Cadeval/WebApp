"""Exercise Docker's actual ignore matcher with fake secrets and nested files."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("docker_context", ROOT / "docker/context.py")
context = importlib.util.module_from_spec(spec)
spec.loader.exec_module(context)


def main():
    with tempfile.TemporaryDirectory(prefix="cadevil-context-canaries-") as directory:
        root = Path(directory) / "input"
        output = Path(directory) / "output"
        root.mkdir()
        allowed = ["plugin_manager/runtime.py", "shared/runtime.py", "resources/static/demo/public.json",
                   "plugins/bim_model_manager/static/bim-demo/public.json",
                   "plugins/example_plugin/static/js/public.js"]
        manifest = {"version": 1, "runtime": allowed, "build_only": ["Dockerfile", ".dockerignore", "docker/image-files.json"]}
        for relative in allowed:
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("PUBLIC_TEST_INPUT")
        (root / "docker").mkdir()
        (root / "docker/image-files.json").write_text(json.dumps(manifest))
        (root / "Dockerfile").write_text("FROM scratch\nCOPY . /context/\n")
        (root / ".dockerignore").write_text(context.dockerignore(manifest))
        canaries = [".env", ".git/config", ".aws/credentials", "plugin_manager/private.py", "shared/private.py", "shared/nested/private.py", "resources/static/private.json", "resources/static/demo/private.json", "docker/private.py", "data/user_uploads/private.ifc",
                    "plugins/bim_model_manager/private.py",
                    "plugins/bim_model_manager/static/bim-demo/private.ifc",
                    "plugins/example_plugin/static/js/private.js",
                    "plugins/example_plugin/resources/private.py",
                    "plugins/example_plugin/src/private.rs",
                    "tests/private.py", "tests/browser/private.test.js", "tests/rust/private.rs"]
        for relative in canaries:
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("PRIVATE_CANARY_DO_NOT_PACKAGE")
        subprocess.run(["docker", "build", "--no-cache", "--output", "type=local,dest=" + str(output), str(root)], check=True)
        observed = {path.relative_to(output / "context").as_posix() for path in (output / "context").rglob("*") if path.is_file()}
        if any(relative in observed for relative in canaries) or not set(allowed) <= observed:
            raise SystemExit("Docker's received context violated the reviewed allowlist.")
        for path in (output / "context").rglob("*"):
            if path.is_file() and b"PRIVATE_CANARY_DO_NOT_PACKAGE" in path.read_bytes():
                raise SystemExit("A private canary reached Docker's exported context.")
        print(f"Docker excluded all {len(canaries)} private/new-file canaries and retained every reviewed public input.")


if __name__ == "__main__":
    main()
