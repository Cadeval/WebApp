from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch
from django.test import TestCase, SimpleTestCase, override_settings
from django.core.management import call_command
from django.core.management.base import CommandError
from .manifest import PluginManifest
from .models import PluginRecord, PluginActivationError
from .registry import PluginRegistry, NavItem, NAV_ITEM_EXTENSION_POINT
from .debug_processes import DebugProcesses, MCPProcess

class CompatibilityTests(TestCase):
    def test_activation_and_nav_follow_current_environment_without_losing_preference(self):
        for mode in ['debug','production','both']:
            record=PluginRecord.objects.create(plugin_id='test.'+mode, compatibility=mode, enabled=True)
            for debug in [False,True]:
                with override_settings(DEBUG=debug):
                    allowed=mode=='both' or mode==('debug' if debug else 'production')
                    self.assertEqual(record.effective_enabled,allowed)
                    if allowed:record.set_enabled(True)
                    else:
                        with self.assertRaises(PluginActivationError):record.set_enabled(True)
                    record.refresh_from_db();self.assertTrue(record.enabled)

    def test_discovery_keeps_labels_but_does_not_run_incompatible_hooks(self):
        for debug in [False,True]:
            with override_settings(DEBUG=debug):
                registry=PluginRegistry();hooks={mode:Mock() for mode in ['debug','production','both']}
                entries=[SimpleNamespace(name=mode,load=lambda mode=mode:PluginManifest(id='test.'+mode, compatibility=mode,register=hooks[mode])) for mode in hooks]
                results=registry.discover_plugins(entries);registry.sync_plugin_records(results)
                for mode,hook in hooks.items():
                    self.assertEqual(hook.called,mode=='both' or mode==('debug' if debug else 'production'))
                    self.assertEqual(PluginRecord.objects.get(plugin_id='test.'+mode).compatibility,mode)

    def test_nav_filters_even_explicit_enabled_ids(self):
        registry=PluginRegistry()
        registry._manifests['test.debug']=PluginManifest(id='test.debug',compatibility='debug')
        registry.register_nav_item('test.debug',NavItem(label='Debug',url='/debug/'))
        with override_settings(DEBUG=False):self.assertEqual(registry.get_active(NAV_ITEM_EXTENSION_POINT,['test.debug']),[])

    def test_invalid_label_is_rejected(self):
        registry=PluginRegistry()
        results=registry.discover_plugins([SimpleNamespace(name='bad',load=lambda:PluginManifest(id='test.bad',compatibility='unknown'))])
        self.assertFalse(results[0].ok)

class DebugProcessTests(SimpleTestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.settings=override_settings(DEBUG=True,DEVELOPMENT_MCP_ENABLED=True,BASE_DIR=Path(self.tmp.name));self.settings.enable();self.addCleanup(self.settings.disable)
        self.spec=MCPProcess('test.debug',('fake-server',),('lookup',),8017)
        self.child=Mock(pid=987654);self.child.poll.return_value=None
        self.spawn=Mock(return_value=self.child)
        self.owner=DebugProcesses([self.spec],'fake-python','bridge.py',popen=self.spawn,clock=lambda:0)

    @patch('apps.plugin_manager.debug_processes.stop_process')
    def test_single_owner_start_toggle_stop_cleanup(self,stop):
        self.owner.reconcile(['test.debug']);self.owner.reconcile(['test.debug'])
        self.assertEqual(self.spawn.call_count,1)
        kwargs=self.spawn.call_args.kwargs
        self.assertTrue(kwargs['start_new_session']);self.assertEqual(kwargs['env']['CADEVIL_DEBUG_MCP'],'1')
        self.owner.reconcile([]);stop.assert_called_once_with(self.child)
        self.assertEqual(self.owner.children,{})
        self.owner.reconcile(['test.debug']);self.owner.close()
        self.assertEqual(stop.call_count,2)

    def test_production_never_spawns(self):
        with override_settings(DEBUG=False):self.owner.reconcile(['test.debug'])
        self.spawn.assert_not_called()
        with override_settings(DEBUG=False):
            with self.assertRaises(CommandError):call_command('debugserver')

    def test_crashed_process_has_backoff(self):
        self.owner.reconcile(['test.debug']);self.child.poll.return_value=1
        self.owner.reconcile(['test.debug']);self.owner.reconcile(['test.debug'])
        self.assertEqual(self.spawn.call_count,1);self.assertEqual(self.owner.children,{})

    def test_spawn_failure_does_not_crash_web_launcher(self):
        self.spawn.side_effect=FileNotFoundError()
        self.owner.reconcile(['test.debug']);self.owner.reconcile(['test.debug'])
        self.assertEqual(self.spawn.call_count,1)


class PackageCompatibilityTests(SimpleTestCase):
    def package(self, mode):
        import io,json
        from zipfile import ZipFile
        manifest={'id':'demo.compatibility','name':'Compatibility fixture','version':'1.0.0','api_version':'1.0','type':'javascript','entrypoint':'worker.js','compatibility':mode}
        data=io.BytesIO()
        with ZipFile(data,'w') as archive:
            archive.writestr('plugin.json',json.dumps(manifest));archive.writestr('worker.js','self.onmessage=()=>{};')
        return data.getvalue()

    def test_all_three_signed_manifest_labels_are_parsed(self):
        from .packages import validate_package
        for mode in ['debug','production','both']:
            self.assertEqual(validate_package(self.package(mode))['compatibility'],mode)

    def test_invalid_labels_are_readable_validation_errors(self):
        from django.core.exceptions import ValidationError
        from .packages import validate_package
        for mode in ['invalid',[],{},None]:
            with self.subTest(mode=mode),self.assertRaisesMessage(ValidationError,'Package compatibility must be'):
                validate_package(self.package(mode))
