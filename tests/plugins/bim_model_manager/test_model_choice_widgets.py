"""Thumbnail presentation preserves owned native form choices and validation."""
from html.parser import HTMLParser
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from plugins.bim_model_manager.assessment_web import PassportForm, ComparisonForm
from plugins.bim_model_manager.django.models import FileUpload, CadevilDocument, BuildingMetrics


class ChoiceMarkup(HTMLParser):
    def __init__(self, markup):
        super().__init__()
        self.inputs = []
        self.images = []
        self.feed(markup)

    def handle_starttag(self, tag, attributes):
        if tag == 'input':
            self.inputs.append(dict(attributes))
        if tag == 'img':
            self.images.append(dict(attributes))


class ModelChoiceWidgetTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='choice-owner')
        self.other = get_user_model().objects.create_user(username='choice-other')
        self.upload = FileUpload.objects.create(user=self.user, description='House <A>', document='owned/source-a.ifc')
        self.foreign = FileUpload.objects.create(user=self.other, description='Foreign house', document='other/source.ifc')
        self.route = patch('plugins.bim_model_manager.model_choice_widgets.reverse',
                           side_effect=lambda name, args: '/plugins/bim/models/' + str(args[0]) + '/thumbnail/')
        self.route.start(); self.addCleanup(self.route.stop)

    def assessment(self, upload=None, user=None):
        user = user or self.user
        document = CadevilDocument.objects.create(user=user, group=user.groups.first(), upload=upload or self.upload,
                                                  description='Saved <assessment>')
        BuildingMetrics.objects.create(project=document, assessment_report={'complete': False})
        return document

    def test_radio_cards_keep_owned_values_initial_selection_and_lazy_safe_images(self):
        form = PassportForm(user=self.user, initial={'model': self.upload})
        markup = str(form['model'].as_field_group())
        parsed = ChoiceMarkup(markup)
        self.assertEqual({item['value'] for item in parsed.inputs}, {'', str(self.upload.pk)})
        self.assertTrue(all(item['name'] == 'model' and item['type'] == 'radio' for item in parsed.inputs))
        selected = [item for item in parsed.inputs if 'checked' in item]
        self.assertEqual([item['value'] for item in selected], [str(self.upload.pk)])
        self.assertIn('<fieldset', markup)
        self.assertIn('<legend', markup)
        self.assertIn('House &lt;A&gt;', markup)
        self.assertNotIn('House <A>', markup)
        self.assertNotIn(str(self.foreign.pk), markup)
        self.assertEqual(len(parsed.images), 1)
        self.assertIn(str(self.upload.pk), parsed.images[0]['data-thumbnail-src'])
        self.assertNotIn('src', parsed.images[0])

    def test_empty_library_retains_an_explicit_upload_choice_without_a_thumbnail(self):
        form = PassportForm(user=get_user_model().objects.create_user(username='empty-choice'))
        markup = str(form['model'])
        parsed = ChoiceMarkup(markup)
        self.assertEqual(len(parsed.inputs), 1)
        self.assertEqual(parsed.inputs[0]['value'], '')
        self.assertIn('checked', parsed.inputs[0])
        self.assertIn('Upload a new IFC below', markup)
        self.assertFalse(parsed.images)

    def test_radio_selection_survives_other_field_errors_and_cannot_bypass_exclusive_upload_validation(self):
        form = PassportForm({'model': str(self.upload.pk)}, user=self.user)
        self.assertFalse(form.is_valid())
        self.assertNotIn('model', form.errors)
        selected = [item for item in ChoiceMarkup(str(form['model'])).inputs if 'checked' in item]
        self.assertEqual(selected[0]['value'], str(self.upload.pk))
        exclusive = PassportForm({'model': str(self.upload.pk)},
                                 files={'model_file': SimpleUploadedFile('new.ifc', b'test data')}, user=self.user)
        self.assertFalse(exclusive.is_valid())
        self.assertIn('Select an existing file or upload one new file.', exclusive.errors['model_file'])

    def test_foreign_model_stays_rejected_and_absent_from_rebound_cards(self):
        form = PassportForm({'model': str(self.foreign.pk)}, user=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn('Select a valid choice', str(form.errors['model']))
        markup = str(form['model'].as_field_group())
        self.assertNotIn(str(self.foreign.pk), markup)
        self.assertIn('aria-invalid="true"', markup)
        self.assertIn('id_model_error', markup)

    def test_comparison_checkboxes_keep_report_values_and_shared_source_thumbnail_identity(self):
        first, second = self.assessment(), self.assessment()
        foreign = self.assessment(upload=self.foreign, user=self.other)
        form = ComparisonForm({'models': [str(first.pk), str(second.pk)]}, user=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        markup = str(form['models'].as_field_group())
        parsed = ChoiceMarkup(markup)
        self.assertEqual({item['value'] for item in parsed.inputs}, {str(first.pk), str(second.pk)})
        self.assertTrue(all(item['type'] == 'checkbox' and item['name'] == 'models' and 'checked' in item for item in parsed.inputs))
        self.assertEqual({item['data-thumbnail-src'] for item in parsed.images},
                         {'/plugins/bim/models/' + str(self.upload.pk) + '/thumbnail/'})
        self.assertIn('Assessment ' + str(first.pk)[:8], markup)
        self.assertIn('Assessment ' + str(second.pk)[:8], markup)
        self.assertIn('Saved &lt;assessment&gt;', markup)
        self.assertNotIn(str(foreign.pk), markup)
        self.assertNotIn('required', parsed.inputs[0])

    def test_comparison_minimum_error_preserves_checked_card_and_rejects_foreign_ids(self):
        own = self.assessment()
        foreign = self.assessment(upload=self.foreign, user=self.other)
        one = ComparisonForm({'models': [str(own.pk)]}, user=self.user)
        self.assertFalse(one.is_valid())
        self.assertIn('Choose at least two assessed models.', one.errors['models'])
        parsed = ChoiceMarkup(str(one['models']))
        self.assertEqual([item['value'] for item in parsed.inputs if 'checked' in item], [str(own.pk)])
        invalid = ComparisonForm({'models': [str(own.pk), str(foreign.pk)]}, user=self.user)
        self.assertFalse(invalid.is_valid())
        self.assertIn('Select a valid choice', str(invalid.errors['models']))
