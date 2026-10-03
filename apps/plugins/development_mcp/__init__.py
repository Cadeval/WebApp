"""Trusted bundled development tooling. Process ownership belongs to debugserver."""
from apps.plugin_manager.manifest import PluginManifest
from django.conf import settings
from pathlib import Path
import shutil
import sys
from apps.plugin_manager.debug_processes import MCPProcess, MCP_PROCESS_EXTENSION_POINT

def register_context7(registry):
    root = Path(settings.DEVELOPMENT_MCP_TOOL_ROOT)
    node = shutil.which('node') or '/opt/homebrew/bin/node'
    registry.register_extension(MCP_PROCESS_EXTENSION_POINT, CONTEXT7_ID, MCPProcess(CONTEXT7_ID, (node, str(root / 'context7/node_modules/@upstash/context7-mcp/dist/index.js')), ('resolve-library-id', 'query-docs'), settings.DEVELOPMENT_MCP_CONTEXT7_PORT))

def register_git(registry):
    root = Path(settings.DEVELOPMENT_MCP_TOOL_ROOT)
    registry.register_extension(MCP_PROCESS_EXTENSION_POINT, GIT_ID, MCPProcess(GIT_ID, (str(root / 'bin/mcp-server-git'), '--repository', str(settings.BASE_DIR)), ('git_status', 'git_diff_unstaged', 'git_diff_staged', 'git_diff', 'git_log', 'git_show', 'git_branch'), settings.DEVELOPMENT_MCP_GIT_PORT))

def register_ui_ux(registry):
    root = Path(settings.DEVELOPMENT_MCP_TOOL_ROOT)
    node = shutil.which('node') or '/opt/homebrew/bin/node'
    bridge = Path(settings.BASE_DIR) / 'apps/plugin_manager/ux_mcp_bridge.py'
    server = root / 'ui-ux-suite/node_modules/ui-ux-suite/lib/mcp-server.js'
    registry.register_extension(MCP_PROCESS_EXTENSION_POINT, UI_UX_ID,
        MCPProcess(UI_UX_ID, (sys.executable, str(bridge), '--node', node,
                             '--server', str(server), '--project', str(settings.BASE_DIR)),
                   ('uiux_audit_local', 'uiux_guidance'), settings.DEVELOPMENT_MCP_UI_UX_PORT))

CONTEXT7_ID = "cadevil.mcp.context7"
GIT_ID = "cadevil.mcp.git"
NATIVE_ID = "cadevil.mcp.native"
UI_UX_ID = "cadevil.mcp.ui_ux"

def context7_manifest():
    return PluginManifest(id=CONTEXT7_ID, name="Context7 documentation MCP", type="MCP subprocess", version="4.1.1", compatibility="debug", priority=200, register=register_context7)

def git_manifest():
    return PluginManifest(id=GIT_ID, name="Gitolite repository MCP", type="MCP subprocess", version="2026.8.18", compatibility="debug", priority=201, register=register_git)

def native_manifest():
    return PluginManifest(id=NATIVE_ID, name="Cadevil native MCP", type="Bolt MCP", version="1.0.0", compatibility="debug", priority=202)

def ui_ux_manifest():
    return PluginManifest(id=UI_UX_ID, name="UI/UX static audit MCP", type="MCP subprocess",
        version="0.6.1", compatibility="debug", priority=203, register=register_ui_ux)
