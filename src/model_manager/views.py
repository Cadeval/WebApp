# -*- coding: utf-8 -*-
import os
import time
from pprint import pprint

# from pprint import pprint
from asgiref.sync import sync_to_async
from django.contrib import messages
from django.contrib.auth.decorators import login_required
# from django.core.paginator import Paginator
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import redirect
from django.template.response import TemplateResponse

# from django.views.decorators.http import require_http_methods
# from django_htmx.middleware import HtmxDetails, HtmxMiddleware
# from typing_extensions import assert_type
from model_manager.forms import DocumentForm, GroupForm, UploadForm
from model_manager.ifc_extractor import helpers
from model_manager.ifc_extractor import chart_plotter
from model_manager.models import (
    CadevilDocument,
    FileUpload,
)


# TODO: Consider putting this on all methods to prevent django from processing PUT UPDATE or DELETE
# @require_http_methods(["GET", "POST"])


# TODO: Add require_POST and require_GET to all functions
async def index(request: HttpRequest):
    if await sync_to_async(lambda: request.user.is_authenticated)():
        files = await sync_to_async(
            lambda: list(FileUpload.objects.filter(user=request.user))
        )()
    else:
        files = None
    return TemplateResponse(
        request,
        template="index.html",
        context={
            "files": files,
        },
    )


@login_required(login_url="/accounts/login")
async def calculate_model(
    request: HttpRequest,
) -> TemplateResponse | HttpResponseRedirect:

    start = time.time()
    request_id = str(request.GET.get("calculate"))
    file = await FileUpload.objects.filter(id=request_id).aget()

    # TODO: Fork the rest of this to background
    # Process the IFC file asynchronously

    messages.info(request, f"Starting Calculation of {file.description}!")
    # TODO: start this in different process

    # FIXME: This shit is currently needed to make this work
    _ = await sync_to_async(lambda: request.user.is_authenticated)()
    # print(json.dumps(elements_by_materials))

    ifc_document = CadevilDocument()
    ifc_document.user = request.user
    ifc_document.description = file.description
    ifc_document.upload = file

    user_groups = await sync_to_async(lambda: list(request.user.groups.all()))()
    ifc_document.group = user_groups[0] if user_groups else None

    pprint(
        f">>>>>> Initiating metrics calculations at {time.time() - start}s",
        width=140,
        sort_dicts=True,
        compact=True,
    )

    building_metrics = await helpers.extract_building_properties(
        ifc_file_path=file.document.path
    )

    building_metrics.project_id = ifc_document.id
    # await helpers.ifc_product_walk(
    #     ifc_document=ifc_document, ifc_file_path=file.document.path
    # )

    # Save the results asynchronously
    await ifc_document.asave()
    await building_metrics.asave()

    material_properties = helpers.ifc_product_walk(
        ifc_document=ifc_document, ifc_file_path=file.document.path
    )

    for material_name in material_properties.keys():
        material_metrics = material_properties.get(material_name)
        if material_metrics:
            material_metrics.name = material_name
            material_metrics.project_id = ifc_document.id
            await material_metrics.asave()
        else:
            pprint(f">>??? {material_name} not in the dictionary")

    # pprint(f">>??? {material_properties.keys()}")

    pprint(
        f">>>>>> Calculation done within {time.time() - start}s",
        width=140,
        sort_dicts=True,
        compact=True,
    )
    return redirect("/model_manager")


@login_required(login_url="/accounts/login")
async def change_group(request: HttpRequest) -> HttpResponseRedirect:
    request_id = str(request.GET.get("set_group"))
    print(f"ID: {request_id}")
    group_choice_id = int(request.GET.get("group_field"))
    group_form = GroupForm(
        user_groups=await sync_to_async(lambda: list(request.user.groups.all()))()
    )

    group = group_form.fields["group_field"].choices[group_choice_id]
    _ = await CadevilDocument.objects.filter(id=request_id).aupdate(group=group[1])
    return redirect("/model_manager")


@login_required(login_url="/accounts/login")
async def delete_file(request: HttpRequest) -> HttpResponseRedirect:
    request_id = str(request.GET.get("delete_file"))

    # Wrap file path retrieval
    _object = (
        await FileUpload.objects.filter(id=request_id)
        .values_list("document", flat=True)
        .aget()
    )
    os.chdir(os.path.join(os.getcwd(), "../media"))
    print(os.getcwd())

    # Use asyncio to run file operations in a thread
    if await sync_to_async(os.path.exists)(_object):
        await sync_to_async(os.remove)(_object)
        print(f"{_object} removed")
    else:
        print(f"{_object} not found")

    # TODO: Use result to send notification after success
    _ = await FileUpload.objects.filter(id=request_id).adelete()
    return redirect("/model_manager")


@login_required(login_url="/accounts/login")
async def delete_model(request: HttpRequest) -> HttpResponseRedirect:
    request_id = str(request.GET.get("delete_model"))
    print(request_id)
    _ = await CadevilDocument.objects.filter(id=request_id).adelete()
    return redirect("/model_manager")


@login_required(login_url="/accounts/login")
async def model_manager(
    request: HttpRequest,
) -> TemplateResponse | HttpResponseRedirect | HttpResponse | None:
    # {{{
    # FIXME: This shit is currently needed to make this work
    _ = await sync_to_async(lambda: request.user.is_authenticated)()

    print(
        f"Current user {request.user} has this many running calculations {request.user.active_calculations}/{request.user.max_calculations}"
    )
    document_form = DocumentForm(user=request.user, user_id=request.user.id)
    group_form = GroupForm(
        user_groups=await sync_to_async(lambda: list(request.user.groups.all()))()
    )
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
            return redirect("/model_manager")
            # return HttpResponse(status=204)
        else:
            pprint("FUCK")

    else:
        # Wrap ORM queries
        files = await sync_to_async(
            lambda: list(FileUpload.objects.filter(user=request.user))
        )()
        data = await sync_to_async(lambda: list(CadevilDocument.objects.all()))()
        return TemplateResponse(
            request,
            "webapp/model_manager.html",
            context={
                "files": files,
                "data": data,
                "document_form": document_form,
                "group_form": group_form,
            },
        )
    # }}}


@login_required(login_url="/accounts/login")
async def user(request: HttpRequest) -> TemplateResponse:

    files = await sync_to_async(
        lambda: list(FileUpload.objects.filter(user=request.user))
    )()
    return TemplateResponse(
        request,
        "registration/user.html",
        context={
            "files": files,
        },
    )


@login_required(login_url="/accounts/login")
async def object_view(
    request: HttpRequest,
) -> TemplateResponse | HttpResponseRedirect | None:
    """
    Detail view of a given CadevilDocument instance
    """

    # Send user to model manager page if:
    #   - user does not specify any models in the url
    #   - user does not specify a valid model id
    if request.GET.get("object"):
        document_id = request.GET.get("object")
    else:
        return redirect("/model_manager")

    files = await sync_to_async(
        lambda: list(FileUpload.objects.filter(user=request.user))
    )()

    # Wrap ORM query
    data = await sync_to_async(
        lambda: list(CadevilDocument.objects.filter(id=document_id)),
        thread_sensitive=True,
    )()

    building_metrics = await data[0].building_metrics.aget()
    # pprint(building_metrics)

    # # Create a sample defaultdict with MaterialAccumulator
    # material_data = defaultdict(material_accumulator)
    #
    # # Populate with some example data
    # material_data["Steel"].recyclable_mass = 50.5
    # material_data["Steel"].waste_mass = 10.2
    # material_data["Steel"].global_brutto_price = 100.0
    #
    # material_data["Aluminum"].recyclable_mass = 45.3
    # material_data["Aluminum"].waste_mass = 8.7
    # material_data["Aluminum"].global_brutto_price = 120.0
    #
    # material_data["Copper"].recyclable_mass = 60.1
    # material_data["Copper"].waste_mass = 12.5
    # material_data["Copper"].global_brutto_price = 150.0

    # Generate plot with specific attributes
    specific_attrs = ["length", "mass"]

    # Create and show the plot
    html_plot = await chart_plotter.plot_material_waste_grades(
        ifc_document=data[0], attributes_to_plot=specific_attrs
    )

    messages.info(request, "Test message!")
    # print(type(data))

    return TemplateResponse(
        request,
        "webapp/object_view.html",
        context={
            "data": data[0],
            "building_metrics": building_metrics,
            "files": files,
            "html_plot": html_plot,
        },
    )


@login_required(login_url="/accounts/login")
async def model_comparison(request: HttpRequest) -> TemplateResponse:
    """
    Comparison view of all objects in a group
    """
    # TODO: Add filter for groups

    # Wrap ORM query
    data = await sync_to_async(
        lambda: list(CadevilDocument.objects.all()), thread_sensitive=True
    )()

    context = {
        "data": data,
    }

    return TemplateResponse(request, "webapp/model_comparison.html", context)
