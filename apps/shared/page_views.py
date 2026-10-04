"""One full-page shell and one HTMX content boundary for browser views."""
from django.shortcuts import render
from django.utils.cache import patch_vary_headers


def render_page(request, template, context=None, status=200):
    fragment = (request.headers.get('HX-Request') == 'true'
                and request.headers.get('HX-History-Restore-Request') != 'true'
                and request.headers.get('HX-Request-Type') != 'full')
    context = {**(context or {}), 'fragment': fragment}
    workspace = getattr(request, 'plugin_workspace', None)
    if workspace:
        context.update(plugin_workspace=workspace, plugin_panel_template=template)
        template = 'shared/plugin_workspace.html'
    context['page_template'] = template
    response = render(request, template if fragment else 'shared/page.html', context, status=status)
    if workspace and workspace.get('history_url') and fragment:
        response['HX-Push-Url'] = workspace['history_url']
    patch_vary_headers(response, ['HX-Request', 'HX-History-Restore-Request', 'HX-Request-Type'])
    return response
