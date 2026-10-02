# -*- coding: utf-8 -*-
import math
import mimetypes
import multiprocessing
import os
import pprint
import re
import tempfile
import time
from pathlib import Path
from uuid import UUID

from asgiref.sync import sync_to_async
from django.conf import settings
from django.contrib.auth.decorators import login_required
# from django.core.paginator import Paginator
from django.urls import reverse
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect, Http404, FileResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.utils import timezone
from django.utils.encoding import smart_str
from django.views.decorators.vary import vary_on_headers
from django.views.decorators.http import require_GET, require_POST
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

import ifcopenshell
import ifcopenshell.geom
import webapp.logger
from ifc_extractor import chart_plotter, energy, helpers
from model_manager.forms import DocumentForm, GroupChangeForm, UploadForm, GroupForm, ConfigUploadForm, \
    CalculationConfigForm, EpwUploadForm
from model_manager.models import (
    CadevilDocument, EpwUpload,
    FileUpload, CalculationConfig, ConfigUpload, BuildingMetrics, MaterialProperties,
)
from model_manager.serializers import CadevilDocumentSerializer, CalculationConfigSerializer, ConfigUploadSerializer, \
    FileUploadSerializer, BuildingMetricserializer, MaterialPropertiesSerializer
from webapp.settings import USER_LOGS


# TODO: Consider putting this on all methods to prevent django from processing PUT UPDATE or DELETE
# @require_http_methods(["GET", "POST"])

# TODO: Add require_POST and require_GET to all functions
def index(request: HttpRequest) -> TemplateResponse:
    if request.user.is_authenticated:
        # Convert to a string (or build HTML)
        user_log_entries = USER_LOGS[str(request.user.id)]
        log_output = "\n".join(user_log_entries)
        files = FileUpload.objects.filter(user=request.user)
        epw_files = EpwUpload.objects.filter(user=request.user)

        return TemplateResponse(
            request,
            "index.jinja2",
            {
                "files": files, "epw_files": epw_files,
                "initial_logs": f"{log_output}\n"},  # pass the logs
        )
    else:
        return TemplateResponse(
            request,
            "index.jinja2",
            {
            },
        )


@login_required(login_url="/accounts/login/")
@vary_on_headers("HX-Request")
def config_editor(request: HttpRequest) -> HttpResponseRedirect | TemplateResponse:
    config_dict = get_object_or_404(CalculationConfig, user=request.user)
    log_output = "\n".join(USER_LOGS[str(request.user.id)])
    pprint.pprint(config_dict)
    if request.headers.get("HX-Request"):
        # For HTMX requests, return an HTML fragment or success message
        pprint.pprint(config_dict.config)
        return TemplateResponse(
            request=request,
            template="webapp/config_editor.jinja2",
            context={
                'data_dict': config_dict.config["data"],
                'headers': config_dict.config["header"],
                "initial_logs": f"{log_output}\n",
            })
    else:
        return redirect("/")


@login_required(login_url="/accounts/login/")
@require_POST
def change_group(request: HttpRequest) -> HttpResponseRedirect:
    request_id = str(request.POST.get("change_group"))
    user_id: str = str(request.user.id)

    logger = webapp.logger.InMemoryLogHandler()  # root logger, or a named one

    group_choice_id = int(request.POST.get("group_field"))
    group_form = GroupChangeForm(
        user_groups=request.user.groups.all()
    )

    logger.sync_emit(f"ID: {request_id}", user_id=user_id)
    group = group_form.fields["group_field"].choices[group_choice_id]
    # Only the document's owner (or a superuser) may change its group -
    # same ownership rule as _get_document_for_user, used elsewhere in
    # this file to authorize access/mutation of a CadevilDocument.
    document_object: CadevilDocument = _get_document_for_user(request, request_id)
    logger.sync_emit(f"FROM ID: {document_object.group}", user_id=user_id)
    document_object.group_id = group[0]
    document_object.group = group[1]
    logger.sync_emit(f"TO ID: {document_object.group}", user_id=user_id)
    document_object.save()

    return redirect("/model_manager/")


@login_required(login_url="/accounts/login")
def create_group(request: HttpRequest) -> HttpResponseRedirect:
    # config_file = await CalculationConfig.objects.filter(id=request_id, user=request.user).aget()

    if request.method == 'POST':
        group_form = GroupForm(request.POST)
        if group_form.is_valid():
            group_form.save()  # saves the new Group to the database
            return redirect('/accounts/user/')  # Replace with your success URL


@login_required(login_url="/accounts/login/")
@require_POST
async def delete_config_file(request: HttpRequest) -> HttpResponseRedirect:
    _ = await sync_to_async(lambda: request.user.is_authenticated)()
    request_id = str(request.POST.get("delete_file"))

    user_id: str = str(request.user.id)

    logger = webapp.logger.InMemoryLogHandler()  # root logger, or a named one

    # Only the owner may delete their own upload; 404 otherwise.
    the_upload = await sync_to_async(get_object_or_404)(
        ConfigUpload, id=request_id, user=request.user
    )

    # Delete via the field's storage API (absolute path resolution handled
    # internally) instead of os.chdir()+os.remove(), which mutated the
    # process-wide working directory.
    await sync_to_async(the_upload.document.delete)(save=False)
    await logger.emit(f"{the_upload.document.name} removed", user_id=user_id)

    # TODO: Use result to send notification after success
    await the_upload.adelete()
    return redirect("/model_manager/")


@login_required(login_url="/accounts/login/")
@require_POST
async def delete_model_file(request: HttpRequest) -> HttpResponseRedirect:
    _ = await sync_to_async(lambda: request.user.is_authenticated)()
    request_id = str(request.POST.get("delete_file"))

    user_id: str = str(request.user.id)

    logger = webapp.logger.InMemoryLogHandler()  # root logger, or a named one

    # Only the owner may delete their own upload; 404 otherwise.
    the_upload = await sync_to_async(get_object_or_404)(
        FileUpload, id=request_id, user=request.user
    )

    # Delete via the field's storage API (absolute path resolution handled
    # internally) instead of os.chdir()+os.remove(), which mutated the
    # process-wide working directory.
    await sync_to_async(the_upload.document.delete)(save=False)
    await logger.emit(f"{the_upload.document.name} removed", user_id=user_id)

    # TODO: Use result to send notification after success
    await the_upload.adelete()
    return redirect("/model_manager/")


def _sanitize_filename_component(value: str) -> str:
    """
    Strip path separators and control characters from a free-text value
    (e.g. a user-supplied ``description``) so it can be safely used as
    part of a download filename.
    """
    value = str(value or "")
    value = re.sub(r'[\\/\x00-\x1f\x7f]', "", value)
    value = value.strip().strip(".")
    return value or "config"


@login_required(login_url="/accounts/login/")
def download_csv(request) -> HttpResponse:
    user_id: str = str(request.user.id)
    logger = webapp.logger.InMemoryLogHandler()  # root logger, or a named one
    config_dict = get_object_or_404(CalculationConfig, user=request.user)
    file_name = f"{_sanitize_filename_component(config_dict.upload.description)}.csv"

    if request.method == "POST":
        logger.sync_emit(f"Sending Config File for Download (ID: {config_dict.id})", user_id=user_id)
        response = HttpResponse(helpers.dict_to_file_string(nested_dict=config_dict.config, filename=file_name),
                                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        response['Content-Disposition'] = f"attachment; filename='{file_name}'"
        return response


@login_required(login_url="/accounts/login/")
@vary_on_headers("HX-Request")
def model_manager(
        request: HttpRequest,
) -> TemplateResponse | HttpResponseRedirect:
    # print(
    #     f"Current user {request.user} has this many running calculations {request.user.active_calculations}/{request.user.max_calculations}"
    # )

    document_form = DocumentForm(user=request.user, user_id=request.user.id)
    group_form = GroupChangeForm(
        user_groups=request.user.groups.all()
    )
    user_log_entries = USER_LOGS[str(request.user.id)]
    log_output = "\n".join(user_log_entries)
    if request.method == "POST":
        upload_form = UploadForm(
            request.POST, request.FILES, user=request.user, user_id=request.user.id
        )
        # Wrap form validation
        if upload_form.is_valid():
            # Save the form asynchronously
            file_upload: FileUpload = upload_form.save(commit=False)
            file_upload.user = request.user
            file_upload.save()
            return redirect("/model_manager/")
            # return HttpResponse(status=204)

    else:
        # Wrap ORM queries
        files = FileUpload.objects.filter(user=request.user)
        epw_files = EpwUpload.objects.filter(user=request.user)
        # ifc_plot_svg = helpers.create_plan_svg_bboxes(ifc_path=files[0].document.path)

        data = CadevilDocument.objects.all()

        if request.headers.get("HX-Request"):
            # For HTMX requests, return an HTML fragment or success message

            return TemplateResponse(
                request=request,
                template="webapp/model_manager.jinja2",
                context={
                    "files": files, "epw_files": epw_files,
                    "data": data,
                    "document_form": document_form,
                    "group_form": group_form,
                    # "ifc_plot_svg": ifc_plot_svg,
                    "initial_logs": f"{log_output}\n",  # pass the logs
                },
            )
        else:
            return redirect("/")


@login_required(login_url="/accounts/login/")
async def save_config(request: HttpRequest) -> HttpResponse:
    # config_file = await CalculationConfig.objects.filter(id=request_id, user=request.user).aget()
    _ = await sync_to_async(lambda: request.user.is_authenticated)()
    user_id: str = str(request.user.id)

    logger = webapp.logger.InMemoryLogHandler()  # root logger, or a named one

    if request.method == "POST":
        config_save_form = CalculationConfigForm(request.POST, user=request.user)

        if await sync_to_async(config_save_form.is_valid)():
            upload = config_save_form.cleaned_data["upload"]
            config_data = await sync_to_async(helpers.file_to_dict)(
                filepath=upload.document.path
            )
            calculation_config, _ = await CalculationConfig.objects.aupdate_or_create(
                user=request.user,
                defaults={"upload": upload, "config": config_data},
            )
            await logger.emit(
                f"Saving Config (ID: {calculation_config.id})", user_id=user_id
            )
        return HttpResponse(status=204)


@login_required(login_url="/accounts/login/")
@vary_on_headers("HX-Request")
async def user(request: HttpRequest) -> HttpResponseRedirect | TemplateResponse:
    # config_file = await CalculationConfig.objects.filter(id=request_id, user=request.user).aget()
    _ = await sync_to_async(lambda: request.user.is_authenticated)()

    user_log_entries = USER_LOGS[str(request.user.id)]
    log_output = "\n".join(user_log_entries)

    config_uploads = await sync_to_async(
        lambda: list(ConfigUpload.objects.filter(user=request.user)),
        thread_sensitive=True,
    )()
    epw_uploads = await sync_to_async(
        lambda: list(EpwUpload.objects.filter(user=request.user)),
        thread_sensitive=True,
    )()
    user_groups = await sync_to_async(
        lambda: list(request.user.groups.all()),
        thread_sensitive=True,
    )()

    config_upload_form = ConfigUploadForm()
    config_save_form = CalculationConfigForm(user=request.user)
    epw_upload_form = EpwUploadForm()
    if request.headers.get("HX-Request"):

        return TemplateResponse(
            request,
            "registration/user.jinja2",
            context={
                "config_uploads": config_uploads,
                "config_upload_form": config_upload_form,
                "config_save_form": config_save_form,
                "epw_uploads": epw_uploads,
                "epw_upload_form": epw_upload_form,
                "user_groups": user_groups,
                "initial_logs": f"{log_output}\n",
            },
        )
    else:
        return redirect("/")


def _epw_library_response(
        request: HttpRequest,
        form: EpwUploadForm | None = None,
        status_code: int = 200,
) -> TemplateResponse:
    return TemplateResponse(
        request,
        "registration/templatetags/_epw_library.jinja2",
        {
            "epw_upload_form": form or EpwUploadForm(),
            "epw_uploads": EpwUpload.objects.filter(user=request.user),
        },
        status=status_code,
    )


@login_required(login_url="/accounts/login/")
@require_POST
def upload_epw(request: HttpRequest) -> TemplateResponse:
    form = EpwUploadForm(request.POST, request.FILES)
    if not form.is_valid():
        return _epw_library_response(request, form=form, status_code=400)

    epw_upload = form.save(commit=False)
    epw_upload.user = request.user
    epw_upload.save()
    return _epw_library_response(request)


@login_required(login_url="/accounts/login/")
@require_POST
def delete_epw(request: HttpRequest, pk) -> TemplateResponse:
    epw_upload = get_object_or_404(EpwUpload, pk=pk, user=request.user)
    epw_upload.document.delete(save=False)
    epw_upload.delete()
    return _epw_library_response(request)


@login_required(login_url="/accounts/login/")
@vary_on_headers("HX-Request")
def object_view(
        request: HttpRequest,
) -> TemplateResponse | HttpResponseRedirect | None:
    """
    Detail view of a given CadevilDocument instance
    """
    user_log_entries = USER_LOGS[str(request.user.id)]
    log_output = "\n".join(user_log_entries)
    # Send user to model manager page if:
    #   - user does not specify any models in the url
    #   - user does not specify a valid model id
    if request.GET.get("object"):
        document_id = request.GET.get("object")
    else:
        return redirect("/model_manager/")

    data = _get_document_for_user(request, document_id)
    materials = list(data.material_properties.order_by("name", "id"))
    building_metrics = data.building_metrics.order_by("id").first()

    building_plots = []
    if materials:
        building_plots.extend(
            (
                chart_plotter.plot_mass(ifc_document=data),
                chart_plotter.plot_material_waste_grades(ifc_document=data),
                chart_plotter.create_onorm_1800_visualization(ifc_document=data),
                chart_plotter.plot_material_costs(ifc_document=data),
            )
        )

    recycling_context, recycling_status = _recycling_simulation_context(
        data, request.GET.get("material_id"), materials=materials
    )
    # messages.info(request, "Test message!")
    if request.headers.get("HX-Request"):

        return TemplateResponse(
            request,
            "webapp/object_view.jinja2",
            context={
                "data": data,
                "building_metrics": building_metrics,
                "files": FileUpload.objects.filter(user=request.user),
                "epw_files": EpwUpload.objects.filter(user=request.user),
                "materials": materials,
                "html_plot": building_plots,
                "initial_logs": f"{log_output}\n",
                **recycling_context,
            },
            status=recycling_status,
        )
    else:
        return redirect("/")


def _get_document_for_user(request: HttpRequest, document_id) -> CadevilDocument:
    queryset = CadevilDocument.objects.select_related("user", "group", "upload")
    if not request.user.is_superuser:
        queryset = queryset.filter(user=request.user)
    return get_object_or_404(queryset, id=document_id)


def _default_recycling_material(
        materials: list[MaterialProperties],
) -> MaterialProperties | None:
    def sort_key(material: MaterialProperties) -> tuple[int, float, str]:
        try:
            mass = float(material.mass)
        except (TypeError, ValueError):
            mass = 0.0
        if math.isfinite(mass) and mass > 0:
            return 0, -mass, str(material.id)
        return 1, 0.0, str(material.id)

    return min(materials, key=sort_key) if materials else None


def _recycling_simulation_context(
        document: CadevilDocument,
        requested_material_id,
        *,
        materials: list[MaterialProperties] | None = None,
) -> tuple[dict, int]:
    materials = materials if materials is not None else list(
        document.material_properties.order_by("name", "id")
    )
    selected_material = None
    error = ""
    status_code = 200

    if requested_material_id:
        selected_material = next(
            (
                material
                for material in materials
                if str(material.id) == str(requested_material_id)
            ),
            None,
        )
        if selected_material is None:
            error = "Select a material from this model."
            status_code = 400
    else:
        selected_material = _default_recycling_material(materials)

    recycling_plot = ""
    cost_plot = ""
    if selected_material is not None:
        calculation_config = CalculationConfig.objects.filter(
            user=document.user
        ).first()
        config = calculation_config.config if calculation_config else {}
        config_data = config.get("data", {}) if isinstance(config, dict) else {}
        recycling_plot = chart_plotter.simulate_material_decay_plotly(
            ifc_document=document,
            config_dict=config_data,
            material_id=selected_material.id,
        )
        cost_plot = chart_plotter.simulate_material_cost_projection_plotly(
            ifc_document=document,
            config_dict=config_data,
            material_id=selected_material.id,
        )

    return {
        "materials": materials,
        "selected_material": selected_material,
        "recycling_plot": recycling_plot,
        "cost_plot": cost_plot,
        "recycling_error": error,
    }, status_code


@login_required(login_url="/accounts/login/")
@require_GET
def material_recycling_simulation(
        request: HttpRequest, pk
) -> TemplateResponse:
    document = _get_document_for_user(request, pk)
    context, status_code = _recycling_simulation_context(
        document, request.GET.get("material_id")
    )
    return TemplateResponse(
        request,
        "webapp/_material_recycling_simulation.jinja2",
        {"data": document, **context},
        status=status_code,
    )


@login_required(login_url="/accounts/login/")
def model_3d_view(request: HttpRequest) -> TemplateResponse | HttpResponseRedirect:
    """
    Full-page Three.js 3D viewer for a single CadevilDocument / FileUpload.

    Query param: ``?object=<CadevilDocument UUID>``
    """
    document_id = request.GET.get("object")
    if not document_id:
        return redirect("/model_manager/")

    # Reuse the same ownership/group check as other CadevilDocument views
    # in this file, instead of an unfiltered lookup that leaked other
    # users'/groups' document metadata.
    document = _get_document_for_user(request, document_id)

    return TemplateResponse(
        request,
        "webapp/model_3d_view.jinja2",
        {
            "document": document,
            "upload_id": str(document.upload.id),
        },
    )


def _sum_material_field(materials: list[MaterialProperties], field: str) -> float:
    """Sum a numeric MaterialProperties field across an already-fetched list.

    Non-finite values (``NaN``/``Infinity``) are ignored rather than being
    allowed to poison the running total, since a single bad value would
    otherwise make every derived statistic unusable.
    """
    total = 0.0
    for material in materials:
        value = getattr(material, field, 0) or 0
        try:
            value = float(value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(value):
            continue
        total += value
    return total


def _safe_ratio(numerator: float | None, denominator: float | None) -> float | None:
    """Return numerator / denominator, or None when unavailable.

    Unavailable covers: a missing numerator/denominator, a non-finite
    (``NaN``/``Infinity``) numerator or denominator, and a denominator that
    is not strictly positive (zero or negative).
    """
    if numerator is None or denominator is None:
        return None
    try:
        numerator = float(numerator)
        denominator = float(denominator)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(numerator) and math.isfinite(denominator)):
        return None
    if denominator <= 0:
        return None
    return numerator / denominator


# Comparison statistics computed per model, and how "best" is decided for
# highlighting purposes. All are derived exclusively from existing A1-A3
# fields (no B4/lifecycle values, no cost/currency figures).
COMPARISON_STAT_BEST_MODE = {
    "gwp_a1_a3_per_bgf": "min",
    "ap_a1_a3_per_bgf": "min",
    "penrt_a1_a3_per_bgf": "min",
    "recyclable_mass_share": "max",
    "waste_mass_share": "min",
}


def compute_comparison_stats(document: CadevilDocument) -> dict[str, float | None]:
    """
    Compute per-model comparison statistics for a single ``CadevilDocument``.

    Reads from the already prefetched ``building_metrics`` and
    ``material_properties`` relations (via ``list(...)``, never ``.first()``)
    so this can be called once per document without issuing additional
    queries.

    Documents with no BuildingMetrics, a zero/missing BGF, or no
    MaterialProperties at all yield ``None`` for the affected statistics,
    which the template renders as an em dash and never highlights as best.
    """
    building_metrics = list(document.building_metrics.all())
    bm = building_metrics[0] if building_metrics else None
    bgf = getattr(bm, "brutto_grundfläche", None) if bm else None

    materials = list(document.material_properties.all())
    has_materials = bool(materials)

    mass_total = _sum_material_field(materials, "mass") if has_materials else None
    gwp_total = _sum_material_field(materials, "gwp_ml_a1_a3") if has_materials else None
    ap_total = _sum_material_field(materials, "ap_ml_a1_a3") if has_materials else None
    penrt_total = _sum_material_field(materials, "penrt_ml_a1_a3") if has_materials else None
    recyclable_total = _sum_material_field(materials, "recyclable_mass") if has_materials else None
    waste_total = _sum_material_field(materials, "waste_mass") if has_materials else None

    recyclable_mass_share = _safe_ratio(recyclable_total, mass_total)
    waste_mass_share = _safe_ratio(waste_total, mass_total)

    return {
        "gwp_a1_a3_per_bgf": _safe_ratio(gwp_total, bgf),
        "ap_a1_a3_per_bgf": _safe_ratio(ap_total, bgf),
        "penrt_a1_a3_per_bgf": _safe_ratio(penrt_total, bgf),
        # Expressed as a percentage (0-100) of total material mass, matching
        # the "%" units shown alongside these figures in the template.
        "recyclable_mass_share": recyclable_mass_share * 100 if recyclable_mass_share is not None else None,
        "waste_mass_share": waste_mass_share * 100 if waste_mass_share is not None else None,
    }


def compute_best_comparison_stats(
        all_stats: list[dict[str, float | None]],
) -> dict[str, float | None]:
    """
    Determine the best (highlight-worthy) value per statistic across all
    documents' computed stats. Missing/unavailable (``None``) and
    non-finite (``NaN``/``Infinity``) values are excluded, so a document
    with no (or corrupted) data can never be picked as "best".
    """
    best: dict[str, float | None] = {}
    for field, mode in COMPARISON_STAT_BEST_MODE.items():
        values = [
            stats[field]
            for stats in all_stats
            if stats.get(field) is not None and math.isfinite(stats[field])
        ]
        if not values:
            best[field] = None
        else:
            best[field] = max(values) if mode == "max" else min(values)
    return best


@login_required(login_url="/accounts/login/")
@vary_on_headers("HX-Request")
def model_comparison(request: HttpRequest) -> TemplateResponse | HttpResponseRedirect:
    """
    Comparison view of the documents the requesting user is allowed to see.

    Non-superusers only ever see documents belonging to groups they are a
    member of; superusers may compare across all groups, consistent with
    ``CadevilGroupPermissionMixin``'s superuser bypass elsewhere in this app.
    """
    user_log_entries = USER_LOGS[str(request.user.id)]
    log_output = "\n".join(user_log_entries)


    if request.user.is_superuser:
        accessible_documents = CadevilDocument.objects.all()
    else:
        accessible_documents = CadevilDocument.objects.filter(
            group__in=request.user.groups.all()
        )

    # Sort available documents stably: description (nulls last by default in many DBs,
    # but we just care about consistency) then id.
    available_documents = list(
        accessible_documents
        .select_related("group")
        .order_by("description", "id")
    )

    selected_ids = request.GET.getlist("models")
    is_selection_active = request.GET.get("selection") == "1"

    if is_selection_active:
        valid_uuids = []
        for sid in selected_ids:
            try:
                valid_uuids.append(UUID(sid))
            except (ValueError, TypeError):
                continue
        data_qs = accessible_documents.filter(id__in=valid_uuids)
    else:
        data_qs = accessible_documents

    data = list(
        data_qs
        .select_related("group")
        .prefetch_related("building_metrics", "material_properties")
        .order_by("description", "id")
    )

    # For template checked state: if selection=1, use the submitted/filtered set;
    # if selection=0 (initial), everything is checked.
    current_selected_ids = [str(d.id) for d in data]
    if not is_selection_active:
        current_selected_ids = [str(d.id) for d in available_documents]


    for document in data:
        document.comparison_stats = compute_comparison_stats(document)

    best_stats = compute_best_comparison_stats([d.comparison_stats for d in data])

    building_plots = []

    if request.headers.get("HX-Request"):
        # For HTMX requests, return an HTML fragment or success message
        return TemplateResponse(request=request,
                                template="webapp/model_comparison.jinja2",
                                context={
                                    "data": data, "available_documents": available_documents, "selected_ids": current_selected_ids,
                                    "best_stats": best_stats,
                                    "html_plot": building_plots,
                                    "initial_logs": f"{log_output}\n",

                                })
    else:
        return redirect("/")


@login_required(login_url="/accounts/login/")
async def update_config(request: HttpRequest) -> HttpResponseRedirect | TemplateResponse:
    _ = await sync_to_async(lambda: request.user.is_authenticated)()
    user_id: str = str(request.user.id)
    logger = webapp.logger.InMemoryLogHandler()  # root logger, or a named one

    # Replace this with the path to your actual CSV
    config_dict = await CalculationConfig.objects.aget(user=request.user)
    headers = config_dict.config["header"]
    data_dict = config_dict.config["data"]

    if request.method == 'POST':
        # 1) Parse the submitted form data
        # 2) Update your data model or in-memory dictionary
        # 3) Potentially re-write your CSV or update your DB
        pprint.pprint(data_dict)
        for row_key in data_dict:
            for header in headers:
                # Construct the key from template
                input_name = f'{row_key}-{header}'
                # Grab the updated value from POST data
                new_value = request.POST.get(input_name)

                # Update in-memory data (or your database model)
                data_dict[row_key][header] = new_value

        config_dict.config["data"] = data_dict
        await config_dict.asave()
        await logger.emit(f"Updated Calculation Config (ID: {config_dict.id})", user_id=user_id)

        # Redirect to avoid re-submitting on page refresh
        return redirect('/config_editor/')


@login_required(login_url="/accounts/login/")
async def upload_config(request: HttpRequest) -> HttpResponse:
    _ = await sync_to_async(lambda: request.user.is_authenticated)()

    user_id: str = str(request.user.id)

    logger = webapp.logger.InMemoryLogHandler()  # root logger, or a named one

    if request.method == "POST":
        config_upload_form = ConfigUploadForm(request.POST, request.FILES)

        is_valid = await sync_to_async(config_upload_form.is_valid)()
        if is_valid:
            config_upload = await config_upload_form.asave(commit=False)
            config_upload.user = request.user
            await config_upload.asave()
            await logger.emit(f"Uploaded new Config File (ID: {config_upload.id})", user_id=user_id)

            # Redirect to step 2, passing the new upload's ID
            return HttpResponse(status=204)


@login_required(login_url="/accounts/login/")
async def upload_model(
        request: HttpRequest,
) -> TemplateResponse | HttpResponseRedirect | HttpResponse | None:
    # {{{
    # FIXME: This shit is currently needed to make this work
    _ = await sync_to_async(lambda: request.user.is_authenticated)()
    user_id: str = str(request.user.id)

    logger = webapp.logger.InMemoryLogHandler()  # root logger, or a named one

    if request.method == "POST":
        upload_form = UploadForm(
            request.POST, request.FILES, user=request.user, user_id=request.user.id
        )
        # Wrap form validation
        is_valid = await sync_to_async(upload_form.is_valid)()
        if is_valid:
            # Save the form asynchronously
            file_upload: FileUpload = await upload_form.asave(commit=False)
            file_upload.user = request.user
            await file_upload.asave()
            await logger.emit(f"Uploaded new Model File (ID: {file_upload.id})", user_id=user_id)

            return redirect("/model_manager/")
            # return HttpResponse(status=204)
        else:
            await logger.emit("Failed to Upload new Model File!", user_id=user_id)

            return redirect("/accounts/user/")

# @login_required(login_url="/accounts/login/")
def ifc_to_glb_path(ifc_path: str) -> str:
    """
    Convert an IFC file on disk to a binary glTF (.glb) file and return its path.

    The GLB is written next to the source IFC file as ``<stem>.glb``.  If that
    cached file already exists it is returned immediately, skipping conversion
    entirely.  On the first call tessellation is parallelised across all
    available CPU cores via ``ifcopenshell.geom.iterator``'s built-in thread
    pool.

    Note: ``ifcopenshell.geom.serializers.gltf`` requires a real filesystem
    path and buffers the entire GLB in memory until ``finalize()`` is called,
    so true mid-conversion partial streaming is not possible.

    Args:
        ifc_path: Absolute filesystem path to the source .ifc file.

    Returns:
        Absolute filesystem path to the resulting .glb file.
    """
    glb_path = str(Path(ifc_path).with_suffix(".glb"))

    if os.path.exists(glb_path):
        return glb_path

    ifc_file = ifcopenshell.open(ifc_path)

    geom_settings = ifcopenshell.geom.settings()
    geom_settings.set(geom_settings.USE_WORLD_COORDS, True)
    geom_settings.set(geom_settings.WELD_VERTICES, True)

    # Source - https://stackoverflow.com/a/79474078
    # Posted by Jonas Frei
    # Retrieved 2026-06-21, License - CC BY-SA 4.0
    serializer_settings = ifcopenshell.geom.serializer_settings()

    # Write to a temp file first so a failed conversion never leaves a
    # partial .glb at the final cache path.
    with tempfile.NamedTemporaryFile(
        suffix=".glb", dir=str(Path(ifc_path).parent), delete=False
    ) as tmp:
        tmp_path = tmp.name

    try:
        serializer = ifcopenshell.geom.serializers.gltf(
            filename=tmp_path, geometry_settings=geom_settings, settings=serializer_settings
        )

        serializer.setFile(ifc_file)
        serializer.writeHeader()

        num_threads = multiprocessing.cpu_count()
        iterator = ifcopenshell.geom.iterator(
            geom_settings, ifc_file, num_threads, include=None
        )
        if iterator.initialize():
            while True:
                shape = iterator.get()
                serializer.write(shape)
                if not iterator.next():
                    break

        serializer.finalize()
        os.replace(tmp_path, glb_path)
        return glb_path
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def _clear_energy_values(building_metrics: BuildingMetrics) -> None:
    building_metrics.annual_site_energy_kwh = None
    building_metrics.annual_electricity_kwh = None
    building_metrics.annual_natural_gas_kwh = None
    building_metrics.energy_use_intensity_kwh_m2_year = None
    building_metrics.openstudio_version = ""


def _apply_energy_result(
        building_metrics: BuildingMetrics,
        result: energy.EnergySimulationResult,
        epw_upload: EpwUpload,
) -> None:
    building_metrics.energy_status = "success"
    building_metrics.annual_site_energy_kwh = result.annual_total_site_energy_kwh
    building_metrics.annual_electricity_kwh = result.annual_electricity_kwh
    building_metrics.annual_natural_gas_kwh = result.annual_natural_gas_kwh
    building_metrics.energy_use_intensity_kwh_m2_year = (
        result.energy_use_intensity_kwh_per_m2
    )
    building_metrics.energy_error = ""
    building_metrics.energy_assumptions = result.assumptions_summary
    building_metrics.energy_weather_file = result.weather_file_name
    building_metrics.energy_weather_upload = epw_upload
    building_metrics.openstudio_version = result.energyplus_version
    building_metrics.energy_simulated_at = timezone.now()


def _apply_energy_failure(
        building_metrics: BuildingMetrics,
        error: energy.EnergySimulationError,
        epw_upload: EpwUpload,
) -> None:
    _clear_energy_values(building_metrics)
    building_metrics.energy_status = "failed"
    building_metrics.energy_error = str(error)[:4000]
    building_metrics.energy_assumptions = (
        energy.EnergySimulationAssumptions().summary()
    )
    building_metrics.energy_weather_file = Path(epw_upload.document.name).name
    building_metrics.energy_weather_upload = epw_upload
    building_metrics.energy_simulated_at = timezone.now()


class BuildingMetricsViewSet(viewsets.ModelViewSet):
    queryset = BuildingMetrics.objects.all()
    serializer_class = BuildingMetricserializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        """
        Scope to metrics of documents owned by the requesting user (or all,
        for a superuser), same ownership rule as CadevilDocumentViewSet -
        BuildingMetrics has no direct 'user' field, only via 'project'.
        """
        queryset = super().get_queryset()
        if self.request.user.is_superuser:
            return queryset
        return queryset.filter(project__user=self.request.user)


class CalculationConfigViewSet(viewsets.ModelViewSet):
    queryset = CalculationConfig.objects.all()
    serializer_class = CalculationConfigSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        """
        Scope to the requesting user's own config (or all, for a superuser) -
        without this, any authenticated user could read/update/delete another
        user's CalculationConfig via id, the same IDOR fixed in config_editor
        and update_config, just reachable through this REST endpoint instead.
        """
        queryset = super().get_queryset()
        if self.request.user.is_superuser:
            return queryset
        return queryset.filter(user=self.request.user)

    def perform_create(self, serializer) -> None:
        """
        Automatically associate the created config with the authenticated
        user, mirroring FileUploadViewSet.perform_create, so a client can't
        assign/reassign a config to another user's account via the API.
        """
        serializer.save(user=self.request.user)


class CadevilDocumentViewSet(viewsets.ModelViewSet):
    queryset = CadevilDocument.objects.all()

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.request.user.is_superuser:
            return queryset
        return queryset.filter(user=self.request.user)

    def perform_create(self, serializer) -> None:
        """
        Automatically associate the created document with the authenticated
        user, mirroring FileUploadViewSet.perform_create, so a client can't
        assign/reassign a document to another user's account via the API.
        """
        serializer.save(user=self.request.user)

    @action(detail=True, methods=["post"], url_path="calculate_energy", url_name="calculate_energy")
    @vary_on_headers("HX-Request")
    def calculate_energy(self, request, pk=None):
        document = self.get_object()
        epw_file_id = request.data.get("epw_file_id")
        if not epw_file_id:
            return Response({"detail": "EPW file ID is required."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            epw_file_id = UUID(str(epw_file_id))
        except (AttributeError, TypeError, ValueError):
            return Response(
                {"detail": "EPW file ID must be a valid UUID."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        epw_upload = get_object_or_404(EpwUpload, pk=epw_file_id, user=request.user)
        building_metrics = document.building_metrics.order_by("id").first()
        if building_metrics is None:
            building_metrics = BuildingMetrics.objects.create(project=document)
        ifc_path = document.upload.document.path

        try:
            energy_result = energy.run_energy_simulation(
                ifc_path,
                epw_upload.document.path,
                cli_path=getattr(settings, "OPENSTUDIO_CLI_PATH", None),
                timeout_seconds=getattr(
                    settings,
                    "OPENSTUDIO_TIMEOUT_SECONDS",
                    energy.DEFAULT_TIMEOUT_SECONDS,
                ),
            )
        except energy.EnergySimulationError as exc:
            _apply_energy_failure(building_metrics, exc, epw_upload)
        else:
            _apply_energy_result(building_metrics, energy_result, epw_upload)

        building_metrics.save(
            update_fields=(
                "energy_status",
                "annual_site_energy_kwh",
                "annual_electricity_kwh",
                "annual_natural_gas_kwh",
                "energy_use_intensity_kwh_m2_year",
                "energy_error",
                "energy_assumptions",
                "energy_weather_file",
                "energy_weather_upload",
                "openstudio_version",
                "energy_simulated_at",
            )
        )

        if request.headers.get("HX-Request"):
            return TemplateResponse(
                request,
                "webapp/_energy_performance.jinja2",
                context={
                    "data": document,
                    "building_metrics": building_metrics,
                    "epw_files": EpwUpload.objects.filter(user=request.user),
                },
            )

        return redirect(reverse("object_view") + f"?object={document.id}")
    serializer_class = CadevilDocumentSerializer
    permission_classes = [IsAuthenticated]


class ConfigUploadViewSet(viewsets.ModelViewSet):
    queryset = ConfigUpload.objects.all()
    serializer_class = ConfigUploadSerializer
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def get_queryset(self):
        """
        Scope to the requesting user's own uploads (or all, for a superuser),
        same ownership rule as FileUploadViewSet - otherwise any authenticated
        user could read/replace/delete another user's uploaded config file via
        this REST endpoint (the same class of IDOR fixed in delete_config_file).
        """
        queryset = super().get_queryset()
        if self.request.user.is_superuser:
            return queryset
        return queryset.filter(user=self.request.user)


class FileUploadViewSet(viewsets.ModelViewSet):
    queryset = FileUpload.objects.all()
    serializer_class = FileUploadSerializer
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.request.user.is_superuser:
            return queryset
        return queryset.filter(user=self.request.user)

    @action(detail=True, methods=['post'], url_path='calculate_model', url_name='calculate_model')
    @vary_on_headers("HX-Request")
    def calculate_model(
            self,
            request: HttpRequest,
            pk=None,
    ) -> HttpResponse:
        user_id: str = str(request.user.id)
        start: float = time.time()
        logger = webapp.logger.InMemoryLogHandler()  # root logger, or a named one
        file = self.get_object()
        epw_upload = None
        epw_file_id = request.data.get("epw_file_id")
        if epw_file_id:
            try:
                epw_file_id = UUID(str(epw_file_id))
            except (AttributeError, TypeError, ValueError):
                return HttpResponse(
                    "EPW file ID must be a valid UUID.",
                    status=400,
                    content_type="text/plain",
                )
            epw_upload = get_object_or_404(
                EpwUpload, pk=epw_file_id, user=request.user
            )

        try:
            user_config = CalculationConfig.objects.get(user=request.user)
        except CalculationConfig.DoesNotExist:
            return HttpResponse(
                "Select a material configuration in your profile before calculating.",
                status=400,
                content_type="text/plain",
            )

        # TODO: Fork the rest of this to background
        # Process the IFC file asynchronously

        logger.sync_emit(f">>>>>> Starting Calculation of {file.description}!", user_id=user_id)
        # TODO: start this in different process
        ifc_path = file.document.path
        ifc_document = CadevilDocument()
        ifc_document.user = request.user
        ifc_document.description = file.description
        ifc_document.upload = file

        user_groups = request.user.groups.all()
        ifc_document.group = user_groups[0] if user_groups else None
        logger.sync_emit(f">>>>>> Initiating metrics calculations at {time.time() - start}s", user_id=user_id)

        material_properties, building_metrics = helpers.ifc_product_walk(
            user_id=user_id,
            user_config=user_config.config["data"],
            ifc_file_path=ifc_path,
        )

        if epw_upload is not None:
            try:
                energy_result = energy.run_energy_simulation(
                    ifc_path,
                    epw_upload.document.path,
                    cli_path=getattr(settings, "OPENSTUDIO_CLI_PATH", None),
                    timeout_seconds=getattr(
                        settings,
                        "OPENSTUDIO_TIMEOUT_SECONDS",
                        energy.DEFAULT_TIMEOUT_SECONDS,
                    ),
                )
            except energy.EnergySimulationError as exc:
                _apply_energy_failure(building_metrics, exc, epw_upload)
                logger.sync_emit(
                    f">>>>>> OpenStudio simulation failed: {exc}", user_id=user_id
                )
            else:
                _apply_energy_result(building_metrics, energy_result, epw_upload)

        building_metrics.project_id = ifc_document.id

        # Save the results asynchronously

        logger.sync_emit(f">>>>>> Saving Ifc Document {ifc_document.id}", user_id=user_id)
        ifc_document.save()

        logger.sync_emit(f">>>>>> Saving IFC Metrics {building_metrics.id}", user_id=user_id)
        building_metrics.save()

        for material_name in material_properties.keys():
            material_metrics = material_properties.get(material_name)
            if material_metrics:
                material_metrics.name = material_name
                material_metrics.project_id = ifc_document.id
                # logger.sync_emit(f">>>>>> Saving IFC Material Metrics {material_metrics.id}", user_id=user_id)
                material_metrics.save()
            else:
                logger.sync_emit(f">>??? {material_name} not in the dictionary", user_id=user_id)

        logger.sync_emit(f">>??? {material_properties.keys()}", user_id=str(request.user.id))
        logger.sync_emit(f">>>>>> Calculation done within {time.time() - start}s", user_id=str(request.user.id))
        start: float = time.time()

        logger.sync_emit(
            f">>>>>> Starting glTF conversion for {file.description} ({ifc_path})",
            user_id=user_id,
        )

        try:
            glb_path = ifc_to_glb_path(ifc_path)
        except Exception as exc:
            logger.sync_emit(
                f">>>>>> glTF conversion failed: {exc}",
                user_id=user_id,
            )
            return HttpResponse(
                f"glTF conversion failed: {exc}",
                status=500,
                content_type="text/plain",
            )

        glb_size = os.path.getsize(glb_path)
        logger.sync_emit(
            f">>>>>> Serving GLB for {file.description} ({glb_size} bytes) from {glb_path}",
            user_id=user_id,
        )
        logger.sync_emit(f">>>>>> Conversion done within {time.time() - start}s", user_id=str(request.user.id))

        return HttpResponse(status=204)

    @action(detail=True, methods=["get"], url_path="stream_gltf", url_name="stream_gltf")
    def stream_gltf(
            self,
            request: HttpRequest,
            pk=None,
    ) -> HttpResponse:
        """
        Convert the IFC file associated with this FileUpload record to binary
        glTF (.glb) and stream it back to the client.

        The IFC file path is resolved from the ``document`` FileField stored in
        the ``archicad_eval_uploads`` table.  Conversion is performed on-the-fly
        using IfcOpenShell's gltf serializer.

        URL: GET /api/model_file/<uuid:pk>/stream_gltf/

        Returns:
            An HTTP response with ``Content-Type: model/gltf-binary`` and the
            raw .glb bytes as the body.

        Raises:
            Http404: When the IFC file does not exist on disk.
            HttpResponse(500): When IfcOpenShell fails to convert the file.
        """
        user_id: str = str(request.user.id)
        logger = webapp.logger.InMemoryLogHandler()
        file = self.get_object()
        ifc_path: str = file.document.path
        # print(user_id, file.description, ifc_path)

        if not os.path.exists(ifc_path):
            raise Http404(f"IFC file not found on disk: {ifc_path}")

        logger.sync_emit(
            f">>>>>> Starting glTF conversion for {file.description} ({ifc_path})",
            user_id=user_id,
        )

        try:
            glb_path = ifc_to_glb_path(ifc_path)
        except Exception as exc:
            logger.sync_emit(
                f">>>>>> glTF conversion failed: {exc}",
                user_id=user_id,
            )
            return HttpResponse(
                f"glTF conversion failed: {exc}",
                status=500,
                content_type="text/plain",
            )

        stem = Path(ifc_path).stem
        glb_size = os.path.getsize(glb_path)
        logger.sync_emit(
            f">>>>>> Serving GLB for {file.description} ({glb_size} bytes) from {glb_path}",
            user_id=user_id,
        )

        # FileResponse streams the file in 8 KB chunks (chunked transfer
        # encoding) — the browser receives and can start rendering bytes
        # immediately rather than waiting for Django to buffer the whole file.
        response = FileResponse(
            open(glb_path, "rb"),
            content_type="model/gltf-binary",
            as_attachment=False,
            filename=f"{smart_str(stem)}.glb",
        )
        response["Content-Length"] = str(glb_size)
        return response

    def perform_create(self, serializer) -> None:
        """
        Automatically associate the uploaded file with the authenticated user.
        """
        serializer.save(user=self.request.user)

    def create(self, request, *args, **kwargs) -> Response:
        """
        Custom create method to handle file uploads and return appropriate response.
        """
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid():
            self.perform_create(serializer)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['get'], url_path='download_model', url_name='download_model')
    @vary_on_headers("HX-Request")
    def download_model(
            self,
            request: HttpRequest,
            pk=None,
    ) -> HttpResponseRedirect | FileResponse:
        file = self.get_object()
        relpath = file.document.path
        # if not default_storage.exists(relpath):
        #     raise Http404("Not found")

        # If your storage backend generates signed URLs (e.g., S3 via django-storages),
        # default_storage.url(relpath) will often return a time-limited URL. Redirect:
        # try:
        #     signed_url = default_storage.url(relpath)  # many backends sign this
        #     return HttpResponseRedirect(signed_url)
        # except Exception:
            # Fallback: stream through Django (not ideal for very large files)
            # f = default_storage.open(relpath, "rb")
        with open(relpath, "rb") as f:
            ft = f.read()
            ctype, _ = mimetypes.guess_type(relpath)
            filename = Path(relpath).name
            resp = FileResponse(ft, content_type=ctype or "application/octet-stream")
            resp["Content-Disposition"] = f'attachment; filename="{smart_str(filename)}"'
            return resp

class MaterialPropertiesViewSet(viewsets.ModelViewSet):
    queryset = MaterialProperties.objects.all()
    serializer_class = MaterialPropertiesSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        """
        Scope to material properties of documents owned by the requesting
        user (or all, for a superuser) - MaterialProperties has no direct
        'user' field, only via 'project'.
        """
        queryset = super().get_queryset()
        if self.request.user.is_superuser:
            return queryset
        return queryset.filter(project__user=self.request.user)