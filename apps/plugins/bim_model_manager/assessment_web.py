"""Material-passport entry point alongside the in-progress Rust/Bolt rewrite."""
import csv
import io
import logging
import tempfile
from pathlib import Path

from django import forms
from django.contrib.auth.decorators import login_required
from django.core.validators import FileExtensionValidator
from django.db import transaction
from django.http import JsonResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_http_methods

from apps.plugins.bim_model_manager.ifc_extractor.diagnostics import grouped_diagnostics
from apps.plugins.bim_model_manager.ifc_extractor.recovery_costs import recovery_cost_analysis
from apps.plugins.bim_model_manager.ifc_extractor.ifc_assessment import InvalidIfc, assess_ifc
from apps.plugins.bim_model_manager.ifc_extractor.material_assessment import AssessmentOptions, load_reference, file_hash
from apps.plugins.bim_model_manager.django.models import BuildingMetrics, CadevilDocument, CalculationConfig, ConfigUpload, FileUpload, MaterialProperties
from apps.plugins.bim_model_manager.django.uploads import validate_config_upload_size, validate_model_upload_size
from apps.shared.page_views import render_page as render
from apps.plugins.bim_model_manager.assessment_presentation import charts, comparison as comparison_data
from apps.plugins.bim_model_manager.model_choice_widgets import ModelThumbnailRadioSelect, ModelThumbnailCheckboxSelect

logger = logging.getLogger('cadevil.assessment')


class PassportForm(forms.Form):
    model = forms.ModelChoiceField(queryset=FileUpload.objects.none(), required=False, blank=True,
                                   empty_label='Upload a new IFC below', widget=ModelThumbnailRadioSelect)
    model_file = forms.FileField(required=False, validators=[FileExtensionValidator(['ifc']), validate_model_upload_size])
    reference = forms.ModelChoiceField(queryset=ConfigUpload.objects.none(), required=False)
    reference_file = forms.FileField(required=False, validators=[FileExtensionValidator(['xlsx','csv']), validate_config_upload_size])
    years = forms.IntegerField(label='Observation period in years', min_value=1, initial=50, required=False)
    replacement_boundary = forms.ChoiceField(choices=[('', 'Choose the replacement boundary'), ('before','Replace strictly before the end of the observation period'),('inclusive','Include replacement at the end of the observation period')])
    lca_averaging = forms.ChoiceField(choices=[('', 'Choose LCA averaging'),('installed_mass','Impacts per initially installed kilogram'),('material_mean','Arithmetic mean of impact totals per distinct material')])
    grade_weighting = forms.ChoiceField(choices=[('', 'Choose grade weighting'),('mass','Weight by installed material mass'),('equal','Average material grades equally')])

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['model'].queryset = FileUpload.objects.filter(user=user)
        self.fields['reference'].queryset = ConfigUpload.objects.filter(user=user)

    def clean(self):
        c = super().clean()
        if c.get('years') is None and 'years' not in self.errors:
            c['years'] = 50
        for existing, uploaded in [('model','model_file'),('reference','reference_file')]:
            if bool(c.get(existing)) == bool(c.get(uploaded)):
                self.add_error(uploaded, 'Select an existing file or upload one new file.')
        return c


def _save_results(user, upload, report):
    group = user.groups.order_by('pk').first()
    if group is None:
        raise ValueError('The account needs a group before results can be saved.')
    doc = CadevilDocument.objects.create(user=user, group=group, upload=upload, description=upload.description)
    BuildingMetrics.objects.create(project=doc, assessment_report=report)
    for name, totals in report['materials'].items():
        values = {k:totals[k] for k in ('volume','area','length','mass','waste_mass','recyclable_mass',
                                       'global_brutto_price','local_brutto_price','local_netto_price')}
        values.update({f'{i}_ml_{p}':totals[f'{i}_{p}'] for i in ('gwp','ap','penrt') for p in ('a1_a3','a1_a3_b4')})
        MaterialProperties.objects.create(project=doc, name=name, **values)
    return doc


@login_required(login_url='/mycelium/login')
@require_http_methods(['GET','POST'])
def calculate(request):
    initial = {}
    if request.method == 'GET':
        model_id = request.GET.get('model')
        if model_id:
            try:
                initial['model'] = FileUpload.objects.get(pk=model_id, user=request.user)
            except (FileUpload.DoesNotExist, ValueError, forms.ValidationError):
                pass
        active = CalculationConfig.objects.filter(user=request.user).first()
        if active:
            initial['reference'] = active.upload_id
    form = PassportForm(request.POST if request.method == 'POST' else None,
                        request.FILES if request.method == 'POST' else None, user=request.user, initial=initial)
    diagnostics = []
    if request.method == 'POST' and form.is_valid():
        c = form.cleaned_data
        options = AssessmentOptions(years=c['years'], include_endpoint=c['replacement_boundary']=='inclusive', grade_weighting=c['grade_weighting'], lca_averaging=c['lca_averaging'])
        # Assess temporary copies first; unreadable IFC/reference files remain fatal. Schema issues are retained as warnings.
        with tempfile.TemporaryDirectory(prefix='cadeval-passport-') as directory:
            def source(existing, uploaded):
                if existing: return existing.document.path
                path = Path(directory)/Path(uploaded.name).name
                with path.open('wb') as output:
                    for block in uploaded.chunks(): output.write(block)
                uploaded.seek(0)
                return str(path)
            try:
                reference_path = source(c.get('reference'),c.get('reference_file'))
                reference = load_reference(reference_path)
                report = assess_ifc(source(c.get('model'),c.get('model_file')), reference, options)
                report['provenance']['reference_file_sha256'] = file_hash(reference_path)
                report['provenance']['reference_filename'] = Path(reference_path).name
            except InvalidIfc as exc:
                diagnostics = exc.diagnostics
            except (ValueError, RuntimeError, OSError) as exc:
                logger.warning('Assessment input could not be processed', extra={
                    'event': 'assessment_input_rejected', 'error_type': type(exc).__name__})
                diagnostics = [str(exc)]
            else:
                stored = []
                try:
                    with transaction.atomic():
                        upload = c.get('model')
                        if upload is None:
                            upload = FileUpload(user=request.user, description=c['model_file'].name)
                            upload.document.save(c['model_file'].name,c['model_file'],save=True)
                            stored.append(upload.document)
                        config_upload = c.get('reference')
                        if config_upload is None:
                            config_upload = ConfigUpload(user=request.user, description=c['reference_file'].name)
                            config_upload.document.save(c['reference_file'].name,c['reference_file'],save=True)
                            stored.append(config_upload.document)
                        CalculationConfig.objects.update_or_create(user=request.user, defaults={'upload':config_upload, 'config':{
                            'data':reference, 'header':list(dict.fromkeys(k for r in reference.values() for k in r)),
                            'assessment':{'include_endpoint':options.include_endpoint,'grade_weighting':options.grade_weighting,'lca_averaging':options.lca_averaging}}})
                        document = _save_results(request.user,upload,report)
                except Exception:
                    logger.exception('Assessment results could not be saved', extra={'event': 'assessment_storage_failed'})
                    for file in stored: file.delete(save=False)
                    raise
                logger.info('Assessment results saved', extra={'event': 'assessment_saved', 'outcome': 'completed'})
                return redirect('material_passport:report',pk=document.pk)
    return render(request,'shared/material_passport_form.html',{'form':form,'diagnostics':diagnostics},status=422 if diagnostics else 200)


@login_required(login_url='/mycelium/login')
def report(request, pk):
    document = get_object_or_404(CadevilDocument,pk=pk,user=request.user)
    metrics = document.building_metrics.order_by('pk').first()
    data = metrics.assessment_report if metrics else {}
    # Enrich old row-based reports in memory, without changing saved assessments.
    if data and 'recovery_cost_analysis' not in data and isinstance(data.get('rows'),list):
        data = {**data, 'recovery_cost_analysis': recovery_cost_analysis(data['rows'])}
    if data:
        from apps.plugins.bim_model_manager.ifc_extractor.element_identity import enrich_report
        data = enrich_report(data)
        data['diagnostic_groups'] = grouped_diagnostics(data)
    if request.GET.get('download') == 'json':
        response = JsonResponse(data,json_dumps_params={'ensure_ascii':False,'allow_nan':False})
        response['Content-Disposition'] = 'attachment; filename="material-passport.json"'
        return response
    if request.GET.get('download') == 'recovery_csv':
        stream=io.StringIO()
        writer=csv.writer(stream,delimiter=';')
        writer.writerow(['recovery_grade','cost_eur','known_cost_eur','share_of_known_cost_percent','priced_rows','missing_price_rows','rows'])
        for group in data.get('recovery_cost_analysis',{}).get('groups',[]):
            writer.writerow(['Ungraded' if group['grade'] is None else group['grade'],group['cost_eur'],group['known_cost_eur'],group['share_of_known_cost_percent'],group['priced_rows'],group['missing_price_rows'],group['rows']])
        response=HttpResponse(stream.getvalue(),content_type='text/csv; charset=utf-8')
        response['Content-Disposition']='attachment; filename="material-passport-recovery-costs.csv"'
        return response
    if request.GET.get('download') == 'csv':
        stream=io.StringIO()
        columns=['display_name','name','element_id','step_id','ifc_class','material','volume','area','length','mass','mass_observation',
                 'waste_mass','waste_mass_observation','recyclable_mass','recyclable_mass_observation',
                 'gwp_a1_a3','gwp_a1_a3_b4','ap_a1_a3','ap_a1_a3_b4','penrt_a1_a3','penrt_a1_a3_b4',
                 'global_brutto_price','local_brutto_price','local_netto_price','recycling_grade',
                 'replacements','combination_adjusted','storey_id','storey_name','quantity_source','issues']
        writer=csv.DictWriter(stream,fieldnames=columns,delimiter=';');writer.writeheader()
        inventory={entry['element_id']:entry for entry in data.get('inventory',[])}
        for row in data.get('rows',[]):
            record={**inventory.get(row['element_id'],{}),**row}
            values={key:record.get(key) for key in columns}
            values['issues']='; '.join(row.get('issues',[]))
            # Material and storey names are external input. Keep CSV inert in Excel.
            for key,value in values.items():
                if isinstance(value,str) and value.lstrip().startswith(('=','+','-','@')):
                    values[key]="'"+value
            writer.writerow(values)
        response=HttpResponse(stream.getvalue(),content_type='text/csv; charset=utf-8')
        response['Content-Disposition']='attachment; filename="material-passport-inventory.csv"'
        return response
    return render(request,'shared/material_passport_report.html',{'document':document,'report':data,
        'materials':list(data.get('materials',{}).items()), 'rows':data.get('rows',[]),
        'elements':list(data.get('elements',{}).items()),
        'charts':charts(list(data.get('materials',{}).items()))})


class ComparisonForm(forms.Form):
    models=forms.ModelMultipleChoiceField(queryset=CadevilDocument.objects.none(),widget=ModelThumbnailCheckboxSelect)

    def __init__(self,*args,user,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['models'].queryset=CadevilDocument.objects.filter(user=user,building_metrics__isnull=False).select_related('upload').distinct()

    def clean_models(self):
        models=self.cleaned_data['models']
        if len(models)<2:
            raise forms.ValidationError('Choose at least two assessed models.')
        return models


@login_required(login_url='/mycelium/login')
@require_http_methods(['GET','POST'])
def compare(request):
    form=ComparisonForm(request.POST if request.method == 'POST' else None,user=request.user)
    context={'form':form}
    if request.method=='POST' and form.is_valid():
        documents=[]
        for document in form.cleaned_data['models']:
            metric=document.building_metrics.order_by('pk').first()
            documents.append((document,metric.assessment_report if metric else {}))
        context.update(comparison_data(documents))
        context['documents']=[document for document,_ in documents]
        context['assessments']=[{'document':document,'report':report} for document,report in documents]
    return render(request,'shared/material_passport_comparison.html',context)
