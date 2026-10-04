"""Project scope, secret projection and lifecycle of the debug Docker provider."""
import io
import json
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings
from plugin_manager.debug_processes import MCP_PROCESS_EXTENSION_POINT
from plugin_manager.registry import PluginRegistry
from plugins.development_mcp import DOCKER_ID, docker_manifest
from plugins.development_mcp import docker_mcp_bridge as bridge


class DockerMCPTests(SimpleTestCase):
    def test_only_reviewed_hosts_and_read_arguments_are_accepted(self):
        for host in ('tcp://127.0.0.1:2375', 'ssh://root@example.org',
                     'unix:///tmp/../secret.sock', bridge.DEFAULT_HOST + '/unexpected'):
            with self.subTest(host=host), self.assertRaises(ValueError):
                bridge.validate_host(host)
        self.assertEqual(bridge.validate_host('unix:///var/run/docker.sock'), 'unix:///var/run/docker.sock')
        for name, arguments in [('run_container', {}), ('fetch_container_logs', {}),
                                (bridge.INFO_TOOL, {'host': 'anything'}),
                                (bridge.READ_TOOLS[0], {'limit': True}),
                                (bridge.READ_TOOLS[0], {'limit': 101}),
                                (bridge.READ_TOOLS[0], {'filters': {'label': 'other'}}),
                                (bridge.READ_TOOLS[1], {'id': 'private'})]:
            with self.subTest(name=name, arguments=arguments), self.assertRaises(ValueError):
                bridge.validate_arguments(name, arguments)

    def test_summaries_never_contain_environment_mounts_raw_labels_health_logs_or_commands(self):
        private = 'fake-secret-credential'
        raw = {'id': 'a' * 64, 'name': 'app', 'status': 'running', 'image': {'id': 'image'},
               'labels': {'com.docker.compose.project': bridge.PROJECT,
                          'com.docker.compose.service': 'app', 'private': private},
               'state': {'Health': {'Status': 'healthy', 'Log': [private]}, 'Error': private},
               'mounts': [{'Source': private}], 'config': {'Env': [private], 'Cmd': [private]},
               'ports': {private: private}, 'restart_count': 0}
        result = bridge.container_summary(raw)
        self.assertEqual(result['health'], 'healthy')
        self.assertNotIn(private, json.dumps(result))
        raw['labels']['com.docker.compose.project'] = 'unrelated'
        with self.assertRaises(ValueError):
            bridge.container_summary(raw)
        with self.assertRaises(ValueError):
            bridge.network_summary({'id': 'n', 'labels': {'com.docker.compose.project': 'unrelated'}})

    @patch.object(bridge.importlib.metadata, 'version', return_value=bridge.PROVIDER_VERSION)
    def test_upstream_list_handlers_receive_forced_project_filters(self, _version):
        client = Mock()
        upstream = Mock()
        upstream.ListContainersFilters.side_effect = lambda **values: SimpleNamespace(**values)
        upstream.ListNetworksFilter.side_effect = lambda **values: SimpleNamespace(**values)
        upstream.list_containers.return_value = [{'id': 'c', 'labels': {'com.docker.compose.project': bridge.PROJECT}}]
        upstream.list_networks.return_value = [{'id': 'n', 'labels': {'com.docker.compose.project': bridge.PROJECT}}]
        upstream.list_images.return_value = [{'id': 'i', 'tags': ['cadevil:0.16.0'], 'size': 123,
                                             'labels': {'secret': 'never-return'}}]
        for name in bridge.READ_TOOLS:
            self.assertEqual(bridge.engine_read(name, {}, bridge.DEFAULT_HOST,
                client=client, upstream=upstream)['total'], 1)
        self.assertEqual(upstream.list_containers.call_args.kwargs['filters'].label, [bridge.PROJECT_LABEL])
        self.assertTrue(upstream.list_containers.call_args.kwargs['all'])
        self.assertEqual(upstream.list_networks.call_args.kwargs['filters'].label, [bridge.PROJECT_LABEL])
        self.assertEqual(upstream.list_images.call_count, 1)
        client.close.assert_not_called()
        for forbidden in ('run_container', 'container_logs', 'docker_compose', 'fetch_container_logs'):
            getattr(upstream, forbidden).assert_not_called()

    def test_image_listing_only_resolves_images_from_scoped_containers(self):
        client = Mock()
        identity = 'sha256:' + 'a' * 64
        client.containers.list.return_value = [SimpleNamespace(attrs={'Image': identity})]
        bridge.ScopedImages(client).list(name='ignored', filters={'label': 'ignored'})
        client.containers.list.assert_called_once_with(all=True, filters={'label': bridge.PROJECT_LABEL})
        client.images.get.assert_called_once_with(identity)
        client.images.list.assert_not_called()

    def test_bounded_worker_environment_excludes_secrets_and_uses_fixed_command(self):
        runner = Mock(return_value=SimpleNamespace(returncode=0, stdout='{}'))
        provider = bridge.DockerProvider(bridge.DEFAULT_HOST, '/tools/docker', runner)
        with patch.dict('os.environ', {'DJANGO_SECRET_KEY': 'private', 'DOCKER_HOST': 'tcp://other',
                                       'HTTPS_PROXY': 'private', 'SSH_AUTH_SOCK': '/agent'}):
            provider.call(bridge.READ_TOOLS[0], {'limit': 2})
        call = runner.call_args
        self.assertEqual(call.kwargs['timeout'], 20)
        self.assertEqual(call.kwargs['input'], '{"limit": 2}')
        self.assertEqual(call.args[0][-1], '/tools/docker')
        for key in ('DJANGO_SECRET_KEY', 'DOCKER_HOST', 'HTTPS_PROXY'):
            self.assertNotIn(key, call.kwargs['env'])
        self.assertEqual(call.kwargs['env']['SSH_AUTH_SOCK'], '/agent')
        runner.reset_mock()
        with self.assertRaises(ValueError):
            provider.call('run_container', {})
        runner.assert_not_called()

    def test_mcp_only_advertises_four_read_tools_without_resources_or_prompts(self):
        provider = Mock()
        requests = [{'id': 1, 'method': 'initialize'}, {'id': 2, 'method': 'tools/list'},
                    {'id': 3, 'method': 'resources/read', 'params': {'uri': 'docker://private/logs'}}]
        output = io.StringIO()
        bridge.serve(provider, io.StringIO('\n'.join(json.dumps(request) for request in requests) + '\n'), output)
        responses = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(responses[0]['result']['capabilities'], {'tools': {}})
        self.assertEqual({tool['name'] for tool in responses[1]['result']['tools']}, set(bridge.TOOL_NAMES))
        self.assertIn('error', responses[2])
        provider.call.assert_not_called()
        self.assertTrue(all(tool['annotations']['readOnlyHint'] for tool in bridge.TOOLS))

    def test_worker_output_is_capped_during_read_and_owned_child_is_stopped(self):
        started = time.monotonic()
        with patch.object(bridge, 'MAX_OUTPUT_BYTES', 512), self.assertRaises(ValueError):
            bridge.bounded_worker([sys.executable, '-c',
                'import sys,time; sys.stdout.write("x"*1000000); sys.stdout.flush(); time.sleep(30)'],
                input='{}', text=True, capture_output=True, env={}, timeout=2, check=False)
        self.assertLess(time.monotonic() - started, 3)

    def test_worker_deadline_kills_its_owned_process_group(self):
        started = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired):
            bridge.bounded_worker([sys.executable, '-c', 'import time; time.sleep(30)'],
                input='{}', text=True, capture_output=True, env={}, timeout=.05, check=False)
        self.assertLess(time.monotonic() - started, 3)

    def test_manifest_is_debug_only_and_opt_out_removes_process_contribution(self):
        manifest = docker_manifest()
        entry = SimpleNamespace(name='docker', load=lambda: manifest)
        self.assertEqual(manifest.compatibility, 'debug')
        with override_settings(DEBUG=False, DEVELOPMENT_MCP_DOCKER_ENABLED=True):
            registry = PluginRegistry()
            self.assertTrue(registry.discover_plugins([entry])[0].ok)
            self.assertEqual(registry.get_extensions(MCP_PROCESS_EXTENSION_POINT), [])
        with override_settings(DEBUG=True, DEVELOPMENT_MCP_DOCKER_ENABLED=False):
            registry = PluginRegistry()
            self.assertTrue(registry.discover_plugins([entry])[0].ok)
            self.assertEqual(registry.get_extensions(MCP_PROCESS_EXTENSION_POINT), [])
        with override_settings(DEBUG=True, DEVELOPMENT_MCP_DOCKER_ENABLED=True,
                               DEVELOPMENT_MCP_TOOL_ROOT='/tools', DEVELOPMENT_MCP_DOCKER_PORT=8022,
                               DEVELOPMENT_MCP_DOCKER_HOST=bridge.DEFAULT_HOST, BASE_DIR=Path('/checkout')):
            registry = PluginRegistry()
            self.assertTrue(registry.discover_plugins([entry])[0].ok)
            spec = registry.get_extensions(MCP_PROCESS_EXTENSION_POINT)[0].value
            self.assertEqual(spec.plugin_id, DOCKER_ID)
            self.assertEqual(spec.port, 8022)
            self.assertEqual(set(spec.tools), set(bridge.TOOL_NAMES))
            self.assertEqual(spec.command[0], '/tools/docker/.venv/bin/python')
