"""Build the production image from reviewed inputs with local attestations."""
import argparse
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def main():
    version = tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']['version']
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tag', default='cadevil:' + version)
    parser.add_argument('--platform', choices=['linux/arm64', 'linux/amd64'])
    parser.add_argument('--metadata-file', type=Path)
    arguments = parser.parse_args()
    spec = importlib.util.spec_from_file_location('cadevil_docker_context', ROOT / 'docker/context.py')
    context = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(context)
    with tempfile.TemporaryDirectory(prefix='cadevil-reviewed-build-') as directory:
        archive = Path(directory) / 'context.tar'
        count = context.archive(archive, root=ROOT)
        print(f'Building {arguments.tag} from {count} reviewed files.', flush=True)
        command = ['docker', 'buildx', 'build', '--load', '--tag', arguments.tag,
                   '--sbom=true', '--provenance=mode=min']
        if arguments.platform:
            command.extend(['--platform', arguments.platform])
        if arguments.metadata_file:
            command.extend(['--metadata-file', str(arguments.metadata_file.resolve())])
        with archive.open('rb') as stream:
            subprocess.run([*command, '-'], stdin=stream, cwd=ROOT, check=True)


if __name__ == '__main__':
    main()
