"""Boundaries and plugin lifecycle for offline, read-only development audits."""
import io
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, TestCase, override_settings

from .code_audit_bridge import (AUDIT_TOOL, INFO_TOOL, TOOLS, MAX_FILE_BYTES,
    LocalAudit, analyzer_command, analyzer_environment, bounded_process,
    normalize_report, read_source, serve, source_snapshot, validate_arguments)
from apps.plugin_manager.debug_processes import MCP_PROCESS_EXTENSION_POINT
from apps.plugin_manager.models import PluginRecord, UserPluginSelection
from apps.plugin_manager.registry import PluginRegistry
from apps.plugin_manager.workflows import is_workflow_plugin, selectable_plugin, selected_plugin_ids
from apps.plugin_manager import tests as existing_tests
from apps.plugins.development_mcp import CODE_AUDIT_ID, code_audit_manifest


class CodeAuditBoundaryTests(SimpleTestCase):
    def test_arbitrary_paths_commands_urls_rules_fixes_and_invalid_bounds_are_denied(self):
        for arguments in ({'path':'/etc'}, {'command':'anything'}, {'url':'https://example.org'},
                          {'rule':'custom'}, {'fix':True}, {'engine':[]}, {'limit':True},
                          {'offset':-1}, {'limit':201}, {'offset':100001}):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                validate_arguments(AUDIT_TOOL, arguments)
        for name, arguments in ((INFO_TOOL, {'path':'/' }), ('semgrep_scan', {})):
            with self.assertRaises(ValueError): validate_arguments(name, arguments)
        self.assertEqual(validate_arguments(AUDIT_TOOL, {}), {'engine':'all','offset':0,'limit':100,'snapshot_hash':None})
        with self.assertRaises(ValueError): validate_arguments(AUDIT_TOOL, {'offset':1})

    def test_snapshot_covers_authored_sources_and_excludes_uploads_env_vendor_and_links(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)/'project'; root.mkdir()
            sources = {'apps/auth.py':'pass\n', 'config/settings/dev.py':'DEBUG = True\n',
                       'resources/static/js/viewer.js':'const x = 1;\n',
                       'apps/plugins/test/src/lib.rs':'fn main() {}\n', 'manage.py':'pass\n',
                       'resources/static/css/site.css':'body { color: #333; }',
                       '.github/workflows/check.yml':'name: Checks\n', 'Makefile':'check:\n\ttrue\n',
                       'pyproject.toml':'[project]\nname = "fixture"\n'}
            omitted = {'data/upload.py':'private', 'tests/data/generated.py':'private', '.env':'secret',
                       'apps/node_modules/vendor.js':'vendor', 'apps/.cache/cache.py':'private',
                       'resources/static/js/htmx.js':'vendor', 'reference/old.py':'archive'}
            for name, content in {**sources, **omitted}.items():
                target=root/name; target.parent.mkdir(parents=True,exist_ok=True); target.write_text(content)
            (root/'apps/link.py').symlink_to(root/'data/upload.py')
            (root/'apps/linked').symlink_to(root/'data', target_is_directory=True)
            snapshot=Path(folder)/'snapshot'
            result=source_snapshot(root,snapshot)
            self.assertEqual(set(result['files']),set(sources))
            self.assertEqual(result['skipped'],[{'path':'apps/link.py','reason':'link or missing file'}])
            self.assertFalse((snapshot/'data').exists()); self.assertFalse((snapshot/'reference').exists())
            first_hash=result['sha256']; (root/'apps/auth.py').write_text('changed = True\n')
            self.assertNotEqual(source_snapshot(root,snapshot)['sha256'],first_hash)

    def test_parent_link_traversal_and_oversized_source_are_refused(self):
        with TemporaryDirectory() as folder:
            root=Path(folder)/'project'; root.mkdir(); private=Path(folder)/'private'; private.mkdir()
            (private/'secret.py').write_text('private')
            (root/'apps').symlink_to(private,target_is_directory=True)
            with self.assertRaises(OSError): read_source(root,Path('apps/secret.py'))
            (root/'apps').unlink(); (root/'apps').mkdir()
            (root/'apps/large.py').write_bytes(b'x'*(MAX_FILE_BYTES+1))
            with self.assertRaisesMessage(ValueError,'1 MiB'): source_snapshot(root,Path(folder)/'snapshot')

    @patch('apps.plugins.development_mcp.code_audit_bridge.Path.is_file', return_value=True)
    @patch('apps.plugins.development_mcp.code_audit_bridge.sys.platform', 'darwin')
    def test_commands_use_fixed_local_rules_and_network_denial_without_autofix(self, _sandbox_exists):
        for engine in ('semgrep','ruff'):
            command=analyzer_command(engine,'/tools','/snapshot')
            self.assertEqual(command[:3],['/usr/bin/sandbox-exec','-p','(version 1) (allow default) (deny network*)'])
            self.assertFalse(any('https://' in arg or arg == '--fix' for arg in command))
        command=analyzer_command('semgrep','/tools','/snapshot')
        self.assertIn('/tools/upstream-rules',command); self.assertIn('--metrics=off',command)
        self.assertIn('--disable-version-check',command); self.assertIn('--no-trace',command)
        with patch('apps.plugins.development_mcp.code_audit_bridge.sys.platform','linux'):
            with self.assertRaisesMessage(ValueError,'no-network'): analyzer_command('semgrep','/tools','/snapshot')
        with patch('apps.plugins.development_mcp.code_audit_bridge.Path.is_file', return_value=False):
            with self.assertRaisesMessage(ValueError,'no-network'): analyzer_command('semgrep','/tools','/snapshot')

    def test_environment_never_inherits_project_tokens_proxies_or_user_configuration(self):
        with TemporaryDirectory() as folder:
            root=Path(folder); certificate=root/'.venv/lib/python9/site-packages/certifi/cacert.pem'
            certificate.parent.mkdir(parents=True); certificate.write_text('fixture')
            with patch.dict('os.environ',{'SEMGREP_APP_TOKEN':'private','HTTPS_PROXY':'https://proxy','DJANGO_SECRET_KEY':'private'}):
                env=analyzer_environment(root,root/'state')
            for key in ('SEMGREP_APP_TOKEN','HTTPS_PROXY','DJANGO_SECRET_KEY','HOME'): self.assertNotIn(key,env)
            self.assertEqual(env['SEMGREP_SEND_METRICS'],'off')
            self.assertEqual(env['SEMGREP_SETTINGS_FILE'],str(root/'state/settings.yml'))

    def test_analyzer_timeout_and_output_limit_stop_the_owned_child(self):
        with self.assertRaises(TimeoutError):
            bounded_process([sys.executable,'-c','import time; time.sleep(30)'],cwd='/',env={},timeout=.1)
        with self.assertRaises(ValueError):
            bounded_process([sys.executable,'-c','print("x" * 100000)'],cwd='/',env={},max_output=512)

    def test_semgrep_rule_ids_are_stable_without_changing_upstream_rule_identity(self):
        raw={'results':[{'check_id':'Users.someone.tools.upstream-rules.python.lang.correctness.fixture',
                         'path':'/snapshot/apps/app.py','start':{'line':2,'col':3},
                         'extra':{'message':'Fixture','severity':'WARNING'}}],
             'paths':{'scanned':['/snapshot/apps/app.py']},'errors':[]}
        report=normalize_report('semgrep',raw,'/snapshot',{'files':['apps/app.py','Makefile']},0)
        self.assertEqual(report['findings'][0]['rule'],'semgrep-rules/python.lang.correctness.fixture')
        self.assertEqual(report['findings'][0]['path'],'apps/app.py')
        self.assertEqual(report['scanned'],['apps/app.py']); self.assertEqual(report['errors'],[])

    @patch('apps.plugins.development_mcp.code_audit_bridge.analyzer_command', return_value=['fixed-tool'])
    def test_pagination_reuses_unchanged_snapshot_but_source_changes_invalidate_cache(self, _command):
        with TemporaryDirectory() as folder:
            root=Path(folder); project=root/'project'; (project/'apps').mkdir(parents=True)
            source=project/'apps/app.py'; source.write_text('import os\n')
            certificate=root/'runtime/.venv/lib/python9/site-packages/certifi/cacert.pem'
            certificate.parent.mkdir(parents=True); certificate.write_text('fixture')
            findings=[{'code':'F401','filename':'apps/app.py','location':{'row':1,'column':1},'message':'unused import'}]*3
            runner=Mock(return_value=(1,json.dumps(findings).encode(),b''))
            audit=LocalAudit(project,root/'runtime',runner)
            first=audit.call(AUDIT_TOOL,{'engine':'ruff','limit':1})
            second=audit.call(AUDIT_TOOL,{'engine':'ruff','offset':1,'limit':1,'snapshot_hash':first['coverage']['sha256']})
            self.assertEqual(first['totalFindings'],3); self.assertEqual(second['nextOffset'],2)
            self.assertTrue(first['complete']); self.assertEqual(runner.call_count,1)
            self.assertEqual(source.read_text(),'import os\n')
            source.write_text('import sys\n')
            with self.assertRaisesMessage(ValueError,'Source changed'):
                audit.call(AUDIT_TOOL,{'engine':'ruff','offset':1,'snapshot_hash':first['coverage']['sha256']})
            self.assertEqual(runner.call_count,1)
            audit.call(AUDIT_TOOL,{'engine':'ruff'})
            self.assertEqual(runner.call_count,2)

    @patch('apps.plugins.development_mcp.code_audit_bridge.analyzer_command', return_value=['fixed-tool'])
    def test_scan_failure_reports_incomplete_and_never_claims_zero_clean_findings(self, _command):
        with TemporaryDirectory() as folder:
            root=Path(folder); (root/'apps').mkdir(); (root/'apps/app.py').write_text('pass\n')
            certificate=root/'.venv/lib/python9/site-packages/certifi/cacert.pem'
            certificate.parent.mkdir(parents=True); certificate.write_text('fixture')
            runner=Mock(side_effect=TimeoutError('fixture'))
            report=LocalAudit(root,root,runner).call(AUDIT_TOOL,{'engine':'semgrep'})
            self.assertFalse(report['complete']); self.assertEqual(report['errors'][0]['type'],'TimeoutError')

    def test_mcp_advertises_two_read_tools_and_rejects_unknown_calls_before_analysis(self):
        audit=Mock(); audit.call.side_effect=lambda name,args: validate_arguments(name,args)
        requests=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-11-25'}},
                  {'jsonrpc':'2.0','id':2,'method':'tools/list'},
                  {'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'semgrep_scan_remote'}},
                  {'jsonrpc':'2.0','id':4,'method':'tools/call','params':{'name':AUDIT_TOOL,'arguments':{'fix':True}}}]
        outgoing=io.StringIO(); serve(audit,io.StringIO('\n'.join(json.dumps(x) for x in requests)+'\n'),outgoing)
        responses=[json.loads(line) for line in outgoing.getvalue().splitlines()]
        self.assertEqual({tool['name'] for tool in responses[1]['result']['tools']},{AUDIT_TOOL,INFO_TOOL})
        self.assertTrue(all(tool['annotations']['readOnlyHint'] and not tool['annotations']['openWorldHint'] for tool in TOOLS))
        self.assertTrue(responses[2]['result']['isError']); self.assertTrue(responses[3]['result']['isError'])

    def test_manifest_registers_allowlisted_subprocess_only_in_debug(self):
        manifest=code_audit_manifest(); self.assertEqual(manifest.compatibility,'debug')
        entry=SimpleNamespace(name='audit',load=lambda:manifest)
        with override_settings(DEBUG=False):
            registry=PluginRegistry(); self.assertTrue(registry.discover_plugins([entry])[0].ok)
            self.assertEqual(registry.get_extensions(MCP_PROCESS_EXTENSION_POINT),[])
        with override_settings(DEBUG=True,DEVELOPMENT_MCP_TOOL_ROOT='/tools',DEVELOPMENT_MCP_CODE_AUDIT_PORT=8020,BASE_DIR=Path('/checkout')):
            registry=PluginRegistry(); self.assertTrue(registry.discover_plugins([entry])[0].ok)
            contribution=registry.get_extensions(MCP_PROCESS_EXTENSION_POINT)[0]
            self.assertEqual(contribution.plugin_id,CODE_AUDIT_ID); self.assertEqual(contribution.value.port,8020)
            self.assertEqual(set(contribution.value.tools),{AUDIT_TOOL,INFO_TOOL})
            self.assertIn('/checkout/apps/plugins/development_mcp/code_audit_bridge.py',contribution.value.command)


@override_settings(DEBUG=True,STATIC_URL='/static/')
class CodeAuditPluginAccessTests(TestCase):
    def setUp(self):
        existing_tests.NativePluginTests.setUp(self)
        self.record,_=PluginRecord.objects.update_or_create(plugin_id=CODE_AUDIT_ID,
            defaults={'name':'Local code audit MCP','enabled':True,'compatibility':'debug','error':''})

    def test_only_staff_catalog_shows_developer_plugin_and_personal_enable_is_denied(self):
        self.client.force_login(self.regular)
        self.assertNotContains(self.client.get('/plugins/manage/'),self.record.name)
        self.assertEqual(self.client.post(f'/plugins/store/{CODE_AUDIT_ID}/enable/').status_code,409)
        self.assertFalse(UserPluginSelection.objects.filter(plugin=self.record).exists())
        self.client.logout(); self.client.force_login(self.staff)
        self.assertContains(self.client.get('/plugins/manage/'),self.record.name)
        self.assertEqual(self.client.post(f'/plugins/store/{CODE_AUDIT_ID}/enable/').status_code,409)
        self.assertFalse(is_workflow_plugin(self.record)); self.assertFalse(selectable_plugin(self.record))

    def test_forged_personal_selection_and_production_environment_cannot_activate_it(self):
        UserPluginSelection.objects.create(user=self.regular,plugin=self.record)
        self.assertNotIn(CODE_AUDIT_ID,selected_plugin_ids(self.regular))
        with override_settings(DEBUG=False):
            self.assertFalse(self.record.effective_enabled)
            self.assertFalse(selectable_plugin(self.record))

    def test_first_discovery_enables_new_plugin_and_preserves_subsequent_admin_disable(self):
        self.record.delete(); registry=PluginRegistry(); manifest=code_audit_manifest()
        registry.sync_plugin_records([SimpleNamespace(plugin_id=CODE_AUDIT_ID,name=manifest.name,
            version=manifest.version,api_version='1.0',priority=204,error='',compatibility='debug',ok=True)])
        record=PluginRecord.objects.get(plugin_id=CODE_AUDIT_ID); self.assertTrue(record.enabled)
        record.enabled=False; record.save()
        registry.sync_plugin_records([SimpleNamespace(plugin_id=CODE_AUDIT_ID,name=manifest.name,
            version=manifest.version,api_version='1.0',priority=204,error='',compatibility='debug',ok=True)])
        record.refresh_from_db(); self.assertFalse(record.enabled)
