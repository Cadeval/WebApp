from typing import Annotated

from django.contrib.auth import aauthenticate, alogin, alogout
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django_bolt import AllowAny, BoltAPI
from django_bolt.params import Form

# Authentication uses Django sessions; browser handlers apply their access guards.
api = BoltAPI(django_middleware=True)

# Session-authenticated browser pages share the same full/fragment renderer.
from django.contrib.auth.decorators import login_required
from apps.shared.bolt_pages import page_endpoint
from apps.shared.page_views import render_page

@api.get('/', guards=[AllowAny()])
@page_endpoint
def index(request):
    return render_page(request, 'index.jinja2')

@api.get('/mycelium/login', guards=[AllowAny()])
@page_endpoint
def login_page(request):
    if request.user.is_authenticated:
        return HttpResponseRedirect('/')
    return render_page(request, 'login.jinja2')

@api.post("/mycelium/login", guards=[AllowAny()])
async def login(
    request: HttpRequest,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
):
    user = await aauthenticate(request=request, username=username, password=password)
    if user:
        await alogin(request=request, user=user)
        response = HttpResponse(status=200)
        response["HX-Redirect"] = "/"
        return response
    else:
        response = HttpResponse(status=401)
        response["HX-Redirect"] = "/mycelium/login"
        return response


@api.post("/mycelium/logout")
async def logout(request: HttpRequest):
    await alogout(request)
    return HttpResponseRedirect(redirect_to="/")


@api.get('/mycelium/user', guards=[AllowAny()])
@api.get('/mycelium/profile', guards=[AllowAny()])
@api.get('/mycelium/user/profile', guards=[AllowAny()])
@page_endpoint
@login_required(login_url='/mycelium/login')
def user_view(request):
    return HttpResponseRedirect('/mycelium/settings')


from .settings_views import user_settings
from .user_admin import admin_users, admin_groups


@api.get('/mycelium/settings', name='user_settings', guards=[AllowAny()])
@api.post('/mycelium/settings', name='user_settings_save', guards=[AllowAny()])
@page_endpoint
def account_settings(request):
    return user_settings(request)


@api.get('/mycelium/settings/users', name='admin_users', guards=[AllowAny()])
@api.post('/mycelium/settings/users', name='admin_users_save', guards=[AllowAny()])
@page_endpoint
def user_management(request):
    return admin_users(request)


@api.get('/mycelium/settings/groups', name='admin_groups', guards=[AllowAny()])
@api.post('/mycelium/settings/groups', name='admin_groups_save', guards=[AllowAny()])
@page_endpoint
def group_management(request):
    return admin_groups(request)

@api.get('/demo', guards=[AllowAny()])
@page_endpoint
def recorded_demo(request):
    response = render_page(request, 'bim/demo.html', {'public_demo': True})
    return response
