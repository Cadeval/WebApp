"""One full-page shell and one HTMX content boundary for browser views."""
from django.shortcuts import render
from django.utils.cache import patch_vary_headers


def render_page(request, template, context=None, status=200):
    fragment = request.headers.get('HX-Request') == 'true' and request.headers.get('HX-History-Restore-Request') != 'true'
    context = {**(context or {}), 'fragment': fragment, 'page_template': template}
    response = render(request, template if fragment else 'shared/page.html', context, status=status)
    patch_vary_headers(response, ['HX-Request', 'HX-History-Restore-Request'])
    return response
