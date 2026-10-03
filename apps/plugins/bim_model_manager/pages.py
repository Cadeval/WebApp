"""BIM library and configuration pages using the current shared data models."""
import csv
import io
import tempfile
from zipfile import BadZipFile
from functools import wraps
from pathlib import Path
from uuid import UUID

from django.core.files.base import ContentFile
from django.db import transaction
from django.http import FileResponse, HttpResponse, JsonResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST
from django.views.decorators.vary import vary_on_headers
from django.contrib.auth.decorators import login_required

from apps.shared.models import CalculationConfig, ConfigUpload, FileUpload, CadevilDocument, BuildingMetrics
from apps.shared.ifc_extractor.material_assessment import load_reference, number
from .forms import UploadForm, ConfigUploadForm, CalculationConfigForm
from .passport_views import _enabled


def bim_page(view):
    @login_required(login_url='/mycelium/login')
    @vary_on_headers('HX-Request')
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        _enabled(request)
        return view(request, *args, **kwargs)
    return wrapped


from apps.shared.page_views import render_page as page


@bim_page
@require_http_methods(['GET', 'POST'])
def model_manager(request):
    form = UploadForm(request.POST or None, request.FILES or None, user=request.user)
    if request.method == 'POST' and form.is_valid():
        upload = form.save(commit=False)
        upload.user = request.user
        upload.save()
        return redirect('bim:model_manager')
    return page(request, 'bim/models.html', {
        'title': 'BIM model manager', 'form': form,
        'files': FileUpload.objects.filter(user=request.user).order_by('-uploaded_at'),
        'documents': CadevilDocument.objects.filter(user=request.user).select_related('upload').order_by('-pk'),
    }, status=400 if request.method == 'POST' and form.errors else 200)


def read_upload(upload):
    # Parse a temporary copy: storage need not expose a local filesystem path.
    with tempfile.TemporaryDirectory(prefix='cadevil-reference-') as folder:
        path = Path(folder) / Path(upload.name).name
        with path.open('wb') as output:
            for chunk in upload.chunks():
                output.write(chunk)
        upload.seek(0)
        return load_reference(path)


def configuration(data):
    return {'header': list(dict.fromkeys(k for row in data.values() for k in row)), 'data': data}


@bim_page
@require_http_methods(['GET', 'POST'])
def library(request):
    form = ConfigUploadForm(request.POST or None, request.FILES or None, user=request.user)
    if request.method == 'POST' and form.is_valid():
        try:
            data = read_upload(form.cleaned_data['document'])
            if not data:
                raise ValueError('Reference table has no material records.')
        except (ValueError, OSError, StopIteration, IndexError, KeyError, BadZipFile) as error:
            form.add_error('document', str(error) or 'Reference table is empty or invalid.')
        else:
            stored = None
            try:
                with transaction.atomic():
                    upload = form.save(commit=False)
                    upload.user = request.user
                    upload.save()
                    stored = upload.document
                    CalculationConfig.objects.update_or_create(user=request.user,
                        defaults={'upload': upload, 'config': configuration(data)})
            except Exception:
                if stored:
                    stored.delete(save=False)
                raise
            return redirect('bim:config_editor')
    return page(request, 'bim/library.html', {
        'title': 'Reference configurations', 'form': form,
        'uploads': ConfigUpload.objects.filter(user=request.user).order_by('-uploaded_at'),
        'selection_form': CalculationConfigForm(user=request.user),
        'active': CalculationConfig.objects.filter(user=request.user).first(),
    }, status=400 if request.method == 'POST' and form.errors else 200)


@bim_page
@require_POST
def select_config(request):
    form = CalculationConfigForm(request.POST, user=request.user)
    if not form.is_valid():
        return HttpResponse('Choose one of your reference files.', status=400)
    upload = form.cleaned_data['upload']
    try:
        with upload.document.open('rb') as source:
            data = read_upload(source)
    except (ValueError, OSError, StopIteration, IndexError, KeyError, BadZipFile) as error:
        return HttpResponse(str(error) or 'Reference table is invalid.', status=400)
    CalculationConfig.objects.update_or_create(user=request.user,
        defaults={'upload': upload, 'config': configuration(data)})
    return redirect('bim:config_editor')


def editor_rows(config, posted=None):
    result = []
    for i, (material, row) in enumerate(config['data'].items()):
        cells = []
        for j, header in enumerate(config['header']):
            field = f'cell_{i}_{j}'
            value = posted.get(field, '') if posted is not None else row.get(header)
            cells.append({'field': field, 'value': '' if value is None else value})
        result.append({'material': material, 'cells': cells})
    return result


def csv_content(config, inert=False):
    stream = io.StringIO(newline='')
    writer = csv.writer(stream, delimiter=';')
    def value(v):
        # Numbers, including negative environmental values, remain numeric.
        if inert and isinstance(v, str) and number(v) is None and v.startswith(('=', '+', '-', '@')):
            return "'" + v
        return '' if v is None else v
    writer.writerow(['Material'] + [value(h) for h in config['header']])
    for material, row in config['data'].items():
        writer.writerow([value(material)] + [value(row.get(h)) for h in config['header']])
    return stream.getvalue()


@bim_page
@require_http_methods(['GET', 'POST'])
def config_editor(request):
    active = CalculationConfig.objects.filter(user=request.user).select_related('upload').first()
    if active is None:
        return page(request, 'bim/editor.html', {'title': 'Configuration editor', 'active': None})
    config = active.config
    errors = []
    if request.method == 'POST':
        # Reject stale submissions after a selection change or another save.
        if request.POST.get('source') != str(active.upload_id):
            errors.append('The active reference changed. Reload this page before saving.')
        rows = editor_rows(config, request.POST)
        if any(cell['field'] not in request.POST for row in rows for cell in row['cells']):
            errors.append('The submitted table is incomplete. Reload and try again.')
        if not errors:
            edited = {'header': config['header'], 'data': {
                row['material']: dict(zip(config['header'], (cell['value'] for cell in row['cells'])))
                for row in rows}}
            # Use the same importer as the assessment and save a new input version.
            with tempfile.TemporaryDirectory(prefix='cadevil-edit-') as folder:
                path = Path(folder) / 'edited-reference.csv'
                path.write_text(csv_content(edited), encoding='utf-8')
                try:
                    data = load_reference(path)
                except ValueError as error:
                    errors.append(str(error))
            if not errors:
                stored = None
                try:
                    with transaction.atomic():
                        locked = CalculationConfig.objects.select_for_update().get(pk=active.pk, user=request.user)
                        if locked.upload_id != active.upload_id:
                            return HttpResponse('The active reference changed. Reload before saving.', status=409)
                        upload = ConfigUpload(user=request.user, description=f'Edited {active.upload.description}'[:255])
                        upload.document.save('edited-reference.csv', ContentFile(csv_content(configuration(data)).encode('utf-8')))
                        stored = upload.document
                        locked.upload = upload
                        locked.config = {**config, **configuration(data)}
                        locked.save(update_fields=['upload', 'config'])
                except Exception:
                    if stored:
                        stored.delete(save=False)
                    raise
                return redirect('bim:config_editor')
    return page(request, 'bim/editor.html', {'title': 'Configuration editor', 'active': active,
        'headers': config['header'], 'rows': editor_rows(config, request.POST if errors else None), 'errors': errors},
        status=409 if errors and request.POST.get('source') != str(active.upload_id) else (400 if errors else 200))


@bim_page
@require_http_methods(['GET'])
def download_config(request):
    active = get_object_or_404(CalculationConfig, user=request.user)
    response = HttpResponse(csv_content(active.config, inert=True), content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="reference-configuration.csv"'
    return response


@bim_page
@require_http_methods(['GET'])
def download_model(request, pk):
    upload = get_object_or_404(FileUpload, pk=pk, user=request.user)
    return FileResponse(upload.document.open('rb'), as_attachment=True, filename=Path(upload.document.name).name)


@bim_page
@require_POST
def delete_model(request, pk):
    upload = get_object_or_404(FileUpload, pk=pk, user=request.user)
    if upload.user_file_upload.exists():
        return HttpResponse('This model has saved assessments. Keep it for report provenance.', status=409)
    file = upload.document
    upload.delete()
    file.delete(save=False)
    return redirect('bim:model_manager')


@bim_page
@require_http_methods(['GET'])
def viewer(request, pk):
    upload = get_object_or_404(FileUpload, pk=pk, user=request.user)
    assessment, report, options = viewer_assessment_selection(request, upload)
    from django.urls import reverse
    metadata_url = reverse('bim:viewer_materials', args=[upload.pk])
    if assessment['id']:
        metadata_url += '?assessment=' + assessment['id']
    return page(request, 'bim/viewer.html', {'title': '3D model viewer',
                'document': upload, 'upload_id': str(upload.pk), 'selected_element':request.GET.get('element',''),
                'metadata_url': metadata_url, 'assessment_options': options,
                'selected_assessment': assessment['id'], 'assessment_status': assessment['status'],
                'assessment_notice': assessment['notice']})


def viewer_assessment_selection(request, upload):
    """Choose only an explicit owned report or the sole unambiguous report.

    Document and metrics IDs are UUIDs, so ordering them does not identify the
    newest calculation. Multiple reports require the visitor's selection.
    """
    # Full reports can contain tens of megabytes of inventory. List IDs first,
    # then load only the chosen report; UUID order is display order only.
    metrics = list(BuildingMetrics.objects.filter(project__user=request.user,
        project__upload=upload).exclude(assessment_report={})
        .values('pk', 'project_id', 'project__description').order_by('project__description', 'project_id'))
    reports = {}
    duplicate_documents = set()
    for metric in metrics:
        identifier = str(metric['project_id'])
        if identifier in reports:
            duplicate_documents.add(identifier)
        reports[identifier] = metric
    for identifier in duplicate_documents:
        reports.pop(identifier)
    options = [{'id': identifier, 'label': f'{metric["project__description"] or "Assessment"} · {identifier[:8]}'}
               for identifier, metric in reports.items()]
    requested = request.GET.get('assessment', '')
    if requested:
        try:
            requested = str(UUID(requested))
        except (ValueError, TypeError, AttributeError):
            raise Http404('Invalid assessment identifier.')
        # Resolve ownership even if the report is unavailable or duplicated.
        get_object_or_404(CadevilDocument, pk=requested, user=request.user, upload=upload)
        metric = reports.get(requested)
        if metric is None:
            return {'status': 'unavailable', 'id': requested, 'label': 'Assessment',
                    'notice': 'This assessment has no unambiguous saved material report.'}, None, options
    elif len(reports) == 1 and not duplicate_documents:
        requested, metric = next(iter(reports.items()))
    else:
        status = 'choose' if reports or duplicate_documents else 'none'
        notice = ('Choose a saved assessment to inspect its material calculations.' if status == 'choose'
                  else 'IFC properties are available. Calculate a material passport to add quantities, impacts and costs.')
        return {'status': status, 'id': '', 'label': '', 'notice': notice}, None, options
    metric = BuildingMetrics.objects.select_related('project').get(pk=metric['pk'],
        project__user=request.user, project__upload=upload)
    report = metric.assessment_report
    if not isinstance(report, dict) or not report:
        return {'status': 'unavailable', 'id': requested, 'label': metric.project.description or 'Assessment',
                'notice': 'This assessment has no readable saved material report.'}, None, options
    return {'status': 'selected', 'id': requested, 'label': metric.project.description or 'Assessment',
            'complete': bool(report.get('complete')), 'options': report.get('options', {}),
            'notice': 'Values come from the selected saved assessment; incomplete quantities remain unavailable.',
            'provenance': {key: value for key, value in report.get('provenance', {}).items()
                           if key in ('ifc_sha256', 'reference_filename', 'reference_file_sha256', 'configuration_sha256')}}, report, options


@bim_page
@require_http_methods(['GET'])
def viewer_materials(request, pk):
    from django.conf import settings
    from apps.shared.viewer_materials import source_materials, attach_assessment, IFC_GUID
    from ifcopenshell import Error as IfcError
    upload = get_object_or_404(FileUpload, pk=pk, user=request.user)
    assessment, report, options = viewer_assessment_selection(request, upload)
    element_id = request.GET.get('element', '')
    if element_id and not IFC_GUID.fullmatch(element_id):
        raise Http404('Invalid IFC element identifier.')
    try:
        data = source_materials(upload.document.path, Path(settings.MEDIA_ROOT) / 'bim-viewer-cache',
                                element_id=element_id or None)
    except (ValueError, RuntimeError, OSError, IfcError):
        return JsonResponse({'error': 'The IFC material properties could not be read.',
                             'elements': {}, 'assessment': assessment, 'assessment_options': options}, status=422)
    if element_id and element_id not in data['elements']:
        raise Http404('The IFC element is not part of this model.')
    if report:
        recorded_hash = report.get('provenance', {}).get('ifc_sha256')
        if not recorded_hash:
            assessment.update(status='unverified', notice='This saved assessment has no source fingerprint. IFC properties remain available; calculate a new passport to verify material values.')
        elif recorded_hash != data['source']['sha256']:
            assessment.update(status='source_mismatch', notice='The IFC source differs from the selected assessment. Recalculate the passport to show matching material values.')
        elif element_id:
            data = attach_assessment(data, report)
    data.update(assessment=assessment, assessment_options=options)
    response = JsonResponse(data)
    response['Cache-Control'] = 'private, no-store'
    return response


@bim_page
@require_http_methods(['GET'])
def stream_model(request, pk):
    from django.conf import settings
    from apps.shared.ifc_viewer import model_glb
    from ifcopenshell import Error as IfcError
    upload = get_object_or_404(FileUpload, pk=pk, user=request.user)
    try:
        path = model_glb(upload.document.path, Path(settings.MEDIA_ROOT) / 'bim-viewer-cache')
    except (ValueError, RuntimeError, OSError, IfcError):
        return HttpResponse('The IFC could not be converted to renderable geometry.', status=422)
    response = FileResponse(path.open('rb'), content_type='model/gltf-binary',
                            filename=Path(upload.document.name).stem + '.glb')
    response['Content-Length'] = str(path.stat().st_size)
    response['Cache-Control'] = 'private, no-store'
    return response
