from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.views.decorators.vary import vary_on_headers


@login_required(login_url="/accounts/login/")
@vary_on_headers("HX-Request")
def snake_game(request: HttpRequest) -> HttpResponse:
    if not request.headers.get("HX-Request"):
        return redirect("/")
    return TemplateResponse(request, "rust_example_plugin/snake_game.jinja2")