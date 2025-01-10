# -*- coding: utf-8 -*-
import asyncio
import os
import time

from asgiref.sync import sync_to_async
from django.contrib.auth.decorators import login_required
# from django.core.paginator import Paginator
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import redirect, render
from django.template.response import TemplateResponse
from django.views.decorators.vary import vary_on_headers
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

import webapp.logger
from ifc_extractor import chart_plotter, helpers
from model_manager.forms import DocumentForm, GroupChangeForm, UploadForm, GroupForm, ConfigUploadForm, \
    CalculationConfigForm
from model_manager.models import (
    CadevilDocument,
    FileUpload, CalculationConfig, ConfigUpload, BuildingMetrics, MaterialProperties,
)
from model_manager.serializers import CadevilDocumentSerializer, CalculationConfigSerializer, ConfigUploadSerializer, \
    FileUploadSerializer, BuildingMetricserializer, MaterialPropertiesSerializer
from webapp.settings import USER_LOGS


# TODO: Consider putting this on all methods to prevent django from processing PUT UPDATE or DELETE
# @require_http_methods(["GET", "POST"])

# TODO: Add require_POST and require_GET to all functions
def index(request: HttpRequest):
    # Convert to a string (or build HTML)
    user_log_entries = USER_LOGS[str(request.user.id)]
    log_output = "\n".join(user_log_entries)
    files = FileUpload.objects.filter(user=request.user)

    return TemplateResponse(
        request,
        "index.html",
        {
            "files": files,
            "initial_logs": f"{log_output}\n"},  # pass the logs
    )


@login_required(login_url="/accounts/login/")
@vary_on_headers("HX-Request")
def config_editor(request: HttpRequest) -> HttpResponseRedirect | TemplateResponse:
    config_dict = CalculationConfig.objects.get()
    user_log_entries = USER_LOGS[str(request.user.id)]
    log_output = "\n".join(user_log_entries)

    if request.headers.get("HX-Request"):
        # For HTMX requests, return an HTML fragment or success message
        return TemplateResponse(
            request=request,
            template="webapp/config_editor.html",
            context={
                'data_dict': config_dict.config["data"],
                'headers': config_dict.config["header"],
                "initial_logs": f"{log_output}\n",
            })
    else:
        return redirect("/")


@login_required(login_url="/accounts/login/")
async def change_group(request: HttpRequest) -> HttpResponseRedirect:
    _ = await sync_to_async(lambda: request.user.is_authenticated)()

    request_id = str(request.GET.get("set_group"))
    user_id: str = str(request.user.id)

    logger = webapp.logger.InMemoryLogHandler()  # root logger, or a named one
    await logger.emit(f"ID: {request_id}", user_id=user_id)

    group_choice_id = int(request.GET.get("group_field"))
    group_form = GroupChangeForm(
        user_groups=request.user.groups.all()
    )

    group = group_form.fields["group_field"].choices[group_choice_id]
    _ = CadevilDocument.objects.filter(id=request_id).aupdate(group=group[1])
    return redirect("/model_manager/")


@login_required(login_url="/accounts/login")
async def create_group(request: HttpRequest) -> HttpResponseRedirect:
    # config_file = await CalculationConfig.objects.filter(id=request_id, user=request.user).aget()
    _ = await sync_to_async(lambda: request.user.is_authenticated)()

    if request.method == 'POST':
        group_form = GroupForm(request.POST)
        if group_form.is_valid():
            await group_form.asave()  # saves the new Group to the database
            return redirect('/accounts/user/')  # Replace with your success URL


@login_required(login_url="/accounts/login/")
async def delete_config_file(request: HttpRequest) -> HttpResponseRedirect:
    _ = await sync_to_async(lambda: request.user.is_authenticated)()
    request_id = str(request.GET.get("delete_file"))

    user_id: str = str(request.user.id)

    logger = webapp.logger.InMemoryLogHandler()  # root logger, or a named one

    # Wrap file path retrieval
    _object = (
        await ConfigUpload.objects.filter(id=request_id)
        .values_list("document", flat=True)
        .aget()
    )
    os.chdir(os.path.join(os.getcwd(), "../user_uploads"))

    # Use asyncio to run file operations in a thread
    if await sync_to_async(os.path.exists)(_object):
        await sync_to_async(os.remove)(_object)
        await logger.emit(f"{_object} removed", user_id=user_id)
    else:
        await logger.emit(f"{_object} not found", user_id=user_id)

    # TODO: Use result to send notification after success
    await ConfigUpload.objects.filter(id=request_id).adelete()
    return redirect("/model_manager/")


@login_required(login_url="/accounts/login/")
async def delete_model_file(request: HttpRequest) -> HttpResponseRedirect:
    _ = await sync_to_async(lambda: request.user.is_authenticated)()
    request_id = str(request.GET.get("delete_file"))

    user_id: str = str(request.user.id)

    logger = webapp.logger.InMemoryLogHandler()  # root logger, or a named one

    # Wrap file path retrieval
    _object = (
        await FileUpload.objects.filter(id=request_id)
        .values_list("document", flat=True)
        .aget()
    )
    os.chdir(os.path.join(os.getcwd(), "../data/user_uploads"))

    # Use asyncio to run file operations in a thread
    if await sync_to_async(os.path.exists)(_object):
        await sync_to_async(os.remove)(_object)
        await logger.emit(f"{_object} removed", user_id=user_id)
    else:
        await logger.emit(f"{_object} not found", user_id=user_id)

    # TODO: Use result to send notification after success
    await FileUpload.objects.filter(id=request_id).adelete()
    return redirect("/model_manager/")


@login_required(login_url="/accounts/login")
async def delete_model(request: HttpRequest) -> HttpResponseRedirect:
    _ = await sync_to_async(lambda: request.user.is_authenticated)()
    request_id = str(request.GET.get("delete_model"))

    user_id: str = str(request.user.id)

    logger = webapp.logger.InMemoryLogHandler()  # root logger, or a named one

    await logger.emit(f"Deleted Model (ID: {request_id})", user_id=user_id)

    await CadevilDocument.objects.filter(id=request_id).adelete()
    return redirect("/model_manager/")


async def download_csv(request) -> HttpResponse:
    _ = await sync_to_async(lambda: request.user.is_authenticated)()

    user_id: str = str(request.user.id)

    logger = webapp.logger.InMemoryLogHandler()  # root logger, or a named one

    if request.method == "POST":
        config_dict = await CalculationConfig.objects.aget()
        await logger.emit(f"Sending Config File for Download (ID: {config_dict.id})", user_id=user_id)
        response = HttpResponse(str(config_dict.config), content_type='text/csv')
        response['Content-Disposition'] = f"attachment; filename='{await config_dict.async_get_description()}.csv'"
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
        # ifc_plot_svg = helpers.create_plan_svg_bboxes(ifc_path=files[0].document.path)

        data = CadevilDocument.objects.all()

        if request.headers.get("HX-Request"):
            # For HTMX requests, return an HTML fragment or success message

            return TemplateResponse(
                request=request,
                template="webapp/model_manager.html",
                context={
                    "files": files,
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
async def save_config(request: HttpRequest) -> HttpResponseRedirect:
    # config_file = await CalculationConfig.objects.filter(id=request_id, user=request.user).aget()
    _ = await sync_to_async(lambda: request.user.is_authenticated)()
    user_id: str = str(request.user.id)

    logger = webapp.logger.InMemoryLogHandler()  # root logger, or a named one

    if request.method == "POST":
        config_save_form = CalculationConfigForm(request.POST)

        if await CalculationConfig.objects.aexists():
            previous_config = await CalculationConfig.objects.aget()
            await logger.emit(f"Deleting Previous Config (ID: {previous_config.id})", user_id=user_id)
            await previous_config.adelete()

        if await sync_to_async(config_save_form.is_valid)():
            calculation_config = config_save_form.save(commit=False)
            calculation_config.user = request.user
            calculation_config.upload = config_save_form.cleaned_data.get("upload")
            calculation_config.config = helpers.csv_to_dict(
                filepath=config_save_form.cleaned_data.get("upload").document.path)
            await logger.emit(f"Saving Config (ID: {calculation_config.id})", user_id=user_id)

            await calculation_config.asave()
            return redirect("/accounts/user/")  # or wherever you want to go


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

    config_upload_form = ConfigUploadForm()
    config_save_form = CalculationConfigForm()
    if request.headers.get("HX-Request"):

        return TemplateResponse(
            request,
            "registration/user.html",
            context={
                "config_uploads": config_uploads,
                "config_upload_form": config_upload_form,
                "config_save_form": config_save_form,
                "initial_logs": f"{log_output}\n",
            },
        )
    else:
        return redirect("/")


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

    files = FileUpload.objects.filter(user=request.user)

    # Wrap ORM query
    data = CadevilDocument.objects.filter(id=document_id).get()

    building_metrics = data.building_metrics.get()
    # pprint(building_metrics)

    building_plots = []

    # Create and show the plots
    building_plots.append(chart_plotter.plot_mass(
        ifc_document=data,
    ))
    building_plots.append(chart_plotter.plot_material_waste_grades(
        ifc_document=data,
    ))
    building_plots.append(chart_plotter.create_onorm_1800_visualization(
        ifc_document=data,
    ))
    # messages.info(request, "Test message!")
    logger = webapp.logger.InMemoryLogHandler()  # root logger, or a named one
    if request.headers.get("HX-Request"):

        return TemplateResponse(
            request,
            "webapp/object_view.html",
            context={
                "data": data,
                "building_metrics": building_metrics,
                "files": files,
                "html_plot": building_plots,
                "initial_logs": f"{log_output}\n",
            },
        )
    else:
        return redirect("/")


@login_required(login_url="/accounts/login/")
@vary_on_headers("HX-Request")
def model_comparison(request: HttpRequest) -> TemplateResponse | HttpResponseRedirect:
    """
    Comparison view of all objects in a group
    """
    # TODO: Add filter for groups
    user_log_entries = USER_LOGS[str(request.user.id)]
    log_output = "\n".join(user_log_entries)

    # Wrap ORM query
    data = CadevilDocument.objects.all()
    if request.headers.get("HX-Request"):
        # For HTMX requests, return an HTML fragment or success message
        return TemplateResponse(request=request,
                                template="webapp/model_comparison.html",
                                context={
                                    "data": data,
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
    config_dict = await CalculationConfig.objects.aget()
    headers = config_dict.config["header"]
    data_dict = config_dict.config["data"]

    if request.method == 'POST':
        # 1) Parse the submitted form data
        # 2) Update your data model or in-memory dictionary
        # 3) Potentially re-write your CSV or update your DB

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
async def upload_config(request: HttpRequest) -> HttpResponseRedirect:
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
            return redirect("/accounts/user/")


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
            await logger.emit(f"Failed to Upload new Model File!", user_id=user_id)

            return redirect("/accounts/user/")


class BuildingMetricsViewSet(viewsets.ModelViewSet):
    queryset = BuildingMetrics.objects.all()
    serializer_class = BuildingMetricserializer
    permission_classes = [IsAuthenticated]


class CalculationConfigViewSet(viewsets.ModelViewSet):
    queryset = CalculationConfig.objects.all()
    serializer_class = CalculationConfigSerializer
    permission_classes = [IsAuthenticated]


class CadevilDocumentViewSet(viewsets.ModelViewSet):
    queryset = CadevilDocument.objects.all()
    serializer_class = CadevilDocumentSerializer
    permission_classes = [IsAuthenticated]

    @action(detail=True, methods=['post'], url_path='delete', url_name='delete')
    @vary_on_headers("HX-Request")
    def delete_via_post(self, request, pk=None):
        """
        Custom action to delete an object using POST.
        """
        instance = self.get_object()
        instance.delete()
        if request.headers.get("HX-Request"):
            # For HTMX requests, return an HTML fragment or success message
            return HttpResponse('<span style="color: green;">Object deleted successfully</span>')
        else:
            return JsonResponse({"message": "Object deleted successfully"})


class ConfigUploadViewSet(viewsets.ModelViewSet):
    queryset = ConfigUpload.objects.all()
    serializer_class = ConfigUploadSerializer
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]


class FileUploadViewSet(viewsets.ModelViewSet):
    queryset = FileUpload.objects.all()
    serializer_class = FileUploadSerializer
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    @action(detail=True, methods=['post'], url_path='calculate_model', url_name='calculate_model')
    @vary_on_headers("HX-Request")
    def calculate_model(
            self,
            request: HttpRequest,
            pk=None,
    ) -> HttpResponse | JsonResponse:
        user_id: str = str(request.user.id)
        start: float = time.time()
        logger = webapp.logger.InMemoryLogHandler()  # root logger, or a named one
        file = instance = self.get_object()
        user_config = CalculationConfig.objects.get()

        # TODO: Fork the rest of this to background
        # Process the IFC file asynchronously

        logger.sync_emit(f">>>>>> Starting Calculation of {file.description}!", user_id=user_id)
        # TODO: start this in different process

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
            ifc_file_path=file.document.path
        )

        building_metrics.project_id = ifc_document.id

        # Save the results asynchronously

        ifc_document.save()
        building_metrics.save()

        for material_name in material_properties.keys():
            material_metrics = material_properties.get(material_name)
            if material_metrics:
                material_metrics.name = material_name
                material_metrics.project_id = ifc_document.id
                material_metrics.save()
            else:
                logger.sync_emit(f">>??? {material_name} not in the dictionary", user_id=user_id)

        logger.sync_emit(f">>??? {material_properties.keys()}", user_id=str(request.user.id))
        logger.sync_emit(f">>>>>> Calculation done within {time.time() - start}s", user_id=str(request.user.id))

    def perform_create(self, serializer):
        """
        Automatically associate the uploaded file with the authenticated user.
        """
        serializer.save(user=self.request.user)

    def create(self, request, *args, **kwargs):
        """
        Custom create method to handle file uploads and return appropriate response.
        """
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid():
            self.perform_create(serializer)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class MaterialPropertiesViewSet(viewsets.ModelViewSet):
    queryset = MaterialProperties.objects.all()
    serializer_class = MaterialPropertiesSerializer
    permission_classes = [IsAuthenticated]
