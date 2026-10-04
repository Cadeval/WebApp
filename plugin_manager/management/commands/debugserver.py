"""Own debug MCP processes outside Bolt's hot-reload/HTTP worker tree."""
import os
import fcntl
from pathlib import Path
import signal
import subprocess
import sys
import threading
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from plugin_manager.debug_processes import DebugProcesses, MCP_PROCESS_EXTENSION_POINT, stop_process
from plugin_manager.models import PluginRecord
from plugin_manager.registry import registry

class Command(BaseCommand):
    help = 'Run Bolt in debug mode with supervised debug-only MCP plugin subprocesses.'

    def add_arguments(self, parser):
        parser.add_argument('--host', default='127.0.0.1')
        parser.add_argument('--port', type=int, default=8000)
        parser.add_argument('--processes', type=int, default=4)
        parser.add_argument('--max-rss', type=int, default=512)

    def handle(self, *args, **options):
        if not settings.DEBUG or not getattr(settings, 'DEVELOPMENT_MCP_ENABLED', False):
            raise CommandError('debugserver requires DEBUG=True and DEVELOPMENT_MCP_ENABLED=True.')
        lock_root = Path(settings.BASE_DIR) / 'data/debug-mcp'
        lock_root.mkdir(parents=True, exist_ok=True)
        lock = (lock_root / 'launcher.lock').open('a')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            raise CommandError('A debug launcher already owns this checkout.')
        self._launch_lock = lock
        registry.discover_and_sync()
        specs = [entry.value for entry in registry.get_extensions(MCP_PROCESS_EXTENSION_POINT)]
        tool_root = Path(settings.DEVELOPMENT_MCP_TOOL_ROOT)
        runner = tool_root / 'python/mcp-server-git/bin/python'
        if not runner.is_file():
            raise CommandError(f'Development MCP runtime missing: {runner}. See docs/DEVELOPMENT_MCP.md.')
        bridge = Path(__file__).resolve().parents[2] / 'mcp_bridge.py'
        supervisor = DebugProcesses(specs, str(runner), bridge)
        stopped = threading.Event()
        original_handlers = {}
        for sig in (signal.SIGINT, signal.SIGTERM):
            original_handlers[sig] = signal.signal(sig, lambda *_: stopped.set())
        web = None
        try:
            command = [sys.executable, str(Path(settings.BASE_DIR) / 'manage.py'), 'runbolt', '--dev', '--settings=' + os.environ['DJANGO_SETTINGS_MODULE'], '--host', options['host'], '--port', str(options['port']), '--processes', str(options['processes']), '--max-rss', str(options['max_rss'])]
            web = subprocess.Popen(command, cwd=settings.BASE_DIR, start_new_session=True)
            self.stdout.write('Debug launcher: supervising debug-only MCP plugins; disable them in Plugin Manager to stop their subprocesses.')
            while not stopped.is_set():
                if web.poll() is not None:
                    if web.returncode:
                        raise CommandError(f'Bolt exited with status {web.returncode}.')
                    break
                enabled = {record.plugin_id for record in PluginRecord.objects.filter(enabled=True, error='', source=PluginRecord.Source.PACKAGE, compatibility='debug')}
                supervisor.reconcile(enabled)
                stopped.wait(1)
        finally:
            supervisor.close()
            if web is not None:
                stop_process(web)
            lock.close()
            for sig, handler in original_handlers.items():
                signal.signal(sig, handler)
