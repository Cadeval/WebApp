"""Install a checkout dispatcher without replacing unrelated Git hooks or config."""
import argparse
import os
from pathlib import Path
import secrets
import stat
import subprocess
import sys

DISPATCHER = b'''#!/bin/sh
# Cadevil managed post-checkout dispatcher v1.
set -eu
checkout_root=$(git rev-parse --show-toplevel)
checkout_hook="$checkout_root/.githooks/post-checkout"
if [ ! -f "$checkout_hook" ] || [ ! -x "$checkout_hook" ]; then
    printf '%s\\n' 'Cadevil checkout checks failed: this revision lacks an executable .githooks/post-checkout. The checkout is complete; restore the hook or use a supported revision.' >&2
    exit 1
fi
exec "$checkout_hook" "$@"
'''


def git(root, *arguments, allowed=(0,)):
    result = subprocess.run(['git', '-C', str(root), *arguments], capture_output=True, text=True)
    if result.returncode not in allowed:
        raise ValueError(result.stderr.strip() or 'The requested path is not a Git worktree.')
    return result


def require_default_hooks(root):
    if git(root, 'config', '--get-all', 'core.hooksPath', allowed=(0, 1)).returncode == 0:
        raise ValueError('core.hooksPath is already configured. It was left unchanged. '
                         'Chain .githooks/post-checkout from your existing hook; see docs/CHECKOUT_HOOKS.md.')


def identity(metadata):
    return metadata.st_dev, metadata.st_ino


def entry_metadata(directory, name):
    try:
        return os.stat(name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return None


def require_directory(path, descriptor):
    metadata = path.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or identity(metadata) != identity(os.fstat(descriptor)):
        raise ValueError('Git hooks directories changed during checks; nothing outside the original metadata was written. Rerun the installer.')


def require_managed_hook(directory, descriptor):
    metadata = os.fstat(descriptor)
    current = entry_metadata(directory, 'post-checkout')
    if (not stat.S_ISREG(metadata.st_mode) or current is None or
            not stat.S_ISREG(current.st_mode) or identity(current) != identity(metadata) or
            metadata.st_size != len(DISPATCHER)):
        raise ValueError('An existing post-checkout hook was left unchanged. '
                         'Chain .githooks/post-checkout from it; see docs/CHECKOUT_HOOKS.md.')
    os.lseek(descriptor, 0, os.SEEK_SET)
    if os.read(descriptor, len(DISPATCHER) + 1) != DISPATCHER:
        raise ValueError('An existing post-checkout hook was left unchanged. '
                         'Chain .githooks/post-checkout from it; see docs/CHECKOUT_HOOKS.md.')
    return metadata


def publish_dispatcher(directory):
    name = '.cadevil-post-checkout-' + secrets.token_hex(16)
    descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o755, dir_fd=directory)
    created = os.fstat(descriptor)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(DISPATCHER)
            stream.flush()
            os.fchmod(stream.fileno(), 0o755)
            os.fsync(stream.fileno())
        # A hard link publishes complete bytes atomically and refuses an existing hook.
        os.link(name, 'post-checkout', src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
    finally:
        current = entry_metadata(directory, name)
        if current is not None and stat.S_ISREG(current.st_mode) and identity(current) == identity(created):
            os.unlink(name, dir_fd=directory)
    return created


def install(root):
    root = Path(git(root, 'rev-parse', '--show-toplevel').stdout.strip()).resolve()
    require_default_hooks(root)
    common = Path(git(root, 'rev-parse', '--path-format=absolute', '--git-common-dir').stdout.strip())
    if common.is_symlink():
        raise ValueError('Refusing to install into a linked Git metadata directory.')
    directory = common.resolve() / 'hooks'
    if directory.is_symlink() or directory.exists() and not directory.is_dir():
        raise ValueError('The default Git hooks directory is linked or obstructed; it was left unchanged.')
    destination = directory / 'post-checkout'
    hook = root / '.githooks/post-checkout'
    if hook.parent.is_symlink() or hook.is_symlink() or not hook.is_file() or not os.access(hook, os.X_OK):
        raise ValueError('The checked-in .githooks/post-checkout must be a regular executable file.')
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    common_descriptor = os.open(common, directory_flags)
    hooks_descriptor = hook_descriptor = None
    try:
        require_directory(common, common_descriptor)
        original_directory = entry_metadata(common_descriptor, 'hooks')
        if original_directory is not None:
            hooks_descriptor = os.open('hooks', directory_flags, dir_fd=common_descriptor)
            require_directory(directory, hooks_descriptor)
            if identity(original_directory) != identity(os.fstat(hooks_descriptor)):
                raise ValueError('Git hooks directory changed during checks; rerun the installer.')
            if entry_metadata(hooks_descriptor, 'post-checkout') is not None:
                hook_descriptor = os.open('post-checkout', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                          dir_fd=hooks_descriptor)
                require_managed_hook(hooks_descriptor, hook_descriptor)
        environment = dict(os.environ)
        environment['CADEVIL_CHECK_PYTHON'] = sys.executable
        subprocess.run([str(hook), '0' * 40, git(root, 'rev-parse', 'HEAD').stdout.strip(), '1'],
                       cwd=root, env=environment, check=True)
        require_default_hooks(root)
        require_directory(common, common_descriptor)
        if hooks_descriptor is None:
            if entry_metadata(common_descriptor, 'hooks') is not None:
                raise ValueError('Git hooks directory appeared during checks; it was left unchanged. Rerun the installer.')
            os.mkdir('hooks', mode=0o755, dir_fd=common_descriptor)
            hooks_descriptor = os.open('hooks', directory_flags, dir_fd=common_descriptor)
        require_directory(directory, hooks_descriptor)
        if hook_descriptor is not None:
            metadata = require_managed_hook(hooks_descriptor, hook_descriptor)
            os.fchmod(hook_descriptor, stat.S_IMODE(metadata.st_mode) | 0o111)
            require_managed_hook(hooks_descriptor, hook_descriptor)
        else:
            if entry_metadata(hooks_descriptor, 'post-checkout') is not None:
                raise ValueError('A post-checkout hook appeared during checks; it was left unchanged. Rerun the installer.')
            published = publish_dispatcher(hooks_descriptor)
            hook_descriptor = os.open('post-checkout', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                      dir_fd=hooks_descriptor)
            if identity(os.fstat(hook_descriptor)) != identity(published):
                raise ValueError('The post-checkout hook changed during installation; it was left unchanged. Rerun the installer.')
            require_managed_hook(hooks_descriptor, hook_descriptor)
        require_directory(common, common_descriptor)
        require_directory(directory, hooks_descriptor)
        require_default_hooks(root)
    finally:
        for descriptor in (hook_descriptor, hooks_descriptor, common_descriptor):
            if descriptor is not None:
                os.close(descriptor)
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path('.'))
    arguments = parser.parse_args()
    try:
        destination = install(arguments.source)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print('Checkout hook installation failed: ' + str(error), file=sys.stderr)
        raise SystemExit(1) from None
    print('Checkout hook installed: ' + str(destination))
