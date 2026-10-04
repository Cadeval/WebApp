"""Rendered journeys expose usable landmarks, labels and validation links."""
from html.parser import HTMLParser
from types import SimpleNamespace
from uuid import UUID

from django import forms
from django.contrib.auth.models import AnonymousUser
from django.template.loader import render_to_string
from django.test import SimpleTestCase


class AccessibilityMarkup(HTMLParser):
    def __init__(self, markup):
        super().__init__()
        self.nodes = []
        self.feed(markup)
        self.ids = {attributes['id']: (tag, attributes) for tag, attributes in self.nodes if 'id' in attributes}

    def handle_starttag(self, tag, attributes):
        self.nodes.append((tag, dict(attributes)))


class AccessibilityTests(SimpleTestCase):
    def render(self, template, **context):
        identifier = UUID('f8371a88-6d8c-4e2b-a93b-aa4493f760e4')
        document = SimpleNamespace(pk=identifier, description='Office model', document=SimpleNamespace(name='office.ifc'))
        return AccessibilityMarkup(render_to_string(template, {
            'fragment': True, 'document': document, 'upload_id': identifier, **context,
        }))

    def test_full_shell_has_a_skip_target_history_boundary_and_accessible_policy_links(self):
        markup = self.render('shared/page.html', fragment=False, page_template='index.jinja2', user=AnonymousUser())
        mains = [(tag, attrs) for tag, attrs in markup.nodes if tag == 'main']
        self.assertEqual(len(mains), 1)
        self.assertEqual(mains[0][1]['id'], 'main-content')
        self.assertEqual(mains[0][1]['tabindex'], '-1')
        self.assertIn('hx-history-elt', mains[0][1])
        skip = next(attrs for tag, attrs in markup.nodes if tag == 'a' and attrs.get('class') == 'skip-to-content')
        self.assertIn(skip['href'][1:], markup.ids)
        policies = [attrs for tag, attrs in markup.nodes if tag == 'a' and attrs.get('href') in ('/security', '/accessibility')]
        self.assertEqual(len(policies), 2)
        self.assertTrue(all(link['hx-target'] == '#content-container' for link in policies))
        self.assertTrue(any(tag == 'nav' and attrs.get('aria-label') == 'Site information' for tag, attrs in markup.nodes))

    def test_invalid_upload_and_assessment_forms_link_the_summary_to_labeled_invalid_fields(self):
        class UploadForm(forms.Form):
            document = forms.FileField(label='Source model', help_text='Choose an IFC source file.')

        form = UploadForm(data={})
        self.assertFalse(form.is_valid())
        for template in ('bim/models.html', 'bim/library.html', 'bim/cityjson_import.html',
                         'bim/building_location.html', 'shared/material_passport_form.html',
                         'shared/material_passport_comparison.html'):
            with self.subTest(template=template):
                markup = self.render(template, form=form)
                summaries = [attrs for _, attrs in markup.nodes if 'data-form-errors' in attrs]
                self.assertEqual(len(summaries), 1)
                self.assertEqual(summaries[0]['tabindex'], '-1')
                self.assertEqual(summaries[0]['role'], 'alert')
                links = [attrs for tag, attrs in markup.nodes if tag == 'a' and attrs.get('href') == '#id_document']
                self.assertEqual(len(links), 1)
                self.assertEqual(links[0]['hx-boost'], 'false')
                self.assertTrue(any(tag == 'label' and attrs.get('for') == 'id_document' for tag, attrs in markup.nodes))
                field = markup.ids['id_document'][1]
                self.assertEqual(field['aria-invalid'], 'true')
                for reference in field['aria-describedby'].split():
                    self.assertIn(reference, markup.ids)

    def test_configuration_cells_use_real_material_and_column_names(self):
        rows = [SimpleNamespace(material='Concrete', cells=[SimpleNamespace(field='density', value=2400)])]
        markup = self.render('bim/editor.html', active=SimpleNamespace(upload=SimpleNamespace(description='Reference', document=SimpleNamespace(name='reference.csv')), upload_id='1'),
                             headers=['Density (kg/m³)'], rows=rows)
        cell = next(attrs for tag, attrs in markup.nodes if tag == 'input' and attrs.get('name') == 'density')
        self.assertEqual(cell['aria-labelledby'].split(), ['config-material-1', 'config-column-1'])
        for reference in cell['aria-labelledby'].split():
            self.assertEqual(markup.ids[reference][0], 'th')
            self.assertIn(markup.ids[reference][1]['scope'], ('row', 'col'))
        viewport = next(attrs for _, attrs in markup.nodes if attrs.get('class') == 'config-editor-pan')
        self.assertEqual(viewport['tabindex'], '0')
        self.assertEqual(viewport['role'], 'region')

    def test_viewer_exposes_a_heading_labeled_native_part_picker_and_keyboard_instructions(self):
        markup = self.render('bim/viewer.html')
        self.assertEqual(markup.ids['viewer-title'][0], 'h1')
        canvas = markup.ids['viewer-canvas'][1]
        self.assertEqual(canvas['tabindex'], '0')
        self.assertIn(canvas['aria-describedby'], markup.ids)
        picker = markup.ids['viewer-part-select'][1]
        self.assertIn('disabled', picker)
        self.assertTrue(any(tag == 'label' and attrs.get('for') == 'viewer-part-select' for tag, attrs in markup.nodes))
        self.assertIn(picker['aria-describedby'], markup.ids)
        self.assertIn(picker['aria-controls'], markup.ids)
        self.assertEqual(markup.ids['selection-status'][1]['aria-live'], 'polite')

    def test_valid_forms_do_not_announce_empty_validation_errors(self):
        markup = self.render('shared/form_errors.html', form=forms.Form())
        self.assertFalse(any('data-form-errors' in attrs for _, attrs in markup.nodes))
