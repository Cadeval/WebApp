# -*- coding: utf-8 -*-
#

# FIXME add argument parser
# import argparse

# from django.http import HttpResponse
# Create your views here.
from django.shortcuts import redirect, render

# from django.template import loader
# from django.template.response import TemplateResponse
# from django.urls import reverse
def index(request):
    return render(
        request,
        "index.html",
        {},
    )


def user(request):
    if not request.user.is_authenticated:
        return redirect(to="/accounts/login")

    return render(
        request,
        "registration/user.html",
        {},
    )