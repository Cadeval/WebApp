"""The localhost MCP bridge forwards only the reviewed transport reference."""
from unittest.mock import patch

from django.test import SimpleTestCase
from plugin_manager.mcp_bridge import upstream_environment


class BridgeEnvironmentTests(SimpleTestCase):
    def test_agent_reference_is_limited_to_docker_without_application_credentials(self):
        with patch.dict('os.environ', {'SSH_AUTH_SOCK': '/private/agent.sock',
                                     'SECRET_KEY': 'private', 'DOCKER_HOST': 'tcp://other'},
                        clear=True):
            self.assertEqual(upstream_environment('cadevil.mcp.docker'),
                             {'CADEVIL_DEBUG_MCP': '1', 'SSH_AUTH_SOCK': '/private/agent.sock'})
            self.assertEqual(upstream_environment('cadevil.mcp.git'),
                             {'CADEVIL_DEBUG_MCP': '1'})

    def test_absent_agent_does_not_create_an_authentication_override(self):
        with patch.dict('os.environ', {}, clear=True):
            self.assertEqual(upstream_environment('cadevil.mcp.docker'),
                             {'CADEVIL_DEBUG_MCP': '1'})
