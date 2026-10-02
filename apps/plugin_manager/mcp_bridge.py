"""Debug-only Streamable HTTP adapter for one trusted stdio MCP subprocess.

Executed in the isolated developer MCP runtime, not the application's interpreter.
"""
import asyncio
from contextlib import asynccontextmanager
import json
import os
import sys


def main():
    if os.environ.get('CADEVIL_DEBUG_MCP') != '1':
        raise SystemExit('MCP subprocesses must be started by the debug launcher.')
    spec = json.loads(sys.argv[1])
    # Imports are deliberately delayed: production has no dependency on the MCP SDK.
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.server.lowlevel import Server
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    from mcp.server.transport_security import TransportSecuritySettings
    from mcp.types import ToolAnnotations
    from starlette.applications import Starlette
    from starlette.routing import Mount
    import uvicorn

    allowed = frozenset(spec['tools'])
    port = int(spec['port'])
    server = Server(spec['plugin_id'])
    upstream = None
    tools = []

    @server.list_tools()
    async def list_tools():
        return tools

    @server.call_tool()
    async def call_tool(name, arguments):
        if name not in allowed:
            raise ValueError('This tool is not enabled by the debug plugin.')
        return await upstream.call_tool(name, arguments)

    manager = StreamableHTTPSessionManager(app=server, stateless=True, json_response=True, security_settings=TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=[f'127.0.0.1:{port}', f'localhost:{port}'], allowed_origins=[f'http://127.0.0.1:{port}', f'http://localhost:{port}']))

    @asynccontextmanager
    async def lifespan(app):
        nonlocal upstream, tools
        command, *args = spec['command']
        params = StdioServerParameters(command=command, args=args, cwd=spec['cwd'])
        # SDK owns/reaps the upstream process group, including on SIGTERM shutdown.
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await asyncio.wait_for(session.initialize(), timeout=30)
                upstream = session
                result = await session.list_tools()
                tools = [tool.model_copy(update={'annotations': ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)}) for tool in result.tools if tool.name in allowed]
                if {tool.name for tool in tools} != allowed:
                    raise RuntimeError('MCP subprocess is missing an expected tool.')
                async with manager.run():
                    yield

    async def handle(scope, receive, send):
        await manager.handle_request(scope, receive, send)

    app = Starlette(routes=[Mount('/mcp', app=handle)], lifespan=lifespan)
    uvicorn.run(app, host='127.0.0.1', port=port, timeout_graceful_shutdown=5, log_level='info')

if __name__ == '__main__':
    main()
