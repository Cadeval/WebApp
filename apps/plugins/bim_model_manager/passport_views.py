"""Load the assessment module only while the BIM plugin is enabled."""
from django.contrib.auth.decorators import login_required
from django.http import Http404
from apps.plugin_manager.workflows import workflow_plugin_enabled
from . import PLUGIN_ID


def _enabled(request):
    if not workflow_plugin_enabled(request.user, PLUGIN_ID):
        raise Http404('Add BIM Workspace to your Plugin Store to use this workflow.')
    from .workspace import workspace_context
    request.plugin_workspace = workspace_context(request)


@login_required(login_url='/mycelium/login')
def calculate(request):
    _enabled(request)
    from apps.plugins.bim_model_manager.assessment_web import calculate as handler
    return handler(request)


@login_required(login_url='/mycelium/login')
def report(request,pk):
    _enabled(request)
    from apps.plugins.bim_model_manager.assessment_web import report as handler
    return handler(request,pk)


@login_required(login_url='/mycelium/login')
def compare(request):
    _enabled(request)
    from apps.plugins.bim_model_manager.assessment_web import compare as handler
    return handler(request)
