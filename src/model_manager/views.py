# -*- coding: utf-8 -*-
import os
from pprint import pprint

from asgiref.sync import sync_to_async
from django.shortcuts import redirect
from django.template.response import TemplateResponse

from model_manager.forms import DocumentForm, UploadForm, GroupForm
# try:
from model_manager.ifc_extractor.data_models import IfcExtractor
# except ImportError as e:
#     pprint(e)
from model_manager.models import CadevilDocument, FileUpload


def index(request):
    return TemplateResponse(
        request,
        "index.html",
        {},
    )


def user(request):
    if not request.user.is_authenticated:
        return redirect(to="/accounts/login")

    return TemplateResponse(
        request,
        "registration/user.html",
        {},
    )


async def model_manager(request):
    # Wrap the access to request.user.is_authenticated
    is_authenticated = await sync_to_async(lambda: request.user.is_authenticated)()
    if not is_authenticated:
        return redirect(to="/accounts/login")

    document_form = DocumentForm(
        user=request.user, user_id=request.user.id
    )
    group_form = GroupForm(
        user_groups = await sync_to_async(lambda: list(request.user.groups.all()))()
    )

    if request.method == "POST":
        document_form = UploadForm(
            request.POST, request.FILES, user=request.user, user_id=request.user.id
        )
        # Wrap form validation
        is_valid = await sync_to_async(document_form.is_valid)()
        if is_valid:
            # Save the form asynchronously
            file_upload = await sync_to_async(document_form.save)(commit=False)
            file_upload.user = request.user
            await sync_to_async(file_upload.save)()
            return redirect("/model_manager")

    elif request.GET.get("toggle_hidden"):
        # Handle toggle_hidden functionality
        return redirect("/model_manager")

    elif request.GET.get("set_group"):
        _id = str(request.GET.get("set_group"))
        print(f"ID: {_id}")
        _group_choice_id = int(request.GET.get("group_field"))
        group = group_form.fields["group_field"].choices[_group_choice_id]
        # Wrap ORM update
        await sync_to_async(
            lambda: CadevilDocument.objects.filter(id=_id).update(group=group[1])
        )()
        return redirect("/model_manager")

    elif request.GET.get("toggle"):
        _id = str(request.GET.get("toggle"))
        # Wrap ORM operations
        document = await sync_to_async(lambda: CadevilDocument.objects.filter(id=_id).get())()
        _is_active = not document.is_active
        await sync_to_async(
            lambda: CadevilDocument.objects.filter(id=_id).update(is_active=_is_active)
        )()
        # Validate group form if needed
        is_valid = await sync_to_async(group_form.is_valid)()
        if is_valid:
            print(group_form.cleaned_data)
        return redirect("/model_manager")

    elif request.GET.get("delete_file"):
        request_id = str(request.GET.get("delete_file"))
        # Wrap file path retrieval
        _object = await sync_to_async(
            lambda: f"data/media/{FileUpload.objects.filter(id=request_id).values_list('document', flat=True).get()}"
        )()
        # Use asyncio to run file operations in a thread
        if await sync_to_async(os.path.exists)(_object):
            await sync_to_async(os.remove)(_object)
            print(f"{_object} removed")
        else:
            print(f"{_object} not found")
        # Wrap ORM delete
        await sync_to_async(lambda: FileUpload.objects.filter(id=request_id).delete())()
        return redirect("/model_manager")

    elif request.GET.get("delete_model"):
        request_id = str(request.GET.get("delete_model"))
        # Wrap ORM delete
        await sync_to_async(lambda: CadevilDocument.objects.filter(id=request_id).delete())()
        return redirect("/model_manager")

    elif request.GET.get("calculate"):
        request_id = str(request.GET.get("calculate"))
        # Wrap ORM get
        _file = await sync_to_async(lambda: FileUpload.objects.filter(id=request_id).get())()
        # Process the IFC file asynchronously
        cadevil_document = IfcExtractor(_file.document.path)
        await cadevil_document.process_products()
        # Save the results asynchronously
        doc = CadevilDocument()
        doc.user = request.user
        user_groups = await sync_to_async(lambda: list(request.user.groups.all()))()
        print(user_groups)
        doc.group = user_groups[0] if user_groups else None
        doc.description = _file
        doc.properties = cadevil_document.properties
        doc.materials = cadevil_document.material_dict
        await sync_to_async(doc.save)()
        return redirect("/model_manager")

    else:
        # Wrap ORM queries
        files = await sync_to_async(lambda: list(FileUpload.objects.filter(user=request.user)))()
        data = await sync_to_async(lambda: list(CadevilDocument.objects.all()))()
        return TemplateResponse(
            request,
            "webapp/model_manager.html",
            context={
                "files": files,
                "data": data,
                "form": document_form,
                "group_form": group_form,
            },
        )


async def object_view(request):
    # Wrap the access to request.user.is_authenticated
    is_authenticated = await sync_to_async(lambda: request.user.is_authenticated)()
    if not is_authenticated:
        return redirect(to="/accounts/login")

    form = DocumentForm()
    if request.GET.get("object"):
        _id = request.GET.get("object")
        # Wrap ORM get
        doc = await sync_to_async(lambda: CadevilDocument.objects.filter(id=_id).get())()
        _is_active = doc.is_active
        if not _is_active:
            return redirect("/model_manager")
    else:
        return redirect("/model_manager")

    # Wrap ORM query
    data = await sync_to_async(lambda: list(CadevilDocument.objects.filter(id=_id)))()
    return TemplateResponse(
        request,
        "webapp/object_view.html",
        context={
            "data": data,
            "form": form,
        },
    )


async def model_comparison(request):
    # Wrap the access to request.user.is_authenticated
    is_authenticated = await sync_to_async(lambda: request.user.is_authenticated)()
    if not is_authenticated:
        return redirect(to="/accounts/login")

    # Wrap ORM query
    documents = await sync_to_async(lambda: list(CadevilDocument.objects.filter(is_active=True)))()

    properties = {
        # ÖNORM B 1800 / ÖNORM EN 15221-6
        "TSA": 0,  # Total site area (FIXME)
        "BF": 0,  # Total plot area (Brutto Fläche)
        "BGF": 0,  # Brutto-Grundfläche (ÖNORM B 1800)
        "BGF/BF": 0,  # Ratio of BF to BGF
        "NGF": 0,  # Netto-Grundfläche (ÖNORM B 1800)
        "NF": 0,  # Nutzfläche (ÖNORM B 1800)
        "KGF": 0,  # Konstruktions-Grundfläche (ÖNORM B 1800)
        "BRI": 0,  # Brutto-Rauminhalt (ÖNORM B 1800)
        "EIKON": 0,  # EIKON value (example placeholder)
        "Energy Rating": "Unknown",  # Energy rating of the building
        "Floors": 0,  # Number of floors
        "Facade Area": 0,
    }

    context = {
        "data": documents,
        "properties": properties,
    }

    return TemplateResponse(request, "webapp/model_comparison.html", context)
