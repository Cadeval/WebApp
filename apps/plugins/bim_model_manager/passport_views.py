"""Load the assessment module only while the BIM plugin is enabled."""
from django.contrib.auth.decorators import login_required
from django.http import Http404
from apps.plugin_manager.models import PluginRecord
from . import PLUGIN_ID


def _enabled():
    if not PluginRecord.objects.filter(plugin_id=PLUGIN_ID,enabled=True,error="",source=PluginRecord.Source.PACKAGE).exists():
        raise Http404('BIM assessment plugin is not enabled.')


@login_required(login_url='/mycelium/login')
def calculate(request):
    _enabled()
    from apps.shared.assessment_web import calculate as handler
    return handler(request)


@login_required(login_url='/mycelium/login')
def report(request,pk):
    _enabled()
    from apps.shared.assessment_web import report as handler
    return handler(request,pk)


@login_required(login_url='/mycelium/login')
def compare(request):
    _enabled()
    from apps.shared.assessment_web import compare as handler
    return handler(request)
