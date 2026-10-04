import copy
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from apps.plugin_manager.models import PluginRecord
from apps.plugins.bim_model_manager import PLUGIN_ID
from .models import BuildingMetrics, CadevilDocument
from . import test_material_passport_web as original


class ThesisAlignmentWebTests(TestCase):
    setUp=original.MaterialPassportWebTests.setUp
    files=original.MaterialPassportWebTests.files

    def assess(self):
        response=self.client.post(self.url,self.files())
        self.assertEqual(response.status_code,302)
        return CadevilDocument.objects.order_by('-pk').filter(upload__document__isnull=False).get(pk=response.url.rstrip('/').split('/')[-1])

    def test_report_has_signed_charts_element_summaries_and_csv_inventory(self):
        document=self.assess()
        url=reverse('material_passport:report',args=[document.pk])
        response=self.client.get(url)
        self.assertContains(response,'Graphical material report')
        self.assertContains(response,'Element summaries')
        self.assertContains(response,'Geometric and storey inventory')
        self.assertContains(response,'Global gross material cost')
        cost_charts = [chart for chart in response.context['charts'] if chart['key'].endswith('_price')]
        self.assertEqual([chart['unit'] for chart in cost_charts], ['EUR'] * 3)
        self.assertContains(response,'<svg xmlns="http://www.w3.org/2000/svg" width="700"',count=16)
        self.assertContains(response,'aria-label=')
        csv=self.client.get(url+'?download=csv')
        self.assertEqual(csv.status_code,200)
        self.assertIn('text/csv',csv['Content-Type'])
        self.assertIn('mass_observation;waste_mass',csv.content.decode())
        self.assertIn('storey_name',csv.content.decode())
        self.assertIn('Beton bewehrt',csv.content.decode())

    def test_averaging_policy_required_and_retained_in_saved_report(self):
        data=self.files();data.pop('lca_averaging')
        response=self.client.post(self.url,data)
        self.assertContains(response,'This field is required')
        self.assertEqual(CadevilDocument.objects.count(),0)
        document=self.assess()
        report=BuildingMetrics.objects.get(project=document).assessment_report
        self.assertEqual(report['options']['lca_averaging'],'installed_mass')
        self.assertAlmostEqual(report['lca_averages']['values']['gwp_a1_a3_b4'],2515.8/8250)

    def test_model_comparison_checks_boundaries_and_shows_all_indicators(self):
        a=self.assess();b=self.assess()
        url=reverse('material_passport:compare')
        response=self.client.post(url,{'models':[str(a.pk),str(b.pk)]})
        self.assertEqual(response.status_code,200)
        self.assertTrue(response.context['comparable'])
        self.assertContains(response,'Graphical model comparison')
        self.assertContains(response,'AP A1–A3 plus B4')
        self.assertContains(response,'Local net material cost')
        cost_charts = [chart for chart in response.context['charts'] if chart['key'].endswith('_price')]
        self.assertEqual([chart['unit'] for chart in cost_charts], ['EUR'] * 3)
        metric=BuildingMetrics.objects.get(project=b)
        report=copy.deepcopy(metric.assessment_report)
        report['options']['include_endpoint']=True
        metric.assessment_report=report;metric.save(update_fields=['assessment_report'])
        response=self.client.post(url,{'models':[str(a.pk),str(b.pk)]})
        self.assertFalse(response.context['comparable'])
        self.assertContains(response,'assessment boundaries differ')

    def test_comparison_is_owner_scoped_authenticated_and_plugin_gated(self):
        a=self.assess();b=self.assess()
        other=get_user_model().objects.create_user(username='comparison-other',password='test-pass')
        b.user=other;b.save(update_fields=['user'])
        url=reverse('material_passport:compare')
        response=self.client.post(url,{'models':[str(a.pk),str(b.pk)]})
        self.assertNotIn('charts',response.context)
        self.assertContains(response,'Select a valid choice')
        PluginRecord.objects.filter(plugin_id=PLUGIN_ID).update(enabled=False)
        self.assertEqual(self.client.get(url).status_code,404)
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code,302)

    def test_configurable_span_changes_replacements_and_report_labels(self):
        data=self.files();data.update(years='100',replacement_boundary='inclusive')
        response=self.client.post(self.url,data)
        self.assertEqual(response.status_code,302)
        report=BuildingMetrics.objects.get(project__user=self.user).assessment_report
        self.assertEqual(report['options']['years'],100)
        self.assertTrue(report['options']['include_endpoint'])
        self.assertGreater(report['building']['gwp_a1_a3_b4'],2515.8)
        rendered=self.client.get(response.url)
        self.assertContains(rendered,'100 years')
        self.assertNotContains(rendered,'year 50')
        data=self.files();data['years']='0'
        invalid=self.client.post(self.url,data)
        self.assertContains(invalid,'Ensure this value is greater than or equal to 1')
        self.assertEqual(BuildingMetrics.objects.count(),1)
