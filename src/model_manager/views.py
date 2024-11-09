# -*- coding: utf-8 -*-
import json
import os

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
from model_manager.models import CadevilDocument, FileUpload

# TODO: Consider putting this on all methods to prevent django from processing PUT UPDATE or DELETE
# @require_http_methods(["GET", "POST"])


# TODO: Add require_POST and require_GET to all functions
def index(request: HttpRequest):
    return TemplateResponse(
        request,
        template="index.html",
        context={},
    )


@login_required(login_url="/accounts/login")
async def calculate_model(
    request: HttpRequest,
) -> TemplateResponse | HttpResponseRedirect:
    request_id = str(request.GET.get("calculate"))
    file = await FileUpload.objects.filter(id=request_id).aget()

    # TODO: Fork the rest of this to background
    # Process the IFC file asynchronously

    messages.info(request, f"Starting Calculation of {file.description}!")
    # TODO: start this in different process
    elements_by_materials, properties = helpers.ifc_product_walk(ifc_file_path=file.document.path)
    messages.info(request, "Test message!")

    # FIXME: This shit is currently needed to make this work
    _ = await sync_to_async(lambda: request.user.is_authenticated)()
    print(json.dumps(elements_by_materials))

    # Save the results asynchronously
    # doc = CadevilDocument()
    # doc.user = request.user
    # user_groups = await sync_to_async(lambda: list(request.user.groups.all()))()
    # doc.group = user_groups[0] if user_groups else None
    # doc.description = file.description
    # doc.properties = properties
    # doc.materials = elements_by_materials
    # await doc.asave()
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
) -> TemplateResponse | HttpResponseRedirect | None:
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
        document_form = UploadForm(
            request.POST, request.FILES, user=request.user, user_id=request.user.id
        )
        # Wrap form validation
        is_valid = await sync_to_async(document_form.is_valid)()
        if is_valid:
            # Save the form asynchronously
            file_upload: FileUpload = await sync_to_async(document_form.save)(
                commit=False
            )
            file_upload.user = request.user
            await sync_to_async(file_upload.save)()
            return HttpResponse(status=204)

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
    return TemplateResponse(
        request,
        "registration/user.html",
        {},
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

    # Wrap ORM query
    data = await sync_to_async(
        lambda: list(CadevilDocument.objects.filter(id=document_id)),
        thread_sensitive=True
    )()
    return TemplateResponse(
        request,
        "webapp/object_view.html",
        context={
            "data": data,
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
        lambda: list(CadevilDocument.objects.all()),
        thread_sensitive=True
    )()

    context = {
        "data": data,
    }

    return TemplateResponse(request, "webapp/model_comparison.html", context)