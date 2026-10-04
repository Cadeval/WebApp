from tests.bolt_browser import BoltBrowser
import csv
import io
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.plugin_manager.models import PluginRecord, UserPluginSelection
from apps.plugin_manager.registry import PluginRegistry
from apps.plugins.bim_model_manager import PLUGIN_ID, plugin_manifest
from .models import BuildingMetrics, CadevilDocument, FileUpload
from .ifc_extractor.test_material_assessment import IfcPassportTests, reference


class MaterialPassportWebTests(TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.settings=override_settings(MEDIA_ROOT=self.directory.name)
        self.settings.enable()
        self.addCleanup(self.settings.disable);self.addCleanup(self.directory.cleanup)
        self.user=get_user_model().objects.create_user(username='passport',password='test-pass')
        self.bim_plugin,_=PluginRecord.objects.update_or_create(plugin_id=PLUGIN_ID,defaults={'enabled':True})
        UserPluginSelection.objects.create(user=self.user,plugin=self.bim_plugin)
        self.url=reverse('material_passport:calculate')
        self.client = BoltBrowser()
        self.addCleanup(self.client.close)
        self.client.force_login(self.user)

    def files(self, invalid=False):
        model=Path(self.directory.name)/'source.ifc'
        f=IfcPassportTests().model()
        if invalid:
            f.by_type('IfcWall')[0].ObjectPlacement=None
        f.write(str(model))
        rows=reference();header=list(dict.fromkeys(k for r in rows.values() for k in r))
        stream=io.StringIO();writer=csv.writer(stream,delimiter=';');writer.writerow(['Material']+header)
        for name,row in rows.items():writer.writerow([name]+[row.get(k,'') for k in header])
        return {'model_file':SimpleUploadedFile('source.ifc',model.read_bytes()),
                'reference_file':SimpleUploadedFile('references.csv',stream.getvalue().encode()),
                'replacement_boundary':'before','grade_weighting':'mass','lca_averaging':'installed_mass'}

    def test_upload_calculation_persistence_and_report_download(self):
        response=self.client.post(self.url,self.files())
        self.assertEqual(response.status_code,302)
        document=CadevilDocument.objects.get(user=self.user)
        metric=BuildingMetrics.objects.get(project=document)
        self.assertTrue(metric.assessment_report['complete'])
        self.assertAlmostEqual(metric.assessment_report['building']['mass'],8250)
        self.assertEqual(document.material_properties.count(),2)
        concrete=document.material_properties.get(name='Beton bewehrt')
        self.assertAlmostEqual(concrete.global_brutto_price,687)
        self.assertAlmostEqual(concrete.gwp_ml_a1_a3,1612.8)
        self.assertAlmostEqual(concrete.gwp_ml_a1_a3_b4,1612.8)
        self.assertIsNone(concrete.gwp_ml_lz)
        report=self.client.get(response.url)
        self.assertContains(report,'2515.8')
        self.assertContains(report,'Element and material inventory')
        downloaded=self.client.get(response.url+'?download=json')
        self.assertEqual(downloaded.status_code,200)
        self.assertEqual(downloaded.json()['provenance']['ifc_schema'],'IFC4')
        self.assertIn('configuration',downloaded.json()['provenance'])
        other=get_user_model().objects.create_user(username='other',password='test-pass')
        UserPluginSelection.objects.create(user=other,plugin=self.bim_plugin)
        self.client.force_login(other)
        self.assertEqual(self.client.get(response.url).status_code,404)
        self.assertEqual(self.client.get(response.url+'?download=json').status_code,404)

    def test_invalid_schema_continues_and_links_warnings_to_viewer(self):
        response=self.client.post(self.url,self.files(invalid=True))
        self.assertEqual(response.status_code,302)
        metric=BuildingMetrics.objects.get(project__user=self.user)
        self.assertGreater(metric.assessment_report['schema_validation']['occurrences'],0)
        self.assertFalse(metric.assessment_report['complete'])
        page=self.client.get(response.url)
        self.assertContains(page,'Provisional assessment')
        self.assertContains(page,'occurrence')
        self.assertContains(page,'?element=')
        group=next(g for g in metric.assessment_report['diagnostic_groups'] if g['elements'])
        identifier=group['elements'][0]['element_id']
        viewer=self.client.get(reverse('bim:viewer',args=[metric.project.upload_id])+'?element='+identifier)
        self.assertContains(viewer,'data-selected-element="'+identifier+'"')

    def test_plugin_runtime_gate_and_anonymous_access(self):
        self.assertEqual(self.client.get(self.url).status_code,200)
        PluginRecord.objects.filter(plugin_id=PLUGIN_ID).update(enabled=False)
        self.assertEqual(self.client.get(self.url).status_code,404)
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code,302)

    def test_policies_required_in_web_form(self):
        data=self.files();data.pop('grade_weighting')
        response=self.client.post(self.url,data)
        self.assertEqual(response.status_code,200)
        self.assertEqual(CadevilDocument.objects.count(),0)
        self.assertContains(response,'This field is required')

    def test_plugin_registers_assessment_navigation(self):
        registry=PluginRegistry();plugin_manifest().register(registry)
        items=registry.get_active('nav_item',enabled_ids=[PLUGIN_ID])
        workspace=reverse('bim:workspace')
        self.assertEqual([(item.label,item.url) for item in items],[('BIM Workspace',workspace)])
        page=self.client.get(workspace+'?tab=calculate',HTTP_HX_REQUEST='true')
        self.assertContains(page,'Material passport calculation')
        self.assertContains(page,'href="'+workspace+'?tab=calculate"')
        self.assertContains(page,'action="'+self.url+'"')
        self.assertEqual([tab['key'] for tab in page.context['plugin_workspace']['tabs'] if tab['selected']],['calculate'])

    def test_foreign_upload_not_selectable(self):
        other=get_user_model().objects.create_user(username='foreign',password='test-pass')
        upload=FileUpload.objects.create(user=other,document='foreign.ifc')
        data=self.files();data.pop('model_file');data['model']=str(upload.pk)
        response=self.client.post(self.url,data)
        self.assertContains(response,'Select a valid choice')
        self.assertEqual(CadevilDocument.objects.count(),0)

    def test_recovery_cost_analysis_saved_displayed_and_downloaded(self):
        response=self.client.post(self.url,self.files())
        metric=BuildingMetrics.objects.get(project__user=self.user)
        analysis=metric.assessment_report['recovery_cost_analysis']
        self.assertAlmostEqual(analysis['known_cost_eur'],729.5)
        self.assertEqual(analysis['currency'],'EUR')
        page=self.client.get(response.url,HTTP_HX_REQUEST='true')
        self.assertContains(page,'Material cost by recovery grade')
        self.assertContains(page,'729.50')
        self.assertContains(page,'?download=recovery_csv')
        self.assertEqual(self.client.get(response.url+'?download=json').json()['recovery_cost_analysis'],analysis)
        csv_response=self.client.get(response.url+'?download=recovery_csv')
        self.assertContains(csv_response,'known_cost_eur')
        other=get_user_model().objects.create_user(username='cost-other')
        UserPluginSelection.objects.create(user=other,plugin=self.bim_plugin)
        self.client.force_login(other)
        self.assertEqual(self.client.get(response.url+'?download=recovery_csv').status_code,404)

    def test_old_reports_gain_analysis_without_database_changes(self):
        response=self.client.post(self.url,self.files())
        metric=BuildingMetrics.objects.get(project__user=self.user)
        data=metric.assessment_report
        data.pop('recovery_cost_analysis')
        data['rows'][0]['global_brutto_price']=None
        data['rows'][0]['recycling_grade']=None
        metric.assessment_report=data;metric.save(update_fields=['assessment_report'])
        page=self.client.get(response.url)
        self.assertContains(page,'Material cost by recovery grade')
        self.assertContains(page,'Ungraded')
        analysis=self.client.get(response.url+'?download=json').json()['recovery_cost_analysis']
        self.assertEqual(analysis['groups'][-1]['missing_price_rows'],1)
        self.assertIsNone(analysis['groups'][-1]['cost_eur'])
        metric.refresh_from_db()
        self.assertNotIn('recovery_cost_analysis',metric.assessment_report)
