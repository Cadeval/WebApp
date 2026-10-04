"""Temporary real runbolt server; never use the user's database or uploads."""
import csv
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

import httpx

root = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='cadevil-bolt-runtime-') as directory:
    folder = Path(directory)
    (folder / 'qa_settings.py').write_text(f'''from config.settings.dev import *
DATABASES = {{'default': {{'ENGINE': 'django.db.backends.sqlite3', 'NAME': {str(folder / 'qa.sqlite3')!r}}}}}
MEDIA_ROOT = {str(folder / 'media')!r}
SECRET_KEY = 'isolated-bolt-runtime-check'
# Isolated test server only: actual A–D models are 179–233 MiB.
BOLT_MAX_UPLOAD_SIZE = 256 * 1024 * 1024
''')
    sys.path[:0] = [str(folder), str(root)]
    os.environ['DJANGO_SETTINGS_MODULE'] = 'qa_settings'
    import django
    django.setup()
    from django.core.management import call_command
    from django.contrib.auth import get_user_model
    from django.test import Client
    from apps.plugin_manager.models import PluginRecord
    from apps.plugins.bim_model_manager import PLUGIN_ID
    from apps.plugins.bim_model_manager.django.models import CalculationConfig, FileUpload, CadevilDocument
    from apps.plugins.bim_model_manager.ifc_extractor.test_material_assessment import IfcPassportTests, reference
    call_command('migrate', verbosity=0)
    user = get_user_model().objects.create_user(username='runtime-qa', password='temporary-runtime-password')
    other = get_user_model().objects.create_user(username='runtime-other')
    PluginRecord.objects.update_or_create(plugin_id=PLUGIN_ID, defaults={'enabled': True})
    model = folder / 'fixture.ifc'
    IfcPassportTests().model().write(str(model))
    records = reference()
    columns = list(dict.fromkeys(key for row in records.values() for key in row))
    table = io.StringIO()
    writer = csv.writer(table, delimiter=';')
    writer.writerow(['Material'] + columns)
    for material, row in records.items():
        writer.writerow([material] + [row.get(key, '') for key in columns])
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    env = dict(os.environ, PYTHONPATH=f'{folder}:{root}')
    log_path = folder / 'server.log'
    with log_path.open('w') as log:
        server = subprocess.Popen(['uv', 'run', '--inexact', 'python', 'manage.py', 'runbolt',
            '--settings=qa_settings', '--host=127.0.0.1', f'--port={port}', '--processes=1', '--no-admin'],
            cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            with httpx.Client(base_url=f'http://127.0.0.1:{port}', timeout=1800, follow_redirects=False) as client:
                for attempt in range(150):
                    if server.poll() is not None:
                        raise RuntimeError(log_path.read_text()[-6000:])
                    try:
                        response = client.get('/mycelium/login')
                        if response.status_code == 200:
                            break
                    except httpx.ConnectError:
                        pass
                    time.sleep(.2)
                else:
                    raise RuntimeError('Bolt startup timed out: ' + log_path.read_text()[-6000:])
                print('Real runbolt startup and discovered login route: PASS', flush=True)
                token = client.cookies['csrftoken']
                response = client.post('/mycelium/login', data={'username': user.username,
                    'password': 'temporary-runtime-password'}, headers={'X-CSRFToken': token})
                assert response.status_code == 200, response.text[:500]
                assert 'sessionid' in client.cookies
                pages = ['/plugins/bim/model_manager/', '/plugins/bim/configuration_library/',
                         '/plugins/bim/config_editor/', '/plugins/bim/material-passport/',
                         '/plugins/bim/material-passport/compare/']
                for path in pages:
                    response = client.get(path)
                    assert response.status_code == 200, (path, response.status_code, response.text[:300])
                print('Authenticated native pages: PASS', flush=True)
                for asset in ['/static/css/bim.css', '/static/css/vendor/normalize.css']:
                    stylesheet = client.get(asset)
                    assert stylesheet.status_code == 200 and 'text/css' in stylesheet.headers['content-type'], asset
                print('Local CSS assets: PASS', flush=True)

                token = client.cookies['csrftoken']
                response = client.post(pages[1], data={'csrfmiddlewaretoken': token},
                    files={'document': ('references.csv', table.getvalue().encode(), 'text/csv')})
                assert response.status_code == 302, response.text[:500]
                active = CalculationConfig.objects.get(user=user)
                original = active.upload_id
                payload = {'source': str(original), 'csrfmiddlewaretoken': token}
                for i, (_, row) in enumerate(active.config['data'].items()):
                    for j, header in enumerate(active.config['header']):
                        payload[f'cell_{i}_{j}'] = '1000' if header == 'Dichte' else (row.get(header) or '')
                response = client.post(pages[2], data=payload)
                assert response.status_code == 302, response.text[:500]
                active.refresh_from_db()
                assert active.upload_id != original
                response = client.post(pages[0], data={'description': 'Runtime IFC', 'csrfmiddlewaretoken': token},
                    files={'document': ('fixture.ifc', model.read_bytes(), 'application/octet-stream')})
                assert response.status_code == 302, response.text[:500]
                upload = FileUpload.objects.get(user=user)
                response = client.get(pages[3], params={'model': str(upload.pk)})
                assert response.status_code == 200
                assert f'value="{upload.pk}" selected' in response.text
                documents = []
                for _ in range(2):
                    response = client.post(pages[3], data={'csrfmiddlewaretoken': token, 'model': str(upload.pk),
                        'reference': str(active.upload_id), 'years': '100', 'replacement_boundary': 'inclusive',
                        'grade_weighting': 'mass', 'lca_averaging': 'installed_mass'})
                    assert response.status_code == 302, response.text[:500]
                    report_url = response.headers['location']
                    documents.append(report_url.rstrip('/').split('/')[-1])
                    assert client.get(report_url).status_code == 200
                    downloaded = client.get(report_url, params={'download': 'json'})
                    assert downloaded.status_code == 200
                    assert abs(downloaded.json()['building']['mass'] - 4000) < .001
                    assert 'text/csv' in client.get(report_url, params={'download': 'csv'}).headers['content-type']
                response = client.post(pages[4], data={'csrfmiddlewaretoken': token, 'models': documents})
                assert response.status_code == 200 and 'Graphical model comparison' in response.text
                viewer_path = f'/plugins/bim/models/{upload.pk}/viewer/'
                geometry_path = f'/plugins/bim/models/{upload.pk}/geometry/'
                assert client.get(viewer_path).status_code == 200
                geometry = client.get(geometry_path)
                assert geometry.status_code == 200 and geometry.content[:4] == b'glTF', geometry.text[:300] if geometry.status_code != 200 else geometry.content[:20]
                print('Native viewer and renderable fixture GLB: PASS', flush=True)
                download_path = f'/plugins/bim/models/{upload.pk}/download/'
                assert client.get(download_path).content == model.read_bytes()
                assert 'text/csv' in client.get('/plugins/bim/download_csv/').headers['content-type']
                assert client.post(f'/plugins/bim/models/{upload.pk}/delete/',
                    data={'csrfmiddlewaretoken': token}).status_code == 409
                assert client.post(pages[2], data={}).status_code == 403
                print('HTTP upload, selection, edited version, assessment, comparison, exports and CSRF: PASS', flush=True)
                if '--real-models' in sys.argv:
                    from html.parser import HTMLParser
                    class Diagnostics(HTMLParser):
                        def __init__(self):
                            super().__init__(); self.in_diagnostics = False; self.depth = 0; self.count = 0; self.samples = []; self.current = None
                        def handle_starttag(self, tag, attrs):
                            if tag == 'ul' and ('class', 'bim-diagnostics') in attrs:
                                self.in_diagnostics = True; self.depth = 1
                            elif self.in_diagnostics and tag == 'ul': self.depth += 1
                            elif self.in_diagnostics and tag == 'li': self.count += 1; self.current = []
                        def handle_data(self, data):
                            if self.current is not None: self.current.append(data)
                        def handle_endtag(self, tag):
                            if self.in_diagnostics and tag == 'li':
                                if len(self.samples) < 3: self.samples.append(''.join(self.current or []))
                                self.current = None
                            elif self.in_diagnostics and tag == 'ul':
                                self.depth -= 1
                                if self.depth == 0: self.in_diagnostics = False
                    model_dir = Path('/Users/mia/Desktop/projects/cadevil-data/IFC')
                    real_models = sorted(model_dir.glob('*28V_new.ifc'))
                    if len(real_models) != 4:
                        raise RuntimeError(f'Expected the four requested A–D models, found {len(real_models)}')
                    real_reference = model_dir.parent / 'schema/MP_indicators_and_modfications_short.csv'
                    with real_reference.open('rb') as source:
                        response = client.post(pages[1], data={'description': 'MP indicators for actual A–D models', 'csrfmiddlewaretoken': token},
                            files={'document': (real_reference.name, source, 'text/csv')})
                    assert response.status_code == 302, response.text[:500]
                    active.refresh_from_db()
                    results = []
                    for actual in real_models:
                        print('Testing actual model: ' + actual.name, flush=True)
                        start = time.monotonic()
                        with actual.open('rb') as source:
                            response = client.post(pages[0], data={'description': actual.name, 'csrfmiddlewaretoken': token},
                                files={'document': (actual.name, source, 'application/octet-stream')})
                        assert response.status_code == 302, (actual.name, 'upload', response.status_code, response.text[:500])
                        real_upload = FileUpload.objects.get(user=user, description=actual.name)
                        before = CadevilDocument.objects.filter(user=user).count()
                        response = client.post(pages[3], data={'csrfmiddlewaretoken': token, 'model': str(real_upload.pk),
                            'reference': str(active.upload_id), 'years': '50', 'replacement_boundary': 'before',
                            'grade_weighting': 'mass', 'lca_averaging': 'installed_mass'})
                        result = {'filename': actual.name, 'bytes': actual.stat().st_size, 'http_status': response.status_code,
                                  'elapsed_seconds': round(time.monotonic() - start, 2), 'upload_succeeded': True}
                        if response.status_code == 422:
                            parser = Diagnostics(); parser.feed(response.text)
                            assert parser.count > 0, (actual.name, response.text[:500])
                            assert CadevilDocument.objects.filter(user=user).count() == before
                            result.update(assessment='rejected_invalid_input', diagnostic_count=parser.count,
                                          diagnostic_samples=parser.samples, saved_invalid_report=False)
                        elif response.status_code == 302:
                            report_response = client.get(response.headers['location'], params={'download': 'json'})
                            assert report_response.status_code == 200
                            report = report_response.json()
                            result.update(assessment='saved', complete=report['complete'], building_mass=report['building']['mass'])
                        else:
                            raise AssertionError((actual.name, response.status_code, response.text[:500]))
                        results.append(result)
                        print(json.dumps(result, ensure_ascii=False), flush=True)
                    destination = root / 'docs/BIM_REAL_MODEL_VALIDATION.json'
                    destination.write_text(json.dumps({'model_directory': str(model_dir),
                        'reference': str(real_reference), 'results': results}, ensure_ascii=False, indent=2) + '\n')
                    print('All four requested real-model workflows checked.', flush=True)
                session = Client(); session.force_login(other)
                client.cookies.clear()
                client.cookies.set('sessionid', session.cookies['sessionid'].value)
                assert client.get(report_url).status_code == 404
                assert client.get(download_path).status_code == 404
                assert client.get(viewer_path).status_code == 404
                assert client.get(geometry_path).status_code == 404
                PluginRecord.objects.filter(plugin_id=PLUGIN_ID).update(enabled=False)
                for path in pages:
                    assert client.get(path).status_code == 404, path
                client.cookies.clear()
                assert client.get(pages[0]).status_code == 302
                print('HTTP foreign ownership, disabled plugin and anonymous redirect: PASS', flush=True)
                if '--safari' in sys.argv:
                    PluginRecord.objects.filter(plugin_id=PLUGIN_ID).update(enabled=True)
                    note = Path(__file__).parent / 'bolt_safari_runtime.json'
                    stop = Path(__file__).parent / 'stop_bolt_safari.flag'
                    stop.unlink(missing_ok=True)
                    note.write_text(json.dumps({'url': f'http://127.0.0.1:{port}',
                        'model': str(upload.pk), 'reports': documents}))
                    # Populate Safari's library with all four requested real models.
                    # Links point at read-only source files through temporary media
                    # symlinks; never copy/edit the user's original IFC files.
                    from django.conf import settings
                    from django.core.files.base import ContentFile
                    from apps.plugins.bim_model_manager.django.models import ConfigUpload
                    from apps.plugins.bim_model_manager.ifc_extractor.material_assessment import load_reference
                    from apps.plugins.bim_model_manager.pages import configuration
                    real_dir = Path('/Users/mia/Desktop/projects/cadevil-data/IFC')
                    media = Path(settings.MEDIA_ROOT) / 'browser-models'
                    media.mkdir(parents=True, exist_ok=True)
                    browser_models = []
                    for source in sorted(real_dir.glob('*28V_new.ifc')):
                        (media / source.name).symlink_to(source)
                        seeded = FileUpload.objects.create(user=user, description=source.name, document='browser-models/' + source.name)
                        browser_models.append({'name': source.name, 'id': str(seeded.pk)})
                    reference_path = real_dir.parent / 'schema/MP_indicators_and_modfications_short.csv'
                    reference_upload = ConfigUpload(user=user, description='MP reference for A–D')
                    reference_upload.document.save(reference_path.name, ContentFile(reference_path.read_bytes()))
                    CalculationConfig.objects.update_or_create(user=user, defaults={'upload': reference_upload,
                        'config': configuration(load_reference(reference_path))})
                    note.write_text(json.dumps({'url': f'http://127.0.0.1:{port}', 'model': str(upload.pk), 'reports': documents, 'real_models': browser_models}))
                    if '--viewer-real' in sys.argv:
                        import struct
                        from apps.plugins.bim_model_manager.ifc_extractor.material_assessment import file_hash
                        session = Client(); session.force_login(user)
                        client.cookies.set('sessionid', session.cookies['sessionid'].value)
                        viewer_results = []
                        for item in browser_models:
                            original = real_dir / item['name']
                            original_hash = file_hash(original)
                            started = time.monotonic()
                            print('Tessellating actual viewer model: ' + item['name'], flush=True)
                            result = client.get('/plugins/bim/models/' + item['id'] + '/geometry/')
                            assert result.status_code == 200, (item['name'], result.status_code, result.text[:300])
                            content = result.content
                            assert content[:4] == b'glTF'
                            length = struct.unpack_from('<I', content, 12)[0]
                            gltf = json.loads(content[20:20+length])
                            identities = sum(bool(n.get('extras', {}).get('GlobalId')) for n in gltf['nodes'])
                            assert gltf['meshes'] and identities, item['name']
                            assert file_hash(original) == original_hash
                            evidence = {'filename': item['name'], 'source_sha256': original_hash,
                                        'http_status': result.status_code, 'glb_bytes': len(content),
                                        'meshes': len(gltf['meshes']), 'nodes_with_ifc_identity': identities,
                                        'elapsed_seconds': round(time.monotonic()-started, 2), 'source_unchanged': True}
                            viewer_results.append(evidence)
                            print(json.dumps(evidence), flush=True)
                        (root/'docs/BIM_VIEWER_VALIDATION.json').write_text(json.dumps(viewer_results, indent=2)+'\n')
                    print('Isolated runtime ready for Safari.', flush=True)
                    deadline = time.monotonic() + 1200
                    while not stop.exists() and time.monotonic() < deadline:
                        time.sleep(.5)
                    note.unlink(missing_ok=True)
                    stop.unlink(missing_ok=True)
        except Exception:
            print(log_path.read_text()[-5000:], flush=True)
            raise
        finally:
            server.terminate()
            try:
                server.wait(timeout=8)
            except subprocess.TimeoutExpired:
                server.kill(); server.wait()
    print('Temporary server stopped; isolated database and files removed.', flush=True)
