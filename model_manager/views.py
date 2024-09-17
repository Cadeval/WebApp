# -*- coding: utf-8 -*-
#

# FIXME add argument parser
# import argparse
import os

from django.forms import ChoiceField
# from django.http import HttpResponse
# Create your views here.
from django.shortcuts import redirect, render

from library.forms import DocumentForm, UploadForm, GroupForm
from library.ifc_extractor import IfcExtractor
from library.models import CadevilDocument, FileUpload  # , CadevilGroup


# from django.template import loader
# from django.template.response import TemplateResponse
# from django.urls import reverse

# example_properties = {
#     "Area": "200",
#     "EIKON": "3,40",
#     "BGF": "1848,36",
#     "NF": "12,82",
#     "BF/BGF": "0,69",
#     "Energy Rating": "C",
#     "Walls": ["343.21.02", "m2", 130, 1111, 62073],
#     "Ceiling": ["343.21.02", "m2", 130, 1111, 62073],
#     "Floor Panels": ["343.21.02", "m2", 130, 1111, 62073],
#     "Roof": ["343.21.02", "m2", 130, 1111, 62073],
#     "Facade": ["343.21.02", "m2", 130, 1111, 62073],
# }
#


def model_manager(request):
    if not request.user.is_authenticated:
        return redirect(to="/accounts/login")

    document_form = DocumentForm()
    group_form = GroupForm(request=request)


    if request.method == "POST":
        document_form = UploadForm(
            request.POST, request.FILES, user=request.user, user_id=request.user.id
        )
        if document_form.is_valid():
            file_upload = document_form.save(commit=False)
            file_upload.user = (
                request.user
            )  # Set the user to the currently logged-in user
            file_upload.save()
            return redirect("/model_manager")

    elif request.GET.get("toggle_hidden"):
        print(request.user)
        # request.user.update(view_hidden=True)
        # CadevilDocument.objects.filter(id=_id).update(properties=_document.properties)

        return redirect("/model_manager")

    elif request.GET.get("set_group"):
        _id = str(request.GET.get("set_group"))
        _group_choice_id = int(request.GET.get("group_field"))
        group = group_form.fields["group_field"].choices[_group_choice_id]
        CadevilDocument.objects.filter(id=_id).update(group=group[1])
        return redirect("/model_manager")

    elif request.GET.get("toggle"):
        _id = str(request.GET.get("toggle"))
        _is_active = not CadevilDocument.objects.filter(id=_id).get().is_active
        CadevilDocument.objects.filter(id=_id).update(is_active=_is_active)
        if group_form.is_valid():
            print(group_form.cleaned_data)
        return redirect("/model_manager")

    elif request.GET.get("delete_file"):
        _id = str(request.GET.get("delete"))

        # # FIXME finish file cleanup routine
        _object = f"data/media/{FileUpload.objects.filter(id=_id).values_list('document', flat=True).get()}"
        print(_object)
        if os.path.exists(_object):
            os.remove(_object)
            FileUpload.objects.filter(id=_id).delete()
        else:
            print(f"{_object} not found")
        return redirect("/model_manager")

    elif request.GET.get("delete_model"):
        _id = str(request.GET.get("delete"))
        CadevilDocument.objects.filter(id=_id).delete()

        return redirect("/model_manager")

    elif request.GET.get("calculate"):
        _id = str(request.GET.get("calculate"))
        _file = FileUpload.objects.filter(id=_id).get()
        _document = IfcExtractor(_file.document.path)
        _document.process_products()
        # properties["document_preview"] = _document.render_object()
        doc = CadevilDocument()
        doc.user = request.user
        doc.group = request.user.groups.all()[0]
        doc.description = _file
        doc.properties = _document.properties
        doc.materials = _document.material_dict
        doc.save()
        return redirect("/model_manager")

    else:
        return render(
            request,
            "cadevil/model_manager.html",
            context={
                "files": list(FileUpload.objects.filter(user=request.user)),
                "data": CadevilDocument.objects.all(),
                "form": document_form,
                "group_form": group_form,
            },
        )


def object_view(request):
    if not request.user.is_authenticated:
        return redirect(to="/accounts/login")
    form = DocumentForm()
    if request.GET.get("object"):
        _id = request.GET.get("object")
        _is_active = CadevilDocument.objects.filter(id=_id).get().is_active
        if not _is_active:
            return redirect("/model_manager")
    else:
        return redirect("/model_manager")

    return render(
        request,
        "cadevil/object_view.html",
        context={
            "data": CadevilDocument.objects.filter(id=_id),
            "form": form,
        },
    )


def model_comparison(request):
    if not request.user.is_authenticated:
        return redirect(to="/accounts/login")
    documents = CadevilDocument.objects.filter(is_active=True)

    properties = {
        # ÖNORM B 1800 / ÖNORM EN 15221-6
        "TSA": 0,  # Total something FIXME
        "BF": 0,  # Total Area of the plot Brutto Fläche
        "BGF": 0,  # BruttGo-Grundfläche (ÖNORM B 1800)
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

    # averages = dict()
    # # Calculate averages for numeric properties
    # for key in properties:
    #     if key not in [
    #         "Energy Rating",
    #         "Units",
    #         "Walls",
    #         "Ceiling",
    #         "Floor Panels",
    #         "Roof",
    #         "Facade",
    #     ]:
    #         avg = sum(properties[key]) / len(properties[key]) if properties[key] else 0
    #         averages[key] = avg
    #     else:
    #         averages[key] = "N/A"

    context = {
        "data": documents,
        "properties": properties,
    }

    return render(request, "cadevil/model_comparison.html", context)
