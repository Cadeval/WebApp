"""One URL-backed workspace for BIM pages; detail routes keep their identities."""
from urllib.parse import quote, urlencode

from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.urls import reverse
from django.views.decorators.http import require_GET


TABS = (
    ('models', 'Models'),
    ('map', 'Map'),
    ('references', 'References'),
    ('editor', 'Reference editor'),
    ('calculate', 'Material passport'),
    ('compare', 'Comparison'),
)

LEGACY_TABS = {
    '/plugins/bim/model_manager/': 'models',
    '/plugins/bim/map/': 'map',
    '/plugins/bim/configuration_library/': 'references',
    '/plugins/bim/config_editor/': 'editor',
    '/plugins/bim/material-passport/': 'calculate',
    '/plugins/bim/material-passport/compare/': 'compare',
}


def workspace_url(tab='models', query=None):
    """Encode supplied query values while replacing the selected tab."""
    values = [('tab', tab)]
    if query is not None:
        values.extend((key, value) for key, entries in query.lists()
                      if key != 'tab' for value in entries)
    # Bolt's native query parser keeps '+' literal. Explicit percent-encoded
    # spaces preserve decoded bookmark values across a subsequent request.
    return reverse('bim:workspace') + '?' + urlencode(values, quote_via=quote)


def active_tab(request):
    path = request.path
    if path == reverse('bim:workspace'):
        return request.GET.get('tab', 'models')
    if path in LEGACY_TABS:
        return LEGACY_TABS[path]
    if '/buildings/' in path and path.endswith(('/location/', '/context/')):
        return 'map'
    if path.endswith('/viewer/') and request.GET.get('from') == 'map':
        return 'map'
    if path.startswith('/plugins/bim/material-passport/compare/'):
        return 'compare'
    if path.startswith('/plugins/bim/material-passport/'):
        return 'calculate'
    return 'models'


def workspace_context(request):
    selected = active_tab(request)
    return {
        'name': 'BIM Workspace', 'slug': 'bim', 'url': reverse('bim:workspace'),
        'active_tab_id': 'plugin-bim-tab-' + selected,
        'tabs': [
            {'key': key, 'label': label, 'url': workspace_url(key),
             'selected': key == selected, 'tab_id': 'plugin-bim-tab-' + key}
            for key, label in TABS
        ],
        # Old bookmarks still render. HTMX links into their top-level aliases
        # adopt the canonical workspace URL, including model/page arguments.
        'history_url': workspace_url(selected, request.GET) if request.path in LEGACY_TABS else '',
    }


@login_required(login_url='/mycelium/login')
@require_GET
def workspace(request):
    from .passport_views import _enabled
    _enabled(request)
    # Dispatch only the selected trusted handler. Inactive map, editor and
    # calculation panels must not parse IFCs, query providers or render forms.
    from . import pages, exchange_pages, passport_views
    handlers = {
        'models': pages.model_manager,
        'map': exchange_pages.building_map,
        'references': pages.library,
        'editor': pages.config_editor,
        'calculate': passport_views.calculate,
        'compare': passport_views.compare,
    }
    handler = handlers.get(request.GET.get('tab', 'models'))
    if handler is None:
        raise Http404('This workspace tab is not available.')
    return handler(request)
