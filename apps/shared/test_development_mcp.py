import json
from pathlib import Path
from unittest.mock import patch
from django.test import SimpleTestCase, override_settings
from django_bolt import BoltAPI
from django_bolt.testing import TestClient
from .development_mcp import mount_development_mcp


class DevelopmentMCPTests(SimpleTestCase):
    def setUp(self):
        gate=patch('apps.plugin_manager.environments.active_plugin',return_value=True)
        self.gate=gate.start();self.addCleanup(gate.stop)

    headers={'Content-Type':'application/json','Accept':'application/json, text/event-stream'}

    def rpc(self,client,method,params=None):
        response=client.post('/dev/mcp',json={'jsonrpc':'2.0','id':1,'method':method,'params':params or {}},headers=self.headers)
        self.assertEqual(response.status_code,200,response.text)
        if response.headers['content-type'].startswith('text/event-stream'):
            return json.loads(next(line[6:] for line in response.text.splitlines() if line.startswith('data: ')))
        return response.json()

    def test_production_never_mounts_mcp_even_with_opt_in(self):
        for debug,enabled in [(False,False),(False,True),(True,False)]:
            with self.subTest(debug=debug,enabled=enabled),override_settings(DEBUG=debug,DEVELOPMENT_MCP_ENABLED=enabled):
                api=BoltAPI();self.assertFalse(mount_development_mcp(api));self.assertEqual(api._mcp_mounts,[])
                with TestClient(api) as client:self.assertEqual(client.post('/dev/mcp',json={}).status_code,404)

    @override_settings(DEBUG=True,DEVELOPMENT_MCP_ENABLED=True,BASE_DIR=Path(__file__).resolve().parents[2])
    def test_native_protocol_lists_only_read_only_tools_and_reads_public_demo(self):
        api=BoltAPI();self.assertTrue(mount_development_mcp(api))
        with TestClient(api,base_url='http://localhost') as client:
            self.rpc(client,'initialize',{'protocolVersion':'2025-06-18','capabilities':{},'clientInfo':{'name':'tests','version':'1'}})
            tools=self.rpc(client,'tools/list')['result']['tools']
            self.assertEqual({tool['name'] for tool in tools},{'project_info','demo_report'})
            self.assertTrue(all(tool['annotations']['readOnlyHint'] for tool in tools))
            info_result=self.rpc(client,'tools/call',{'name':'project_info','arguments':{}})
            self.assertIn('structuredContent',info_result.get('result',{}),info_result)
            info=info_result['result']['structuredContent']
            self.assertEqual(info['material_cost_currency'],'EUR')
            report=self.rpc(client,'tools/call',{'name':'demo_report','arguments':{'model':'house-a'}})['result']['structuredContent']
            self.assertTrue(report['provisional']);self.assertIn('building',report);self.assertIn('recovery_cost_analysis',report)
            self.gate.return_value=False
            denied=self.rpc(client,'tools/call',{'name':'project_info','arguments':{}})
            self.assertTrue(denied.get('error') or denied.get('result',{}).get('isError'))
            self.gate.return_value=True
            invalid=self.rpc(client,'tools/call',{'name':'demo_report','arguments':{'model':'../../private'}})
            self.assertTrue(invalid.get('error') or invalid.get('result',{}).get('isError'))
            self.assertEqual(client.post('/dev/mcp',json={'jsonrpc':'2.0','id':1,'method':'tools/list'},headers={**self.headers,'Host':'outside.example'}).status_code,403)
