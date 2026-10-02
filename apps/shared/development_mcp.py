"""Development-only MCP tools over the already-public demonstration fixtures."""
import json
from pathlib import Path
from typing import Literal
from django.conf import settings

DEMO_REPORTS = {
    'house-a': 'house-a-provisional.json',
    'house-b': 'house-b-provisional.json',
    'house-c': 'house-c-provisional.json',
    'house-d': 'house-d-provisional.json',
    'example-a': 'example-a-report.json',
    'example-b': 'example-b-report.json',
}
READ_ONLY = {'readOnlyHint': True, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False}


def project_info() -> dict:
    """Describe the public development features and signed plugin package contract."""
    return {
        'project': 'Cadevil',
        'framework': 'Django-Bolt',
        'purpose': 'IFC model assessment and material recovery analysis',
        'demo_models': list(DEMO_REPORTS),
        'plugin_upload': {'formats': ['zip', 'tar', 'tar.gz', 'tar.xz'], 'metadata': 'plugin.json', 'signatures': 'Ed25519', 'activation': 'administrator review'},
        'material_cost_currency': 'EUR',
        'scope': 'Development tools and public demo fixtures only',
    }


def demo_report(model: Literal['house-a', 'house-b', 'house-c', 'house-d', 'example-a', 'example-b']) -> dict:
    """Read precomputed public demo statistics, including provisional assessment status."""
    if model not in DEMO_REPORTS:
        raise ValueError('Choose a listed public demo model.')
    root = Path(settings.BASE_DIR) / 'resources/static/bim-demo'
    report = json.loads((root / DEMO_REPORTS[model]).read_text())
    keys = ['building', 'lca_averages', 'recovery_cost_analysis', 'complete', 'calculation_complete', 'status', 'issue_count', 'quantity_warning_count', 'units', 'method', 'grade_note']
    return {'model': model, 'provisional': model.startswith('house-'), 'currency': 'EUR', **{key: report[key] for key in keys if key in report}}


def mount_development_mcp(api) -> bool:
    # Neither a stray opt-in on prod nor DEBUG on its own can create a mount.
    if not settings.DEBUG or not getattr(settings, 'DEVELOPMENT_MCP_ENABLED', False):
        return False
    from bolt_mcp import MCP
    from django_bolt import AllowAny
    server = MCP('cadevil-development', '1.0.0', stateless=True, json_response=True)
    server.tool(project_info, annotations=READ_ONLY)
    server.tool(demo_report, annotations=READ_ONLY)
    api.mount_mcp(server, path='/dev/mcp', auth=[], guards=[AllowAny()])
    return True
