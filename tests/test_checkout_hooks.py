"""Real temporary Git checkouts exercise the read-only source hook and installer."""
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class CheckoutHookTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(prefix='cadevil-checkout-hook-')
        self.addCleanup(self.temporary.cleanup)
        self.parent = Path(self.temporary.name).resolve()
        self.checkout = self.parent / 'checkout with spaces'
        self.checkout.mkdir()
        manifest = json.loads((ROOT / 'docker/image-files.json').read_text())
        inputs = set(manifest['runtime'] + manifest['build_only']) | {
            '.githooks/post-checkout', 'scripts/install_checkout_hook.py', 'scripts/check_source.py',
        }
        for name in inputs:
            target = self.checkout / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, target)
        (self.checkout / '.githooks/post-checkout').chmod(0o755)
        (self.checkout / 'local-notes.txt').write_text('Initial fake developer notes.\n')
        self.environment = {**os.environ, 'GIT_CONFIG_NOSYSTEM':'1', 'GIT_CONFIG_GLOBAL':os.devnull,
                            'CADEVIL_CHECK_PYTHON':sys.executable, 'PYTHONDONTWRITEBYTECODE':'1'}
        for name in subprocess.check_output(['git', 'rev-parse', '--local-env-vars'], text=True).splitlines():
            self.environment.pop(name, None)
        self.git('init', '--initial-branch=main')
        self.git('add', '.')
        self.commit('test: seed disposable checkout fixture')

    def command(self, arguments, *, root=None, environment=None):
        return subprocess.run(arguments, cwd=root or self.checkout, env=environment or self.environment,
                              text=True, capture_output=True, timeout=30)

    def git(self, *arguments, ok=True, root=None, environment=None):
        result = self.command(['git', *arguments], root=root, environment=environment)
        if ok:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def commit(self, subject):
        self.git('-c', 'user.name=Fake Checkout QA', '-c', 'user.email=checkout-qa@example.invalid',
                 'commit', '--no-gpg-sign', '-m', subject)

    def install(self, *, ok=True):
        result = self.command([sys.executable, '-I', '-B', 'scripts/install_checkout_hook.py', '--source', '.'])
        if ok:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def installer_module(self):
        path = ROOT / 'scripts/install_checkout_hook.py'
        namespace = {'__name__':'checkout_installer_test', '__file__':str(path)}
        exec(compile(path.read_text(), str(path), 'exec'), namespace)
        return SimpleNamespace(**namespace)

    def install_with_preflight_change(self, change):
        installer = self.installer_module()
        original_run = subprocess.run
        hook = str(self.checkout / '.githooks/post-checkout')

        def during_preflight(arguments, **options):
            if arguments[0] == hook:
                change(original_run)
                return subprocess.CompletedProcess(arguments, 0)
            return original_run(arguments, **options)

        with patch.dict(os.environ, self.environment, clear=True), patch.object(subprocess, 'run', side_effect=during_preflight):
            with self.assertRaises((ValueError, OSError)) as rejected:
                installer.install(self.checkout)
        return str(rejected.exception)

    def seed_private_data(self):
        directory = self.checkout / 'data'
        directory.mkdir()
        private = directory / 'fake-local.sqlite3'
        private.write_bytes(b'Fake private state: must never be opened or overwritten by checkout checks.\n')
        scratch = self.checkout / 'untracked-scratch.txt'
        scratch.write_bytes(b'Fake untracked work.\n')
        return {path:sha256(path.read_bytes()).hexdigest() for path in (private, scratch)}

    def assert_preserved(self, files):
        self.assertEqual({path:sha256(path.read_bytes()).hexdigest() for path in files}, files)
        self.assertFalse(list(self.checkout.rglob('__pycache__')))

    def test_checkout_runs_real_shared_checks_and_preserves_work(self):
        self.install()
        files = self.seed_private_data()
        notes = self.checkout / 'local-notes.txt'
        notes.write_text('Fake dirty tracked work.\n')
        switched = self.git('checkout', '-b', 'checked-feature')
        self.assertIn('Cadevil checkout checks passed.', switched.stdout + switched.stderr)
        self.assertTrue(self.git('ls-files', '--stage', '.githooks/post-checkout').stdout.startswith('100755 '))
        self.assertEqual(notes.read_text(), 'Fake dirty tracked work.\n')
        self.assert_preserved(files)

    def test_invalid_checkout_returns_failure_without_reverting_checkout_or_data(self):
        self.git('checkout', '-b', 'invalid-package-policy')
        manifest_path = self.checkout / 'docker/image-files.json'
        manifest = json.loads(manifest_path.read_text())
        manifest['runtime'].append('data/fake-local.sqlite3')
        manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')
        self.git('add', 'docker/image-files.json')
        self.commit('test: include a forbidden private distribution input')
        self.git('checkout', 'main')
        self.install()
        files = self.seed_private_data()
        failed = self.git('checkout', 'invalid-package-policy', ok=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn('Cadevil checkout checks failed.', failed.stderr)
        self.assertIn('data/fake-local.sqlite3', failed.stdout + failed.stderr)
        self.assertEqual(self.git('branch', '--show-current').stdout.strip(), 'invalid-package-policy')
        self.assert_preserved(files)

    def test_linked_worktree_uses_main_existing_interpreter_and_its_own_source(self):
        self.install()
        interpreter = self.checkout / '.venv/bin/python'
        interpreter.parent.mkdir(parents=True)
        interpreter.symlink_to(sys.executable)
        linked = self.parent / 'linked worktree'
        environment = {key:value for key,value in self.environment.items() if key != 'CADEVIL_CHECK_PYTHON'}
        added = self.git('worktree', 'add', '--detach', str(linked), 'HEAD', environment=environment)
        self.assertIn('Cadevil checkout checks passed.', added.stdout + added.stderr)
        self.assertTrue((linked / '.git').is_file())
        self.assertFalse((linked / '.venv').exists())
        self.assertFalse(list(linked.rglob('__pycache__')))

    def test_file_checkout_also_runs_the_hook(self):
        self.install()
        files = self.seed_private_data()
        (self.checkout / 'local-notes.txt').write_text('Fake notes deliberately restored by Git.\n')
        restored = self.git('checkout', '--', 'local-notes.txt')
        self.assertIn('Cadevil checkout checks passed.', restored.stdout + restored.stderr)
        self.assertEqual((self.checkout / 'local-notes.txt').read_text(), 'Initial fake developer notes.\n')
        self.assert_preserved(files)

    def test_installer_preserves_unrelated_hooks_and_is_idempotent(self):
        unrelated = self.checkout / '.git/hooks/pre-commit'
        unrelated.write_bytes(b'#!/bin/sh\n# Fake unrelated hook.\nexit 0\n')
        unrelated.chmod(0o755)
        original = unrelated.read_bytes()
        self.install()
        dispatcher = self.checkout / '.git/hooks/post-checkout'
        before = dispatcher.read_bytes()
        self.install()
        self.assertEqual(dispatcher.read_bytes(), before)
        self.assertEqual(unrelated.read_bytes(), original)
        self.assertEqual(self.git('config', '--get', 'core.hooksPath', ok=False).returncode, 1)

    def test_installer_refuses_existing_unmanaged_post_checkout(self):
        existing = self.checkout / '.git/hooks/post-checkout'
        existing.write_bytes(b'#!/bin/sh\n# Fake existing checkout integration.\nexit 0\n')
        original = existing.read_bytes()
        for mode in (0o600, 0o755):
            with self.subTest(mode=oct(mode)):
                existing.chmod(mode)
                refused = self.install(ok=False)
                self.assertNotEqual(refused.returncode, 0)
                self.assertIn('existing post-checkout hook was left unchanged', refused.stderr)
                self.assertEqual(existing.read_bytes(), original)
                self.assertEqual(existing.stat().st_mode & 0o777, mode)

    def test_installer_keeps_existing_custom_hooks_path(self):
        custom = self.parent / 'custom hooks'
        custom.mkdir()
        existing = custom / 'pre-commit'
        existing.write_bytes(b'Fake custom hook configuration.\n')
        self.git('config', 'core.hooksPath', str(custom))
        refused = self.install(ok=False)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn('core.hooksPath is already configured', refused.stderr)
        self.assertEqual(self.git('config', '--get', 'core.hooksPath').stdout.strip(), str(custom))
        self.assertEqual(set(path.name for path in custom.iterdir()), {'pre-commit'})
        self.assertFalse((self.checkout / '.git/hooks/post-checkout').exists())

    def test_invalid_interpreter_fails_clearly_without_installation_or_data_changes(self):
        self.install()
        files = self.seed_private_data()
        environment = {**self.environment, 'CADEVIL_CHECK_PYTHON':shutil.which('true')}
        failed = self.git('checkout', '-b', 'missing-interpreter', ok=False, environment=environment)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn('Python 3.13 or newer is required', failed.stderr)
        self.assertFalse((self.checkout / '.venv').exists())
        self.assert_preserved(files)

    def test_checkout_ignores_shadowed_standard_library_modules(self):
        canary = self.checkout / 'scripts/json.py'
        canary.write_text('from pathlib import Path\n'
                          'Path(__file__).with_suffix(".executed").write_text("Fake canary executed.")\n'
                          'raise RuntimeError("Local json.py must not run in checkout checks.")\n')
        original = sha256(canary.read_bytes()).hexdigest()
        self.install()
        checked = self.git('checkout', '-b', 'isolated-python-check')
        self.assertIn('Cadevil checkout checks passed.', checked.stdout + checked.stderr)
        self.assertFalse(canary.with_suffix('.executed').exists())
        self.assert_preserved({canary:original})

    def test_installer_rejects_hook_created_during_preflight(self):
        destination = self.checkout / '.git/hooks/post-checkout'
        content = b'Fake concurrent unrelated checkout hook.\n'

        def create_hook(_):
            destination.write_bytes(content)
            destination.chmod(0o600)

        rejected = self.install_with_preflight_change(create_hook)
        self.assertIn('appeared during checks', rejected)
        self.assertEqual(destination.read_bytes(), content)
        self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
        self.assertFalse(list(destination.parent.glob('.cadevil-post-checkout-*')))

    def test_installer_rejects_managed_hook_replacement_during_preflight(self):
        self.install()
        destination = self.checkout / '.git/hooks/post-checkout'
        content = b'Fake replacement of an initially managed hook.\n'

        def replace_hook(_):
            destination.unlink()
            destination.write_bytes(content)
            destination.chmod(0o600)

        rejected = self.install_with_preflight_change(replace_hook)
        self.assertIn('existing post-checkout hook was left unchanged', rejected)
        self.assertEqual(destination.read_bytes(), content)
        self.assertEqual(destination.stat().st_mode & 0o777, 0o600)

    def test_installer_rejects_hooks_directory_symlink_swap_during_preflight(self):
        directory = self.checkout / '.git/hooks'
        original_files = {path.name:path.read_bytes() for path in directory.iterdir()}
        outside = self.parent / 'unrelated directory'
        outside.mkdir()
        (outside / 'kept-file').write_bytes(b'Fake unrelated file.\n')
        preserved = self.parent / 'preserved original hooks'

        def replace_directory(_):
            directory.rename(preserved)
            directory.symlink_to(outside, target_is_directory=True)

        rejected = self.install_with_preflight_change(replace_directory)
        self.assertIn('directories changed during checks', rejected)
        self.assertEqual({path.name:path.read_bytes() for path in preserved.iterdir()}, original_files)
        self.assertEqual({path.name:path.read_bytes() for path in outside.iterdir()}, {'kept-file':b'Fake unrelated file.\n'})
        self.assertTrue(directory.is_symlink())

    def test_installer_rejects_custom_hooks_path_configured_during_preflight(self):
        custom = self.parent / 'concurrent custom hooks'
        custom.mkdir()
        (custom / 'kept-hook').write_bytes(b'Fake custom hook.\n')

        def configure_path(original_run):
            original_run(['git', 'config', 'core.hooksPath', str(custom)], cwd=self.checkout,
                         env=self.environment, check=True)

        rejected = self.install_with_preflight_change(configure_path)
        self.assertIn('core.hooksPath is already configured', rejected)
        self.assertEqual(self.git('config', '--get', 'core.hooksPath').stdout.strip(), str(custom))
        self.assertEqual({path.name:path.read_bytes() for path in custom.iterdir()}, {'kept-hook':b'Fake custom hook.\n'})
        self.assertFalse((self.checkout / '.git/hooks/post-checkout').exists())

    def test_failed_publication_removes_only_its_own_staging_file(self):
        installer = self.installer_module()
        directory = self.checkout / '.git/hooks'
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            with patch.object(os, 'fsync', side_effect=OSError('Fake owned-file synchronization failure.')):
                with self.assertRaises(OSError):
                    installer.publish_dispatcher(descriptor)
        finally:
            os.close(descriptor)
        self.assertFalse((directory / 'post-checkout').exists())
        self.assertFalse(list(directory.glob('.cadevil-post-checkout-*')))

    def test_failed_publication_preserves_a_replaced_staging_inode(self):
        installer = self.installer_module()
        directory = self.checkout / '.git/hooks'
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        replaced = []
        content = b'Fake unrelated replacement of the staging path.\n'

        def replace_staging(_):
            staging, = directory.glob('.cadevil-post-checkout-*')
            staging.rename(directory / 'preserved-owned-staging')
            staging.write_bytes(content)
            staging.chmod(0o600)
            replaced.append(staging)
            raise OSError('Fake synchronization failure after a path replacement.')

        try:
            with patch.object(os, 'fsync', side_effect=replace_staging):
                with self.assertRaises(OSError):
                    installer.publish_dispatcher(descriptor)
        finally:
            os.close(descriptor)
        self.assertEqual(len(replaced), 1)
        self.assertEqual(replaced[0].read_bytes(), content)
        self.assertEqual(replaced[0].stat().st_mode & 0o777, 0o600)
        self.assertFalse((directory / 'post-checkout').exists())


if __name__ == '__main__':
    unittest.main()
