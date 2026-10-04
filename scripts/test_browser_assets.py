#!/usr/bin/env python3
"""Stage root browser tests beside the merged assets served by Django."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from plugins.resources import static_directories  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset-root", type=Path, default=ROOT,
                        help="Use static assets from an extracted, verified wheel payload.")
    arguments = parser.parse_args()
    asset_root = arguments.asset_root
    if not asset_root.is_dir() or asset_root.is_symlink():
        raise SystemExit("The asset root is missing or symlinked.")
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
        for source_root in static_directories(asset_root):
            if not source_root.is_dir():
                raise SystemExit(f"Static root is missing: {source_root}")
            for source in sorted(source_root.rglob("*")):
                if source.is_symlink():
                    raise SystemExit(f"Static assets must not be symlinks: {source}")
                if not source.is_file():
                    continue
                if source.name.endswith((".test.js", ".test.mjs")):
                    raise SystemExit(f"Browser tests belong in tests/browser: {source}")
                relative = source.relative_to(source_root)
                if relative in seen:
                    raise SystemExit(f"Duplicate static asset: {relative}")
                seen.add(relative)
                target = assets / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)

        test_root = ROOT / "tests/browser"
        if not test_root.is_dir() or test_root.is_symlink():
            raise SystemExit("The tests/browser directory is missing or symlinked.")
        tests: list[Path] = []
        for source in sorted(test_root.rglob("*")):
            if source.is_symlink():
                raise SystemExit(f"Browser tests must not be symlinks: {source}")
            if not source.is_file():
                continue
            is_test = source.name.endswith((".test.js", ".test.mjs"))
            is_fixture = "fixtures" in source.relative_to(test_root).parts and source.suffix in {".json", ".js"}
            if not is_test and not is_fixture:
                continue
            relative = source.relative_to(test_root)
            if relative in seen:
                raise SystemExit(f"Browser test conflicts with a static asset: {relative}")
            target = assets / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            if is_test:
                tests.append(target)

        (workspace / "package.json").write_text(json.dumps({"type": "module"}) + "\n")
        (workspace / "node_modules").symlink_to(dependencies.resolve(), target_is_directory=True)
        if not tests:
            raise SystemExit("No browser tests found in tests/browser.")
        return subprocess.run([node, "--test", *(str(path) for path in tests)],
                              cwd=workspace, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
