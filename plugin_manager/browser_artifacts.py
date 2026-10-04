"""Finite source-owned worker bytes; no path supplied by a request is opened."""
from pathlib import Path
from django.core.exceptions import ValidationError
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, Http404
from django.templatetags.static import static

ROOT = Path(__file__).resolve().parent.parent
MAX_ARTIFACT_BYTES = 2 * 1024 * 1024
BUNDLED = {
    'cadevil.example.editor': {
        'worker.js': ('plugins/example_plugin/static/js/plugins/example_plugin_worker.js', 'js/plugins/example_plugin_worker.js'),
        'module.wasm': ('plugins/example_plugin/static/wasm/example_plugin.wasm', 'wasm/example_plugin.wasm'),
    },
    'cadevil.rust-example.editor': {
        'worker.js': ('resources/static/js/plugins/snake_game_worker.js', 'js/plugins/snake_game_worker.js'),
        'module.wasm': ('resources/static/wasm/rust_example_plugin.wasm', 'wasm/rust_example_plugin.wasm'),
    },
    'cadevil.browser.wasm-wrapper': {
        'worker.js': ('resources/static/js/plugins/wasm_plugin_worker.js', 'js/plugins/wasm_plugin_worker.js'),
    },
}


def bundled_plugin_trust(plugin_id):
    from .certificate_authority import bundled_trust

    declared = BUNDLED.get(plugin_id)
    if declared is None:
        raise ValidationError('This installed plugin has no registered browser artifact bundle.')
    files = {}
    for name, (relative, _) in declared.items():
        path = ROOT / relative
        if any(part.is_symlink() for part in (path, *path.parents) if part.is_relative_to(ROOT)) or not path.is_file():
            raise ValidationError('Bundled browser artifacts must be regular source-owned files.')
        if not 0 < path.stat().st_size <= MAX_ARTIFACT_BYTES:
            raise ValidationError('Bundled browser artifact exceeds its size limit.')
        content = path.read_bytes()
        if not 0 < len(content) <= MAX_ARTIFACT_BYTES:
            raise ValidationError('Bundled browser artifact exceeds its size limit.')
        files[name] = content
    return {**bundled_trust(plugin_id, files),
            'urls': {name: static(asset) for name, (_, asset) in declared.items()},
            'entrypoint': 'worker.js', 'wasm': 'module.wasm' if 'module.wasm' in files else ''}


def bundled_wasm_wrapper_trust():
    return bundled_plugin_trust('cadevil.browser.wasm-wrapper')


@login_required(login_url='/mycelium/login')
def worker_bootstrap(request):
    from .workflows import selected_plugin_ids

    if not selected_plugin_ids(request.user):
        raise Http404('Select an available workflow before starting a plugin worker.')
    response = HttpResponse((ROOT / 'resources/static/js/plugin_worker_bootstrap.js').read_bytes(),
                            content_type='application/javascript')
    response['Content-Security-Policy'] = "default-src 'none'; script-src blob: 'wasm-unsafe-eval'; connect-src 'none'; worker-src 'none'; base-uri 'none'; form-action 'none'"
    response['Cache-Control'] = 'private, no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    response['Cross-Origin-Resource-Policy'] = 'same-origin'
    return response
