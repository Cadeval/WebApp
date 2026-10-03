"""Real rendered workflows keep file encoding, native fallback and task labels."""
from html.parser import HTMLParser
from types import SimpleNamespace

from django.template.loader import render_to_string
from django.test import SimpleTestCase


class FormMarkup(HTMLParser):
    def __init__(self, markup):
        super().__init__()
        self.forms = []
        self.downloads = []
        self.feed(markup)

    def handle_starttag(self, tag, attributes):
        values = dict(attributes)
        if tag == 'form':
            self.forms.append(values)
        if tag == 'a' and 'data-download-notice' in values:
            self.downloads.append(values)


class TaskActivityFormTests(SimpleTestCase):
    def markup(self, template, **context):
        document = SimpleNamespace(pk='f8371a88-6d8c-4e2b-a93b-aa4493f760e4', description='Test model',
                                   document=SimpleNamespace(name='source.ifc'))
        return FormMarkup(render_to_string(template, {'fragment': True, 'document': document, **context}))

    def test_long_running_forms_keep_native_fallback_and_complete_multipart_submission(self):
        cases = (
            ('bim/cityjson_import.html', 'Uploading and converting CityJSON model', True, {}),
            ('bim/cityjson_export.html', 'Preparing CityJSON export', False, {}),
            ('bim/models.html', 'Uploading IFC model', True, {}),
            ('bim/library.html', 'Uploading and checking reference data', True, {}),
            ('shared/material_passport_form.html', 'Calculating material passport', True, {}),
            ('shared/material_passport_comparison.html', 'Comparing material passports', False, {}),
        )
        for template, label, multipart, context in cases:
            with self.subTest(template=template):
                form = self.markup(template, **context).forms[0]
                self.assertEqual(form['method'], 'post')
                self.assertEqual(form['action'], form['hx-post'])
                self.assertEqual(form['hx-target'], '#content-container')
                self.assertEqual(form['hx-swap'], 'outerHTML')
                self.assertEqual(form['hx-push-url'], 'true')
                self.assertEqual(form['data-task-label'], label)
                if multipart:
                    self.assertEqual(form['enctype'], 'multipart/form-data')
                self.assertNotIn('hx-disable', form)

    def test_refresh_uses_its_local_target_and_downloads_retain_native_browser_behavior(self):
        form = self.markup('bim/_building_context_results.html', result_id='building-map-context-7',
                           lookup_url='/plugins/bim/models/model/buildings/guid/context/').forms[0]
        self.assertEqual(form['hx-target'], '#building-map-context-7')
        self.assertEqual(form['data-task-label'], 'Refreshing local prices and building rules')
        exported = self.markup('bim/cityjson_export.html', ready=True)
        self.assertEqual(len(exported.downloads), 1)
        self.assertEqual(exported.downloads[0]['hx-boost'], 'false')
        self.assertNotIn('hx-get', exported.downloads[0])
        report = self.markup('shared/material_passport_report.html', report={})
        self.assertGreaterEqual(len(report.downloads), 2)
        for link in report.downloads:
            self.assertEqual(link['hx-boost'], 'false')
            self.assertNotIn('hx-get', link)
