"""Single-owner debug subprocess lifecycle; never started from AppConfig.ready."""
from dataclasses import dataclass
import logging
import os
from pathlib import Path
import signal
import subprocess
import time
from django.conf import settings

logger = logging.getLogger('plugin_manager.debug')
MCP_PROCESS_EXTENSION_POINT = 'debug_mcp_process'

@dataclass(frozen=True)
class MCPProcess:
    plugin_id: str
    command: tuple[str, ...]
    tools: tuple[str, ...]
    port: int


def stop_process(process):
    if process.poll() is not None:
        return
    # Only signal the process group created and owned by this launcher.
    os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


class DebugProcesses:
    def __init__(self, specs, runner, bridge, *, popen=subprocess.Popen, clock=time.monotonic):
        self.specs = {spec.plugin_id: spec for spec in specs}
        self.runner = runner
        self.bridge = bridge
        self.popen = popen
        self.clock = clock
        self.children = {}
        self.retry_at = {}
        self.failures = {}
        self.streams = {}

    def reconcile(self, enabled_ids):
        if not settings.DEBUG or not getattr(settings, 'DEVELOPMENT_MCP_ENABLED', False):
            self.close()
            return
        import json
        enabled = set(enabled_ids)
        for disabled in self.specs.keys() - enabled:
            self.retry_at.pop(disabled, None)
            self.failures.pop(disabled, None)
        for plugin_id, process in list(self.children.items()):
            if plugin_id not in enabled:
                stop_process(process)
                logger.info('Stopped debug-only MCP plugin %s', plugin_id)
                self._forget(plugin_id)
            elif process.poll() is not None:
                logger.error('Debug-only MCP plugin %s exited with status %s; retrying with backoff', plugin_id, process.returncode)
                self._forget(plugin_id)
                self._retry(plugin_id)
        for plugin_id in enabled & self.specs.keys():
            if plugin_id in self.children or self.clock() < self.retry_at.get(plugin_id, 0):
                continue
            spec = self.specs[plugin_id]
            log_root = Path(settings.BASE_DIR) / 'data/debug-mcp'
            log_root.mkdir(parents=True, exist_ok=True)
            stream = (log_root / (plugin_id + '.log')).open('ab')
            env = {**os.environ, 'CADEVIL_DEBUG_MCP': '1'}
            payload = json.dumps({'plugin_id': spec.plugin_id, 'command': spec.command, 'tools': spec.tools, 'port': spec.port, 'cwd': str(settings.BASE_DIR)})
            try:
                process = self.popen([self.runner, str(self.bridge), payload], cwd=settings.BASE_DIR, env=env, stdin=subprocess.DEVNULL, stdout=stream, stderr=stream, start_new_session=True)
            except OSError:
                stream.close()
                logger.exception('Unable to start debug-only MCP plugin %s', plugin_id)
                self._retry(plugin_id)
                continue
            self.streams[plugin_id] = stream
            self.children[plugin_id] = process
            logger.info('Started debug-only MCP plugin %s (PID %s, localhost:%s)', plugin_id, process.pid, spec.port)

    def _retry(self, plugin_id):
        self.failures[plugin_id] = self.failures.get(plugin_id, 0) + 1
        self.retry_at[plugin_id] = self.clock() + min(60, 2 ** min(self.failures[plugin_id], 6))

    def _forget(self, plugin_id):
        self.children.pop(plugin_id, None)
        stream = self.streams.pop(plugin_id, None)
        if stream:
            stream.close()

    def close(self):
        for plugin_id, process in list(self.children.items()):
            try:
                stop_process(process)
            finally:
                self._forget(plugin_id)
