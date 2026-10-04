#!/usr/bin/env python3
"""Run browser tests against the same merged static layout served by Django."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from apps.plugins.resources import static_directories  # noqa: E402


def main() -> int:
    node = shutil.which("node")
    if node is None:
        raise SystemExit("Node.js is required to run browser tests.")
    dependencies = ROOT / "node_modules"
    if not dependencies.is_dir():
        raise SystemExit("Run npm ci --ignore-scripts before running browser tests.")

    with tempfile.TemporaryDirectory(prefix="cadevil-browser-tests-") as directory:
        workspace = Path(directory)
        assets = workspace / "static"
        seen: set[Path] = set()
        for source_root in static_directories(ROOT):
            if not source_root.is_dir():
                raise SystemExit(f"Static root is missing: {source_root}")
            for source in sorted(source_root.rglob("*")):
                if source.is_symlink():
                    raise SystemExit(f"Static assets must not be symlinks: {source}")
                if not source.is_file():
                    continue
                relative = source.relative_to(source_root)
                if relative in seen:
                    raise SystemExit(f"Duplicate static asset: {relative}")
                seen.add(relative)
                target = assets / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)

        (workspace / "package.json").write_text(json.dumps({"type": "module"}) + "\n")
        (workspace / "node_modules").symlink_to(dependencies.resolve(), target_is_directory=True)
        tests = sorted(path for path in assets.rglob("*")
                       if path.name.endswith((".test.js", ".test.mjs")))
        if not tests:
            raise SystemExit("No browser tests found in the registered static roots.")
        return subprocess.run([node, "--test", *(str(path) for path in tests)],
                              cwd=workspace, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
