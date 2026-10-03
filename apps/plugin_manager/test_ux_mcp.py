"""Security boundaries for the local development UI/UX tool."""
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock
from django.test import SimpleTestCase, override_settings
from .ux_mcp_bridge import (AUDIT_TOOL, GUIDANCE_TOOL, TOOLS, MAX_FILE_BYTES,
                            source_snapshot, validate_arguments, serve)
from .registry import PluginRegistry
from .debug_processes import MCP_PROCESS_EXTENSION_POINT
from apps.plugins.development_mcp import ui_ux_manifest, UI_UX_ID


class UXToolBoundaryTests(SimpleTestCase):
    def test_browser_paths_urls_output_and_unknown_tools_are_rejected(self):
        for args in ({'projectPath':'/etc'}, {'baseUrl':'http://localhost'}, {'depth':'deep'},
                     {'output':'report.html'}, {'command':'anything'}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                validate_arguments(AUDIT_TOOL, args)
        for args in ({'file':'../../secret'}, {'topic':'../secret'}, {'topic':'forms','file':'other'},
                     {'topic':[]}, {}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                validate_arguments(GUIDANCE_TOOL, args)
        with self.assertRaises(ValueError):
            validate_arguments('uiux_audit_log', {})
        self.assertEqual(validate_arguments(GUIDANCE_TOOL, {'topic':'forms'}), {'search':'form'})

    def test_snapshot_omits_private_data_and_links_and_maps_django_sources(self):
        with TemporaryDirectory() as folder:
            root = Path(folder) / 'project'; root.mkdir()
            css = root / 'resources/static/css'; css.mkdir(parents=True)
            templates = root / 'resources/templates'; templates.mkdir(parents=True)
            (css/'style.css').write_text('button { color: #777; }')
            (templates/'index.jinja2').write_text('<button>{{ label }}</button>')
            (templates/'.env').write_text('not frontend')
            private = root/'data'; private.mkdir(); (private/'private.html').write_text('private')
            (css/'linked.css').symlink_to(private/'private.html')
            (templates/'linked-directory').symlink_to(private, target_is_directory=True)
            snapshot = Path(folder)/'snapshot'; snapshot.mkdir()
            aliases = source_snapshot(root, snapshot)
            self.assertEqual(set(aliases.values()), {'resources/static/css/style.css', 'resources/templates/index.jinja2'})
            self.assertEqual((snapshot/'resources/templates/index.jinja2.html').read_text(), '<button>{{ label }}</button>')
            self.assertFalse((snapshot/'data').exists())

    def test_oversized_source_is_refused(self):
        with TemporaryDirectory() as root, TemporaryDirectory() as snapshot:
            css = Path(root)/'resources/static/css'; css.mkdir(parents=True)
            (css/'huge.css').write_bytes(b'x'*(MAX_FILE_BYTES+1))
            with self.assertRaises(ValueError): source_snapshot(root, snapshot)

    def test_only_two_read_tools_are_advertised_and_unknown_calls_never_reach_vendor(self):
        upstream = Mock()
        incoming = io.StringIO('\n'.join(json.dumps(r) for r in [
            {'jsonrpc':'2.0','id':1,'method':'tools/list'},
            {'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'uiux_audit_log','arguments':{}}},
            {'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':AUDIT_TOOL,'arguments':{'depth':'deep'}}},
        ])+'\n')
        outgoing = io.StringIO(); serve(upstream, '/unused', incoming, outgoing)
        results = [json.loads(line) for line in outgoing.getvalue().splitlines()]
        self.assertEqual({tool['name'] for tool in results[0]['result']['tools']}, {AUDIT_TOOL, GUIDANCE_TOOL})
        self.assertTrue(all(tool['annotations']['readOnlyHint'] for tool in TOOLS))
        self.assertTrue(results[1]['result']['isError']); self.assertTrue(results[2]['result']['isError'])
        upstream.call.assert_not_called()

    def test_plugin_registers_only_in_debug_and_has_bounded_tools(self):
        manifest = ui_ux_manifest()
        self.assertEqual(manifest.compatibility, 'debug'); self.assertEqual(manifest.version, '0.6.1')
        entry = Mock(name='uiux-entry'); entry.load.return_value=manifest
        with override_settings(DEBUG=False):
            registry = PluginRegistry(); results = registry.discover_plugins([entry])
            self.assertTrue(results[0].ok)
            self.assertEqual(registry.get_extensions(MCP_PROCESS_EXTENSION_POINT), [])
        with override_settings(DEBUG=True, DEVELOPMENT_MCP_TOOL_ROOT='/local/tools',
                               DEVELOPMENT_MCP_UI_UX_PORT=8019, BASE_DIR=Path('/local/project')):
            registry = PluginRegistry(); results = registry.discover_plugins([entry])
            self.assertTrue(results[0].ok)
            extension = registry.get_extensions(MCP_PROCESS_EXTENSION_POINT)[0]
            self.assertEqual(extension.plugin_id, UI_UX_ID)
            self.assertEqual(set(extension.value.tools), {AUDIT_TOOL, GUIDANCE_TOOL})
            self.assertEqual(extension.value.port, 8019)
